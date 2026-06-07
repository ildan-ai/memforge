# memforge.recall: query-triggered recall reference implementation.
#
# Spec: 0.6.0  (SPEC.md §"Recall operation")
#
# Two operations, deliberately separate per the spec:
#   build_index(folder)            -> compile the inverted index from frontmatter
#   recall(query, payloads, ...)   -> query-time matching over compiled indexes
#
# The build step is a "compile-time" activity (walk + parse + tokenize); the
# query step is a latency-sensitive "run-time" activity that a per-query hook
# may invoke on every prompt. Keeping them separate lets the build run on memory
# change while the query stays a single index load + rank.
#
# Design constraints honored here:
#   - Recall surfaces DESCRIPTIONS, never bodies. `description` is public-class
#     metadata that must be free of secrets / PII / codenames (SPEC
#     §"Sensitivity and the description field"), so injection is safe by
#     construction.
#   - All recall frontmatter fields are OPTIONAL. Absence => derive triggers
#     from name+tags+description, always=false, do_not_inject=false. A malformed
#     value degrades to its default; it never raises.
#   - Pure-Python. No runtime dependency beyond PyYAML (already required), and
#     PyYAML is only needed for the optional operator synonym override file.
#
# This module is UI-neutral. It knows nothing about any specific agent or IDE.

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from memforge.frontmatter import has_frontmatter, parse as _mf_parse

# --- artifact constants -----------------------------------------------------

INDEX_VERSION = 1
INDEX_REL_PATH = ".memforge/recall-index.json"
SYNONYMS_REL_PATH = ".memforge/recall-synonyms.yaml"

LIVE_STATUSES = frozenset({"active", "proposed", "gated"})
_ARCHIVE = "archive"

# Default recall budget + ranking weights. All implementation-defined per spec;
# overridable at the CLI layer.
DEFAULT_TOP_K = 8
DEFAULT_CHAR_BUDGET = 1200
TOKEN_WEIGHT = 1.0
PHRASE_WEIGHT = 2.5
DESC_MAX_CHARS = 200  # descriptions are spec'd <~200 chars; truncate for the index

# Defensive bounds against a tampered/pathological index or oversized files.
MAX_INDEX_BYTES = 8 * 1024 * 1024     # load-time cap on the index artifact size
MAX_INDEX_ENTRIES = 50_000            # load-time cap on entry count
FRONTMATTER_READ_BYTES = 65536        # only the file head is needed for frontmatter

# Control characters that must never reach injected output (defense in depth
# against content smuggling via crafted descriptions / names / paths).
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_WORD = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}")

_STOP = frozenset(
    """a an the and or of to in on for with at by from as is are be was were this
    that these those it its into via per not no nor than then so if when you your
    we our my me i he she they them his her their use used using do does did how
    what why which who whom whose can may might will would should could about over
    under after before between out off up down only just also more most some any
    each new now get got make made set run see read write file files""".split()
)

# Minimal, GENERIC default synonym map (operator-extensible via
# .memforge/recall-synonyms.yaml). Deliberately small and domain-neutral; the
# spec leaves the synonym scheme implementation-defined. Keys + forms are
# normalized to lowercase canonical tokens.
_DEFAULT_SYNONYMS: dict[str, list[str]] = {
    "config": ["configuration", "settings", "cfg", "conf"],
    "auth": ["authentication", "authorization", "login", "signin", "oauth"],
    "database": ["db", "datastore", "sql", "postgres", "sqlite"],
    "documentation": ["doc", "docs", "readme"],
    "dependency": ["dependencies", "deps", "package", "packages"],
    "environment": ["env", "envvar", "environ"],
    "directory": ["dir", "folder"],
    "repository": ["repo", "repos"],
    "function": ["func", "fn", "method"],
    "release": ["publish", "ship", "deploy"],
    "error": ["err", "bug", "failure", "exception"],
    "test": ["tests", "testing", "pytest"],
}


# --- normalization (shared by build + query) --------------------------------


def _build_synonym_rev(synonyms: dict[str, list[str]]) -> tuple[dict[str, str], set[str]]:
    """Return (surface->canonical, canonical-set) from a synonym map."""
    rev: dict[str, str] = {}
    canon: set[str] = set()
    for c, forms in (synonyms or {}).items():
        cl = str(c).lower().strip()
        if not cl:
            continue
        canon.add(cl)
        rev[cl] = cl
        for f in forms or []:
            fl = str(f).lower().strip()
            if fl:
                rev[fl] = cl
    return rev, canon


def _stem(token: str, rev: dict[str, str], canon: set[str]) -> str:
    """Light suffix fold (ing/es/s) + synonym canonicalization. Pure-Python;
    deliberately conservative to avoid over-folding."""
    t = token.lower().strip("-_")
    for suf in ("ing", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            cand = t[: -len(suf)]
            if cand in rev or cand in canon:
                t = cand
                break
    return rev.get(t, t)


def _tokenize(text: str) -> list[str]:
    return [m.group(0) for m in _WORD.finditer(text.lower())]


def _normalize_tokens(text: str, rev: dict[str, str], canon: set[str]) -> list[str]:
    out: list[str] = []
    for raw in _tokenize(text):
        if raw in _STOP:
            continue
        n = _stem(raw, rev, canon)
        if n in _STOP or len(n) < 3:
            continue
        out.append(n)
    return out


def _normalize_desc(value: Any) -> str:
    """One-line, length-bounded description for the index. NFKD-normalize, strip
    newlines, truncate. Never mutates the source file."""
    s = "" if value is None else str(value)
    s = unicodedata.normalize("NFKD", s).replace("\n", " ").replace("\r", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > DESC_MAX_CHARS:
        s = s[: DESC_MAX_CHARS - 3].rstrip() + "..."
    return s


def _clean(value: Any, limit: int) -> str:
    """Strip control characters + newlines and length-cap a string. Used to
    sanitize any field that may reach injected output or come from a tampered
    index."""
    s = _CTRL.sub(" ", str(value if value is not None else ""))
    s = s.replace("\n", " ").replace("\r", " ").strip()
    return s[:limit]


def _as_bool(value: Any) -> bool:
    """Spec: a present-but-non-boolean always/do_not_inject degrades to default
    false (NO truthy-string coercion)."""
    return value is True


def _as_str_list(value: Any) -> list[str]:
    """Spec: malformed `triggers` (not a list of strings) -> ignore, derive."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    return []


def _access_labels(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(a).strip().lower() for a in value if str(a).strip()]
    if isinstance(value, str) and value.strip():
        return [a.strip().lower() for a in value.split(",") if a.strip()]
    return []


# --- synonym loading --------------------------------------------------------


def load_synonyms(folder: Path) -> dict[str, list[str]]:
    """Default synonym map merged with an optional operator override at
    .memforge/recall-synonyms.yaml (override keys win). Never raises."""
    merged: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_SYNONYMS.items()}
    override = folder / SYNONYMS_REL_PATH
    if not override.is_file():
        return merged
    try:
        import yaml  # local import: only needed when an override exists
        data = yaml.safe_load(override.read_text(encoding="utf-8"))
        custom = (data or {}).get("map", data) if isinstance(data, dict) else None
        if isinstance(custom, dict):
            for k, v in custom.items():
                if isinstance(k, str) and isinstance(v, list):
                    merged[k.lower().strip()] = [str(x).lower().strip() for x in v if str(x).strip()]
    except Exception:
        pass  # malformed override -> fall back to defaults
    return merged


def _synonyms_hash(synonyms: dict[str, list[str]]) -> str:
    return hashlib.sha256(
        json.dumps(synonyms, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


# --- index build ------------------------------------------------------------


def _iter_memory_files(folder: Path):
    """Yield non-archive .md files under folder. Does NOT follow symlinks
    (symlinked dirs are not descended via followlinks=False; symlinked files are
    skipped) to prevent reading or stat-leaking files outside the memory root
    and to avoid symlink-loop DoS."""
    folder = Path(folder)
    for root, dirs, files in os.walk(folder, followlinks=False):
        # Prune reserved subtrees in place (top-down walk).
        dirs[:] = [d for d in dirs if d not in (_ARCHIVE, ".memforge", ".git")]
        root_path = Path(root)
        for fn in sorted(files):
            if not fn.endswith(".md") or fn == "MEMORY.md":
                continue
            p = root_path / fn
            if p.is_symlink():
                continue  # never ingest a symlinked file
            yield p


def build_index(folder: Path, synonyms: Optional[dict[str, list[str]]] = None) -> dict[str, Any]:
    """Compile the recall inverted index for one memory folder.

    Walks every non-archive .md with frontmatter, includes only LIVE memories
    (status in {active, proposed, gated}; absence => active), and derives the
    trigger set from explicit `triggers` UNION name + tags + description.
    Returns the index payload dict (caller writes it via write_index).
    """
    folder = Path(folder)
    if synonyms is None:
        synonyms = load_synonyms(folder)
    rev, canon = _build_synonym_rev(synonyms)

    entries: dict[str, dict[str, Any]] = {}
    tokens_map: dict[str, list[str]] = {}
    phrases_map: dict[str, list[str]] = {}
    always: list[str] = []
    manifest: dict[str, float] = {}
    dup = 0

    for path in _iter_memory_files(folder):
        rel = path.relative_to(folder).as_posix()
        try:
            # Read only the head: frontmatter is small, and this caps memory use
            # if a large non-memory file is mistakenly named *.md (local DoS).
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read(FRONTMATTER_READ_BYTES)
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # Track every readable .md (not just live ones) so the staleness check
        # matches the file set it re-scans; editing a superseded file back to
        # active, or adding a no-frontmatter file, correctly triggers a rebuild.
        manifest[rel] = mtime
        if not has_frontmatter(text):
            continue
        fm, _body = _mf_parse(text)
        if not fm:
            continue
        status = fm.get("status", "active")
        if status not in LIVE_STATUSES:
            continue

        uid = fm.get("uid") or f"path:{rel}"
        if uid in entries:
            dup += 1
            uid = f"{uid}#{dup}"

        name = str(fm.get("name") or path.stem)
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        explicit = _as_str_list(fm.get("triggers"))

        entries[uid] = {
            "uid": uid,
            "path": rel,
            "name": name,
            "desc": _normalize_desc(fm.get("description")),
            "tier": str(fm.get("tier") or "index"),
            "sensitivity": str(fm.get("sensitivity") or "internal").lower(),
            "access": _access_labels(fm.get("access")),
            "always": _as_bool(fm.get("always")),
            "do_not_inject": _as_bool(fm.get("do_not_inject")),
        }
        if entries[uid]["always"] and not entries[uid]["do_not_inject"]:
            always.append(uid)

        # Derived trigger tokens: explicit triggers + name + tags + description.
        seen: set[str] = set()
        sources = list(explicit) + [name] + [str(t).replace("topic:", "") for t in tags]
        sources.append(str(fm.get("description") or ""))
        for src in sources:
            for tok in _normalize_tokens(src, rev, canon):
                if tok in seen:
                    continue
                seen.add(tok)
                bucket = tokens_map.setdefault(tok, [])
                if uid not in bucket:
                    bucket.append(uid)

        # 2-word phrases from name + explicit triggers (high-signal sources).
        for src in [name] + list(explicit):
            toks = _normalize_tokens(src, rev, canon)
            for i in range(len(toks) - 1):
                ph = f"{toks[i]} {toks[i + 1]}"
                bucket = phrases_map.setdefault(ph, [])
                if uid not in bucket:
                    bucket.append(uid)

    payload = {
        "version": INDEX_VERSION,
        "spec": "0.6.0",
        "folder": str(folder),
        "counts": {
            "entries": len(entries),
            "tokens": len(tokens_map),
            "phrases": len(phrases_map),
            "always": len(always),
        },
        "always": sorted(always),
        "entries": entries,
        "tokens": tokens_map,
        "phrases": phrases_map,
        "manifest": manifest,
        "synonyms_hash": _synonyms_hash(synonyms),
    }
    return payload


def write_index(folder: Path, payload: dict[str, Any]) -> Path:
    """Atomically write the index to <folder>/.memforge/recall-index.json."""
    folder = Path(folder)
    out = folder / INDEX_REL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    return out


def index_is_stale(folder: Path, payload: dict[str, Any]) -> bool:
    """True if any in-scope file changed/added/removed vs the index manifest.
    Cheap whole-index staleness check (stat only); lets a rebuild be skipped
    when nothing changed. Conservative: any error => stale (rebuild)."""
    try:
        folder = Path(folder)
        prior = payload.get("manifest", {})
        if not isinstance(prior, dict):
            return True
        current: dict[str, float] = {}
        for path in _iter_memory_files(folder):
            try:
                current[path.relative_to(folder).as_posix()] = path.stat().st_mtime
            except OSError:
                return True
        if set(current) != set(prior):
            return True
        for rel, mt in current.items():
            if abs(float(prior.get(rel, -1)) - mt) > 1e-6:
                return True
        return False
    except Exception:
        return True


# --- query ------------------------------------------------------------------


@dataclass
class Hit:
    uid: str
    name: str
    path: str
    desc: str
    score: float
    always: bool
    folder: str


def _sanitize_payload(payload: dict[str, Any], folder: str) -> Optional[dict[str, Any]]:
    """Validate + sanitize a loaded index. The index is a derived artifact that a
    tampered workspace could rewrite, and its contents are injected into the
    agent's context, so every field is re-validated and bounded here (drop
    malformed entries; cap counts + lengths; strip control characters)."""
    entries = payload.get("entries")
    if not isinstance(entries, dict) or len(entries) > MAX_INDEX_ENTRIES:
        return None
    clean: dict[str, dict[str, Any]] = {}
    for uid, e in entries.items():
        if not isinstance(uid, str) or not isinstance(e, dict):
            continue
        access = e.get("access") or []
        clean[uid] = {
            "uid": uid,
            "path": _clean(e.get("path", ""), 512),
            "name": _clean(e.get("name", uid), 256),
            "desc": _clean(e.get("desc", ""), DESC_MAX_CHARS),
            "tier": _clean(e.get("tier", "index"), 32),
            "sensitivity": _clean(e.get("sensitivity", "internal"), 32).lower(),
            "access": [_clean(a, 64).lower() for a in access if isinstance(a, str)][:32],
            "always": e.get("always") is True,
            "do_not_inject": e.get("do_not_inject") is True,
        }
    payload["entries"] = clean
    for key in ("tokens", "phrases"):
        m = payload.get(key)
        payload[key] = (
            {k: [u for u in v if isinstance(u, str)]
             for k, v in m.items() if isinstance(k, str) and isinstance(v, list)}
            if isinstance(m, dict) else {}
        )
    payload["folder"] = str(payload.get("folder") or folder)
    return payload


def load_index(folder: Path) -> Optional[dict[str, Any]]:
    """Load a folder's recall index, or None if missing/unreadable/incompatible/
    oversized. Never raises (fail-open-empty per spec post-condition 7)."""
    try:
        p = Path(folder) / INDEX_REL_PATH
        if p.stat().st_size > MAX_INDEX_BYTES:
            return None
        payload = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != INDEX_VERSION:
            return None
        return _sanitize_payload(payload, str(folder))
    except Exception:
        return None


def _sensitivity_ok(entry_sens: str, ceiling: Optional[str]) -> bool:
    if ceiling is None:
        return True
    try:
        rank = {"public": 0, "internal": 1, "restricted": 2, "privileged": 3}
        # Unknown entry sensitivity is treated as the strictest (privileged) so
        # an unrecognized label fails CLOSED rather than leaking past a ceiling.
        return rank.get(entry_sens, 3) <= rank.get(ceiling, 3)
    except Exception:
        return False  # fail closed on any internal error in a security check


def _access_ok(entry_access: list[str], viewer_teams: Optional[set[str]]) -> bool:
    # Team-scoped entries are visible only to viewers in a listed team. Entries
    # with no team label are visible. Hierarchical access is handled by the
    # sensitivity ceiling above; access teams are the orthogonal gate.
    teams = [a for a in entry_access if a.startswith("team:")]
    if not teams:
        return True
    if not viewer_teams:
        return False
    return bool(set(teams) & viewer_teams)


def recall(
    query: str,
    payloads: list[dict[str, Any]],
    top_k: int = DEFAULT_TOP_K,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    sensitivity_max: Optional[str] = None,
    viewer_teams: Optional[set[str]] = None,
    synonyms: Optional[dict[str, list[str]]] = None,
) -> list[Hit]:
    """Match a query against one or more compiled index payloads and return a
    ranked, budgeted list of Hits (descriptions only). Honors the spec
    post-conditions: always-set inclusion, do_not_inject suppression, body
    exclusion, sensitivity/access filtering, liveness (baked in at build)."""
    if synonyms is None:
        synonyms = _DEFAULT_SYNONYMS
    rev, canon = _build_synonym_rev(synonyms)
    qtoks = _normalize_tokens(query or "", rev, canon)
    qtokset = set(qtoks)
    qphrases = {f"{qtoks[i]} {qtoks[i + 1]}" for i in range(len(qtoks) - 1)}

    always_hits: list[Hit] = []
    matched: list[Hit] = []

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        entries = payload.get("entries", {})
        tokens_map = payload.get("tokens", {})
        phrases_map = payload.get("phrases", {})
        folder = str(payload.get("folder", ""))

        score: dict[str, float] = {}
        for t in qtokset:
            for uid in tokens_map.get(t, []):
                score[uid] = score.get(uid, 0.0) + TOKEN_WEIGHT
        for ph in qphrases:
            for uid in phrases_map.get(ph, []):
                score[uid] = score.get(uid, 0.0) + PHRASE_WEIGHT

        for uid, e in entries.items():
            if not isinstance(e, dict):
                continue
            if e.get("do_not_inject"):
                continue
            if not _sensitivity_ok(str(e.get("sensitivity", "internal")), sensitivity_max):
                continue
            if not _access_ok(list(e.get("access", [])), viewer_teams):
                continue
            hit = Hit(
                uid=uid,
                name=str(e.get("name", uid)),
                path=str(e.get("path", "")),
                desc=str(e.get("desc", "")),
                score=score.get(uid, 0.0),
                always=bool(e.get("always")),
                folder=folder,
            )
            if hit.always:
                always_hits.append(hit)
            elif hit.score > 0:
                matched.append(hit)

    # Rank matched: score desc, then shorter description, then uid for stability.
    matched.sort(key=lambda h: (-h.score, len(h.desc), h.uid))
    # Always-set is unconditional and comes first (dedup by uid against matched).
    always_uids = {h.uid for h in always_hits}
    ordered = always_hits + [h for h in matched if h.uid not in always_uids]

    # Apply top-K + char budget. Always-items are never dropped; matched items
    # stop once top_k or the char budget is reached.
    out: list[Hit] = []
    used = 0
    for h in ordered:
        if not h.always:
            line_len = len(h.name) + len(h.desc) + len(h.path) + 6
            if out and (len(out) >= top_k or used + line_len > char_budget):
                break
            used += line_len
        out.append(h)
    return out
