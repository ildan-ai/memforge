"""memforge-migrate-claim-block - rewrite legacy `status:` to canonical `state:`
in the per-group competing-claim fenced block of MEMORY.md.

Closes the v4 → v5+ rename in SPEC.md §"Multi-agent concurrency / Migration
story for the per-group field rename":

    During v0.4.x, parsers MUST accept BOTH `state:` (canonical) and `status:`
    (legacy) in the per-group competing-claim block. Generators MUST emit
    `state:` only. Adapters SHOULD provide a one-shot fixer that rewrites
    legacy blocks to the canonical form.

This module is the fixer. It walks MEMORY.md files and rewrites:

    # memforge:competing-claims:begin
    - decision_topic: foo
      status: competing      ← rewritten to `state: competing`
      members:
        - uid: mem-a
          ...
          status: active     ← LEFT ALONE (member-level field)
        ...
    # memforge:competing-claims:end

Per-group `state:` lines live at indent depth 2 (siblings of `decision_topic`,
`members`); per-member `status:` lines live at indent depth 6 (or more).
The fixer is depth-aware to avoid clobbering member-level fields.

Idempotent: running on an already-migrated file is a no-op.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional


BEGIN_MARK = "# memforge:competing-claims:begin"
END_MARK = "# memforge:competing-claims:end"

# Per-group status line: exactly 2-space indent, then `status:`. This is the
# legacy shape we rewrite. Members nest under `members:` at deeper indent.
PER_GROUP_STATUS_RE = re.compile(r"^(  )status:(\s.*)$")


def _default_paths() -> list[Path]:
    user = os.environ.get("USER", "")
    return [
        Path.home() / ".claude" / "projects" / f"{user}-claude-projects" / "memory",
        Path.home() / ".claude" / "global-memory",
    ]


def migrate_text(text: str) -> tuple[str, int]:
    """Rewrite legacy `status:` to `state:` inside competing-claim fenced
    blocks. Returns (new_text, rewrite_count). Idempotent.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    rewrites = 0
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if stripped == BEGIN_MARK:
            inside = True
            out.append(line)
            continue
        if stripped == END_MARK:
            inside = False
            out.append(line)
            continue
        if inside:
            m = PER_GROUP_STATUS_RE.match(line.rstrip("\n").rstrip("\r"))
            if m:
                indent, rest = m.group(1), m.group(2)
                trailing = ""
                if line.endswith("\r\n"):
                    trailing = "\r\n"
                elif line.endswith("\n"):
                    trailing = "\n"
                out.append(f"{indent}state:{rest}{trailing}")
                rewrites += 1
                continue
        out.append(line)
    return ("".join(out), rewrites)


def migrate_file(path: Path, *, dry_run: bool) -> int:
    """Migrate one MEMORY.md file. Returns rewrite count."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"warn: cannot read {path}: {e}", file=sys.stderr)
        return 0
    new_text, count = migrate_text(text)
    if count == 0:
        return 0
    if dry_run:
        print(f"  would rewrite {count} per-group status: line(s) in {path}")
        return count
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        print(f"warn: cannot write {path}: {e}", file=sys.stderr)
        return 0
    print(f"  rewrote {count} per-group status: line(s) in {path}")
    return count


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memforge-migrate-claim-block",
        description=(
            "Rewrite legacy `status:` to canonical `state:` inside the "
            "per-group competing-claim fenced block of MEMORY.md."
        ),
    )
    parser.add_argument(
        "--memory-root",
        action="append",
        help="Memory folder to scan (repeatable). Defaults to per-cwd memory + global-memory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing.",
    )
    args = parser.parse_args(argv)

    if args.memory_root:
        roots = [Path(p).expanduser().resolve() for p in args.memory_root]
    else:
        roots = _default_paths()

    total = 0
    files_touched = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("MEMORY.md")):
            count = migrate_file(path, dry_run=args.dry_run)
            if count:
                total += count
                files_touched += 1

    if total == 0:
        print("No legacy per-group status: lines found. (Already migrated, or none present.)")
    else:
        action = "would rewrite" if args.dry_run else "rewrote"
        print(f"\n{action} {total} per-group status: line(s) across {files_touched} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
