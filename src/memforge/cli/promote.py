"""memory-promote - move a memory file between MemForge folders.

Removes the pointer from the source MEMORY.md, appends to the target
MEMORY.md, and commits each folder's git repo. Cross-platform Python
rewrite of the prior bash implementation.

Scope limitation (not yet automated): promote moves the file and updates
both MEMORY.md indexes, but it does NOT rewrite inbound cross-file links to
the moved memory and does NOT reconcile the spec's cross-folder mirror
fields (references_global / referenced_by_global). Any path-form
`[text](file.md)` link to the moved memory from another file in the source
folder is left dangling, and mem:uid links/mirror fields are not updated.

To make that unreconciled state detectable by automation, promote SCANS the
source folder after the move for inbound path-form links to the moved file. If
any exist, promote exits with a distinct nonzero code (EXIT_UNRECONCILED_LINKS)
after completing the move, so a scripted caller (--yes / --no-commit) does not
get a clean exit while dangling links remain. Run `memory-link-rewriter check`
(and fix any reported breaks) after such a promote until this seam is automated.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


# Distinct nonzero exit so automated callers can tell "promote succeeded but
# left inbound links to reconcile" apart from a hard failure (rc=2).
EXIT_UNRECONCILED_LINKS = 3

# Markdown link regex: captures [text](target), skipping images (![...](...))
# and reference-style links ([text][ref]). Mirrors link_rewriter.LINK_RE.
_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+?)\]\(([^)\n]+?)\)")


def _default_source() -> Path:
    from memforge.paths import default_memory_paths
    # The per-cwd (first) memory folder is the historical promote source.
    return default_memory_paths()[0]


def _default_target() -> Path:
    from memforge.paths import default_memory_paths
    # Global memory is the last folder in the centralized resolver's order.
    return default_memory_paths()[-1]


def _scan_inbound_path_links(folder: Path, moved_file: Path) -> list[tuple[Path, str]]:
    """Return [(linking_file, link_target), ...] for every path-form markdown
    link in `folder` (excluding MEMORY.md and archive/) that resolves to
    `moved_file`. Used post-move to detect links left dangling by promote."""
    moved_resolved = moved_file.resolve()
    out: list[tuple[Path, str]] = []
    for path in sorted(folder.rglob("*.md")):
        if path.name == "MEMORY.md":
            continue
        if "archive" in path.parts or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _LINK_RE.finditer(text):
            target = m.group(2).strip()
            if "://" in target or target.startswith("#"):
                continue
            target_path = target.split("#", 1)[0]
            if not target_path.endswith(".md"):
                continue
            try:
                resolved = (path.parent / target_path).resolve()
            except (OSError, ValueError):
                continue
            if resolved == moved_resolved:
                out.append((path, target))
    return out


def _find_pointer_line(index_text: str, filename: str) -> tuple[int, str, int]:
    """Return (1-indexed line number, full line text, total occurrences) of
    the first markdown pointer that targets `filename`. (0, '', 0) if absent.

    The match is anchored to the link target's basename: every `[text](target)`
    link on a line is parsed with `_LINK_RE` and the line matches only when some
    link's `Path(target).name` (fragment stripped) equals `filename` exactly. A
    prior substring regex (`\\([^)]*<filename>\\)`) matched on bare-name
    substring, so promoting `auth.md` while a pointer to `prior_auth.md` /
    `oauth.md` sat earlier in MEMORY.md would delete the OTHER file's pointer
    line (closes promote-01).
    """
    matches: list[tuple[int, str]] = []
    for i, line in enumerate(index_text.splitlines(), start=1):
        for m in _LINK_RE.finditer(line):
            target = m.group(2).strip().split("#", 1)[0]
            if Path(target).name == filename:
                matches.append((i, line))
                break
    if not matches:
        return 0, "", 0
    return matches[0][0], matches[0][1], len(matches)


def _commit_folder(folder: Path, message: str, paths: list[Path]) -> None:
    """Commit ONLY the specific paths promote modified, mirroring resolve.py's
    scope-locked commit (`git commit -o -m <msg> -- <paths>`) so an unrelated
    pre-staged or untracked file in the memory folder cannot ride along in a
    promote commit (SPEC commit-scope discipline; promote-scope-01)."""
    if not (folder / ".git").is_dir():
        return
    relpaths: list[str] = []
    for p in paths:
        try:
            relpaths.append(str(p.resolve().relative_to(folder.resolve())))
        except ValueError:
            # A path outside this folder cannot be part of its scoped commit.
            continue
    if not relpaths:
        return
    subprocess.run(["git", "-C", str(folder), "add", "--", *relpaths], check=True)
    # `-o` (== --only) plus the trailing pathspec commits exactly these paths.
    subprocess.run(
        ["git", "-C", str(folder), "commit", "-q", "-o", "-m", message, "--", *relpaths],
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
    # Refuse to follow a symlink for either endpoint. A hostile contributor can
    # land a symlink named like a memory file in the (auto-committed)
    # source folder; shutil.copy2 would follow it and copy out-of-folder content
    # into the destination MEMORY folder. Mirror index_gen's symlink guards
    # (closes adv-04).
    if src_file.is_symlink():
        print(f"error: refusing to promote a symlinked source file: {src_file}", file=sys.stderr)
        return 2
    if tgt_file.is_symlink():
        print(f"error: refusing to write through a symlinked target path: {tgt_file}", file=sys.stderr)
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

    # Scan for inbound path-form links to the moved file BEFORE unlinking it
    # (links resolve to its source path, which still exists at this point).
    inbound_links = _scan_inbound_path_links(source, src_file)

    src_file.unlink()

    if not args.no_commit:
        if src_in_git:
            _commit_folder(
                source,
                f"memory: promote {filename} out to {target.name}/",
                [src_file, src_idx],
            )
        if tgt_in_git:
            _commit_folder(
                target,
                f"memory: promote {filename} in from {source.name}/",
                [tgt_file, tgt_idx],
            )

    print()
    print(f"Promoted {filename}.")
    print("Next: run memory-audit-deep to confirm both folders are still well-formed,")
    print("run memory-link-rewriter check to catch links left dangling by the move")
    print("(promote does not rewrite inbound links or cross-folder mirror fields),")
    print(f"and re-sort {tgt_idx} if your target index groups pointers by section.")

    if inbound_links:
        print(file=sys.stderr)
        print(
            f"warning: {len(inbound_links)} inbound path-form link(s) to {filename} "
            f"in {source} are now dangling and were NOT rewritten by promote:",
            file=sys.stderr,
        )
        for linking_file, target_str in inbound_links:
            try:
                rel = linking_file.relative_to(source)
            except ValueError:
                rel = linking_file
            print(f"    {rel}: {target_str}", file=sys.stderr)
        print(
            "       run `memory-link-rewriter check` (and fix) to reconcile. "
            f"Exiting {EXIT_UNRECONCILED_LINKS} so automated callers detect the "
            "unreconciled state.",
            file=sys.stderr,
        )
        return EXIT_UNRECONCILED_LINKS

    return 0


if __name__ == "__main__":
    sys.exit(main())
