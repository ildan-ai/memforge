# memory-frontmatter-backfill — populate v0.3.0 frontmatter on existing
# memory files (Phase 1 migration support).
#
# ADR: 0001 §Phase 0 schema migration
# Spec: 0.3.0
#
# Closes the migration cliff that BLOCKERed memory-index-gen,
# memory-audit-deep, memory-query, memory-cluster-suggest. Existing memory
# files mostly lack uid / tier / pinned / last_reviewed / topic-tags. This
# tool fills them in based on file location + filename heuristics WITHOUT
# overwriting any operator-curated fields.
#
# Inferences (only used when the field is absent):
#   uid             = mem-<created-date>-<filename-slug>
#                     (created date = file mtime YYYY-MM-DD)
#   tier            = `index` for root and rollup-README files;
#                     `detail` for files inside named subfolders.
#   pinned          = false
#   last_reviewed   = mtime YYYY-MM-DD
#   updated         = mtime YYYY-MM-DD
#   created         = (kept if present; else mtime YYYY-MM-DD)
#   tags            = filename / folder heuristic for topic:<value>
#                     (only the topic prefix; operator adds others later)
#   status          = `active`
#   sensitivity     = `internal` (deny-public default)
#   access          = `internal`
#   owner           = `operator`
#
# Modes:
#   --dry-run   Print planned changes; write nothing.
#   --apply     Write changes back to files.
#
# Defaults: per-cwd memory + ~/.claude/global-memory/.

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from memforge.frontmatter import (  # noqa: E402
    parse as _mf_parse,
    has_frontmatter as _mf_has_fm,
    render as _mf_render,
)


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class PlannedChange:
    path: Path
    rel: str
    additions: dict = field(default_factory=dict)


def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """Returns (fm dict, raw frontmatter block including --- delimiters, body after).

    Raw block is reconstructed from the parsed dict via memforge.frontmatter
    when needed; backfill code that referenced it for line-based mutation is
    being phased out in favor of round-trip render. The shim returns the
    original raw block to preserve byte-level call-site behavior."""
    if not _mf_has_fm(text):
        return {}, "", text
    fm, body = _mf_parse(text)
    end = text.find("\n---", 4)
    raw_end = end + len("\n---")
    if raw_end < len(text) and text[raw_end] == "\n":
        raw_end += 1
    raw_block = text[:raw_end]
    return fm, raw_block, body


def _frontmatter_present_but_unparseable(text: str) -> bool:
    """True iff the file has frontmatter delimiters with substantive content
    but yaml.safe_load returned an empty dict.

    Distinguishes broken YAML (unquoted colon-space, duplicate keys, etc.)
    from genuinely empty frontmatter blocks. Backfill must skip these — the
    line-based field-append in apply_change otherwise produced duplicate
    keys (observed 2026-05-08 on a rollup README whose `description` value
    contained `: `, which yaml.safe_load rejects as a nested mapping)."""
    if not _mf_has_fm(text):
        return False
    fm, _ = _mf_parse(text)
    if fm:
        return False
    end = text.find("\n---", 4)
    if end == -1:
        return False
    block = text[4:end]
    return bool(block.strip())


def filename_slug(path: Path) -> str:
    stem = path.stem.lower()
    stem = re.sub(r"^(feedback|project|user|reference)_", "", stem)
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem[:60]


def infer_topic_from_path(path: Path, folder_root: Path) -> Optional[str]:
    """Infer topic tag from path. Files under a named subfolder inherit
    the subfolder name as topic. Top-level files get a topic derived
    from filename prefix when it matches a value in the controlled
    vocabulary (spec/taxonomy.yaml).

    The KNOWN_TOPICS set below mirrors the starter taxonomy. Extend it
    when you extend taxonomy.yaml for your project."""
    try:
        rel = path.relative_to(folder_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) > 1 and parts[0] not in ("archive", ".git"):
        return parts[0]
    stem = re.sub(r"^(feedback|project|user|reference)_", "", path.stem.lower())
    tokens = re.split(r"[_\-]", stem)
    KNOWN_TOPICS = {
        "auth", "api", "build", "ci", "db", "deploy", "docs", "frontend",
        "infra", "monitoring", "perf", "security", "testing", "tooling",
    }
    if tokens:
        first = tokens[0]
        if first in KNOWN_TOPICS:
            return first
    return None


def infer_tier(path: Path, folder_root: Path) -> str:
    rel = path.relative_to(folder_root)
    parts = rel.parts
    if len(parts) == 1:
        return "index"
    if path.name == "README.md":
        return "index"
    return "detail"


def file_mtime_date(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def plan_change(path: Path, folder_root: Path) -> Optional[PlannedChange]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if _frontmatter_present_but_unparseable(text):
        sys.stderr.write(
            f"warning: skipping {path} "
            "(frontmatter present but YAML parse failed; "
            "check for unquoted colon-space or duplicate keys)\n"
        )
        return None
    fm, raw_block, after = parse_frontmatter(text)
    if not raw_block:
        return None

    additions: dict = {}
    mtime_ymd = file_mtime_date(path)
    slug = filename_slug(path)

    if "uid" not in fm:
        rel = path.relative_to(folder_root)
        uid_prefix_parts = list(rel.parts[:-1])
        if uid_prefix_parts:
            slug_with_path = "-".join(uid_prefix_parts + [slug])
        else:
            slug_with_path = slug
        slug_with_path = re.sub(r"[^a-z0-9-]+", "-", slug_with_path.lower())[:60]
        created = fm.get("created") or mtime_ymd
        additions["uid"] = f"mem-{created}-{slug_with_path}"

    if "tier" not in fm:
        additions["tier"] = infer_tier(path, folder_root)

    if "pinned" not in fm:
        additions["pinned"] = False

    if "last_reviewed" not in fm:
        additions["last_reviewed"] = mtime_ymd

    if "updated" not in fm:
        additions["updated"] = mtime_ymd

    if "created" not in fm:
        additions["created"] = mtime_ymd

    if "status" not in fm:
        additions["status"] = "active"

    if "sensitivity" not in fm:
        additions["sensitivity"] = "internal"

    if "access" not in fm:
        additions["access"] = "internal"

    if "owner" not in fm:
        additions["owner"] = "operator"

    existing_tags = fm.get("tags", [])
    if isinstance(existing_tags, str):
        existing_tags = [existing_tags]
    if not isinstance(existing_tags, list):
        existing_tags = []
    has_topic = any(isinstance(t, str) and t.startswith("topic:") for t in existing_tags)
    if not has_topic:
        topic = infer_topic_from_path(path, folder_root)
        if topic:
            new_tags = list(existing_tags) + [f"topic:{topic}"]
            additions["tags"] = new_tags

    if not additions:
        return None
    rel = path.relative_to(folder_root).as_posix()
    return PlannedChange(path=path, rel=rel, additions=additions)


def discover_files(folder: Path) -> list[Path]:
    out: list[Path] = []
    skip = {"archive", ".git", "__pycache__"}
    for p in sorted(folder.rglob("*.md")):
        parts = p.relative_to(folder).parts
        if any(part in skip for part in parts):
            continue
        if p.name == "MEMORY.md":
            continue
        out.append(p)
    return out


def render_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def apply_change(path: Path, additions: dict) -> None:
    """Merge `additions` into the file's existing frontmatter and re-render
    via memforge.frontmatter.render (PyYAML safe_dump round-trip).

    The previous implementation appended new field lines to the raw
    frontmatter text. That worked when YAML parsed cleanly, but on broken
    YAML (e.g., unquoted colon-space) every backfill run added the same
    fields again, producing growing blocks of duplicate keys. Round-trip
    rendering avoids that class entirely: the dict-level merge can't have
    duplicate keys, and PyYAML quotes any value that needs it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    if not _mf_has_fm(text):
        return
    if _frontmatter_present_but_unparseable(text):
        # Defense in depth: should already be filtered in plan_change.
        return
    fm, body = _mf_parse(text)
    field_order = [
        "name", "description", "type", "sensitivity", "uid", "tier", "tags",
        "owner", "created", "updated", "last_reviewed", "status",
        "supersedes", "superseded_by", "aliases", "pinned",
        "dynamic_supplement", "references_global", "referenced_by_global",
        "access",
    ]
    merged: dict = dict(fm)
    for key in field_order:
        if key in additions and key not in merged:
            merged[key] = additions[key]
    new_text = _mf_render(merged, body)
    path.write_text(new_text, encoding="utf-8")


def cmd_run(folders: list[Path], apply: bool, limit: int) -> int:
    total_files = 0
    total_changed = 0
    for folder in folders:
        if not folder.exists():
            sys.stderr.write(f"warning: skipping nonexistent {folder}\n")
            continue
        print(f"\n====== {folder} ======")
        files = discover_files(folder)
        total_files += len(files)
        plans: list[PlannedChange] = []
        for f in files:
            plan = plan_change(f, folder)
            if plan is not None:
                plans.append(plan)
        if not plans:
            print("  (no changes needed)")
            continue
        print(f"  {len(plans)} file(s) need additions:")
        shown = plans if limit <= 0 else plans[:limit]
        for plan in shown:
            keys = ", ".join(f"{k}={render_value(v)}" for k, v in plan.additions.items())
            print(f"    {plan.rel}")
            for k, v in plan.additions.items():
                print(f"      + {k}: {render_value(v)}")
        if limit > 0 and len(plans) > limit:
            print(f"    ... and {len(plans) - limit} more (use --limit 0 to show all)")

        if apply:
            for plan in plans:
                apply_change(plan.path, plan.additions)
            print(f"  WROTE additions to {len(plans)} file(s)")
            total_changed += len(plans)
        else:
            print(f"  (dry-run; pass --apply to write)")

    print(f"\nTotal files inspected: {total_files}; "
          f"{'changed' if apply else 'would change'}: "
          f"{total_changed if apply else sum(len(plan_change(f, folder).additions) > 0 for folder in folders if folder.exists() for f in discover_files(folder) if plan_change(f, folder) is not None)}")
    return 0


def default_paths() -> list[Path]:
    out: list[Path] = []
    home = Path.home()
    user = os.environ.get("USER", "")
    if user:
        per_cwd = home / ".claude" / "projects" / f"{user}-claude-projects" / "memory"
        if per_cwd.exists():
            out.append(per_cwd)
    glob = home / ".claude" / "global-memory"
    if glob.exists():
        out.append(glob)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-frontmatter-backfill",
        description="Populate v0.3.0 frontmatter on existing memory files (migration helper).",
    )
    p.add_argument("--path", action="append", default=[], help="Folder (repeatable)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Default. Print planned changes; write nothing.")
    g.add_argument("--apply", action="store_true", help="Write changes back to files.")
    p.add_argument("--limit", type=int, default=10, help="Limit per-folder lines printed (0 = no limit)")
    args = p.parse_args()

    folders = [Path(x).resolve() for x in args.path] if args.path else default_paths()
    if not folders:
        sys.stderr.write("error: no folders specified and no defaults found\n")
        return 2

    if args.apply:
        print("=== APPLY MODE: changes will be written ===")
    else:
        print("=== DRY-RUN MODE: pass --apply to write ===")

    return cmd_run(folders, apply=args.apply, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
