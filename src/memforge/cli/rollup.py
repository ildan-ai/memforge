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
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


HISTORY_DIRNAME = ".memforge-rollup-history"


README_TEMPLATE = """\
---
name: {title}
description: Rollup of {n} memories on topic '{topic}'.
type: project
sensitivity: internal
uid: mem-{ymd}-rollup-{slug}
tier: index
tags: [topic:{topic}]
owner: operator
created: {ymd}
updated: {ymd}
last_reviewed: {ymd}
status: active
pinned: false
---

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
    target_dir = folder / slug
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
    readme_content = README_TEMPLATE.format(
        title=final_title,
        topic=final_topic,
        slug=slug,
        n=len(abs_files),
        ymd=today_ymd(),
        file_list=file_list_md,
    )

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
        for src, dst in pairs:
            shutil.move(str(src), str(dst))
            moved.append((src, dst))

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
    for m in moves:
        dst = Path(m["from"])
        src = Path(m["to"])
        if not src.exists():
            sys.stderr.write(f"warning: source {src} missing; skipping\n")
            continue
        if rewriter is not None:
            rc = subprocess.call(
                [str(rewriter), "--path", str(folder), "rename", str(src), str(dst)]
            )
            if rc != 0:
                sys.stderr.write(f"warning: link-rewriter rename failed for {src} (rc={rc}); leaving in place\n")
                continue
        else:
            shutil.move(str(src), str(dst))

    readme = Path(record.get("readme", target_dir / "README.md"))
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
        home = Path.home()
        user = os.environ.get("USER", "")
        folder = home / ".claude" / "projects" / f"{user}-claude-projects" / "memory"

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
