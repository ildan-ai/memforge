# memory-rollup: bulk-move files into a topic subfolder and generate a
# rollup README.md (Phase 1 T1).
#
# ADR: 0001 §Phase 1 T1, §Phase 2 D1
# Spec: 0.3.0
#
# Operations:
#   create  --slug <name> --files A.md B.md ...   Move files into <slug>/
#                                                  and generate <slug>/README.md
#   undo    --slug <name>                          Reverse the most recent
#                                                  create operation for <slug>
#   list                                           List rollup history entries
#
# Cross-reference rewriting: shells out to memory-link-rewriter rename for
# each moved file so internal links update consistently.
#
# History ledger: .memforge-rollup-history/<timestamp>-<slug>.json records
# each create operation, enabling --undo. Per-folder ledger; never deleted
# (auto-commit hook commits it like any other file).

from __future__ import annotations

import argparse
import errno
import fnmatch
import json
import os
import secrets
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from memforge._terminal import terminal_safe, visible
from memforge._programs import ProgramInFolder, find_program, refuse_inside
from memforge.cli._concurrency_audit import _is_valid_slug
from memforge.discovery import (
    copy_file_inside,
    create_new_inside,
    read_text_no_follow,
    write_text_inside,
)
from memforge.frontmatter import render as _render_frontmatter


HISTORY_DIRNAME = ".memforge-rollup-history"


def _has_control_char(s: str) -> bool:
    """True if `s` carries a C0/C1 control char (newline, CR, tab, NUL, etc.).
    Secondary cleanliness guard for slug/topic/title before they reach the
    rendered README frontmatter (built via frontmatter.render) and body."""
    for c in s:
        o = ord(c)
        if o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F:
            return True
    return False


# Body-only template. The YAML frontmatter is now built as a dict and emitted
# via memforge.frontmatter.render() (PyYAML safe_dump) rather than str.format
# into a hand-written YAML block, so an attacker-shaped --topic/--title (e.g.
# `evil] injected: true [` or `x'y`) is quoted/escaped by the YAML emitter and
# cannot corrupt or inject structure into the frontmatter (closes adv-02). The
# operator-supplied title/topic still flow into the markdown BODY below, where
# they are plain prose with no YAML significance.
README_BODY_TEMPLATE = """\
# {title}

Rollup parent for {topic}-tagged memories. Detail files in this folder are
not pointed to from MEMORY.md (they live under this rollup).

## Why

Topic-coherent cluster of {n} memories accumulated past the rollup threshold
(5+ files in the same topic). Aggregating into a subfolder keeps the per-cwd
MEMORY.md index parseable and surfaces the topic as a single entry.

## How to apply

Reading: load this README first; descend into individual detail files when
they are referenced. Writing: add new {topic}-tagged memories to this folder
as `tier: detail` files; do NOT add a new pointer line in MEMORY.md (the
rollup README is the index entry).

## Files in this rollup

{file_list}

## Last reviewed

{ymd}: initial rollup creation.
"""


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def today_ymd() -> str:
    return date.today().isoformat()


def _within(folder: Path, candidate: Path) -> bool:
    """True if `candidate` resolves to a path inside `folder`. Used to refuse
    attacker-chosen paths read from the auto-committed history JSON (a hostile
    contributor controls that file's contents)."""
    try:
        candidate.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _recorded_file(recorded: object, parent: Path) -> Optional[Path]:
    """``parent / <name>`` for a history record naming a file directly in
    ``parent``, else None. Anyone who can commit may write the record, so a
    path in it is taken only where create could have put it: no other
    folder, no `..`. Both folders are compared resolved, because earlier
    create recorded a source by its resolved path and the rest by the folder
    as given; the path returned is built from ``parent``, never the record."""
    if not isinstance(recorded, str):
        return None
    path = Path(recorded)
    if path.name in ("", "..") or ".." in path.parts:
        return None
    try:
        if path.parent.resolve() != parent.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    return parent / path.name


def _entry(path: Path, follow: bool) -> Optional[os.stat_result]:
    """The status of ``path``, or None when nothing is there (ENOENT,
    ENOTDIR). Any other OSError propagates: a folder that cannot be searched
    is unreadable, not absent."""
    try:
        return os.stat(path, follow_symlinks=follow)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise


def _history_present(history_dir: Path) -> Optional[bool]:
    """Whether the rollup history folder is there; None, after reporting why,
    when it cannot be used: unreadable, a symlink, or not a folder."""
    try:
        st = _entry(history_dir, follow=False)
    except OSError as exc:
        sys.stderr.write(f"error: cannot read rollup history at {history_dir}: {exc}\n")
        return None
    if st is not None and not stat.S_ISDIR(st.st_mode):
        sys.stderr.write(
            f"error: refusing to use a rollup history folder that is a symlink "
            f"or not a folder: {history_dir}\n"
        )
        return None
    return st is not None


def _probe_name() -> str:
    """An unpredictable name for the history write check, so nobody can plant
    a file there that the check would then empty."""
    return f".write-check-{secrets.token_hex(8)}"


def _remove_probe(probe: Path, made: Optional[os.stat_result]) -> None:
    """Remove ``probe`` if the entry at that name is still the file this call
    created (same device and inode); otherwise leave it and say so. A probe
    that is already gone needs nothing. Raises OSError when removal fails."""
    try:
        now = os.lstat(probe)
    except FileNotFoundError:
        return
    if made is None or (now.st_dev, now.st_ino) != (made.st_dev, made.st_ino):
        sys.stderr.write(
            f"warning: left {visible(probe)} in place: it is not the check file "
            "this run created; remove it by hand if it is not yours\n"
        )
        return
    os.unlink(probe)


def _close_quietly(fd: int) -> None:
    """Close ``fd``; a close that fails leaves nothing to undo."""
    try:
        os.close(fd)
    except OSError:
        pass


def _history_takes_a_record(folder: Path, history_dir: Path) -> bool:
    """Whether a record can be written into the existing history folder, tried
    with a file that is removed again, so a folder that cannot take it is
    refused before any file moves. Reports why when it cannot."""
    probe = history_dir / _probe_name()
    fd: Optional[int] = None
    made: Optional[os.stat_result] = None
    settled = False
    try:
        # One exclusive create: a name already taken is refused and the file
        # there is never opened, so it cannot be emptied or later removed.
        try:
            fd = create_new_inside(folder, probe)
        except FileExistsError:
            raise FileExistsError(
                errno.EEXIST, "the check file name is already in use", str(probe)
            ) from None
        made = os.fstat(fd)
        if os.name == "nt":
            # Windows will not delete a file that is still open. Its st_ino is
            # the NTFS file id, which carries a sequence number, so a reused
            # file record does not repeat the value after the close.
            _close_quietly(fd)
            fd = None
        # Elsewhere the descriptor stays open through the identity check and
        # the unlink: a closed, unlinked file frees its inode number, and a
        # replacement could then reuse it and pass for the file made here.
        _remove_probe(probe, made)
        settled = True
    except OSError as exc:
        sys.stderr.write(
            f"error: cannot write to the rollup history folder {visible(history_dir)}: "
            f"{visible(exc)}; nothing was moved\n"
        )
        return False
    finally:
        # Whatever stopped the run after the create, the file this call made
        # is removed, and a probe that stays is reported, never silent. The
        # descriptor is closed last, after the final removal attempt.
        if not settled and (made is not None or fd is not None):
            try:
                _remove_probe(probe, made)
            except OSError as exc:
                sys.stderr.write(
                    f"error: left behind {visible(probe)}: {visible(exc)}; "
                    "remove it by hand\n"
                )
        if fd is not None:
            _close_quietly(fd)
    return True


def find_link_rewriter() -> Optional[Path]:
    """Locate the memory-link-rewriter script. Tries the sibling path
    first (when invoked from a checkout), then the absolute $PATH entries
    (when installed)."""
    sibling = Path(__file__).resolve().parent / "memory-link-rewriter"
    if os.path.exists(sibling):
        return sibling
    found = find_program("memory-link-rewriter")
    return Path(found) if found else None


def _rewriter_inside(rewriter: Optional[Path], folder: Path) -> bool:
    """Report, and return True, when the link rewriter lies inside the memory
    folder: anyone who can commit there could have placed it."""
    if rewriter is None:
        return False
    try:
        refuse_inside("memory-link-rewriter", str(rewriter), folder)
    except ProgramInFolder as exc:
        sys.stderr.write(f"error: {exc}; refusing to run it, nothing changed\n")
        return True
    return False


def cmd_create(folder: Path, slug: str, files: list[Path], topic: Optional[str], title: Optional[str], dry_run: bool) -> int:
    try:
        found = _entry(folder, follow=True)
    except OSError as exc:
        sys.stderr.write(f"error: cannot read folder {folder}: {exc}\n")
        return 2
    if found is None:
        sys.stderr.write(f"error: folder not found: {folder}\n")
        return 2
    # --slug becomes both a directory name (folder / slug) and is interpolated
    # into the generated README frontmatter, so it must pass the canonical slug
    # pipeline before any path interpolation. Without this a traversal slug
    # (e.g. ../evil) escapes the memory folder (matches resolve.py:311).
    if not _is_valid_slug(slug):
        sys.stderr.write(
            f"error: slug '{slug}' fails the slug pipeline "
            f"(must be lowercase ASCII, hyphen-separated, <=64 bytes, "
            f"regex `^[a-z0-9]+(-[a-z0-9]+)*$`, not a reserved name)\n"
        )
        return 2
    # --topic / --title flow into the README. Structural YAML safety is provided
    # by rendering the frontmatter via frontmatter.render() (PyYAML safe_dump,
    # see README_BODY_TEMPLATE note / adv-02). This control-char guard is a
    # secondary cleanliness check: it rejects C0/C1 control characters
    # (newlines, NUL, etc.) so a crafted value cannot smuggle a multi-line or
    # control-char scalar into the rendered block or the markdown body.
    if topic is not None and _has_control_char(topic):
        sys.stderr.write("error: --topic contains a control character\n")
        return 2
    if title is not None and _has_control_char(title):
        sys.stderr.write("error: --title contains a control character\n")
        return 2
    target_dir = folder / slug
    # Containment assert: even with a valid slug, confirm the target resolves
    # inside the folder before any mkdir / move.
    if not _within(folder, target_dir):
        sys.stderr.write(f"error: rollup target escapes the memory folder: {target_dir}\n")
        return 2
    try:
        taken = _entry(target_dir, follow=False)
    except OSError as exc:
        sys.stderr.write(f"error: cannot read rollup folder {target_dir}: {exc}\n")
        return 2
    if taken is not None:
        sys.stderr.write(f"error: rollup folder already exists: {target_dir}\n")
        return 2
    abs_files: list[Path] = []
    for f in files:
        p = f if f.is_absolute() else folder / f
        if not os.path.exists(p):
            sys.stderr.write(f"error: file not found: {p}\n")
            return 2
        if p.parent != folder:
            sys.stderr.write(f"error: file not in folder root (rollup moves only top-level files): {p}\n")
            return 2
        if p.name == "MEMORY.md":
            sys.stderr.write(f"error: refusing to move MEMORY.md\n")
            return 2
        # Kept as named, never resolved: a committed link would otherwise put
        # the file it names, in or out of the store, in its place.
        if os.path.islink(p) or not os.path.isfile(p):
            sys.stderr.write(f"error: refusing to move a symlink or a non-file: {p}\n")
            return 2
        abs_files.append(p)

    if not abs_files:
        sys.stderr.write("error: no files specified\n")
        return 2

    final_topic = topic or slug
    final_title = title or f"{slug.replace('-', ' ').title()} rollup"
    file_list_md = "\n".join(f"- `{f.name}`" for f in abs_files)
    ymd = today_ymd()
    # Build frontmatter as a dict; render() (PyYAML safe_dump) quotes/escapes
    # any YAML-significant characters in the attacker-influenceable title/topic
    # so the generated block is always well-formed (closes adv-02).
    readme_frontmatter = {
        "name": final_title,
        "description": f"Rollup of {len(abs_files)} memories on topic '{final_topic}'.",
        "type": "project",
        "sensitivity": "internal",
        "uid": f"mem-{ymd}-rollup-{slug}",
        "tier": "index",
        "tags": [f"topic:{final_topic}"],
        "owner": "operator",
        "created": ymd,
        "updated": ymd,
        "last_reviewed": ymd,
        "status": "active",
        "pinned": False,
    }
    readme_body = README_BODY_TEMPLATE.format(
        title=final_title,
        topic=final_topic,
        slug=slug,
        n=len(abs_files),
        ymd=ymd,
        file_list=file_list_md,
    )
    readme_content = _render_frontmatter(readme_frontmatter, readme_body)

    history_dir = folder / HISTORY_DIRNAME
    ts = now_ts()
    history_record = {
        "schema": "memforge-rollup-history/v1",
        "operation": "create",
        "slug": slug,
        "timestamp": ts,
        "folder": str(folder),
        "moved": [
            {"from": str(f), "to": str(target_dir / f.name)} for f in abs_files
        ],
        "readme": str(target_dir / "README.md"),
    }

    print(f"=== rollup create: slug='{slug}' topic='{final_topic}' ({len(abs_files)} files) ===")
    print(f"  target subfolder: {target_dir}")
    for f in abs_files:
        print(f"    move: {visible(f.name)} -> {visible(slug)}/{visible(f.name)}")
    print(f"  generate: {target_dir}/README.md")
    print(f"  history:  {history_dir}/{ts}-{slug}.json")

    if dry_run:
        print("\n[dry-run] no changes written")
        return 0

    # The history is written last; a link or a file there would only be
    # refused after the files had moved, so refuse it before anything changes.
    present = _history_present(history_dir)
    if present is None:
        return 2
    if present and not _history_takes_a_record(folder, history_dir):
        return 2
    rewriter = find_link_rewriter()
    if _rewriter_inside(rewriter, folder):
        return 2
    target_dir.mkdir(exist_ok=False)
    pairs = [(src, target_dir / src.name) for src in abs_files]
    if rewriter is not None:
        # Single rename-batch dispatch: one folder index + one file walk for
        # the whole rollup, regardless of cluster size. Replaces the prior
        # O(N) subprocess-spawn pattern per code-review-panel performance-
        # reviewer:4 + code-craftsman:CC-007 (2026-05-07).
        batch_payload = json.dumps([{"src": str(s), "dst": str(d)} for s, d in pairs])
        rc = subprocess.run(
            [str(rewriter), "--path", str(folder), "rename-batch"],
            input=batch_payload,
            text=True,
        ).returncode
        if rc != 0:
            sys.stderr.write(f"error: link-rewriter rename-batch failed (rc={rc})\n")
            _abort_partial(folder, target_dir, [])
            return 2
        moved = list(pairs)
    else:
        moved = []
        try:
            for src, dst in pairs:
                _move_inside(folder, src, dst)
                moved.append((src, dst))
        except OSError as exc:
            # Roll back the files already moved and remove the empty target dir,
            # mirroring the rename-batch failure handling, so a partial move
            # never leaves an orphaned half-rollup with no undo record.
            sys.stderr.write(f"error: rollup move failed: {exc}; rolling back\n")
            _abort_partial(folder, target_dir, moved)
            return 2

    readme_path = target_dir / "README.md"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"{ts}-{slug}.json"
    try:
        write_text_inside(folder, readme_path, readme_content)
        write_text_inside(folder, history_path, json.dumps(history_record, indent=2) + "\n")
    except OSError as exc:
        sys.stderr.write(
            f"error: {exc}; the files are in {target_dir} but the rollup has no "
            "history record, so undo cannot reverse it\n"
        )
        return 2

    print(f"\n  WROTE rollup at {target_dir} ({len(moved)} files moved)")
    print(f"  WROTE history record at {history_path}")
    print("\n  Next: review the README, run memory-index-gen --write to refresh MEMORY.md")
    return 0


def _move_inside(folder: Path, src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst``, both in ``folder``, without the link rewrite:
    copied as :func:`memforge.discovery.copy_file_inside` copies (never
    through a link), then the source name removed. Raises FileExistsError for a ``dst`` already there, which a
    rename would replace."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "destination already exists", str(dst))
    copy_file_inside(folder, src, folder, dst)
    os.unlink(src)


def _abort_partial(folder: Path, target_dir: Path, moved: list[tuple[Path, Path]]) -> None:
    for src, dst in reversed(moved):
        if os.path.exists(dst) and not os.path.exists(src):
            _move_inside(folder, dst, src)
    if os.path.exists(target_dir) and not any(target_dir.iterdir()):
        target_dir.rmdir()


def cmd_undo(folder: Path, slug: str, dry_run: bool) -> int:
    # --slug is glob-interpolated into the history scan and used as `folder /
    # slug` for the target dir; validate it through the canonical slug pipeline
    # before any path interpolation (matches cmd_create / resolve.py:311).
    if not _is_valid_slug(slug):
        sys.stderr.write(
            f"error: slug '{slug}' fails the slug pipeline "
            f"(must be lowercase ASCII, hyphen-separated, <=64 bytes, "
            f"regex `^[a-z0-9]+(-[a-z0-9]+)*$`, not a reserved name)\n"
        )
        return 2
    target_dir = folder / slug
    if not _within(folder, target_dir):
        sys.stderr.write(f"error: rollup target escapes the memory folder: {target_dir}\n")
        return 2
    history_dir = folder / HISTORY_DIRNAME
    present = _history_present(history_dir)
    if present is None:
        return 2
    if not present:
        sys.stderr.write(f"error: no rollup history at {history_dir}\n")
        return 2
    try:
        matches = _history_by_age(history_dir, f"*-{slug}.json", newest_first=True)
    except OSError as exc:
        sys.stderr.write(f"error: cannot read rollup history at {history_dir}: {exc}\n")
        return 2
    if not matches:
        sys.stderr.write(f"error: no rollup history entry for slug '{slug}'\n")
        return 2
    record_path = matches[0]
    try:
        record = json.loads(read_text_no_follow(record_path, root=folder))
    except (OSError, ValueError, RecursionError) as exc:
        # ValueError covers invalid UTF-8 and invalid JSON; RecursionError a
        # record nested too deep to parse.
        sys.stderr.write(f"error: cannot read history record {record_path}: {exc}\n")
        return 2
    if not isinstance(record, dict):
        sys.stderr.write(f"error: history record {record_path} is not a JSON object\n")
        return 2
    if record.get("operation") != "create":
        sys.stderr.write(f"error: history record at {record_path} is not a 'create' op\n")
        return 2

    target_dir = folder / slug
    try:
        rollup_entry = _entry(target_dir, follow=False)
    except OSError as exc:
        sys.stderr.write(f"error: cannot read rollup folder {target_dir}: {exc}\n")
        return 2
    if rollup_entry is not None and not stat.S_ISDIR(rollup_entry.st_mode):
        sys.stderr.write(
            f"error: refusing to undo through a rollup folder that is a symlink "
            f"or not a folder: {target_dir}\n"
        )
        return 2

    # The history JSON lives inside the auto-committed memory folder, so a
    # hostile contributor controls its contents. Every path it names is taken
    # only where create puts it: the README at <slug>/README.md, each moved
    # file directly in <slug>/, and its old place directly in the folder.
    readme_recorded = record.get("readme", str(target_dir / "README.md"))
    readme = _recorded_file(readme_recorded, target_dir)
    moves = record.get("moved", [])
    pairs: list[tuple[Path, Path]] = []
    bad = None if readme is not None and readme.name == "README.md" else readme_recorded
    if bad is None and not isinstance(moves, list):
        bad = moves
    for m in moves if bad is None else []:
        src = _recorded_file(m.get("to"), target_dir) if isinstance(m, dict) else None
        dst = _recorded_file(m.get("from"), folder) if isinstance(m, dict) else None
        if src is None or dst is None:
            bad = m
            break
        pairs.append((src, dst))
    if bad is not None:
        sys.stderr.write(
            f"error: history record {record_path} references a path outside the memory folder "
            f"or not where create puts it ({bad!r}); refusing undo (possible "
            "tampered history)\n"
        )
        return 2
    for src, dst in pairs:
        if os.path.lexists(dst):
            sys.stderr.write(f"error: refusing undo: {dst} already exists, nothing changed\n")
            return 2
        if os.path.islink(src):
            sys.stderr.write(f"error: refusing undo: {src} is a symlink, nothing changed\n")
            return 2

    print(f"=== rollup undo: slug='{slug}' ({len(pairs)} files) ===")
    print(f"  history record: {record_path}")
    print(f"  rollup folder:  {target_dir}")
    for src, dst in pairs:
        print(f"    move-back: {visible(src.name)} -> {visible(dst.name)}")
    print(f"  remove: {target_dir}/README.md + {target_dir}/")

    if dry_run:
        print("\n[dry-run] no changes written")
        return 0

    rewriter = find_link_rewriter()
    if _rewriter_inside(rewriter, folder):
        return 2
    skipped = 0
    for src, dst in pairs:
        if not os.path.exists(src):
            sys.stderr.write(f"warning: source {src} missing; skipping\n")
            skipped += 1
            continue
        if rewriter is not None:
            rc = subprocess.call(
                [str(rewriter), "--path", str(folder), "rename", str(src), str(dst)]
            )
            if rc != 0:
                sys.stderr.write(f"warning: link-rewriter rename failed for {src} (rc={rc}); leaving in place\n")
                skipped += 1
                continue
        else:
            try:
                _move_inside(folder, src, dst)
            except OSError as exc:
                sys.stderr.write(f"warning: move failed for {src} ({exc}); leaving in place\n")
                skipped += 1

    # If any move-back was skipped, the rollup is only partially disassembled.
    # Do NOT consume the history record (leave the `create` record in place) and
    # do NOT remove the README / target dir, so the operator can fix the cause
    # and re-run undo against the still-actionable record. Consuming the record
    # on a partial undo left a half-disassembled rollup with no `create` record
    # to retry against (closes rollup-undo-01).
    if skipped:
        sys.stderr.write(
            f"error: {skipped} of {len(moves)} file(s) could not be moved back; "
            f"rollup '{slug}' is only partially undone. Leaving the history "
            f"record {record_path.name} in place and keeping README/target dir "
            "so you can resolve the cause and re-run undo.\n"
        )
        return 2

    if os.path.exists(readme):
        readme.unlink()
    if os.path.exists(target_dir) and not any(target_dir.iterdir()):
        target_dir.rmdir()

    undone_path = history_dir / f"{record_path.stem}.undone.json"
    record["operation"] = "undone"
    record["undone_at"] = now_ts()
    write_text_inside(folder, undone_path, json.dumps(record, indent=2) + "\n")
    record_path.unlink()
    print(f"\n  UNDONE rollup '{slug}'; history record renamed to {undone_path.name}")
    print(f"\n  Next: run memory-index-gen --write to refresh MEMORY.md")
    return 0


def _history_by_age(history_dir: Path, pattern: str, newest_first: bool = False) -> list[Path]:
    """History records matching ``pattern``, ordered by modification time.
    Raises OSError when a record cannot be checked: without every record's age
    the order, and so the record undo picks, is not known."""
    # os.scandir, not Path.glob: glob drops a folder it cannot list and
    # returns nothing, which would read as no history.
    with os.scandir(history_dir) as entries:
        names = [e.name for e in entries if fnmatch.fnmatch(e.name, pattern)]
    ages = {history_dir / n: os.stat(history_dir / n).st_mtime for n in names}
    return sorted(ages, key=ages.__getitem__, reverse=newest_first)


def cmd_list(folder: Path) -> int:
    history_dir = folder / HISTORY_DIRNAME
    present = _history_present(history_dir)
    if present is None:
        return 2
    if not present:
        print(f"  (no rollup history at {history_dir})")
        return 0
    try:
        entries = _history_by_age(history_dir, "*.json")
    except OSError as exc:
        sys.stderr.write(f"error: cannot read rollup history at {history_dir}: {exc}\n")
        return 2
    if not entries:
        print(f"  (empty: {history_dir})")
        return 0
    print(f"=== rollup history at {history_dir} ===")
    for e in entries:
        try:
            data = json.loads(read_text_no_follow(e, root=folder))
        except (OSError, ValueError, RecursionError):
            print(f"  [unreadable] {visible(e.name)}")
            continue
        if not isinstance(data, dict):
            print(f"  [malformed] {visible(e.name)}")
            continue
        op, ts, slug = (data.get(k, "?") for k in ("operation", "timestamp", "slug"))
        moved = data.get("moved", [])
        if not all(isinstance(v, str) for v in (op, ts, slug)) or not isinstance(moved, list):
            print(f"  [malformed] {visible(e.name)}")
            continue
        print(f"  [{op:7s}] {ts}  slug={visible(slug)}  files={len(moved)}")
    return 0


@terminal_safe
def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-rollup",
        description="Rollup primitive: bulk-move files into topic subfolder + generate README parent (Phase 1 T1).",
    )
    p.add_argument("--path", default=None, help="Memory folder root (default: per-cwd memory)")
    sub = p.add_subparsers(dest="op", required=True)

    c_create = sub.add_parser("create", help="Create a new rollup")
    c_create.add_argument("--slug", required=True)
    c_create.add_argument("--topic", default=None, help="Topic tag value (defaults to slug)")
    c_create.add_argument("--title", default=None)
    c_create.add_argument("--dry-run", action="store_true")
    c_create.add_argument("files", nargs="+", help="Files to move (relative to --path)")

    c_undo = sub.add_parser("undo", help="Undo most recent rollup matching slug")
    c_undo.add_argument("--slug", required=True)
    c_undo.add_argument("--dry-run", action="store_true")

    sub.add_parser("list", help="List rollup history entries")

    args = p.parse_args()

    if args.path:
        folder = Path(args.path).resolve()
    else:
        # Default to the first (per-cwd) memory folder via the centralized
        # resolver (env override -> grandfathered .claude layout -> ~/.memforge).
        from memforge.paths import default_memory_paths
        folder = default_memory_paths()[0]

    if args.op == "create":
        files = [Path(f) for f in args.files]
        return cmd_create(folder, args.slug, files, args.topic, args.title, args.dry_run)
    if args.op == "undo":
        return cmd_undo(folder, args.slug, args.dry_run)
    if args.op == "list":
        return cmd_list(folder)
    return 2


if __name__ == "__main__":
    sys.exit(main())
