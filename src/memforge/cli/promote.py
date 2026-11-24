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
import os
import re
import subprocess
import sys
from pathlib import Path

from memforge._programs import ProgramInFolder, find_program_outside, git_env
from memforge._terminal import terminal_safe, visible
from memforge.discovery import (
    copy_file_inside,
    read_text_no_follow,
    walk_md_files,
    write_text_inside,
)


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


def _scan_inbound_path_links(folder: Path, moved_file: Path) -> tuple[list[tuple[Path, str]], list[Path]]:
    """Return ([(linking_file, link_target), ...], unreadable) for every
    path-form markdown link in `folder` (excluding MEMORY.md and archive/)
    that resolves to `moved_file`, and every file or folder that could not be
    read. Run before the move: an unreadable file may hold such a link, so the
    caller refuses rather than leave it dangling unreported."""
    moved_resolved = moved_file.resolve()
    out: list[tuple[Path, str]] = []
    unreadable: list[Path] = []
    for path in sorted(walk_md_files(folder, skip_dirs=("archive", ".git"))):
        if path.name == "MEMORY.md":
            continue
        if "archive" in path.parts or ".git" in path.parts:
            continue
        try:
            text = read_text_no_follow(path, root=folder)
        except (OSError, UnicodeDecodeError):
            unreadable.append(path)
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
    return out, unreadable


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


def _commit_folder(git: str, folder: Path, message: str, paths: list[Path]) -> None:
    """Commit ONLY the specific paths promote modified, mirroring resolve.py's
    scope-locked commit (`git commit -o -m <msg> -- <paths>`) so an unrelated
    pre-staged or untracked file in the memory folder cannot ride along in a
    promote commit (SPEC commit-scope discipline; promote-scope-01)."""
    if not os.path.isdir(folder / ".git"):
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
    # The paths are file names, never patterns: a memory named `*.md` read as
    # a pathspec would sweep other files into the scope-locked commit.
    subprocess.run(
        [git, "--literal-pathspecs", "-C", str(folder), "add", "--", *relpaths],
        env=git_env(),
        check=True,
    )
    # `-o` (== --only) plus the trailing pathspec commits exactly these paths.
    subprocess.run(
        [git, "--literal-pathspecs", "-C", str(folder), "commit", "-q", "-o", "-m", message,
         "--", *relpaths],
        env=git_env(),
        check=True,
    )
    head = subprocess.check_output(
        [git, "-C", str(folder), "log", "--oneline", "-1"],
        env=git_env(),
        text=True,
    ).strip()
    print(f"  committed in {visible(folder.name)}: {visible(head)}")


@terminal_safe
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
    if not os.path.isdir(source):
        print(f"error: source directory missing: {visible(source)}", file=sys.stderr)
        return 2
    if not os.path.isdir(target):
        print(f"error: target directory missing: {visible(target)}", file=sys.stderr)
        return 2

    src_file = source / filename
    tgt_file = target / filename
    src_idx = source / "MEMORY.md"
    tgt_idx = target / "MEMORY.md"

    if not os.path.isfile(src_file):
        print(f"error: source file missing: {visible(src_file)}", file=sys.stderr)
        return 2
    # Refuse to follow a symlink for either endpoint. A hostile contributor can
    # land a symlink named like a memory file in the (auto-committed)
    # source folder; a copy would follow it and copy out-of-folder content
    # into the destination MEMORY folder. Mirror index_gen's symlink guards
    # (closes adv-04).
    if os.path.islink(src_file):
        print(f"error: refusing to promote a symlinked source file: {visible(src_file)}", file=sys.stderr)
        return 2
    if os.path.islink(tgt_file):
        print(f"error: refusing to write through a symlinked target path: {visible(tgt_file)}", file=sys.stderr)
        return 2
    if os.path.exists(tgt_file):
        print(f"error: target already has a file with this name: {visible(tgt_file)}", file=sys.stderr)
        return 2
    if not os.path.isfile(src_idx):
        print(f"error: source MEMORY.md missing: {visible(src_idx)}", file=sys.stderr)
        return 2

    tgt_idx_existed = os.path.isfile(tgt_idx)

    try:
        src_idx_text = read_text_no_follow(src_idx, root=source)
        tgt_text = read_text_no_follow(tgt_idx, root=target) if tgt_idx_existed else ""
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read a MEMORY.md, nothing promoted: {visible(exc)}", file=sys.stderr)
        return 2
    pointer_lineno, pointer_text, dupe_count = _find_pointer_line(src_idx_text, filename)
    if pointer_lineno == 0:
        print(
            f"error: no pointer for {visible(filename)} in {visible(src_idx)} (nothing to promote)",
            file=sys.stderr,
        )
        return 2

    if dupe_count > 1:
        print(
            f"warning: {visible(filename)} pointer appears {dupe_count} times in "
            f"{visible(src_idx)}; will remove only the first. Re-run memory-audit-deep after.",
            file=sys.stderr,
        )

    # Scanned before anything moves (links resolve to the source path, which
    # exists now): a file that cannot be read may link to the memory, and
    # the move would leave that link dangling with nothing reported.
    inbound_links, unreadable = _scan_inbound_path_links(source, src_file)
    if unreadable:
        print(f"error: cannot read {len(unreadable)} file(s) or folder(s) in {visible(source)} "
              f"that could link to {visible(filename)}, nothing promoted:", file=sys.stderr)
        for path in unreadable:
            print(f"    {visible(path)}", file=sys.stderr)
        return 2

    src_in_git = os.path.isdir(source / ".git")
    tgt_in_git = os.path.isdir(target / ".git")

    plan_lines = [
        "Planned promotion:",
        f"  file:           {visible(filename)}",
        f"  from:           {visible(source)}",
        f"  to:             {visible(target)}",
        f"  pointer:        {visible(pointer_text)}",
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

    # Found before anything moves, so a git committed to either folder is
    # refused while there is still nothing to undo.
    git: str | None = None
    if not args.no_commit and (src_in_git or tgt_in_git):
        try:
            git = find_program_outside("git", source, target)
        except ProgramInFolder as exc:
            print(f"error: {visible(exc)}; refusing to run it, nothing promoted.", file=sys.stderr)
            return 2
        if git is None:
            print("error: git not found on PATH, nothing promoted "
                  "(--no-commit moves the file without committing).", file=sys.stderr)
            return 2

    if not args.yes:
        try:
            ans = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("aborted.")
            return 1

    # ---- execute ----
    # Both indexes were read before anything changed, and every write below
    # refuses a link, so a link swapped in after the checks above stops the
    # promote at the step that meets it.
    try:
        copy_file_inside(source, src_file, target, tgt_file)
        src_lines = src_idx_text.splitlines()
        del src_lines[pointer_lineno - 1]
        write_text_inside(
            source,
            src_idx,
            "\n".join(src_lines) + ("\n" if src_idx_text.endswith("\n") else ""),
        )
        if not tgt_idx_existed:
            tgt_text = "# Memory Index\n\n"
        if tgt_text and not tgt_text.endswith("\n"):
            tgt_text += "\n"
        tgt_text += pointer_text + "\n"
        write_text_inside(target, tgt_idx, tgt_text)
    except OSError as exc:
        print(
            f"error: promote stopped: {visible(exc)}. {visible(src_file)} was not removed; check "
            f"{visible(tgt_file)} and both MEMORY.md files before running it again.",
            file=sys.stderr,
        )
        return 2

    src_file.unlink()

    if git is not None:
        if src_in_git:
            _commit_folder(
                git,
                source,
                f"memory: promote {filename} out to {target.name}/",
                [src_file, src_idx],
            )
        if tgt_in_git:
            _commit_folder(
                git,
                target,
                f"memory: promote {filename} in from {source.name}/",
                [tgt_file, tgt_idx],
            )

    print()
    print(f"Promoted {visible(filename)}.")
    print("Next: run memory-audit-deep to confirm both folders are still well-formed,")
    print("run memory-link-rewriter check to catch links left dangling by the move")
    print("(promote does not rewrite inbound links or cross-folder mirror fields),")
    print(f"and re-sort {visible(tgt_idx)} if your target index groups pointers by section.")

    if inbound_links:
        print(file=sys.stderr)
        print(
            f"warning: {len(inbound_links)} inbound path-form link(s) to {visible(filename)} "
            f"in {visible(source)} are now dangling and were NOT rewritten by promote:",
            file=sys.stderr,
        )
        for linking_file, target_str in inbound_links:
            try:
                rel = linking_file.relative_to(source)
            except ValueError:
                rel = linking_file
            print(f"    {visible(rel)}: {visible(target_str)}", file=sys.stderr)
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
