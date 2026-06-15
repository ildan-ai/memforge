# memforge.recall: query-triggered recall reference implementation.
#
# Spec: 0.6.1  (SPEC.md §"Recall operation"; tier-vs-recall clarification v0.6.1+)
#
# v0.6.1 conformance note: recall eligibility is governed by liveness +
# sensitivity, NOT tier (SPEC §"Tier and recall"). This module stores `tier` but
# never excludes on it; the optional "rank index above detail on a tie" allowance
# is deliberately not implemented (ranking stays score / desc-length / uid).
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

    Synonym symmetry (recall-syn-01): when `synonyms` is None (the default and
    the shipped-CLI path), both build and query resolve the SAME per-folder map
    (default + any .memforge/recall-synonyms.yaml override), so they match. If
    you pass an EXPLICIT `synonyms` map here that is NOT persisted as that
    override file, you MUST pass the same map to recall(synonyms=...) at query
    time. The default reader path reconstructs the folder map and, finding its
    hash differs from the one this index was built with, falls back to the
    built-in defaults -- so synonym-expanded triggers would silently fail to
    match. Either persist the map as the folder override, or thread the explicit
    map through both build_index and recall.
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
            # Loop the disambiguation suffix until it is genuinely unused. A
            # single `#{dup}` could collide with a memory that legitimately
            # carries `uid: A#1`, silently overwriting that live entry and
            # dropping it from the recall index (closes idxgen-02).
            base = uid
            while uid in entries:
                dup += 1
                uid = f"{base}#{dup}"

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
        # Skip pairs whose two tokens are identical post-canonicalization (e.g.
        # "oauth login" -> "auth auth"): that is an artifact of stopword removal
        # + synonym collapse, not genuine source adjacency, and it only ever
        # inflates phrase score. Done symmetrically on the query side (recall-05).
        for src in [name] + list(explicit):
            toks = _normalize_tokens(src, rev, canon)
            for i in range(len(toks) - 1):
                if toks[i] == toks[i + 1]:
                    continue
                ph = f"{toks[i]} {toks[i + 1]}"
                bucket = phrases_map.setdefault(ph, [])
                if uid not in bucket:
                    bucket.append(uid)

    payload = {
        "version": INDEX_VERSION,
        "spec": "0.6.1",
        "folder": str(folder),
        "counts": {
            "entries": len(entries),
            "tokens": len(tokens_map),
            "phrases": len(phrases_map),
            # Human/debug visibility only. The per-entry `always` flag on each
            # entry is authoritative; recall() reconstructs the always-set from
            # those flags and never reads a top-level always list (recall-03).
            "always": len(always),
        },
        "entries": entries,
        "tokens": tokens_map,
        "phrases": phrases_map,
        "manifest": manifest,
        "synonyms_hash": _synonyms_hash(synonyms),
    }
    # Route the freshly built payload through the SAME sanitizer the load path
    # uses (_sanitize_payload: strips control chars from name/desc/path, caps
    # lengths, drops malformed entries). build_index feeds both the persisted
    # index AND the CLI --rebuild path, which passes the in-memory payload
    # straight to recall() without load_index. Without this, ANSI/control-char
    # escapes smuggled via crafted frontmatter reach injected output on the
    # rebuild path while being stripped on the load path (recall-01).
    sanitized = _sanitize_payload(payload, str(folder))
    return sanitized if sanitized is not None else payload


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
    when nothing changed. Conservative: any error => stale (rebuild).

    Limitation (recall-stale-01): this compares st_mtime with a 1e-6 tolerance.
    On filesystems with coarse mtime granularity (some network/FAT volumes) or
    for a content edit that lands within the same mtime tick, a real change can
    be missed, leaving --rebuild serving a stale index. The escape hatch is
    `memory-recall --force-rebuild`, which bypasses this check entirely; an
    operator with a known-changed store on such a volume should use it."""
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


# Open hierarchical access labels: at recall time (no viewer-tier authorization
# is plumbed through), only these two never restrict. Every OTHER non-team label
# (counsel, restricted, privileged, or any unknown role) is treated as a
# restriction the viewer cannot satisfy through this interface, so it fails
# CLOSED. This mirrors cli/index_gen.apply_rbac_filter, where with no
# --viewer-tier any tier/role label that is not satisfiable defaults to deny.
_OPEN_ACCESS = frozenset({"public", "internal"})


def _access_ok(entry_access: list[str], viewer_teams: Optional[set[str]]) -> bool:
    # Access RBAC, fail-CLOSED on any restricting label. Mirrors the structure of
    # cli/index_gen.apply_rbac_filter (tier gate AND team gate) so build-time and
    # recall-time RBAC agree:
    #   - no access labels at all          -> visible (operator default)
    #   - label in {public, internal}      -> open, does not restrict
    #   - team:<x>                         -> the team gate passes iff the viewer
    #                                         holds at least one listed team
    #   - ANY OTHER label (counsel, an
    #     unknown/privileged role, etc.)   -> default-DENY (the tier/role gate
    #                                         cannot pass without viewer-tier auth,
    #                                         which recall does not plumb through)
    # This closes the prior bypass where a memory with access: [counsel] surfaced
    # its description to every viewer because only `team:`-prefixed labels were
    # gated; any non-`team:` restricting label slipped through as "no team".
    if not entry_access:
        return True
    teams = set(viewer_teams or set())
    team_labels = [a for a in entry_access if a.startswith("team:")]
    other_labels = [a for a in entry_access if not a.startswith("team:")]

    # Tier/role gate: any non-team label that is not open is unsatisfiable here.
    for label in other_labels:
        if label not in _OPEN_ACCESS:
            return False

    # Team gate: present team labels require the viewer to hold one of them.
    if team_labels and not (set(team_labels) & teams):
        return False
    return True


def _resolve_payload_synonyms(
    payload: dict[str, Any], folder: str
) -> tuple[dict[str, str], set[str]]:
    """Build the (rev, canon) normalization tables to use for one payload's query
    normalization, preferring the merged synonym map from the payload's own
    folder so a per-folder operator override is applied symmetrically with build.
    Falls back to the built-in defaults when the folder is unknown/unreadable or
    when the loaded map's hash does not match the one the index was built with
    (a stale index relative to the override; rebuild is the operator's fix)."""
    if folder:
        try:
            merged = load_synonyms(Path(folder))
            stored = payload.get("synonyms_hash")
            # When the index recorded a hash, only trust the folder map if it
            # still matches; otherwise the override changed since the last build.
            if stored is None or _synonyms_hash(merged) == stored:
                return _build_synonym_rev(merged)
        except Exception:
            pass  # fall through to defaults; recall never raises
    return _build_synonym_rev(_DEFAULT_SYNONYMS)


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
    exclusion, sensitivity/access filtering, liveness (baked in at build).

    Synonym symmetry: the query MUST be normalized with the same synonym map the
    index was built with, or an operator synonym override (e.g. {kubernetes:
    [k8s]}) silently fails to match. When `synonyms` is given, it is applied to
    every payload. When it is None (the common reader path), each payload's query
    normalization uses the merged synonym map loaded from that payload's own
    folder, so a per-folder override is honored. The build-time synonyms_hash is
    used to confirm the loaded map matches the one the index was compiled with;
    on mismatch we fall back to the built-in defaults for that payload (the index
    is stale wrt the override and a rebuild is the operator's fix)."""
    explicit_syn = synonyms is not None
    # Cache rev/canon by folder so repeated payloads from the same folder do not
    # re-load + re-build the synonym map.
    _norm_cache: dict[str, tuple[dict[str, str], set[str]]] = {}
    if explicit_syn:
        _shared_rev, _shared_canon = _build_synonym_rev(synonyms)

    always_hits: list[Hit] = []
    matched: list[Hit] = []

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        entries = payload.get("entries", {})
        tokens_map = payload.get("tokens", {})
        phrases_map = payload.get("phrases", {})
        folder = str(payload.get("folder", ""))

        # Resolve the synonym map to normalize THIS payload's query against.
        if explicit_syn:
            rev, canon = _shared_rev, _shared_canon
        else:
            cached = _norm_cache.get(folder)
            if cached is None:
                cached = _resolve_payload_synonyms(payload, folder)
                _norm_cache[folder] = cached
            rev, canon = cached
        qtoks = _normalize_tokens(query or "", rev, canon)
        qtokset = set(qtoks)
        # Symmetric with build: drop identical-adjacent pairs (recall-05).
        qphrases = {
            f"{qtoks[i]} {qtoks[i + 1]}"
            for i in range(len(qtoks) - 1)
            if qtoks[i] != qtoks[i + 1]
        }

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
    # Always-set is unconditional and comes first. Dedup the WHOLE ordered list
    # by uid (first occurrence wins) so a uid present in two payloads (e.g. a
    # global-memory file configured under two folders) is listed once, whether
    # it is an always or a matched hit (recall-04).
    ordered: list[Hit] = []
    seen_uids: set[str] = set()
    for h in always_hits + matched:
        if h.uid in seen_uids:
            continue
        seen_uids.add(h.uid)
        ordered.append(h)

    # Apply top-K + char budget. Always-items are never dropped; matched items
    # stop once top_k or the char budget is reached.
    out: list[Hit] = []
    used = 0
    for h in ordered:
        if not h.always:
            # Mirror the rendered markdown line so the budget is honest:
            #   "- {name} ({folder}/{path}): {desc}"
            # The folder prefix (often 30-50+ chars) is part of the injected
            # line, so it MUST be counted; the literal "- " + " (" + "/" + "): "
            # separators are ~8 chars. The folder rstrip mirrors cli/recall.py.
            line_len = (
                len(h.name)
                + len(h.folder.rstrip("/"))
                + len(h.path)
                + len(h.desc)
                + 8
            )
            if out and (len(out) >= top_k or used + line_len > char_budget):
                break
            used += line_len
        out.append(h)
    return out
