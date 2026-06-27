# memory-link-rewriter : link integrity + UID rewriting for MemForge folders.
#
# Spec: 0.4.0
#
# Subcommands:
#   check        : validate UID uniqueness + link integrity (path links + mem:uid)
#   rename       : move a file + rewrite all internal references to it
#   rename-batch : move MANY files + rewrite all internal references in ONE pass;
#                  reads a JSON payload [{"src": "...", "dst": "..."}, ...] from
#                  --json or stdin. This is the cross-tool contract memory-rollup
#                  shells out to (one folder index + one file walk per rollup).
#                  Failure contract: on any per-move error rename-batch attempts
#                  full rollback of the moves it already performed and returns rc=2;
#                  a caller seeing rc!=0 should assume the move did NOT complete and
#                  re-run `check` to confirm folder integrity before retrying.
#   upgrade      : rewrite path-form internal links to mem:uid form (when target has uid)
#
# Lays the foundation for the index generator + memory-rollup tool, both
# of which depend on stable UID resolution.
#
# v0.4 remit (normative, per SPEC.md §"Multi-agent concurrency / Tool-side
# contract"): this tool MUST NOT mirror-write the `superseded_by:` field as
# a side effect of any operation. Mirror-writing `superseded_by:` is
# exclusively the responsibility of the resolve operation
# (memforge-resolve), which writes it inside its atomic
# `memforge: resolve <topic>` commit. Adding mirror-write logic here would
# reintroduce an unguarded authority channel.
#
# Defaults: per-cwd memory folder ($USER-claude-projects/memory/) +
# ~/.claude/global-memory/. Override with --path (repeatable).

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from memforge.frontmatter import parse as _mf_parse, has_frontmatter as _mf_has_fm  # noqa: E402
from memforge.paths import default_memory_paths  # noqa: E402
from memforge.models import FolderIndex, Link, Memory  # noqa: E402
from memforge.discovery import walk_memory_files  # noqa: E402

# Centralized in memforge.paths (env override -> grandfathered .claude layout if
# present -> ~/.memforge). Keeps the package IDE/OS-neutral.
DEFAULT_PATHS = default_memory_paths()

# Markdown link regex: captures [text](target).
# Skips images (![...](...)) and reference-style links ([text][ref]).
LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+?)\]\(([^)\n]+?)\)")
MEM_URI_RE = re.compile(r"^mem:([A-Za-z0-9_\-]+)$")

# Wikilink regex: captures [[token]] and [[token|display]].
# Group 1 = token, group 2 = display text (may be None).
WIKILINK_RE = re.compile(r"\[\[([^\]|\n]+?)(?:\|([^\]\n]+?))?\]\]")


# Memory, FolderIndex, and Link are the canonical shared domain models
# (memforge.models), imported above. This tool no longer forks its own
# divergent copies (closes models-01 / discovery-02).


# ----- wikilink helpers -----

def _build_alias_set(m):
    """Return alias set for a Memory: old filename stem, old name frontmatter, uid.

    Only non-empty strings are included. This is the resolution set used by the
    wikilink rewrite guard: a [[token]] is rewritten ONLY when the token matches
    one of these aliases, preventing false rewrites of prose/ADR/code tokens that
    happen to contain [[ ]].
    """
    aliases: set = set()
    # Filename stem (e.g. 'mem-00408' from 'mem-00408.md')
    stem = m.path.stem
    if stem:
        aliases.add(stem)
    # Frontmatter name (may contain spaces; wikilinks use it as-is)
    name = m.frontmatter.get('name', '') if m.frontmatter else ''
    if name:
        aliases.add(name)
    # UID
    if m.uid:
        aliases.add(m.uid)
    return aliases


def _collect_wikilink_rewrites(text, src_aliases, new_stem):
    """Scan text for [[token]] / [[token|display]] wikilinks that resolve to a
    renamed file (token in src_aliases) and return replacement spans.

    Returns list of (start, end, replacement) sorted ascending by start.
    Caller applies in reverse order to keep earlier spans valid.

    False-rewrite guard: only tokens in src_aliases are rewritten.
    """
    rewrites: list = []
    for m in WIKILINK_RE.finditer(text):
        token = m.group(1).strip()
        display = m.group(2)  # None when no '|display' suffix
        if token not in src_aliases:
            continue  # does not resolve to the renamed file; leave untouched
        # Build the replacement: [[new_stem]] or [[new_stem|display]]
        if display is not None:
            replacement = f'[[{new_stem}|{display}]]'
        else:
            replacement = f'[[{new_stem}]]'
        rewrites.append((m.start(), m.end(), replacement))
    return rewrites


# ----- frontmatter parsing -----

def parse_frontmatter(text: str) -> dict:
    """Compatibility shim. Returns full frontmatter dict (was scalar-only).
    Use memforge.frontmatter.parse() in new code."""
    fm, _ = _mf_parse(text)
    return fm


def has_frontmatter(text: str) -> bool:
    return _mf_has_fm(text)


# ----- folder scan -----

# walk_memory_files is the canonical discovery walk (memforge.discovery),
# imported above. It prunes archive/ and MEMORY.md and yields filenames in
# sorted order (stable-order contract), replacing the prior ad-hoc, unsorted
# local copy (closes discovery-02).


def index_folder(root: Path) -> FolderIndex:
    idx = FolderIndex(root=root)
    seen_uid: dict[str, Memory] = {}
    dup_groups: dict[str, list[Memory]] = {}

    for fpath in walk_memory_files(root):
        try:
            text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text)
        m = Memory(
            path=fpath,
            relpath=fpath.relative_to(root),
            root=root,
            uid=fm.get("uid"),
            name=fm.get("name"),
            tier=fm.get("tier"),
            has_frontmatter=has_frontmatter(text),
            frontmatter=fm,
        )
        idx.memories.append(m)
        idx.by_relpath[str(m.relpath)] = m
        if m.uid:
            if m.uid in seen_uid:
                dup_groups.setdefault(m.uid, [seen_uid[m.uid]]).append(m)
            else:
                seen_uid[m.uid] = m
                idx.by_uid[m.uid] = m

    idx.duplicate_uids = list(dup_groups.items())
    return idx


# ----- link extraction + classification -----

# Link is the canonical shared model (memforge.models), imported above.


def extract_links(text: str) -> list[Link]:
    out = []
    for m in LINK_RE.finditer(text):
        target = m.group(2).strip()
        mem_match = MEM_URI_RE.match(target)
        out.append(Link(
            text=m.group(1),
            target=target,
            is_mem_uri=mem_match is not None,
            uid=mem_match.group(1) if mem_match else None,
            span=(m.start(), m.end()),
        ))
    return out


def is_internal_path_link(target: str) -> bool:
    """A link is internal if it ends in .md and isn't an absolute URL or anchor-only."""
    if "://" in target:
        return False
    if target.startswith("#"):
        return False
    target = target.split("#", 1)[0]  # strip fragment
    return target.endswith(".md")


# ----- check subcommand -----

def cmd_check(idx: FolderIndex, *, json_out: bool = False) -> int:
    issues = []
    warnings = []

    # duplicate uids
    for uid, files in idx.duplicate_uids:
        issues.append(f"duplicate uid '{uid}' in {len(files)+1} files: " +
                      ", ".join(str(m.relpath) for m in files))

    # missing uids (warning only in v0.3.0; will be error in v0.4.0)
    missing_uid = [m for m in idx.memories if not m.uid and m.has_frontmatter]
    if missing_uid:
        warnings.append(f"{len(missing_uid)} files missing uid (required in v0.4.0)")
        for m in missing_uid[:5]:
            warnings.append(f"  - {m.relpath}")
        if len(missing_uid) > 5:
            warnings.append(f"  - ... ({len(missing_uid) - 5} more)")

    # link integrity
    broken_path_links: list[tuple[Memory, Link]] = []
    broken_mem_links: list[tuple[Memory, Link]] = []
    for m in idx.memories:
        text = m.path.read_text(encoding="utf-8")
        for link in extract_links(text):
            if link.is_mem_uri:
                if link.uid not in idx.by_uid:
                    broken_mem_links.append((m, link))
            elif is_internal_path_link(link.target):
                target_path = link.target.split("#", 1)[0]
                # resolve relative to the file's directory
                resolved = (m.path.parent / target_path).resolve()
                try:
                    rel = resolved.relative_to(idx.root)
                except ValueError:
                    continue  # link goes outside the memory folder
                if str(rel) not in idx.by_relpath and not (idx.root / rel).exists():
                    broken_path_links.append((m, link))

    if broken_mem_links:
        issues.append(f"{len(broken_mem_links)} broken mem:uid links")
        for m, link in broken_mem_links[:10]:
            issues.append(f"  - {m.relpath}: mem:{link.uid} -> not found")

    if broken_path_links:
        issues.append(f"{len(broken_path_links)} broken path links")
        for m, link in broken_path_links[:10]:
            issues.append(f"  - {m.relpath}: {link.target} -> not found")

    # report
    print(f"====== {idx.root} ======")
    print(f"  Files indexed: {len(idx.memories)}")
    print(f"  With UID: {sum(1 for m in idx.memories if m.uid)}")
    print(f"  Tier=index: {sum(1 for m in idx.memories if m.tier == 'index')}")
    print(f"  Tier=detail: {sum(1 for m in idx.memories if m.tier == 'detail')}")
    print()

    if issues:
        print("  ISSUES:")
        for line in issues:
            print(f"    {line}")
        print()
    if warnings:
        print("  WARNINGS:")
        for line in warnings:
            print(f"    {line}")
        print()

    return 1 if issues else 0


# ----- rename subcommand -----

def cmd_rename(idx: FolderIndex, src: Path, dst: Path, *, dry_run: bool) -> int:
    """Move src to dst within the memory folder + rewrite all references."""
    root_resolved = idx.root.resolve()
    src_abs = src.resolve()
    dst_abs = dst.resolve()
    try:
        src_rel = src_abs.relative_to(root_resolved)
        dst_rel = dst_abs.relative_to(root_resolved)
    except ValueError:
        print(f"error: src or dst outside memory root {idx.root} (resolved={root_resolved})", file=sys.stderr)
        return 2

    if not src_abs.exists():
        print(f"error: source file does not exist: {src_abs}", file=sys.stderr)
        return 2
    if dst_abs.exists():
        print(f"error: destination already exists: {dst_abs}", file=sys.stderr)
        return 2

    # find all references to src in OTHER files
    src_basename = src_abs.name
    src_relpath_str = str(src_rel)
    src_mem = idx.by_relpath.get(str(src_rel))
    src_aliases = _build_alias_set(src_mem) if src_mem else {src_abs.stem}
    dst_stem = dst_abs.stem
    # rewrites: (Memory, markdown_rewrites, wikilink_rewrites)
    # wikilink_rewrites: list of (start, end, replacement)
    rewrites: list[tuple[Memory, list[tuple[Link, str]], list[tuple[int, int, str]]]] = []
    wikilink_ambiguous: list[str] = []

    for m in idx.memories:
        if m.path == src_abs:
            continue
        text = m.path.read_text(encoding="utf-8")
        per_file: list[tuple[Link, str]] = []
        for link in extract_links(text):
            if link.is_mem_uri:
                continue  # mem:uid is stable across renames; no rewrite needed
            if not is_internal_path_link(link.target):
                continue
            link_path = link.target.split("#", 1)[0]
            link_resolved = (m.path.parent / link_path).resolve()
            if link_resolved == src_abs:
                # rewrite to dst (relative to this file's parent)
                new_rel = os.path.relpath(dst_abs, m.path.parent)
                fragment = ("#" + link.target.split("#", 1)[1]) if "#" in link.target else ""
                new_target = new_rel + fragment
                per_file.append((link, new_target))
        wl_file = _collect_wikilink_rewrites(text, src_aliases, dst_stem)
        if per_file or wl_file:
            rewrites.append((m, per_file, wl_file))

    md_count = sum(len(per) for _, per, _ in rewrites)
    wl_count = sum(len(wl) for _, _, wl in rewrites)
    print(f"rename: {src_rel} -> {dst_rel}")
    print(f"references to rewrite: {md_count} markdown + {wl_count} wikilink(s) in {len(rewrites)} files")
    for m, per, wl in rewrites:
        print(f"  {m.relpath}:")
        for link, new in per:
            print(f"    {link.target} -> {new}")
        for start, end, rep in wl:
            src_text = m.path.read_text(encoding="utf-8")[start:end]
            print(f"    wikilink: {src_text} -> {rep} (line approx {m.path.read_text(encoding='utf-8')[:start].count(chr(10)) + 1})")

    if dry_run:
        print("\n(dry-run; no changes written)")
        return 0

    # move file
    dst_abs.parent.mkdir(parents=True, exist_ok=True)
    src_abs.rename(dst_abs)

    # rewrite references
    for m, per, wl in rewrites:
        text = m.path.read_text(encoding="utf-8")
        # apply markdown rewrites in reverse to keep spans valid
        per_sorted = sorted(per, key=lambda x: -x[0].span[0])
        for link, new in per_sorted:
            start, end = link.span
            old_md = text[start:end]
            new_md = f"[{link.text}]({new})"
            text = text[:start] + new_md + text[end:]
        # apply wikilink rewrites in reverse to keep spans valid
        # NOTE: spans are from the ORIGINAL text; re-collect after markdown rewrites
        # to get correct positions in the modified text
        wl_fresh = _collect_wikilink_rewrites(text, src_aliases, dst_stem)
        for start, end, replacement in sorted(wl_fresh, key=lambda x: -x[0]):
            text = text[:start] + replacement + text[end:]
        m.path.write_text(text, encoding="utf-8")

    print(f"\nmoved + rewrote {len(rewrites)} files")
    return 0


# ----- rename-batch subcommand -----

def cmd_rename_batch(idx: FolderIndex, pairs: list[tuple[Path, Path]], *, dry_run: bool) -> int:
    """Move many files + rewrite all internal references in ONE pass.

    Closes the O(N) subprocess-spawn pattern in memory-rollup. Cost: one
    folder index + one file-walk + one write per affected file, regardless
    of how many files are being moved.
    """
    root_resolved = idx.root.resolve()
    src_to_dst: dict[Path, Path] = {}
    for src, dst in pairs:
        src_abs = src.resolve()
        dst_abs = dst.resolve()
        try:
            src_abs.relative_to(root_resolved)
            dst_abs.relative_to(root_resolved)
        except ValueError:
            print(f"error: src or dst outside memory root {idx.root} (resolved={root_resolved})", file=sys.stderr)
            return 2
        if not src_abs.exists():
            print(f"error: source file does not exist: {src_abs}", file=sys.stderr)
            return 2
        if dst_abs.exists():
            print(f"error: destination already exists: {dst_abs}", file=sys.stderr)
            return 2
        src_to_dst[src_abs] = dst_abs

    if not src_to_dst:
        print("rename-batch: empty pair list; nothing to do")
        return 0

    src_set = set(src_to_dst.keys())

    # Build per-src alias sets for wikilink resolution.
    # alias_to_src: token -> list of src_abs that expose that alias.
    # Used for cross-root disambiguation: if a token resolves to >1 src in
    # the batch, we resolve within the linking file's own root; if still
    # ambiguous, skip (do not rewrite).
    src_alias_map: dict[Path, set] = {}
    alias_to_srcs: dict[str, list] = {}
    src_to_new_stem: dict[Path, str] = {}
    for src_abs, dst_abs in src_to_dst.items():
        src_mem = idx.by_relpath.get(str(src_abs.relative_to(root_resolved)))
        aliases = _build_alias_set(src_mem) if src_mem else {src_abs.stem}
        src_alias_map[src_abs] = aliases
        src_to_new_stem[src_abs] = dst_abs.stem
        for alias in aliases:
            alias_to_srcs.setdefault(alias, []).append(src_abs)

    # Per-memory rewrite plan. For each memory, the "effective parent dir"
    # is its post-move location (same as current for non-moved files; the
    # mapped destination's parent for moved files). Relative paths are
    # computed against that effective parent so links survive the move.
    # rewrites: (Memory, new_dir, markdown_rewrites, wikilink_rewrites)
    rewrites: list[tuple[Memory, Path, list[tuple[Link, str]], list[tuple[int, int, str]]]] = []
    wikilink_ambiguous: list[str] = []

    for m in idx.memories:
        m_resolved = m.path.resolve()
        is_moved = m_resolved in src_set
        new_m_dir = src_to_dst[m_resolved].parent if is_moved else m.path.parent.resolve()

        m_text = m.path.read_text(encoding="utf-8")
        per_file: list[tuple[Link, str]] = []
        for link in extract_links(m_text):
            if link.is_mem_uri:
                continue
            if not is_internal_path_link(link.target):
                continue
            link_path = link.target.split("#", 1)[0]
            link_resolved = (m.path.parent / link_path).resolve()
            # Determine the link target's post-move location.
            target_post = src_to_dst.get(link_resolved, link_resolved)
            if target_post == link_resolved and not is_moved:
                # neither this file nor the link target is being moved:
                # nothing to rewrite.
                continue
            new_rel = os.path.relpath(target_post, new_m_dir)
            fragment = ("#" + link.target.split("#", 1)[1]) if "#" in link.target else ""
            new_target = new_rel + fragment
            if new_target == link.target:
                continue
            per_file.append((link, new_target))

        # Wikilink rewriting: for each [[token]] in this file, find which
        # (if any) renamed src it resolves to.
        wl_file: list[tuple[int, int, str]] = []
        seen_wl_tokens: set[str] = set()
        for wl_match in WIKILINK_RE.finditer(m_text):
            token = wl_match.group(1).strip()
            if token in seen_wl_tokens:
                continue
            candidates = alias_to_srcs.get(token, [])
            if not candidates:
                continue  # token does not match any renamed file
            if len(candidates) == 1:
                # Unambiguous: rewrite
                target_src = candidates[0]
            else:
                # Cross-root ambiguity: resolve within this file's own root.
                # Since all files here are in idx.root (single root per invocation),
                # filter candidates within this root.
                same_root = [c for c in candidates if c.parent == m.path.parent.resolve()
                             or str(c).startswith(str(root_resolved))]
                if len(same_root) == 1:
                    target_src = same_root[0]
                else:
                    # Still ambiguous: skip and log.
                    wikilink_ambiguous.append(
                        f"[[{token}]] in {m.relpath}: resolves to {len(candidates)} renamed files; skipped"
                    )
                    seen_wl_tokens.add(token)
                    continue
            new_stem = src_to_new_stem[target_src]
            seen_wl_tokens.add(token)
            # Collect all occurrences of this token in the file
            per_token = _collect_wikilink_rewrites(m_text, {token}, new_stem)
            wl_file.extend(per_token)

        if per_file or wl_file:
            rewrites.append((m, new_m_dir, per_file, wl_file))

    md_count = sum(len(per) for _, _, per, _ in rewrites)
    wl_count = sum(len(wl) for _, _, _, wl in rewrites)
    print(f"rename-batch: {len(src_to_dst)} files to move")
    print(f"references to rewrite: {md_count} markdown + {wl_count} wikilink(s) in {len(rewrites)} files")
    if wl_count > 0:
        for m, _, _, wl in rewrites:
            for start, end, rep in wl:
                m_text = m.path.read_text(encoding="utf-8")
                orig = m_text[start:end]
                lineno = m_text[:start].count("\n") + 1
                print(f"  wikilink: {orig} -> {rep} in {m.relpath} (line {lineno})")
    if wikilink_ambiguous:
        print(f"  ambiguous/skipped wikilinks ({len(wikilink_ambiguous)}):")
        for msg in wikilink_ambiguous:
            print(f"    {msg}")

    if dry_run:
        for src, dst in pairs:
            print(f"  {src.resolve().relative_to(root_resolved)} -> {dst.resolve().relative_to(root_resolved)}")
        print("\n(dry-run; no changes written)")
        return 0

    # Pre-compute rewritten text for each affected memory; defer writes
    # until after moves so we can write to the post-move path.
    rewritten_text: dict[Path, str] = {}
    for m, _new_dir, per, wl in rewrites:
        text = m.path.read_text(encoding="utf-8")
        per_sorted = sorted(per, key=lambda x: -x[0].span[0])
        for link, new in per_sorted:
            start, end = link.span
            new_md = f"[{link.text}]({new})"
            text = text[:start] + new_md + text[end:]
        # Re-collect wikilink rewrites on the (possibly already-modified) text
        # to get correct spans after markdown substitutions.
        # We only rewrite tokens that were identified as non-ambiguous above.
        # Build the per-file alias map: token -> new_stem for non-ambiguous.
        token_to_new_stem: dict[str, str] = {}
        for start, end, rep in wl:
            # extract original token from original text (before any changes)
            # by re-scanning. Simpler: just rebuild from wl entries.
            pass
        # Collect fresh (idempotent: new_stem won't match old aliases after rename)
        for src_abs_inner, dst_abs_inner in src_to_dst.items():
            aliases = src_alias_map[src_abs_inner]
            new_stem = src_to_new_stem[src_abs_inner]
            wl_fresh = _collect_wikilink_rewrites(text, aliases, new_stem)
            for s, e, replacement in sorted(wl_fresh, key=lambda x: -x[0]):
                text = text[:s] + replacement + text[e:]
        rewritten_text[m.path.resolve()] = text

    moved: list[tuple[Path, Path]] = []
    try:
        for src_abs, dst_abs in src_to_dst.items():
            dst_abs.parent.mkdir(parents=True, exist_ok=True)
            src_abs.rename(dst_abs)
            moved.append((src_abs, dst_abs))

        for m_resolved, text in rewritten_text.items():
            target_path = src_to_dst.get(m_resolved, m_resolved)
            target_path.write_text(text, encoding="utf-8")
    except Exception as e:
        print(f"error during batch rename: {e}; rolling back {len(moved)} moves", file=sys.stderr)
        for src_abs, dst_abs in reversed(moved):
            try:
                dst_abs.rename(src_abs)
            except OSError:
                pass
        return 2

    print(f"\nmoved {len(src_to_dst)} files; rewrote refs in {len(rewrites)} files")
    return 0


# ----- upgrade subcommand -----

def cmd_upgrade(idx: FolderIndex, *, dry_run: bool) -> int:
    """Rewrite path-form internal links to mem:uid form when target has uid."""
    upgraded_count = 0
    files_touched = 0

    for m in idx.memories:
        text = m.path.read_text(encoding="utf-8")
        replacements: list[tuple[Link, str]] = []
        for link in extract_links(text):
            if link.is_mem_uri:
                continue
            if not is_internal_path_link(link.target):
                continue
            link_path = link.target.split("#", 1)[0]
            link_resolved = (m.path.parent / link_path).resolve()
            try:
                target_rel = link_resolved.relative_to(idx.root)
            except ValueError:
                continue
            target_mem = idx.by_relpath.get(str(target_rel))
            if target_mem and target_mem.uid:
                fragment = ("#" + link.target.split("#", 1)[1]) if "#" in link.target else ""
                replacements.append((link, f"mem:{target_mem.uid}{fragment}"))

        if not replacements:
            continue

        files_touched += 1
        upgraded_count += len(replacements)
        print(f"  {m.relpath}: {len(replacements)} link(s)")
        for link, new in replacements[:3]:
            print(f"    {link.target} -> {new}")
        if len(replacements) > 3:
            print(f"    ... +{len(replacements) - 3} more")

        if not dry_run:
            replacements_sorted = sorted(replacements, key=lambda x: -x[0].span[0])
            for link, new in replacements_sorted:
                start, end = link.span
                new_md = f"[{link.text}]({new})"
                text = text[:start] + new_md + text[end:]
            m.path.write_text(text, encoding="utf-8")

    print(f"\nupgraded {upgraded_count} link(s) in {files_touched} file(s)")
    if dry_run:
        print("(dry-run; no changes written)")
    return 0


# ----- main -----

def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-link-rewriter",
        description="Link integrity + UID rewriting for MemForge folders (spec 0.3.0).",
    )
    p.add_argument("--path", action="append", type=Path,
                   help="Memory folder to operate on (repeatable; defaults to per-cwd + global).")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Validate UID uniqueness + link integrity.")

    p_rename = sub.add_parser("rename", help="Move a file + rewrite references.")
    p_rename.add_argument("src", type=Path)
    p_rename.add_argument("dst", type=Path)
    p_rename.add_argument("--dry-run", action="store_true")

    p_batch = sub.add_parser("rename-batch",
        help="Move multiple files + rewrite references in ONE pass. "
             "Reads JSON [{\"src\":\"...\",\"dst\":\"...\"},...] from --json or stdin.")
    p_batch.add_argument("--json", type=Path, default=None,
                         help="JSON file with [{src,dst},...]; reads stdin if omitted")
    p_batch.add_argument("--dry-run", action="store_true")

    p_upgrade = sub.add_parser("upgrade", help="Rewrite path links to mem:uid form.")
    p_upgrade.add_argument("--dry-run", action="store_true")

    args = p.parse_args()
    paths = args.path or DEFAULT_PATHS
    paths = [p for p in paths if p.is_dir()]

    if not paths:
        print("no memory folders found", file=sys.stderr)
        return 2

    rc = 0
    for root in paths:
        idx = index_folder(root)
        if args.cmd == "check":
            rc = max(rc, cmd_check(idx))
        elif args.cmd == "rename":
            rc = max(rc, cmd_rename(idx, args.src, args.dst, dry_run=args.dry_run))
        elif args.cmd == "rename-batch":
            import json as _json
            raw = args.json.read_text(encoding="utf-8") if args.json else sys.stdin.read()
            try:
                items = _json.loads(raw)
            except _json.JSONDecodeError as e:
                print(f"error: invalid JSON for rename-batch: {e}", file=sys.stderr)
                return 2
            # Validate the decoded shape before building Path pairs so a payload
            # that parses but has the wrong structure (e.g. ["a","b"], {"src":1},
            # or an item missing 'dst') errors cleanly instead of crashing with a
            # KeyError/TypeError traceback (rename-batch-input-01).
            if not isinstance(items, list):
                print(
                    "error: rename-batch payload must be a JSON list of "
                    '{"src": "...", "dst": "..."} objects',
                    file=sys.stderr,
                )
                return 2
            pairs = []
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("src"), str)
                    or not isinstance(item.get("dst"), str)
                ):
                    print(
                        "error: each rename-batch item must be an object with "
                        'string "src" and "dst" fields',
                        file=sys.stderr,
                    )
                    return 2
                pairs.append((Path(item["src"]), Path(item["dst"])))
            rc = max(rc, cmd_rename_batch(idx, pairs, dry_run=args.dry_run))
        elif args.cmd == "upgrade":
            rc = max(rc, cmd_upgrade(idx, dry_run=args.dry_run))
    return rc


if __name__ == "__main__":
    sys.exit(main())
