"""memory-audit - health + integrity checks for MemForge memory folders.

Cross-platform Python rewrite of the prior bash implementation.

Default: audits both per-cwd memory and ~/.claude/global-memory/.
Integrity violations (orphans, bad frontmatter, cap violations) return
nonzero when --strict is set, so this script is CI-wireable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from memforge.frontmatter import has_frontmatter, parse


VALID_TYPES = {"user", "feedback", "project", "reference"}
VALID_SENSITIVITIES = {"public", "internal", "restricted", "privileged"}
MEMORY_MD_LINE_CAP = 150
POINTER_LINE_BYTE_CAP = 150


# ---------- helpers ----------


def _default_paths() -> list[Path]:
    user = os.environ.get("USER", "")
    return [
        Path.home() / ".claude" / "projects" / f"{user}-claude-projects" / "memory",
        Path.home() / ".claude" / "global-memory",
    ]


_POINTER_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")
_BULLET_POINTER_RE = re.compile(r"^- \[")


def _extract_pointers(memory_md: Path) -> list[str]:
    if not memory_md.is_file():
        return []
    text = memory_md.read_text(encoding="utf-8", errors="replace")
    return _POINTER_RE.findall(text)


def _disk_md_files(folder: Path) -> list[str]:
    """Top-level .md files (NOT MEMORY.md). Sorted."""
    out = []
    for p in sorted(folder.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        out.append(p.name)
    return out


def _file_has_why(body: str) -> bool:
    return "**Why:**" in body


def _file_has_apply(body: str) -> bool:
    return "**How to apply:**" in body


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


def audit_target(
    target: Path,
    *,
    stale_days: int,
    strict: bool,
    fix: bool,
    add_defaults: bool,
    json_out: bool,
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
            violations.append(
                f"MEMORY.md is {line_count} lines (>{MEMORY_MD_LINE_CAP} cap)"
            )
        long_pointer_lines = 0
        for line in index_text.splitlines():
            if _BULLET_POINTER_RE.match(line) and len(line.encode("utf-8")) > POINTER_LINE_BYTE_CAP:
                long_pointer_lines += 1
        if long_pointer_lines > 0:
            violations.append(
                f"MEMORY.md has {long_pointer_lines} pointer lines >{POINTER_LINE_BYTE_CAP} bytes"
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

    for f in sorted(disk_set - pointer_set):
        orphan_files.append(f)
        violations.append(f"Orphan file (no pointer): {f}")
    for p in sorted(pointer_set - disk_set):
        orphan_ptrs.append(p)
        violations.append(f"Orphan pointer (no file): {p}")

    # ---- per-file frontmatter audit ----
    now = datetime.now(timezone.utc)
    stale_cutoff = now.timestamp() - (stale_days * 86400)
    ledger = _read_ledger(target)

    for fname in sorted(disk_set):
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

        if not body.strip():
            violations.append(f"{fname}: empty body")

        if ftype in ("feedback", "project"):
            if not _file_has_why(body):
                health.append(f"{fname} ({ftype}): missing **Why:** line")
            if not _file_has_apply(body):
                health.append(f"{fname} ({ftype}): missing **How to apply:** line")

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
                mtime_iso = datetime.fromtimestamp(mtime).date().isoformat()
                if ledger:
                    stale.append(f"{fname} (mtime {mtime_iso}; never read)")
                else:
                    stale.append(f"{fname} (mtime {mtime_iso})")

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
                fpath.write_text("\n".join(new_lines), encoding="utf-8")
                print(f"      wrote sensitivity: internal to {f}")
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
    args = p.parse_args(argv)

    targets: list[Path] = [pp.expanduser().resolve() for pp in args.path] or _default_paths()

    total_violations = 0
    json_reports: list[dict] = []

    for t in targets:
        nv, blob = audit_target(
            t,
            stale_days=args.stale_days,
            strict=args.strict,
            fix=args.fix,
            add_defaults=args.add_defaults,
            json_out=args.json_out,
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
