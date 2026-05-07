# memory-audit-deep — v0.3.0-aware recursive memory audit (Phase 1 T4).
#
# Sibling to the bash `memory-audit` (which audits flat-folder structural
# integrity). This tool walks rollup subfolders and validates:
#   - UID uniqueness across the entire tree
#   - tag membership against taxonomy.yaml
#   - supersedes / superseded_by resolution
#   - broken `mem:uid` cross-references
#   - rollup README.md `last_reviewed` staleness (>90 days)
#
# ADR: 0001 §Phase 1 T4
# Spec: 0.3.0
#
# Defaults: per-cwd memory + ~/.claude/global-memory/. Override via --path.
# Excludes archive/ and .git/. Returns exit 1 with --strict on violations.

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from memforge.frontmatter import parse as _mf_parse  # noqa: E402

try:
    import yaml  # type: ignore
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False


MEM_URI_RE = re.compile(r"mem:([a-zA-Z0-9][a-zA-Z0-9_\-]*)")


@dataclass
class FileRecord:
    path: Path
    rel: str
    fm: dict = field(default_factory=dict)
    is_rollup_readme: bool = False


def parse_frontmatter(text: str) -> dict:
    """Compatibility shim. Use memforge.frontmatter.parse() in new code."""
    fm, _ = _mf_parse(text)
    return fm


def load_taxonomy(memforge_root: Optional[Path]) -> dict:
    """Loads taxonomy.yaml from memforge/spec/. Returns empty dict if not
    found or PyYAML missing."""
    if memforge_root is None:
        memforge_root = Path(__file__).resolve().parent.parent
    tax_path = memforge_root / "spec" / "taxonomy.yaml"
    if not tax_path.exists() or not _HAVE_YAML:
        return {}
    try:
        with tax_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return {}


def known_namespace_values(taxonomy: dict) -> dict[str, set[str]]:
    """Returns {namespace: {canonical values + synonyms}} from the loaded
    taxonomy."""
    out: dict[str, set[str]] = {}
    namespaces = taxonomy.get("namespaces", {}) if isinstance(taxonomy, dict) else {}
    for ns, ns_def in namespaces.items():
        values: set[str] = set()
        ns_values = ns_def.get("values", {}) if isinstance(ns_def, dict) else {}
        for canonical, val_def in ns_values.items():
            values.add(canonical)
            if isinstance(val_def, dict):
                for syn in val_def.get("synonyms", []) or []:
                    values.add(syn)
        out[ns] = values
    return out


def discover_files(folder: Path) -> list[FileRecord]:
    out: list[FileRecord] = []
    skip = {"archive", ".git", "__pycache__"}
    for path in sorted(folder.rglob("*.md")):
        parts = path.relative_to(folder).parts
        if any(p in skip for p in parts):
            continue
        if path.name == "MEMORY.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text)
        if not fm:
            continue
        rec = FileRecord(
            path=path,
            rel=path.relative_to(folder).as_posix(),
            fm=fm,
            is_rollup_readme=(path.name == "README.md" and path.parent != folder),
        )
        out.append(rec)
    return out


def parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def audit(folder: Path, taxonomy_values: dict[str, set[str]], stale_days: int) -> list[str]:
    """Returns a list of violation strings. Empty list = clean."""
    violations: list[str] = []
    files = discover_files(folder)
    if not files:
        return violations

    by_uid: dict[str, list[FileRecord]] = {}
    all_uids: set[str] = set()
    for rec in files:
        uid = rec.fm.get("uid")
        if isinstance(uid, str) and uid:
            by_uid.setdefault(uid, []).append(rec)
            all_uids.add(uid)

    for uid, recs in by_uid.items():
        if len(recs) > 1:
            paths = ", ".join(r.rel for r in recs)
            violations.append(f"UID collision '{uid}' across: {paths}")

    for rec in files:
        tags = rec.fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, str) or ":" not in tag:
                continue
            ns, _, val = tag.partition(":")
            if ns in taxonomy_values:
                if val not in taxonomy_values[ns]:
                    violations.append(
                        f"{rec.rel}: tag '{tag}' value not in taxonomy namespace '{ns}'"
                    )

    for rec in files:
        for field_name in ("supersedes", "superseded_by", "aliases"):
            refs = rec.fm.get(field_name, [])
            if isinstance(refs, str):
                refs = [refs]
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if not isinstance(ref, str) or not ref:
                    continue
                if ref not in all_uids:
                    violations.append(
                        f"{rec.rel}: {field_name} references unknown UID '{ref}'"
                    )

    for rec in files:
        try:
            text = rec.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in MEM_URI_RE.finditer(text):
            target = m.group(1)
            if target not in all_uids:
                violations.append(f"{rec.rel}: broken mem:uid link 'mem:{target}'")

    today = date.today()
    for rec in files:
        if not rec.is_rollup_readme:
            continue
        last = rec.fm.get("last_reviewed")
        if not last:
            violations.append(f"{rec.rel}: rollup README missing last_reviewed")
            continue
        d = parse_date(last) if isinstance(last, str) else None
        if d is None:
            violations.append(f"{rec.rel}: last_reviewed='{last}' unparseable")
            continue
        delta = (today - d).days
        if delta > stale_days:
            violations.append(
                f"{rec.rel}: rollup last_reviewed {last} is {delta} days old (>{stale_days})"
            )

    return violations


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
        prog="memory-audit-deep",
        description="v0.3.0-aware recursive memory audit (Phase 1 T4).",
    )
    p.add_argument("--path", action="append", default=[], help="Folder (repeatable)")
    p.add_argument("--strict", action="store_true", help="Exit 1 on any violation")
    p.add_argument(
        "--stale-days",
        type=int,
        default=90,
        help="Rollup last_reviewed staleness threshold (default 90)",
    )
    p.add_argument(
        "--memforge-root",
        default=None,
        help="Override memforge repo root for taxonomy.yaml lookup",
    )
    args = p.parse_args()

    folders = [Path(p).resolve() for p in args.path] if args.path else default_paths()
    if not folders:
        sys.stderr.write("error: no folders specified and no defaults found\n")
        return 2

    memforge_root = Path(args.memforge_root).resolve() if args.memforge_root else None
    taxonomy = load_taxonomy(memforge_root)
    tax_values = known_namespace_values(taxonomy)
    if not tax_values:
        sys.stderr.write(
            "warning: taxonomy.yaml not loaded (PyYAML missing or file not found); "
            "tag-membership checks will be skipped\n"
        )

    total = 0
    for folder in folders:
        if not folder.exists():
            sys.stderr.write(f"warning: skipping nonexistent {folder}\n")
            continue
        violations = audit(folder, tax_values, args.stale_days)
        print(f"\n====== {folder} ======")
        if not violations:
            print("  clean (no violations)")
        else:
            print(f"  VIOLATIONS ({len(violations)}):")
            for v in violations:
                print(f"    - {v}")
            total += len(violations)

    print(f"\nTotal violations: {total}")
    if args.strict and total > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
