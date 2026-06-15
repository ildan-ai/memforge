# memory-rollup — bulk-move files into a topic subfolder and generate a
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
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from memforge.cli._concurrency_audit import _is_valid_slug
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

{ymd} — initial rollup creation.
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


def find_link_rewriter() -> Optional[Path]:
    """Locate the memory-link-rewriter script. Tries the sibling path
    first (when invoked from a checkout), then $PATH (when installed)."""
    sibling = Path(__file__).resolve().parent / "memory-link-rewriter"
    if sibling.exists():
        return sibling
    found = shutil.which("memory-link-rewriter")
    return Path(found) if found else None


def cmd_create(folder: Path, slug: str, files: list[Path], topic: Optional[str], title: Optional[str], dry_run: bool) -> int:
    if not folder.exists():
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
    if target_dir.exists():
        sys.stderr.write(f"error: rollup folder already exists: {target_dir}\n")
        return 2
    abs_files: list[Path] = []
    for f in files:
        p = f if f.is_absolute() else folder / f
        if not p.exists():
            sys.stderr.write(f"error: file not found: {p}\n")
            return 2
        if p.parent != folder:
            sys.stderr.write(f"error: file not in folder root (rollup moves only top-level files): {p}\n")
            return 2
        if p.name == "MEMORY.md":
            sys.stderr.write(f"error: refusing to move MEMORY.md\n")
            return 2
        abs_files.append(p.resolve())

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
        print(f"    move: {f.name} -> {slug}/{f.name}")
    print(f"  generate: {target_dir}/README.md")
    print(f"  history:  {history_dir}/{ts}-{slug}.json")

    if dry_run:
        print("\n[dry-run] no changes written")
        return 0

    target_dir.mkdir(exist_ok=False)
    rewriter = find_link_rewriter()
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
            _abort_partial(target_dir, [])
            return 2
        moved = list(pairs)
    else:
        moved = []
        try:
            for src, dst in pairs:
                shutil.move(str(src), str(dst))
                moved.append((src, dst))
        except OSError as exc:
            # Roll back the files already moved and remove the empty target dir,
            # mirroring the rename-batch failure handling, so a partial move
            # never leaves an orphaned half-rollup with no undo record.
            sys.stderr.write(f"error: rollup move failed: {exc}; rolling back\n")
            _abort_partial(target_dir, moved)
            return 2

    readme_path = target_dir / "README.md"
    readme_path.write_text(readme_content, encoding="utf-8")

    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"{ts}-{slug}.json"
    history_path.write_text(json.dumps(history_record, indent=2) + "\n", encoding="utf-8")

    print(f"\n  WROTE rollup at {target_dir} ({len(moved)} files moved)")
    print(f"  WROTE history record at {history_path}")
    print("\n  Next: review the README, run memory-index-gen --write to refresh MEMORY.md")
    return 0


def _abort_partial(target_dir: Path, moved: list[tuple[Path, Path]]) -> None:
    for src, dst in reversed(moved):
        if dst.exists() and not src.exists():
            shutil.move(str(dst), str(src))
    if target_dir.exists() and not any(target_dir.iterdir()):
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
    if not history_dir.exists():
        sys.stderr.write(f"error: no rollup history at {history_dir}\n")
        return 2
    matches = sorted(
        history_dir.glob(f"*-{slug}.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        sys.stderr.write(f"error: no rollup history entry for slug '{slug}'\n")
        return 2
    record_path = matches[0]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("operation") != "create":
        sys.stderr.write(f"error: history record at {record_path} is not a 'create' op\n")
        return 2

    moves: list[dict] = record.get("moved", [])
    target_dir = folder / slug

    # The history JSON lives inside the auto-committed memory folder, so a
    # hostile contributor controls its contents. Treat every path it names as
    # untrusted: refuse the whole undo if any from/to/readme path escapes the
    # memory folder, rather than feeding it to shutil.move / unlink.
    readme = Path(record.get("readme", target_dir / "README.md"))
    untrusted_paths = [readme]
    for m in moves:
        untrusted_paths.append(Path(m.get("from", "")))
        untrusted_paths.append(Path(m.get("to", "")))
    for cand in untrusted_paths:
        if not _within(folder, cand):
            sys.stderr.write(
                f"error: history record references a path outside the memory "
                f"folder ({cand}); refusing undo (possible tampered history)\n"
            )
            return 2

    print(f"=== rollup undo: slug='{slug}' ({len(moves)} files) ===")
    print(f"  history record: {record_path}")
    print(f"  rollup folder:  {target_dir}")
    for m in moves:
        print(f"    move-back: {Path(m['to']).name} -> {Path(m['from']).name}")
    print(f"  remove: {target_dir}/README.md + {target_dir}/")

    if dry_run:
        print("\n[dry-run] no changes written")
        return 0

    rewriter = find_link_rewriter()
    skipped = 0
    for m in moves:
        dst = Path(m["from"])
        src = Path(m["to"])
        if not src.exists():
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
            shutil.move(str(src), str(dst))

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

    if readme.exists():
        readme.unlink()
    if target_dir.exists() and not any(target_dir.iterdir()):
        target_dir.rmdir()

    undone_path = history_dir / f"{record_path.stem}.undone.json"
    record["operation"] = "undone"
    record["undone_at"] = now_ts()
    undone_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    record_path.unlink()
    print(f"\n  UNDONE rollup '{slug}'; history record renamed to {undone_path.name}")
    print(f"\n  Next: run memory-index-gen --write to refresh MEMORY.md")
    return 0


def cmd_list(folder: Path) -> int:
    history_dir = folder / HISTORY_DIRNAME
    if not history_dir.exists():
        print(f"  (no rollup history at {history_dir})")
        return 0
    entries = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not entries:
        print(f"  (empty: {history_dir})")
        return 0
    print(f"=== rollup history at {history_dir} ===")
    for e in entries:
        try:
            data = json.loads(e.read_text(encoding="utf-8"))
            op = data.get("operation", "?")
            slug = data.get("slug", "?")
            n = len(data.get("moved", []))
            ts = data.get("timestamp", "?")
            print(f"  [{op:7s}] {ts}  slug={slug}  files={n}")
        except (OSError, json.JSONDecodeError):
            print(f"  [unreadable] {e.name}")
    return 0


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
