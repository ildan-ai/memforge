"""memory-audit - health + integrity checks for MemForge memory folders.

Cross-platform Python rewrite of the prior bash implementation.

Default: audits both per-cwd memory and ~/.claude/global-memory/.
Integrity violations (orphans, bad frontmatter, cap violations) return
nonzero when --strict is set, so this script is CI-wireable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from memforge.frontmatter import has_frontmatter, parse
# S5: caps are single-sourced in memforge.constants so audit, validate, and
# index-gen cannot drift (closes the 150-vs-180 split).
from memforge.constants import MEMORY_MD_LINE_CAP, POINTER_LINE_BYTE_CAP


VALID_TYPES = {"user", "feedback", "project", "reference"}
VALID_SENSITIVITIES = {"public", "internal", "restricted", "privileged"}
SPEC_TIERS = {"index", "detail"}


# ---------- helpers ----------


def _default_paths() -> list[Path]:
    """Default memory folders via the centralized, IDE/OS-neutral resolver.

    Existence-filtered for consistency with the sibling audit-deep / audit-log
    tools (ext-01): a non-Claude-Code user gets [] and the no-folders path
    rather than a per-folder '(directory does not exist, skipping)' banner."""
    from memforge.paths import default_memory_paths

    return [p for p in default_memory_paths() if p.exists()]


_POINTER_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")
_BULLET_POINTER_RE = re.compile(r"^- \[")


def _extract_pointers(memory_md: Path) -> list[str]:
    if not memory_md.is_file():
        return []
    text = memory_md.read_text(encoding="utf-8", errors="replace")
    return _POINTER_RE.findall(text)


def _disk_md_files(folder: Path) -> list[str]:
    """Files comparable to MEMORY.md pointers.

    Returns top-level .md files (excluding MEMORY.md itself) plus rollup-
    subfolder README.md files. Per spec §"Rollup subfolders", a rollup
    README.md is a tier:index file that surfaces in the parent MEMORY.md;
    detail-tier files inside the same subfolder do NOT. The archive/
    subfolder is excluded from recursion.

    Returns sorted POSIX-relative paths (subfolder READMEs as
    "<topic>/README.md").
    """
    out: list[str] = []
    for p in sorted(folder.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        out.append(p.name)
    for sub in sorted(folder.iterdir()):
        if not sub.is_dir() or sub.name == "archive":
            continue
        readme = sub / "README.md"
        if readme.is_file():
            out.append(f"{sub.name}/README.md")
    return out


def _all_md_files_recursive(folder: Path) -> set[str]:
    """All .md files under folder, POSIX-relative.

    Used to distinguish "pointer at truly-missing file" (integrity violation)
    from "pointer at subfolder detail file that exists but is non-canonical"
    (health advisory). Excludes MEMORY.md and the top-level archive/
    subfolder (matching `_files_to_audit` semantics; a nested folder named
    "archive" deeper in the tree is NOT excluded).
    """
    out: set[str] = set()
    for p in folder.rglob("*.md"):
        if p.name == "MEMORY.md":
            continue
        try:
            # rglob may yield paths reached via a symlink that resolves
            # outside `folder`; relative_to then raises ValueError. Skip
            # those rather than crash.
            rel = p.relative_to(folder)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "archive":
            continue
        out.add(rel.as_posix())
    return out


def _files_to_audit(folder: Path) -> list[str]:
    """All files whose frontmatter must be validated.

    Per spec §"Rollup subfolders": "Audit tools MUST recurse into rollup
    subfolders (excluding archive/) to validate frontmatter, but MUST NOT
    generate parent-MEMORY.md pointers for detail files." This function
    is the recursion target: top-level .md files (excluding MEMORY.md)
    plus every .md file inside any first-level subfolder (excluding
    archive/). The orphan-pointer check still runs against
    `_disk_md_files()`, which only includes pointer-comparable files.

    Depth contract: this is intentionally ONE level deep (top-level + first-
    level subfolder), because the spec §"Rollup subfolders" rollup model is a
    single `<topic>/` directory of detail files. This is shallower than the
    rglob helpers (`_all_md_files_recursive`, the v0.4 concurrency
    `collect_state`), which are fully recursive for cross-file invariants (uid
    uniqueness, alias graphs) that must see the whole tree. A .md file two-plus
    levels deep is therefore walked by those helpers but NOT frontmatter-
    validated here; that is by design for the current rollup model (audit-03).
    Switch to rglob (excluding archive/) if a deeper nesting convention lands.

    Returns sorted POSIX-relative paths.
    """
    out: list[str] = []
    for p in sorted(folder.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        out.append(p.name)
    for sub in sorted(folder.iterdir()):
        if not sub.is_dir() or sub.name == "archive":
            continue
        for p in sorted(sub.glob("*.md")):
            out.append(f"{sub.name}/{p.name}")
    return out


def _file_has_why(body: str) -> bool:
    return "**Why:**" in body


def _file_has_apply(body: str) -> bool:
    return "**How to apply:**" in body


# v0.6.0 recall-field shape checks. The spec (§"Recall operation") makes
# `triggers`, `always`, and `do_not_inject` optional, but says a consumer MUST
# fall back to safe defaults AND a tool SHOULD emit an audit WARN when a field
# is present but malformed. These are WARN-only (health), never integrity
# BLOCKERs: a malformed recall field degrades recall, it does not corrupt the
# store.


def _recall_field_warnings(fname: str, fm: dict) -> list[str]:
    out: list[str] = []
    if "triggers" in fm:
        trig = fm.get("triggers")
        if not (isinstance(trig, list) and all(isinstance(t, str) for t in trig)):
            out.append(
                f"{fname}: triggers_malformed (present but not a list of strings; "
                "recall falls back to derived triggers)"
            )
    for field in ("always", "do_not_inject"):
        if field in fm and not isinstance(fm.get(field), bool):
            out.append(
                f"{fname}: {field}_malformed (present but not a boolean; "
                "recall treats it as the default false)"
            )
    return out


# v0.6.1 relative-date heuristic. The spec (§"project") says project memories
# MUST use absolute dates ("Thursday"/"next week" lose meaning as a memory
# ages). This is a high-precision WARN scoped to type:project only, per the
# design-panel guidance (deterministic, never fails audit, suggest an absolute
# date rather than rewrite). Kept tight to avoid false positives on legitimate
# rolling phrases ("current branch", "Q3 roadmap", "this quarter we close the
# round"). "this <period>" is intentionally NOT matched: it is overwhelmingly a
# legitimate rolling reference, not an aging absolute date (closes recall-02).
# Only "last"/"next" (unambiguously past/future relative offsets) are flagged.
# craft-04: covers number-WORDS, DIGITS ("2 weeks ago", "revisit in 3 days"),
# and last/next + (period | weekday). Bare weekday names ("we ship Thursday")
# are intentionally NOT matched: standalone weekday words are too common in
# legitimate prose to flag at high precision, so this heuristic is a deliberate
# high-precision subset and NOT a completeness guarantee (a clean run does not
# prove "no relative dates present").
_REL_DATE_RE = re.compile(
    r"\b(yesterday|tomorrow"
    r"|(last|next)\s+(week|month|quarter|year"
    r"|mon(day)?|tue(s|sday)?|wed(nesday)?|thu(r|rs|rsday)?|fri(day)?|"
    r"sat(urday)?|sun(day)?)"
    r"|(a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|several|\d+)\s+"
    r"(day|days|week|weeks|month|months)\s+ago)\b",
    re.IGNORECASE,
)


def _relative_date_warning(fname: str, ftype: str, body: str) -> Optional[str]:
    if ftype != "project":
        return None
    m = _REL_DATE_RE.search(body)
    if not m:
        return None
    return (
        f"{fname} (project): relative date '{m.group(0)}' "
        "(project memories should use absolute dates; they lose meaning as the "
        "memory ages)"
    )


def _is_live_status(status: str) -> bool:
    """Live set per spec §'Status semantics': active|proposed|gated (and the
    absent default, which is active)."""
    return status in ("", "active", "proposed", "gated")


def _read_ledger(folder: Path) -> dict[str, str]:
    p = folder / ".last_used.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_iso_z(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------- core audit ----------


def _export_tier_gate(
    sens: str,
    export_tier: Optional[str],
    enforce: bool,
) -> Optional[str]:
    """Return a violation message if the declared sensitivity exceeds the
    export tier; None otherwise. Privileged-labeled files always block when
    export_tier is set below privileged, regardless of the enforce flag.
    """
    if export_tier is None:
        return None
    from memforge.cli._config import tier_rank
    sens_effective = sens if sens else "internal"
    sens_r = tier_rank(sens_effective)
    export_r = tier_rank(export_tier)
    if sens_r < 0:
        # Unrecognized sensitivity label: fail-closed. tier_rank returns -1 for
        # an unknown tier, which would otherwise be <= any real export tier and
        # silently clear the gate. An unknown label cannot be cleared for
        # export (closes audit-helper-01).
        return (
            f"unrecognized sensitivity '{sens_effective}' cannot be cleared "
            f"for export tier '{export_tier}'"
        )
    if sens_r <= export_r:
        return None
    is_privileged = sens_effective == "privileged"
    if not enforce and not is_privileged:
        return None
    # Annotate EVERY privileged block with the hard-floor note, not only when
    # enforcement is disabled. Privileged-always-blocks is the semantics firing
    # in both cases; omitting the note under the default (enforce=True) left a
    # partner unable to tell the file would block even with the gate disabled
    # (closes recall-04).
    suffix = " (privileged hard-floor)" if is_privileged else ""
    return (
        f"sensitivity '{sens_effective}' exceeds export tier "
        f"'{export_tier}'{suffix}"
    )


def audit_target(
    target: Path,
    *,
    stale_days: int,
    fix: bool,
    add_defaults: bool,
    json_out: bool,
    export_tier: Optional[str] = None,
    enforce_sensitivity_export_gate: bool = True,
    max_always_count: int = 8,
    max_always_description_chars: int = 600,
) -> tuple[int, Optional[dict]]:
    """Audit one folder. Returns (violation_count, optional json blob)."""
    print()
    print(f"====== {target} ======")

    if not target.is_dir():
        print("  (directory does not exist, skipping)")
        return 0, None

    memory_md = target / "MEMORY.md"
    violations: list[str] = []
    health: list[str] = []
    stale: list[str] = []
    orphan_files: list[str] = []
    orphan_ptrs: list[str] = []

    type_counts = {"user": 0, "feedback": 0, "project": 0, "reference": 0, "other": 0}
    sens_counts = {"public": 0, "internal": 0, "restricted": 0, "privileged": 0, "missing": 0}
    missing_sens_files: list[str] = []
    # v0.6.1 always-set budget accumulation: (fname, description) for every live
    # memory carrying always: true. Checked against the configured budget after
    # the per-file loop.
    always_live: list[tuple[str, str]] = []
    file_count = 0

    # ---- MEMORY.md index checks ----
    if not memory_md.is_file():
        violations.append("MEMORY.md missing")
        index_text = ""
    else:
        index_text = memory_md.read_text(encoding="utf-8")
        if has_frontmatter(index_text):
            violations.append("MEMORY.md has frontmatter (should be index only)")
        line_count = len(index_text.splitlines())
        if line_count > MEMORY_MD_LINE_CAP:
            health.append(
                f"[convention-drift] MEMORY.md is {line_count} lines "
                f"(>{MEMORY_MD_LINE_CAP} cap; SHOULD stay under it)"
            )
        long_pointer_lines = 0
        for line in index_text.splitlines():
            if _BULLET_POINTER_RE.match(line) and len(line.encode("utf-8")) > POINTER_LINE_BYTE_CAP:
                long_pointer_lines += 1
        if long_pointer_lines > 0:
            health.append(
                f"[convention-drift] MEMORY.md has {long_pointer_lines} pointer "
                f"lines >{POINTER_LINE_BYTE_CAP} bytes "
                f"(spec SHOULD stay under the {POINTER_LINE_BYTE_CAP}-byte cap; em-dashes cost 3 bytes each)"
            )

    # ---- pointer / disk-file set comparison ----
    pointers = _extract_pointers(memory_md)
    seen: dict[str, int] = {}
    for p in pointers:
        seen[p] = seen.get(p, 0) + 1
    for p, n in seen.items():
        if n > 1:
            violations.append(f"Duplicate pointer in MEMORY.md: {p}")
    pointer_set = set(pointers)
    disk_set = set(_disk_md_files(target))
    all_md_set = _all_md_files_recursive(target)

    for f in sorted(disk_set - pointer_set):
        orphan_files.append(f)
        violations.append(f"Orphan file (no pointer): {f}")
    for p in sorted(pointer_set - disk_set):
        if p in all_md_set:
            # File exists inside a subfolder but is not a canonical rollup
            # README. Per spec §"Rollup subfolders" the canonical pattern is
            # to point MEMORY.md at `<topic>/README.md` (the rollup) and let
            # the rollup link the detail files. Non-canonical but the file
            # IS on disk, so this is a health advisory, not "no file".
            health.append(
                f"Pointer at subfolder detail file (consider rollup README): {p}"
            )
        else:
            orphan_ptrs.append(p)
            violations.append(f"Orphan pointer (no file): {p}")

    # ---- per-file frontmatter audit ----
    # Per spec §"Rollup subfolders", audit MUST recurse into rollup
    # subfolders (excluding archive/) for frontmatter validation. The
    # pointer comparison above stays scoped to disk_set (top-level + rollup
    # READMEs); only the per-file audit recurses to detail-tier files.
    now = datetime.now(timezone.utc)
    stale_cutoff = now.timestamp() - (stale_days * 86400)
    ledger = _read_ledger(target)

    for fname in _files_to_audit(target):
        fpath = target / fname
        try:
            text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(f"{fname}: cannot read file")
            continue

        file_count += 1

        if not has_frontmatter(text):
            violations.append(f"{fname}: no frontmatter block")
            continue
        fm, body = parse(text)

        # parse() returns ({}, text) on YAML parse failure even when the
        # fence pattern is intact. Distinguish that from a present-but-
        # short frontmatter so the operator gets a single actionable
        # message instead of a missing-name/description/type trio.
        if not fm:
            violations.append(
                f"{fname}: frontmatter YAML failed to parse "
                "(check for unquoted special chars, unbalanced quotes, etc.)"
            )
            continue

        name = fm.get("name", "")
        desc = fm.get("description", "")
        ftype = fm.get("type", "")
        sens = fm.get("sensitivity", "")

        if not name:
            violations.append(f"{fname}: missing 'name' in frontmatter")
        if not desc:
            violations.append(f"{fname}: missing 'description' in frontmatter")
        if not ftype:
            violations.append(f"{fname}: missing 'type' in frontmatter")

        if ftype in VALID_TYPES:
            type_counts[ftype] += 1
        elif ftype:
            violations.append(f"{fname}: invalid type '{ftype}'")
            type_counts["other"] += 1

        if sens == "":
            sens_counts["missing"] += 1
            missing_sens_files.append(fname)
        elif sens in VALID_SENSITIVITIES:
            sens_counts[sens] += 1
        else:
            violations.append(
                f"{fname}: invalid sensitivity '{sens}' "
                "(must be public|internal|restricted|privileged)"
            )

        gate_msg = _export_tier_gate(sens, export_tier, enforce_sensitivity_export_gate)
        if gate_msg:
            violations.append(f"{fname}: {gate_msg}")

        tier = fm.get("tier", "")
        if tier and tier not in SPEC_TIERS:
            health.append(
                f"{fname}: tier '{tier}' is not a spec tier (index|detail)"
            )

        if not body.strip():
            violations.append(f"{fname}: empty body")

        if ftype in ("feedback", "project"):
            if not _file_has_why(body):
                health.append(f"{fname} ({ftype}): missing **Why:** line")
            if not _file_has_apply(body):
                health.append(f"{fname} ({ftype}): missing **How to apply:** line")

        # v0.6.0 recall-field shape + v0.6.1 relative-date heuristic (WARN-only).
        for w in _recall_field_warnings(fname, fm):
            health.append(f"[v0.6 WARN] {w}")
        rel = _relative_date_warning(fname, ftype, body)
        if rel:
            health.append(f"[v0.6 WARN] {rel}")

        # v0.6.1 always-set budget: accumulate live always:true memories.
        # Per spec §"Recall operation" post-condition 1, the always-set is
        # always:true AND do_not_inject:false (a do_not_inject memory is never
        # recall-injected, so it costs nothing per query). Exclude it.
        if (
            fm.get("always") is True
            and fm.get("do_not_inject") is not True
            and _is_live_status(fm.get("status", ""))
        ):
            always_live.append((fname, desc))

        # Staleness: prefer the .last_used.json ledger when present, fall back
        # to mtime. The ledger is written by the read-tracker hook in the
        # Claude Code adapter.
        last_used_str = ledger.get(fname)
        if last_used_str:
            last_used = _parse_iso_z(last_used_str)
            if last_used and last_used.timestamp() < stale_cutoff:
                stale.append(f"{fname} (last_used {last_used_str})")
        else:
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                mtime = 0
            if mtime and mtime < stale_cutoff:
                # Display in UTC to match the UTC framing of stale_cutoff and the
                # ledger-branch 'Z' timestamps (craft-03). Local-time
                # fromtimestamp could show a date off by a day near midnight and
                # disagreed with the ledger branch.
                mtime_iso = datetime.fromtimestamp(mtime, timezone.utc).date().isoformat()
                if ledger:
                    stale.append(f"{fname} (mtime {mtime_iso}; never read)")
                else:
                    stale.append(f"{fname} (mtime {mtime_iso})")

    # ---- v0.6.1 always-set budget (advisory WARN, never BLOCKER) ----
    # The always-set is injected on every recall query, so it is the recurring
    # token cost. Spec §"Recall operation" asks operators to keep it small and
    # bounded. This is a WARN by design: existing repos may already exceed the
    # budget and MUST NOT fail audit on upgrade (design-panel decision; same
    # rationale as v0.3->v0.4 degraded mode).
    always_count = len(always_live)
    always_chars = sum(len(d) for _, d in always_live)
    if always_count > max_always_count:
        health.append(
            f"[v0.6 WARN] always-set has {always_count} live memories "
            f"(budget {max_always_count}); every always:true memory is injected "
            "on every recall query. Consider do_not_inject or demoting some."
        )
    if always_chars > max_always_description_chars:
        health.append(
            f"[v0.6 WARN] always-set descriptions total {always_chars} chars "
            f"(budget {max_always_description_chars}); this is the per-query "
            "recall floor. Tighten descriptions or shrink the always-set."
        )

    # ---- v0.4 multi-agent concurrency invariants ----
    # Tier 1 (HEAD-pure) is always run; Tier 2 (commit-log walk) is best-effort.
    try:
        from memforge.cli._concurrency_audit import run_concurrency_audit
        blockers_v04, majors_v04, warns_v04 = run_concurrency_audit(target)
        for _, msg in blockers_v04:
            violations.append(f"[v0.4] {msg}")
        for _, msg in majors_v04:
            health.append(f"[v0.4 MAJOR] {msg}")
        for _, msg in warns_v04:
            health.append(f"[v0.4 WARN] {msg}")
    except Exception as e:  # noqa: BLE001 — never let v0.4 audit kill the existing audit
        health.append(f"[v0.4] concurrency audit raised {type(e).__name__}: {e}")

    # ---- report ----
    print()
    print(
        f"  Files: {file_count}  | "
        f"user: {type_counts['user']}  feedback: {type_counts['feedback']}  "
        f"project: {type_counts['project']}  reference: {type_counts['reference']}  "
        f"other: {type_counts['other']}"
    )
    print(
        f"  Sensitivity: public: {sens_counts['public']}  internal: {sens_counts['internal']}  "
        f"restricted: {sens_counts['restricted']}  privileged: {sens_counts['privileged']}  "
        f"(missing: {sens_counts['missing']})"
    )
    print()

    if violations:
        print(f"  INTEGRITY VIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"    - {v}")
        print()

    if health:
        print(f"  HEALTH ({len(health)}):")
        for h in health:
            print(f"    - {h}")
        print()

    if stale:
        print(f"  STALE >{stale_days}d ({len(stale)}):")
        for s in stale:
            print(f"    - {s}")
        print()

    # ---- --add-defaults: insert sensitivity: internal where missing ----
    if add_defaults and missing_sens_files:
        print(f"  --add-defaults: {len(missing_sens_files)} file(s) missing sensitivity field")
        for f in missing_sens_files:
            print(f"    - {f}")
        try:
            ans = input("    Add 'sensitivity: internal' to all of the above? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans in ("y", "yes"):
            for f in missing_sens_files:
                fpath = target / f
                text = fpath.read_text(encoding="utf-8")
                # Insert before the closing fence of the frontmatter block.
                lines = text.split("\n")
                new_lines: list[str] = []
                in_fm = False
                inserted = False
                for i, line in enumerate(lines):
                    if i == 0 and line.strip() == "---":
                        in_fm = True
                        new_lines.append(line)
                        continue
                    if in_fm and not inserted and line.strip() == "---":
                        new_lines.append("sensitivity: internal")
                        new_lines.append(line)
                        in_fm = False
                        inserted = True
                        continue
                    new_lines.append(line)
                # Only rewrite + report success when the closing fence was
                # actually found and the field inserted. If the insertion loop
                # never matched its closing `---` (the two fence-detection paths
                # are not guaranteed identical), report the anomaly instead of
                # claiming a write that did not change the file (closes
                # recall-03). This also avoids needless mtime churn on no-ops.
                if inserted:
                    fpath.write_text("\n".join(new_lines), encoding="utf-8")
                    print(f"      wrote sensitivity: internal to {f}")
                else:
                    print(
                        f"      SKIPPED {f}: could not locate the frontmatter "
                        "closing fence; sensitivity field NOT added"
                    )
        else:
            print("    kept as-is (absence treated as internal at runtime).")
        print()

    # ---- --fix: remove orphan pointers with y/N prompt ----
    if fix and orphan_ptrs and memory_md.is_file():
        print("  --fix: orphan pointer removal")
        index_text = memory_md.read_text(encoding="utf-8")
        for p in orphan_ptrs:
            try:
                ans = input(f"    Remove pointer '{p}' from {memory_md}? [y/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("y", "yes"):
                pat = re.compile(r"^.*\(" + re.escape(p) + r"\).*$\n?", re.MULTILINE)
                index_text, n = pat.subn("", index_text, count=1)
                print(f"      removed ({n} line)." if n else "      not found (skipped).")
            else:
                print("      kept.")
        memory_md.write_text(index_text, encoding="utf-8")
        print()

    blob: Optional[dict] = None
    if json_out:
        blob = {
            "target": str(target),
            "file_count": file_count,
            "types": type_counts,
            "violations": violations,
            "health": health,
            "stale": stale,
            "orphan_files": orphan_files,
            "orphan_pointers": orphan_ptrs,
        }

    return len(violations), blob


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="memory-audit",
        description=(
            "Health + integrity checks for MemForge memory folders. "
            "Defaults: per-cwd memory + ~/.claude/global-memory/."
        ),
    )
    p.add_argument("--path", action="append", type=Path, default=[],
                   help="Audit only this dir (repeatable; overrides defaults).")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 on any integrity violation.")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="Emit machine-readable JSON.")
    p.add_argument("--fix", action="store_true",
                   help="Prompt to remove orphan pointers from MEMORY.md.")
    p.add_argument("--add-defaults", action="store_true",
                   help="Prompt to add 'sensitivity: internal' to files missing the field.")
    p.add_argument("--stale-days", type=int, default=90,
                   help="Flag files older than N days (default: 90).")
    p.add_argument(
        "--export-tier",
        choices=["public", "internal", "restricted", "privileged"],
        default=None,
        help=(
            "v0.4 sensitivity export-tier gate: fail BLOCKER on any file whose "
            "declared sensitivity exceeds <tier>. Defaults to "
            "audit.default_export_tier from .memforge/config.yaml when set, "
            "otherwise the gate is no-op. Privileged files always block when "
            "the gate runs, regardless of config disable."
        ),
    )
    args = p.parse_args(argv)

    from memforge.cli._config import load_config
    cfg = load_config()
    audit_cfg = cfg.get("audit", {})
    export_tier = args.export_tier or audit_cfg.get("default_export_tier")
    if export_tier == "":
        export_tier = None
    enforce_export = bool(audit_cfg.get("enforce_sensitivity_export_gate", True))

    recall_cfg = cfg.get("recall", {})
    max_always_count = int(recall_cfg.get("max_always_count", 8))
    max_always_description_chars = int(recall_cfg.get("max_always_description_chars", 600))

    targets: list[Path] = [pp.expanduser().resolve() for pp in args.path] or _default_paths()

    total_violations = 0
    json_reports: list[dict] = []

    for t in targets:
        nv, blob = audit_target(
            t,
            stale_days=args.stale_days,
            fix=args.fix,
            add_defaults=args.add_defaults,
            json_out=args.json_out,
            export_tier=export_tier,
            enforce_sensitivity_export_gate=enforce_export,
            max_always_count=max_always_count,
            max_always_description_chars=max_always_description_chars,
        )
        total_violations += nv
        if blob is not None:
            json_reports.append(blob)

    if args.json_out:
        print()
        print("----- JSON -----")
        print(json.dumps(json_reports, indent=2))

    print()
    print(f"Total integrity violations across targets: {total_violations}")

    if args.strict and total_violations > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
