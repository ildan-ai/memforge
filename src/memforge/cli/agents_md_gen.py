"""agents-md-gen: generate AGENTS.md from MemForge folders.

Reads:
  the memory folders memforge.paths resolves for <cwd> (MEMFORGE_MEMORY_PATH,
  else the Claude Code layout, else ~/.memforge), per-cwd before global
  <cwd>/CLAUDE.md                                     (repo-specific, if present)

Nothing else is read: AGENTS.md is committed, so no other file in the home
folder (such as ~/.claude/CLAUDE.md) is opened or copied into it.

Writes:
  <cwd>/AGENTS.md                                     (cross-tool rules file)

Filters memories by the sensitivity frontmatter field (spec 0.2.0). Default
max-sensitivity is internal, which excludes restricted and privileged memories
entirely (no file content, no index entry, no mention).

Usage:
  agents-md-gen                               # generate in current directory
  agents-md-gen --cwd /path/to/repo           # generate in a specific repo
  agents-md-gen --max-sensitivity restricted  # include restricted memories
  agents-md-gen --dry-run                     # print to stdout, don't write

Exit codes:
  0 success
  2 write error, a refused read or write (CLAUDE.md, AGENTS.md or the backup
    in the repo is a symlink or not a regular file), DLP refusal (rendered
    AGENTS.md carried a BLOCKER or MAJOR secret finding), or usage error
    (argparse exits 2 on bad args)

A pinned body is copied as literal text inside a backtick fence one longer
than the longest backtick run in it, so nothing in it reaches the AGENTS.md
around it.

A memory whose field the generator cannot place (a non-text sensitivity, or a
name or description carrying generator-only text) is left out and named on
stderr; the run still exits 0 for the others.

(agentsmd-mainexit-01: exit code 1 was documented but unreachable; the only
non-success paths return 2, and argparse's own bad-arg exit is also 2.)
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from memforge.cli.index_gen import (
    _ANY_LINE_BREAK,
    fenced_literal,
    index_line_fields,
)
from memforge.discovery import (
    access_error,
    read_bytes_no_follow,
    read_text_no_follow,
    write_text_inside,
)
from memforge.paths import default_memory_paths
from memforge._terminal import terminal_safe, visible

SENSITIVITY_LEVELS = ["public", "internal", "restricted", "privileged"]
SENSITIVITY_RANK = {s: i for i, s in enumerate(SENSITIVITY_LEVELS)}
DEFAULT_MAX_SENSITIVITY = "internal"
MARKER = "<!-- generated-by: agents-md-gen -->"
CEILING_BYTES = 20000


@dataclass
class MemoryEntry:
    path: Path
    name: str
    description: str
    type: str
    sensitivity: str
    body: str
    mtime: float
    # v0.4+ frontmatter `created` (ISO date string) when present. Preferred over
    # filesystem mtime for drop ordering, since mtime is bumped by any edit (the
    # auto-commit hook, a lint reformat) and so is a weak memory-age proxy
    # (agentsmd-mtime-01).
    created: str = ""
    # Frontmatter `pinned: true`: the body carries a rule the IDE agent must see
    # verbatim, so it is inlined instead of listed as a description-only entry.
    pinned: bool = False


def parse_frontmatter(path: Path, root: Path) -> tuple[dict, str]:
    """Parse a memory file's frontmatter + body via the canonical
    memforge.frontmatter.parse (yaml.safe_load).

    AGENTS.md is committed/shared, so the sensitivity classification this tool
    gates on MUST use the SAME parser as audit/recall and cannot drift on
    quoted / flow-style / list YAML values (agentsmd-01). Returns ({}, text)
    when there is no parseable frontmatter.
    """
    from memforge.frontmatter import parse as _mf_parse

    text = read_text_no_follow(path, root=root)
    return _mf_parse(text)


def load_memory_folder(folder: Path) -> list[MemoryEntry]:
    """The memories in ``folder``; none when it is absent or not a folder.

    Raises OSError for a folder that is there but cannot be looked at or
    listed: AGENTS.md would otherwise leave out its memories without a word.
    """
    err = access_error(folder)
    if err is not None:
        raise err
    if not os.path.isdir(folder):
        return []
    entries: list[MemoryEntry] = []
    for p in folder.glob("*.md"):
        if p.name == "MEMORY.md":
            continue
        try:
            fm, body = parse_frontmatter(p, folder)
            if not fm:
                continue
            # The same one-line cleaning MEMORY.md applies: a line break in a
            # name or description must not start a line of its own in AGENTS.md.
            name, description, _, _ = index_line_fields(fm, p.stem, p.name)
            # Only a YAML boolean pins: the string "true" or a 1 is not a
            # deliberate pin and must not pull a body into a committed file.
            pinned = fm.get("pinned") is True
            # A reader splitting lines with `str.splitlines` must see the
            # lines a Markdown reader sees, so every line break it honours
            # is one here.
            body = _ANY_LINE_BREAK.sub("\n", body).strip()
            ftype = fm.get("type")
            entries.append(MemoryEntry(
                path=p,
                name=name,
                description=description,
                type=ftype if isinstance(ftype, str) else "",
                sensitivity=_sensitivity(fm.get("sensitivity"), p),
                body=body,
                mtime=p.stat().st_mtime,
                created=str(fm.get("created") or fm.get("updated") or ""),
                pinned=pinned,
            ))
        except Exception as e:
            print(f"warning: skipping {visible(p)}: {visible(e)}; it is left out of AGENTS.md", file=sys.stderr)
    return entries


# Ranks above every ceiling, so a memory carrying it is never included.
_UNRANKED_SENSITIVITY = ""


def _sensitivity(value: object, path: Path) -> str:
    """The sensitivity a memory is filtered by; absent or empty means internal.

    A value that is not text names no level, so it cannot be placed under any
    ceiling: the memory is left out and the file named, as memory-audit
    reports it.
    """
    if value is None or value == "":
        return "internal"
    if isinstance(value, str):
        return value
    print(
        f"warning: {visible(path)}: field 'sensitivity' must be text; it is left out of AGENTS.md",
        file=sys.stderr,
    )
    return _UNRANKED_SENSITIVITY


def filter_by_sensitivity(entries: list[MemoryEntry], max_level: str) -> list[MemoryEntry]:
    threshold = SENSITIVITY_RANK[max_level]
    return [e for e in entries if SENSITIVITY_RANK.get(e.sensitivity, 99) <= threshold]


def render_critical_rules(
    entries: list[MemoryEntry], inline_above_public: bool = False
) -> str:
    """Inline full bodies for memories pinned in their frontmatter.

    These are rules the IDE agent MUST see verbatim, not just as index entries.

    AGENTS.md is a committed/shared surface (read by five external tools), so the
    inline path defaults to PUBLIC content only: a body above `public` sensitivity
    is inlined ONLY with the explicit ``inline_above_public`` opt-in, mirroring the
    dedup/lint local-only-by-default posture (agentsmd-01). Absent sensitivity ==
    ``internal`` (the safe default), so by default nothing above public is inlined
    verbatim into the committed file.
    """
    criticals = [e for e in entries if e.pinned]
    if not inline_above_public:
        criticals = [
            e for e in criticals
            if SENSITIVITY_RANK.get(e.sensitivity, 99) <= SENSITIVITY_RANK["public"]
        ]
    if not criticals:
        return ""
    criticals.sort(key=lambda e: e.path.name)
    lines = ["## Critical rules (inlined from memory)", ""]
    for e in criticals:
        lines.append(f"### {e.name}")
        lines.append("")
        if e.description:
            lines.append(f"_{e.description}_")
            lines.append("")
        # Literal text: nothing in a body can reach the sections around it.
        body = e.body.strip()
        if body:
            lines.append(fenced_literal(body))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_memory_index(entries: list[MemoryEntry], heading: str) -> str:
    """Render memories as a description-only index. Pinned ones are skipped
    here because they're already inlined via render_critical_rules."""
    non_critical = [e for e in entries if not e.pinned]
    if not non_critical:
        return ""
    lines = [f"## {heading}", ""]
    by_type: dict[str, list[MemoryEntry]] = {}
    for e in non_critical:
        by_type.setdefault(e.type or "other", []).append(e)
    type_order = ["feedback", "user", "project", "reference", "other"]
    for t in type_order:
        items = by_type.get(t, [])
        items.sort(key=lambda e: e.path.name)
        if not items:
            continue
        lines.append(f"### {t}")
        lines.append("")
        for e in items:
            desc = e.description or "(no description)"
            lines.append(f"- **{e.name}**: {desc}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def summarize_filter(all_entries: list[MemoryEntry], kept: list[MemoryEntry], max_level: str) -> str:
    total = len(all_entries)
    excluded = total - len(kept)
    by_sens: dict[str, int] = {s: 0 for s in SENSITIVITY_LEVELS}
    for e in all_entries:
        by_sens[e.sensitivity] = by_sens.get(e.sensitivity, 0) + 1
    return (
        f"{len(kept)} included, {excluded} filtered above {max_level}. "
        f"Counts: public={by_sens['public']} internal={by_sens['internal']} "
        f"restricted={by_sens['restricted']} privileged={by_sens['privileged']}"
    )


def enforce_ceiling(
    fixed_content: str,
    per_cwd_section: str,
    global_section: str,
    per_cwd_entries: list[MemoryEntry],
    global_entries: list[MemoryEntry],
    ceiling: int,
    global_heading: str = "Global memory",
) -> tuple[str, str, list[str]]:
    """With description-only rendering, per-memory cost is tiny so the ceiling
    rarely triggers. If it does, drop reference entries first (lowest signal),
    then project, then feedback (oldest first within each type).

    "Oldest" prefers the frontmatter `created` date (required since v0.4) over
    filesystem mtime, because mtime is bumped by any edit (auto-commit hook,
    lint reformat) and so does not track memory age (agentsmd-mtime-01). Files
    with a `created` date sort before files without one (which fall back to
    mtime and are treated as newest within their type)."""
    dropped: list[str] = []

    def _age_key(e: MemoryEntry) -> tuple:
        # created present -> sort by it (ISO strings sort chronologically), and
        # rank ahead of created-absent entries; created absent -> fall back to
        # mtime, ranked after all dated entries.
        if e.created:
            return (0, e.created, e.mtime)
        return (1, "", e.mtime)

    def total_size() -> int:
        return sum(len(s.encode("utf-8")) for s in (fixed_content, per_cwd_section, global_section))

    if total_size() <= ceiling:
        return per_cwd_section, global_section, dropped

    drop_order = ["reference", "project", "feedback"]
    for drop_type in drop_order:
        # Drop from per-cwd first, then global, oldest within each.
        candidates = [(per_cwd_entries, "per-cwd"), (global_entries, "global")]
        for entries, label in candidates:
            victims = sorted([e for e in entries if e.type == drop_type], key=_age_key)
            while victims and total_size() > ceiling:
                v = victims.pop(0)
                entries.remove(v)
                if label == "per-cwd":
                    per_cwd_section = render_memory_index(entries, "Per-project memory")
                else:
                    global_section = render_memory_index(entries, global_heading)
                dropped.append(f"{label} {drop_type}: {v.name}")
            if total_size() <= ceiling:
                return per_cwd_section, global_section, dropped

    return per_cwd_section, global_section, dropped


def render_agents_md(
    repo_claude_md: str,
    per_cwd_entries: list[MemoryEntry],
    global_entries: list[MemoryEntry],
    source_lines: list[str],
    max_sensitivity: str,
    ceiling: int,
    inline_above_public: bool = False,
    global_heading: str = "Global memory",
) -> str:
    combined_critical = render_critical_rules(
        per_cwd_entries + global_entries, inline_above_public=inline_above_public
    )
    per_cwd_section = render_memory_index(per_cwd_entries, "Per-project memory")
    global_section = render_memory_index(global_entries, global_heading)

    # Copy lists so enforce_ceiling can mutate locally.
    per_cwd_section, global_section, dropped = enforce_ceiling(
        combined_critical, per_cwd_section, global_section,
        list(per_cwd_entries), list(global_entries), ceiling, global_heading,
    )

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        "# AGENTS.md",
        "",
        MARKER,
        f"Generated: {timestamp}. Max sensitivity: {max_sensitivity}.",
        "",
        "> Cross-tool rules file read by Cursor, Antigravity, Claude Code, Copilot, Continue, Windsurf.",
        "> Regenerate after memory updates via: `agents-md-gen`",
        "",
        "## Source",
        "",
        *source_lines,
    ]
    if repo_claude_md:
        parts.append(f"- `<repo>/CLAUDE.md`: repo-specific instructions included")
    if dropped:
        parts.append("")
        parts.append(f"Dropped for ceiling ({ceiling} bytes): {len(dropped)} entries.")
    parts.append("")

    if combined_critical.strip():
        parts.append(combined_critical.strip())
        parts.append("")

    if global_section.strip():
        parts.append(global_section.strip())
        parts.append("")

    if per_cwd_section.strip():
        parts.append(per_cwd_section.strip())
        parts.append("")

    if repo_claude_md.strip():
        parts.append("# Repo-specific (from <repo>/CLAUDE.md)")
        parts.append("")
        parts.append(repo_claude_md.strip())
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def split_store_folders(folders: list[Path], home: Path) -> tuple[list[Path], list[Path], bool]:
    """Split the resolved store folders into (per-cwd, global, layout-defined).

    The Claude Code layout and the ~/.memforge default each end with a
    `global-memory` folder in the home, after at most one per-cwd `memory`
    folder; only those layouts say which folder is per-cwd. Any other list (a
    MEMFORGE_MEMORY_PATH the operator set) is used as given, every folder
    read in order, with no per-cwd versus global distinction.
    """
    global_folders = (home / ".claude" / "global-memory", home / ".memforge" / "global-memory")
    if (
        folders
        and len(folders) <= 2
        and folders[-1] in global_folders
        and all(p.name == "memory" for p in folders[:-1])
    ):
        return folders[:-1], folders[-1:], True
    return [], list(folders), False


def folder_label(folder: Path, home: Path) -> str:
    """How AGENTS.md names a store folder: home-relative, else by its last
    name only, so the committed file does not carry a machine path."""
    try:
        return f"~/{folder.relative_to(home).as_posix()}/"
    except ValueError:
        return f".../{folder.name}/"


def scan_rendered_for_secrets(rendered: str) -> list[str]:
    """Run the rendered AGENTS.md through the DLP secret/sensitivity scan and
    return a list of secret finding strings. Empty list == clean.

    AGENTS.md is committed and shared with five external tools, so before it is
    written to disk it MUST pass the same secret pre-scan the rest of the package
    ships for exactly this purpose (agentsmd-01). Every BLOCKER refuses the write,
    and so does every MAJOR except the kinds in _NOT_A_SECRET: a JWT or a
    password assignment is MAJOR in a memory only because the label cross-check
    adds the BLOCKER there, and the rendered file has no label to check.

    Fail CLOSED (agentsmd-dlp-failopen-01): if the DLP scanner cannot be imported
    or raises while scanning, this is the gate that protects a committed/shared
    surface, so an unavailable scanner is treated as a BLOCKER (refuse the write)
    rather than silently returning a clean result. A scanner that cannot run is
    indistinguishable from a scanner that found nothing only if you fail open,
    which defeats the gate.
    """
    try:
        from memforge.cli.dlp_scan import scan_text
    except Exception as e:
        return [f"DLP scanner unavailable (import failed: {e}) -- refusing to write"]
    try:
        findings = scan_text(rendered, Path("AGENTS.md"))
    except Exception as e:
        return [f"DLP scanner failed during scan ({e}) -- refusing to write"]
    return [
        f"line {f.line_no} [{f.pattern}]: {f.excerpt}"
        for f in findings
        if f.severity == "BLOCKER"
        or (f.severity == "MAJOR" and f.pattern not in _NOT_A_SECRET)
    ]


# MAJOR findings that are not a credential: an ARN names a resource and is
# legitimate in a restricted memory written under --max-sensitivity restricted;
# the others describe the scanned file, not its text.
_NOT_A_SECRET = frozenset({"aws_arn", "invalid_utf8", "nul_bytes", "symlink_not_scanned"})


def read_repo_file(path: Path, repo: Path) -> str | None:
    """Text of a file in the working repo, or None when it does not exist.

    The repo may be a clone whose CLAUDE.md or AGENTS.md is a symlink to a file
    outside it, so the read never follows a link (LinkReadRefused).
    """
    try:
        data = read_bytes_no_follow(path, root=repo)
    except FileNotFoundError:
        return None
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace").read()


def back_up_if_foreign(target: Path, repo: Path) -> Path | None:
    content = read_repo_file(target, repo)
    if content is None or MARKER in content:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.stem}.bak-{stamp}{target.suffix}")
    write_text_inside(repo, backup, content)
    return backup


@terminal_safe
def main():
    parser = argparse.ArgumentParser(
        description="Generate <cwd>/AGENTS.md from the memory folders memforge resolves "
                    "for <cwd> and from <cwd>/CLAUDE.md. Nothing else is read: no other "
                    "file in the home folder, such as ~/.claude/CLAUDE.md, is opened.",
    )
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--max-sensitivity", choices=SENSITIVITY_LEVELS, default=DEFAULT_MAX_SENSITIVITY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ceiling-bytes", type=int, default=CEILING_BYTES)
    parser.add_argument(
        "--inline-above-public", action="store_true",
        help="Inline full bodies of pinned memories above 'public' sensitivity "
             "into the committed AGENTS.md. OFF by default: AGENTS.md is shared "
             "with external tools, so above-public inline content requires this "
             "explicit opt-in (mirrors dedup/lint local-only-by-default posture).",
    )
    args = parser.parse_args()

    # The store is the one for the repo AGENTS.md is written into, not the one
    # for the folder the command runs in: AGENTS.md is committed in that repo.
    repo = args.cwd.resolve()
    home = Path.home()
    per_cwd_folders, global_folders, layout_defined = split_store_folders(
        default_memory_paths(repo), home
    )
    repo_claude_md_path = args.cwd / "CLAUDE.md"
    target = args.cwd / "AGENTS.md"

    try:
        repo_claude_md = read_repo_file(repo_claude_md_path, args.cwd) or ""
    except OSError as e:
        print(f"error: could not read {visible(repo_claude_md_path)}: {visible(e)}", file=sys.stderr)
        return 2

    try:
        per_cwd_all = [e for f in per_cwd_folders for e in load_memory_folder(f)]
        global_loaded = [(f, load_memory_folder(f)) for f in global_folders]
    except OSError as e:
        print(f"error: could not read the memory folder {visible(e.filename)}: {visible(e)}; "
              f"{visible(target)} not written", file=sys.stderr)
        return 2
    per_cwd_kept = filter_by_sensitivity(per_cwd_all, args.max_sensitivity)
    per_cwd_summary = summarize_filter(per_cwd_all, per_cwd_kept, args.max_sensitivity)

    global_kept: list[MemoryEntry] = []
    source_lines: list[str] = []
    summaries: list[tuple[str, str]] = []
    for folder, folder_all in global_loaded:
        if not folder_all and not layout_defined:
            print(
                f"warning: {visible(folder)}: no memories found in this MEMFORGE_MEMORY_PATH folder",
                file=sys.stderr,
            )
        folder_kept = filter_by_sensitivity(folder_all, args.max_sensitivity)
        global_kept.extend(folder_kept)
        summary = summarize_filter(folder_all, folder_kept, args.max_sensitivity)
        source_lines.append(f"- `{folder_label(folder, home)}`: {summary}")
        summaries.append(("global" if layout_defined else folder_label(folder, home), summary))
    if layout_defined:
        source_lines.append(f"- per-cwd memory: {per_cwd_summary}")
        summaries.append(("per-cwd", per_cwd_summary))

    rendered = render_agents_md(
        repo_claude_md=repo_claude_md,
        per_cwd_entries=per_cwd_kept,
        global_entries=global_kept,
        source_lines=source_lines,
        max_sensitivity=args.max_sensitivity,
        ceiling=args.ceiling_bytes,
        inline_above_public=args.inline_above_public,
        global_heading="Global memory" if layout_defined else "Memory",
    )

    # DLP pre-scan: AGENTS.md is committed and shared with external tools, so it
    # MUST pass a secret scan before any write (agentsmd-01).
    secret_findings = scan_rendered_for_secrets(rendered)
    if secret_findings:
        print(
            f"error: refusing to write {visible(target)}: DLP scan found "
            f"{len(secret_findings)} secret finding(s) in the rendered AGENTS.md "
            "(AGENTS.md is committed/shared; it must not carry secrets):",
            file=sys.stderr,
        )
        for f in secret_findings:
            print(f"  - {visible(f)}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(rendered)
        return 0

    try:
        backup = back_up_if_foreign(target, args.cwd)
        write_text_inside(args.cwd, target, rendered)
    except Exception as e:
        print(f"error: could not write {visible(target)}: {visible(e)}", file=sys.stderr)
        return 2

    size = len(rendered.encode("utf-8"))
    print(f"wrote {visible(target)} ({size} bytes, ceiling {args.ceiling_bytes})")
    for label, summary in summaries:
        print(f"  {visible(label)}: {visible(summary)}")
    if backup:
        print(f"  foreign AGENTS.md detected; backed up to {visible(backup)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
