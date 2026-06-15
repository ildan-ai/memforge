"""memory-lint - recall-readiness + token-cost quality analysis for MemForge.

Sibling to `memory-audit`, with a deliberately different contract:

  - `memory-audit` is DETERMINISTIC conformance: frontmatter validity, enums,
    required fields, structural invariants. Binary pass/fail. CI-wireable.
  - `memory-lint` is GRADED quality: is a memory recall-findable? is its
    description distinctive? is the always-set carrying its weight? Advisory,
    never a hard gate, and it NEVER mutates files.

Safety posture (load-bearing, do not weaken without a panel):

  - LOCAL-ONLY BY DEFAULT. The judgment layer (LLM suggestions) is OFF unless
    the operator passes --allow-cloud (or points --dispatcher at a local model).
  - METADATA-ONLY cloud payload by default: name + tags + description. The body
    is shipped to a cloud model ONLY with the stronger --allow-cloud-body opt-in.
  - A deterministic secret/PII pre-scan runs BEFORE any dispatch; a hit blocks
    that memory from cloud analysis (fail-closed).
  - Descriptions are public-classification metadata per spec, but an existing
    memory set may be noncompliant; we warn rather than assume.

The recall-context score reuses the EXACT trigger-derivation primitives from
`memforge.recall` (tokenization, stemming, synonym canonicalization, the
inverted token index) so a score reflects how the real recall consumer would
see the memory, not a parallel heuristic that could drift.

Spec ref: §"Recall operation (v0.6.0+)", §"Sensitivity and the description
field".
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from memforge.frontmatter import has_frontmatter, parse
from memforge.recall import (
    DESC_MAX_CHARS,
    LIVE_STATUSES,
    _access_labels,
    _access_ok,
    _build_synonym_rev,
    _iter_memory_files,
    _normalize_tokens,
    _sensitivity_ok,
    build_index,
    load_synonyms,
)
from memforge.cli._llm_dispatch import is_local_dispatcher


LONG_BODY_CHARS = 1500          # index-tier body over this -> suggest rollup/detail
DISPATCH_TIMEOUT_S = 120

# Cloud-egress sensitivity ceiling (lint-sensitivity-01). Only memories at or
# below this sensitivity are eligible for a cloud dispatcher; restricted /
# privileged memories are never shipped to a cloud model regardless of whether
# they carry a secret pattern. Matches agents_md_gen's DEFAULT_MAX_SENSITIVITY
# and recall's default surfacing posture. An unknown/absent sensitivity is
# treated by recall._sensitivity_ok as the strictest (fails CLOSED), so it is
# excluded too. SPEC §"Recall-readiness lint" > "Cloud-dispatch safety posture
# (normative)" point 4: external dispatch MUST honor the same sensitivity/access
# filtering as any other surfacing path.
CLOUD_SENSITIVITY_CEILING = "internal"


def cloud_egress_eligible(fm: dict) -> Optional[str]:
    """Return None when this memory may be shipped to a CLOUD dispatcher, or a
    machine-readable skip reason string when it must not.

    Reuses recall._sensitivity_ok / _access_ok so lint's external-dispatch
    filtering is identical to every other surfacing path (no parallel heuristic
    that could drift). Restricted/privileged sensitivity, or any restricting
    `access` label (anything outside the open public/internal set, e.g.
    access: [counsel]), excludes the memory from cloud egress. Local dispatch is
    NOT gated here: a local model never leaves the box, so the sensitivity/access
    egress posture applies only to the cloud path (caller decides)."""
    sens = str(fm.get("sensitivity") or "internal").lower()
    if not _sensitivity_ok(sens, CLOUD_SENSITIVITY_CEILING):
        return "sensitivity_above_ceiling"
    # No viewer-team authorization is plumbed through lint, so any restricting
    # access label fails closed exactly as recall does for an unauthenticated
    # surfacing path.
    if not _access_ok(_access_labels(fm.get("access")), None):
        return "access_restricted"
    return None

_GENERIC_DESC_RE = re.compile(
    r"^\s*(see\s+(the\s+)?file|notes?|details?|info(rmation)?|misc(ellaneous)?|"
    r"important(\s+note)?|various|stuff|things?|tbd|todo|context|background|"
    r"reference|summary|overview|this|that|it)\b[\s\.:;,-]*$",
    re.IGNORECASE,
)

# Narrow, high-precision rules: unconditional (AKIA, PEM, gh*, xox*, keyword:=).
_SECRET_RES = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token|bearer|"
               r"private[_-]?key|client[_-]?secret)\b\s*[:=]"),
]

# Broad, low-precision rules: 40+ base64 / 32+ hex. These false-positive on
# benign long tokens (git anchors, long CamelCase names), silently suppressing
# LLM suggestions for safe memories (lint-prescan-01). Gate them behind a Shannon
# entropy floor (reusing dlp_scan.shannon_entropy) so English CamelCase and
# low-entropy hex slugs do not trip them; only genuinely high-entropy tokens do.
_BROAD_SECRET_RES = [
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]
# 4.2 floor: a 40-char hex git anchor / commit hash maxes at ~4.0 bits-per-char
# (16-symbol alphabet) and long English CamelCase identifiers sit around
# ~4.0-4.1, so both clear; a real base64/base62 secret runs ~5+ bits and still
# fires. Hex-shaped secrets co-located with a keyword are caught by the
# unconditional keyword rule, so the residual false-negative window (a secret
# whose entropy falls below 4.2 with no keyword) is narrow and the prior
# fail-closed posture is preserved for credential shapes (lint-prescan-01).
_BROAD_SECRET_ENTROPY_FLOOR = 4.2


def default_paths() -> list[Path]:
    """Default memory folders via the centralized, IDE/OS-neutral resolver
    (existence-filtered)."""
    from memforge.paths import default_memory_paths

    return [p for p in default_memory_paths() if p.exists()]


def _iter_live_memories(folder: Path) -> list[tuple[str, dict, str]]:
    """Return (rel_path, frontmatter, body) for every live memory under folder.

    Live = status in {active, proposed, gated} or absent. Uses the SAME file
    iterator as recall.build_index (_iter_memory_files: os.walk, no symlink
    follow, prunes archive/.memforge/.git) so lint's per-memory set and the
    collision index it scores against are computed over an identical corpus.
    A divergent walk (e.g. rglob, which follows symlinks and descends
    .memforge/) would make collision scores reference a different n_entries
    than the memories being scored.
    """
    folder = Path(folder)
    out: list[tuple[str, dict, str]] = []
    for path in _iter_memory_files(folder):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not has_frontmatter(text):
            continue
        fm, body = parse(text)
        if not fm:
            continue
        if fm.get("status", "active") not in LIVE_STATUSES:
            continue
        out.append((path.relative_to(folder).as_posix(), fm, body))
    return out


def _collision_threshold(n_entries: int) -> int:
    """A token is 'distinctive in this corpus' when it occurs in at most this
    many memories. Scales with corpus size: ~5%, floored at 2."""
    return max(2, math.ceil(0.05 * n_entries))


def score_recall_context(
    fm: dict,
    body: str,
    tokens_map: dict[str, list[str]],
    rev: dict[str, str],
    canon: set[str],
    n_entries: int,
) -> dict[str, Any]:
    """Score how findable this memory is by the recall consumer.

    Returns {score:int 0-5, finding:str, evidence:str, distinctive:list[str]}.
    The score reflects the description's contribution of distinctive,
    low-collision query terms beyond what the name already supplies.
    """
    desc = str(fm.get("description") or "").strip()
    name = str(fm.get("name") or "")
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    if not desc:
        return {"score": 0, "finding": "description_missing",
                "evidence": "no description field (also an audit violation)",
                "distinctive": []}

    if _GENERIC_DESC_RE.match(desc):
        return {"score": 1, "finding": "description_generic",
                "evidence": f"description '{desc[:80]}' is a contentless filler "
                            "phrase; recall queries will not match it",
                "distinctive": []}

    desc_tokens = set(_normalize_tokens(desc, rev, canon))
    name_tokens = set(_normalize_tokens(name, rev, canon))
    tag_tokens: set[str] = set()
    for t in tags:
        tag_tokens |= set(_normalize_tokens(str(t).replace("topic:", ""), rev, canon))
    all_derived = desc_tokens | name_tokens | tag_tokens

    if not all_derived:
        return {"score": 1, "finding": "no_keywords",
                "evidence": "no query-bearing tokens derivable from name/tags/"
                            "description (all stopwords or too short)",
                "distinctive": []}

    thresh = _collision_threshold(n_entries)
    low_collision = {t for t in all_derived if len(tokens_map.get(t, [])) <= thresh}
    distinctive_desc = (desc_tokens - name_tokens)
    desc_low_collision = sorted(distinctive_desc & low_collision)

    if desc_low_collision:
        return {"score": 5, "finding": "recall_strong",
                "evidence": "description carries distinctive query term(s): "
                            f"{', '.join(desc_low_collision[:5])}",
                "distinctive": desc_low_collision}

    name_low_collision = sorted(low_collision & name_tokens)
    if name_low_collision:
        return {"score": 3, "finding": "description_adds_no_distinct_term",
                "evidence": "name carries the distinctive term(s) "
                            f"({', '.join(name_low_collision[:5])}) but the "
                            "description adds none; description is recall-redundant",
                "distinctive": name_low_collision}

    return {"score": 2, "finding": "all_terms_common",
            "evidence": "every derived term is high-collision across the corpus; "
                        "this memory is hard to disambiguate from its neighbors",
            "distinctive": []}


def token_cost_findings(fm: dict, body: str, recall_score: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    desc = str(fm.get("description") or "")
    if len(desc) > DESC_MAX_CHARS:
        out.append({"code": "description_too_long", "dimension": "token_cost",
                    "evidence": f"description is {len(desc)} chars (>{DESC_MAX_CHARS}); "
                                "trim for the per-query recall budget"})
    if fm.get("always") is True and fm.get("do_not_inject") is not True and recall_score <= 2:
        out.append({"code": "always_with_weak_recall", "dimension": "token_cost",
                    "evidence": "always:true pays the per-query cost on every recall, "
                                "but this memory's recall-context score is weak; "
                                "improve the description or drop always"})
    tier = str(fm.get("tier") or "index")
    if tier == "index" and len(body) > LONG_BODY_CHARS:
        out.append({"code": "long_index_body", "dimension": "token_cost",
                    "evidence": f"index-tier body is {len(body)} chars; consider "
                                "memory-rollup or tier:detail to keep the read cheap"})
    return out


def injected_file_findings(fm: dict, injected_texts: list[str]) -> list[dict[str, Any]]:
    """Only runs when the operator supplied --injected-file. Flags a memory that
    appears to already live in an always-loaded instruction file, so recall need
    not re-inject it. Manual finding only; never auto-set do_not_inject."""
    if not injected_texts:
        return []
    name = str(fm.get("name") or "").strip()
    if len(name) < 8:
        return []
    if fm.get("do_not_inject") is True:
        return []
    for txt in injected_texts:
        if name.lower() in txt.lower():
            return [{"code": "possible_duplicate_of_injected", "dimension": "token_cost",
                     "evidence": f"name string '{name}' appears in an injected/always-"
                                 "loaded file (substring match only; content-equivalence "
                                 "NOT established). If the rule itself already lives there, "
                                 "consider do_not_inject: true (operator decision, not "
                                 "auto-applied)"}]
    return []


def secret_prescan(payload: str) -> Optional[str]:
    """Return the matched pattern's label if the payload looks sensitive, else
    None. Runs before any cloud dispatch; a hit fails closed.

    The narrow high-precision rules match unconditionally. The broad base64/hex
    rules only fire when the matched token clears an entropy floor, so benign
    long tokens (git anchors, long CamelCase names) no longer silently suppress
    suggestions for safe memories (lint-prescan-01)."""
    from memforge.cli.dlp_scan import shannon_entropy

    for rx in _SECRET_RES:
        if rx.search(payload):
            return rx.pattern[:40]
    for rx in _BROAD_SECRET_RES:
        for m in rx.finditer(payload):
            if shannon_entropy(m.group(0)) >= _BROAD_SECRET_ENTROPY_FLOOR:
                return rx.pattern[:40]
    return None


# ---------- judgment layer (opt-in LLM suggestions) ----------

_LLM_PROMPT = (
    "You improve MemForge memory recall metadata. Given a memory's name, tags, "
    "and description (and optionally body), suggest a sharper one-line "
    "description (<=200 chars, no secrets/PII/codenames), up to 6 high-signal "
    "trigger keywords NOT already obvious from name/tags, and up to 3 canonical "
    "queries that should retrieve this memory. Return STRICT JSON: "
    '{"suggested_description": str, "suggested_triggers": [str], '
    '"canonical_queries": [str]}. No prose.\n\nMEMORY:\n'
)


def _dispatch_llm(dispatcher: str, payload: str) -> Optional[dict]:
    # shell=True executes the OPERATOR-supplied dispatcher string verbatim
    # (dedup-shell-injection-06). The prompt+payload (which may carry hostile
    # memory content) is passed on STDIN, never interpolated into the command, so
    # crafted memory content cannot inject shell. The dispatcher string itself is
    # operator-controlled (--dispatcher / env / probed PATH) and is trusted as
    # such; treat env/flag dispatcher values as trusted input.
    try:
        proc = subprocess.run(
            dispatcher, shell=True, input=_LLM_PROMPT + payload,
            capture_output=True, text=True, timeout=DISPATCH_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = (proc.stdout or "").strip()
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ---------- orchestration ----------


def lint_folder(
    folder: Path,
    *,
    injected_texts: list[str],
    allow_cloud: bool,
    allow_cloud_body: bool,
    dispatcher: Optional[str],
    min_score: int,
) -> dict[str, Any]:
    """Analyze one folder. Read-only; returns a report dict."""
    synonyms = load_synonyms(folder)
    rev, canon = _build_synonym_rev(synonyms)
    payload_idx = build_index(folder, synonyms=synonyms)
    tokens_map: dict[str, list[str]] = payload_idx.get("tokens", {})
    n_entries = int(payload_idx.get("counts", {}).get("entries", 0))

    records: list[dict[str, Any]] = []
    for rel, fm, body in _iter_live_memories(folder):
        rc = score_recall_context(fm, body, tokens_map, rev, canon, n_entries)
        findings = token_cost_findings(fm, body, rc["score"])
        findings += injected_file_findings(fm, injected_texts)

        rec: dict[str, Any] = {
            "path": rel,
            "uid": fm.get("uid"),
            "recall_context": {"score": rc["score"], "finding": rc["finding"],
                               "evidence": rc["evidence"]},
            "findings": findings,
        }

        # Judgment layer: only for weak memories, only when a dispatcher was
        # given. Every non-dispatch path records an explicit, machine-readable
        # skip reason so the operator knows why no suggestion appeared.
        if rc["score"] <= min_score and dispatcher:
            is_local = is_local_dispatcher(dispatcher)
            if not is_local and not allow_cloud:
                # A cloud dispatcher with no opt-in: never builds a payload, never
                # dispatches. Fail-closed and transparent.
                rec["llm"] = {"skipped": "cloud_disabled"}
            elif not is_local and (egress_skip := cloud_egress_eligible(fm)):
                # CLOUD dispatch is sensitivity/access-gated (lint-sensitivity-01):
                # a restricted/privileged memory, or one with a restricting access
                # label, never reaches a cloud model even when it carries no secret
                # pattern and --allow-cloud[-body] is set. The secret pre-scan is a
                # net for credential SHAPES only; the label gate is the normative
                # egress posture per SPEC. Local dispatch is unaffected (never
                # leaves the box).
                rec["llm"] = {"skipped": egress_skip}
            else:
                meta = (f"name: {fm.get('name')}\ntags: {fm.get('tags')}\n"
                        f"description: {fm.get('description')}\n")
                cloud_payload = meta + (f"body:\n{body}\n" if allow_cloud_body else "")
                leak = secret_prescan(cloud_payload)
                if leak:
                    rec["llm"] = {"skipped": "secret_prescan_blocked", "pattern": leak}
                else:
                    suggestion = _dispatch_llm(dispatcher, cloud_payload)
                    rec["llm"] = suggestion or {"skipped": "no_parseable_suggestion"}

        records.append(rec)

    weak = [r for r in records if r["recall_context"]["score"] <= min_score]
    # Deterministic recall floor (lint-strict-01): only these findings are a
    # hard, reproducible defect (no description / contentless filler). The graded
    # collision score is NOT in this set, so --strict never trips on it.
    _FLOOR_FINDINGS = {"description_missing", "description_generic"}
    floor = [r for r in records
             if r["recall_context"]["finding"] in _FLOOR_FINDINGS]
    return {
        "folder": str(folder),
        "entries": n_entries,
        "weak_count": len(weak),
        "floor_count": len(floor),
        "records": records,
    }


# ---------- rendering ----------


def _render_text(report: dict[str, Any]) -> None:
    print()
    print(f"====== {report['folder']} ======")
    print(f"  live memories: {report['entries']}  | recall-weak: {report['weak_count']}")
    print()
    for rec in report["records"]:
        rc = rec["recall_context"]
        llm = rec.get("llm")
        if rc["score"] <= 3 or rec["findings"] or llm:
            print(f"  {rec['path']}  recall={rc['score']}/5 ({rc['finding']})")
            print(f"      {rc['evidence']}")
            for f in rec["findings"]:
                print(f"      - [{f['dimension']}] {f['code']}: {f['evidence']}")
            if isinstance(llm, dict) and "suggested_description" in llm:
                print(f"      LLM suggested description: {llm['suggested_description']}")
                if llm.get("suggested_triggers"):
                    print(f"      LLM suggested triggers: {llm['suggested_triggers']}")
                if llm.get("canonical_queries"):
                    print(f"      LLM canonical queries: {llm['canonical_queries']}")
            elif isinstance(llm, dict) and llm.get("skipped"):
                print(f"      LLM: skipped ({llm['skipped']})")
            print()


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="memory-lint",
        description=(
            "Recall-readiness + token-cost quality analysis for MemForge folders. "
            "Advisory and READ-ONLY: it never modifies memory files. Local-only by "
            "default; the LLM suggestion layer requires an explicit opt-in."
        ),
    )
    p.add_argument("--path", action="append", type=Path, default=[],
                   help="Lint only this dir (repeatable; overrides defaults).")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="Emit machine-readable JSON.")
    p.add_argument("--min-score", type=int, default=3,
                   help="Treat memories at or below this recall score (0-5) as "
                        "weak; only weak memories get LLM suggestions (default 3).")
    p.add_argument("--injected-file", action="append", type=Path, default=[],
                   help="An always-loaded instruction file (e.g. CLAUDE.md). "
                        "Enables do_not_inject duplicate suggestions. Repeatable.")
    p.add_argument("--dispatcher", default=None,
                   help="Shell command for the LLM suggestion layer. Must read the "
                        "prompt on stdin. Off by default (deterministic-only).")
    p.add_argument("--allow-cloud", action="store_true",
                   help="Permit a non-local dispatcher. Without this, only "
                        "dispatchers matching a known local-model pattern run.")
    p.add_argument("--allow-cloud-body", action="store_true",
                   help="Include the memory BODY in the cloud payload (default: "
                        "metadata-only). Implies the body may contain sensitive "
                        "content; use only on a sanitized memory set.")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 ONLY on the DETERMINISTIC recall floor "
                        "(description_missing / description_generic), NOT on the "
                        "graded collision score. SPEC §'Recall-readiness lint' "
                        "keeps lint advisory and forbids it as a hard gate on a "
                        "conformance-correct store; the graded score must never "
                        "fail CI on legacy not-yet-optimized descriptions "
                        "(lint-strict-01). This flag is opt-in CI tightening on "
                        "the deterministic floor only.")
    args = p.parse_args(argv)

    injected_texts: list[str] = []
    for f in args.injected_file:
        try:
            injected_texts.append(Path(f).expanduser().read_text(encoding="utf-8", errors="replace"))
        except OSError:
            sys.stderr.write(f"warn: cannot read injected file {f}\n")

    if args.allow_cloud_body and not args.allow_cloud and not (
            args.dispatcher and is_local_dispatcher(args.dispatcher)):
        sys.stderr.write("error: --allow-cloud-body requires --allow-cloud or a local dispatcher\n")
        return 2

    # Cloud opt-in with no dispatcher is a silent no-op: the judgment layer at
    # lint_folder gates on `dispatcher`, so no LLM suggestion ever runs. Make
    # the no-op visible rather than letting the operator believe cloud body
    # analysis was enabled (lint-03).
    if (args.allow_cloud or args.allow_cloud_body) and not args.dispatcher:
        sys.stderr.write(
            "warn: cloud opt-in flag(s) set but no --dispatcher provided; "
            "the LLM suggestion layer is off (deterministic-only)\n"
        )

    targets: list[Path] = [pp.expanduser().resolve() for pp in args.path] or default_paths()
    if not targets:
        sys.stderr.write("error: no memory folders found; pass --path\n")
        return 2

    reports: list[dict[str, Any]] = []
    total_weak = 0
    total_floor = 0
    for t in targets:
        if not t.is_dir():
            sys.stderr.write(f"warn: {t} is not a directory; skipping\n")
            continue
        rep = lint_folder(
            t,
            injected_texts=injected_texts,
            allow_cloud=args.allow_cloud,
            allow_cloud_body=args.allow_cloud_body,
            dispatcher=args.dispatcher,
            min_score=args.min_score,
        )
        reports.append(rep)
        total_weak += rep["weak_count"]
        total_floor += rep.get("floor_count", 0)
        if not args.json_out:
            _render_text(rep)

    if args.json_out:
        print(json.dumps(reports, indent=2))
    else:
        print(f"Total recall-weak memories across targets: {total_weak}")
        print(
            f"Deterministic recall-floor violations "
            f"(description_missing/generic): {total_floor}"
        )

    # --strict trips ONLY on the deterministic floor, never the graded score
    # (lint-strict-01: SPEC keeps lint advisory; the graded score must not fail
    # CI on a conformance-correct but not-yet-recall-optimized store).
    if args.strict and total_floor > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
