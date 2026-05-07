"""memory-promote - move a memory file between MemForge folders.

Removes the pointer from the source MEMORY.md, appends to the target
MEMORY.md, and commits each folder's git repo. Cross-platform Python
rewrite of the prior bash implementation.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _default_source() -> Path:
    user = os.environ.get("USER", "")
    return Path.home() / ".claude" / "projects" / f"{user}-claude-projects" / "memory"


def _default_target() -> Path:
    return Path.home() / ".claude" / "global-memory"


def _find_pointer_line(index_text: str, filename: str) -> tuple[int, str, int]:
    """Return (1-indexed line number, full line text, total occurrences) of
    the first markdown pointer that targets `filename`. (0, '', 0) if absent.
    """
    pat = re.compile(r"\([^)]*" + re.escape(filename) + r"\)")
    matches = []
    for i, line in enumerate(index_text.splitlines(), start=1):
        if pat.search(line):
            matches.append((i, line))
    if not matches:
        return 0, "", 0
    return matches[0][0], matches[0][1], len(matches)


def _commit_folder(folder: Path, message: str) -> None:
    if not (folder / ".git").is_dir():
        return
    subprocess.run(["git", "-C", str(folder), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(folder), "commit", "-q", "-m", message],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(folder), "log", "--oneline", "-1"],
        text=True,
    ).strip()
    print(f"  committed in {folder.name}: {head}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory-promote",
        description=(
            "Move a memory file from a per-cwd MemForge folder to "
            "the global-memory folder (or between any two MemForge folders). "
            "Updates MEMORY.md in both locations and commits each folder."
        ),
    )
    parser.add_argument("filename", help="Memory file to move (bare filename or path).")
    parser.add_argument("--source", type=Path, default=None,
                        help="Source folder (default: per-cwd memory folder).")
    parser.add_argument("--target", type=Path, default=None,
                        help="Target folder (default: ~/.claude/global-memory).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan, make no changes.")
    parser.add_argument("--no-commit", action="store_true",
                        help="Move and update indexes but skip git commits.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt.")
    args = parser.parse_args(argv)

    source: Path = (args.source or _default_source()).expanduser().resolve()
    target: Path = (args.target or _default_target()).expanduser().resolve()
    filename = Path(args.filename).name

    if source == target:
        print("error: source and target are the same directory", file=sys.stderr)
        return 2
    if not source.is_dir():
        print(f"error: source directory missing: {source}", file=sys.stderr)
        return 2
    if not target.is_dir():
        print(f"error: target directory missing: {target}", file=sys.stderr)
        return 2

    src_file = source / filename
    tgt_file = target / filename
    src_idx = source / "MEMORY.md"
    tgt_idx = target / "MEMORY.md"

    if not src_file.is_file():
        print(f"error: source file missing: {src_file}", file=sys.stderr)
        return 2
    if tgt_file.exists():
        print(f"error: target already has a file with this name: {tgt_file}", file=sys.stderr)
        return 2
    if not src_idx.is_file():
        print(f"error: source MEMORY.md missing: {src_idx}", file=sys.stderr)
        return 2

    tgt_idx_existed = tgt_idx.is_file()

    src_idx_text = src_idx.read_text(encoding="utf-8")
    pointer_lineno, pointer_text, dupe_count = _find_pointer_line(src_idx_text, filename)
    if pointer_lineno == 0:
        print(
            f"error: no pointer for {filename} in {src_idx} (nothing to promote)",
            file=sys.stderr,
        )
        return 2

    if dupe_count > 1:
        print(
            f"warning: {filename} pointer appears {dupe_count} times in "
            f"{src_idx}; will remove only the first. Re-run memory-audit-deep after.",
            file=sys.stderr,
        )

    src_in_git = (source / ".git").is_dir()
    tgt_in_git = (target / ".git").is_dir()

    plan_lines = [
        "Planned promotion:",
        f"  file:           {filename}",
        f"  from:           {source}",
        f"  to:             {target}",
        f"  pointer:        {pointer_text}",
        f"  src MEMORY.md:  remove line {pointer_lineno}",
        f"  tgt MEMORY.md:  "
        + ("append pointer" if tgt_idx_existed else "create index and add pointer"),
        f"  src git:        " + ("commit enabled" if src_in_git else "not a git repo"),
        f"  tgt git:        " + ("commit enabled" if tgt_in_git else "not a git repo"),
        f"  commits:        "
        + ("skipped (--no-commit)" if args.no_commit else "yes"),
    ]
    print("\n".join(plan_lines))
    print()

    if args.dry_run:
        print("--dry-run: no changes made.")
        return 0

    if not args.yes:
        try:
            ans = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("aborted.")
            return 1

    # ---- execute ----
    shutil.copy2(src_file, tgt_file)

    src_lines = src_idx_text.splitlines()
    del src_lines[pointer_lineno - 1]
    src_idx.write_text("\n".join(src_lines) + ("\n" if src_idx_text.endswith("\n") else ""),
                       encoding="utf-8")

    if not tgt_idx_existed:
        tgt_idx.write_text("# Memory Index\n\n", encoding="utf-8")

    tgt_text = tgt_idx.read_text(encoding="utf-8")
    if tgt_text and not tgt_text.endswith("\n"):
        tgt_text += "\n"
    tgt_text += pointer_text + "\n"
    tgt_idx.write_text(tgt_text, encoding="utf-8")

    src_file.unlink()

    if not args.no_commit:
        if src_in_git:
            _commit_folder(source, f"memory: promote {filename} out to {target.name}/")
        if tgt_in_git:
            _commit_folder(target, f"memory: promote {filename} in from {source.name}/")

    print()
    print(f"Promoted {filename}.")
    print("Next: run memory-audit-deep to confirm both folders are still well-formed,")
    print(f"and re-sort {tgt_idx} if your target index groups pointers by section.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
