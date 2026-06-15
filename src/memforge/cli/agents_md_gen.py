"""agents-md-gen — generate AGENTS.md from MemForge folders.

Reads:
  ~/.claude/CLAUDE.md                                 (voice + rule sections only)
  ~/.claude/global-memory/                            (global memory folder)
  ~/.claude/projects/<user>-claude-projects/memory/   (per-cwd memory folder)
  <cwd>/CLAUDE.md                                     (repo-specific, if present)

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
  2 write error, or DLP refusal (rendered AGENTS.md carried a secret), or
    usage error (argparse exits 2 on bad args)

(agentsmd-mainexit-01: exit code 1 was documented but unreachable; the only
non-success paths return 2, and argparse's own bad-arg exit is also 2.)
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SENSITIVITY_LEVELS = ["public", "internal", "restricted", "privileged"]
SENSITIVITY_RANK = {s: i for i, s in enumerate(SENSITIVITY_LEVELS)}
DEFAULT_MAX_SENSITIVITY = "internal"
MARKER = "<!-- generated-by: agents-md-gen -->"
CEILING_BYTES = 20000

CLAUDE_MD_INCLUDE_HEADINGS = [
    "## Writing voice",
    "## No fabrication",
    "## One question at a time",
    "## Shell",
]

# Memory filenames whose bodies are inlined verbatim in AGENTS.md because
# they carry load-bearing rules the IDE agent MUST see, not just index.
# Other memories appear as description-only index entries.
CRITICAL_MEMORY_FILENAMES = {
    "feedback_employer_vs_ildan.md",
    "feedback_ildan_firm_vs_founder.md",
    "feedback_cipm_iapp_format.md",
    "feedback_no_keys_in_files.md",
    "feedback_precommit_secrets_scan.md",
    "feedback_no_fabrication.md",
    "assistant_name.md",
    "tone_personality.md",
}


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


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Parse a memory file's frontmatter + body via the canonical
    memforge.frontmatter.parse (yaml.safe_load).

    AGENTS.md is committed/shared, so the sensitivity classification this tool
    gates on MUST use the SAME parser as audit/recall and cannot drift on
    quoted / flow-style / list YAML values (agentsmd-01). Returns ({}, text)
    when there is no parseable frontmatter.
    """
    from memforge.frontmatter import parse as _mf_parse

    text = path.read_text(encoding="utf-8", errors="replace")
    return _mf_parse(text)


def load_memory_folder(folder: Path) -> list[MemoryEntry]:
    if not folder.is_dir():
        return []
    entries: list[MemoryEntry] = []
    for p in folder.glob("*.md"):
        if p.name == "MEMORY.md":
            continue
        try:
            fm, body = parse_frontmatter(p)
            if not fm:
                continue
            entries.append(MemoryEntry(
                path=p,
                name=fm.get("name", p.stem),
                description=fm.get("description", ""),
                type=fm.get("type", ""),
                sensitivity=fm.get("sensitivity", "internal") or "internal",
                body=body,
                mtime=p.stat().st_mtime,
                created=str(fm.get("created") or fm.get("updated") or ""),
            ))
        except Exception as e:
            print(f"warning: skipping {p}: {e}", file=sys.stderr)
    return entries


def filter_by_sensitivity(entries: list[MemoryEntry], max_level: str) -> list[MemoryEntry]:
    threshold = SENSITIVITY_RANK[max_level]
    return [e for e in entries if SENSITIVITY_RANK.get(e.sensitivity, 99) <= threshold]


def extract_claude_md_sections(path: Path, included_headings: list[str]) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    out: list[str] = []
    including = False
    current_heading_level = 0

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+?)$", line)
        if heading_match:
            hashes, title = heading_match.groups()
            level = len(hashes)
            full = f"{hashes} {title}"
            if any(full.startswith(h) for h in included_headings):
                including = True
                current_heading_level = level
                out.append(line)
                continue
            if including and level <= current_heading_level:
                including = False
        if including:
            out.append(line)

    return "\n".join(out).strip() + "\n"


def render_critical_rules(
    entries: list[MemoryEntry], inline_above_public: bool = False
) -> str:
    """Inline full bodies for memories whose filename is in CRITICAL_MEMORY_FILENAMES.

    These are rules the IDE agent MUST see verbatim, not just as index entries.

    AGENTS.md is a committed/shared surface (read by five external tools), so the
    inline path defaults to PUBLIC content only: a body above `public` sensitivity
    is inlined ONLY with the explicit ``inline_above_public`` opt-in, mirroring the
    dedup/lint local-only-by-default posture (agentsmd-01). Absent sensitivity ==
    ``internal`` (the safe default), so by default nothing above public is inlined
    verbatim into the committed file.
    """
    criticals = [e for e in entries if e.path.name in CRITICAL_MEMORY_FILENAMES]
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
        body = e.body.strip()
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_memory_index(entries: list[MemoryEntry], heading: str) -> str:
    """Render memories as a description-only index. Critical ones are skipped
    here because they're already inlined via render_critical_rules."""
    non_critical = [e for e in entries if e.path.name not in CRITICAL_MEMORY_FILENAMES]
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
            lines.append(f"- **{e.name}** — {desc}")
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
                    global_section = render_memory_index(entries, "Global memory")
                dropped.append(f"{label} {drop_type}: {v.name}")
            if total_size() <= ceiling:
                return per_cwd_section, global_section, dropped

    return per_cwd_section, global_section, dropped


def render_agents_md(
    claude_md_content: str,
    repo_claude_md: str,
    per_cwd_entries: list[MemoryEntry],
    global_entries: list[MemoryEntry],
    per_cwd_summary: str,
    global_summary: str,
    max_sensitivity: str,
    ceiling: int,
    inline_above_public: bool = False,
) -> str:
    combined_critical = render_critical_rules(
        per_cwd_entries + global_entries, inline_above_public=inline_above_public
    )
    per_cwd_section = render_memory_index(per_cwd_entries, "Per-project memory")
    global_section = render_memory_index(global_entries, "Global memory")

    fixed_content = claude_md_content + combined_critical
    # Copy lists so enforce_ceiling can mutate locally.
    per_cwd_section, global_section, dropped = enforce_ceiling(
        fixed_content, per_cwd_section, global_section,
        list(per_cwd_entries), list(global_entries), ceiling,
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
        f"- `~/.claude/CLAUDE.md` — voice + rule sections",
        f"- `~/.claude/global-memory/` — {global_summary}",
        f"- per-cwd memory — {per_cwd_summary}",
    ]
    if repo_claude_md:
        parts.append(f"- `<repo>/CLAUDE.md` — repo-specific instructions included")
    if dropped:
        parts.append("")
        parts.append(f"Dropped for ceiling ({ceiling} bytes): {len(dropped)} entries.")
    parts.append("")

    if claude_md_content.strip():
        parts.append("# Voice and rules (from ~/.claude/CLAUDE.md)")
        parts.append("")
        parts.append(claude_md_content.strip())
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


def find_per_cwd_memory_folder() -> Path | None:
    """Locate the per-cwd MEMORY folder via the centralized path resolver.

    Routes through memforge.paths.default_memory_paths so the MEMORY-folder
    discovery is IDE/OS-neutral. The ~/.claude/CLAUDE.md read in main() stays
    intentionally Claude-Code-specific (that file is this tool's voice source);
    only the MEMORY-folder lookup is generalized.
    """
    from memforge.paths import default_memory_paths

    for p in default_memory_paths():
        # The per-cwd folder is conventionally the one named "memory"; the
        # global folder ("global-memory") is read separately in main().
        if p.name == "memory" and p.is_dir():
            return p
    return None


def scan_rendered_for_secrets(rendered: str) -> list[str]:
    """Run the rendered AGENTS.md through the DLP secret/sensitivity scan and
    return a list of BLOCKER finding strings. Empty list == clean.

    AGENTS.md is committed and shared with five external tools, so before it is
    written to disk it MUST pass the same secret pre-scan the rest of the package
    ships for exactly this purpose. Any BLOCKER finding refuses the write
    (agentsmd-01).

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
    ]


def back_up_if_foreign(target: Path) -> Path | None:
    if not target.exists():
        return None
    content = target.read_text(encoding="utf-8", errors="replace")
    if MARKER in content:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.stem}.bak-{stamp}{target.suffix}")
    backup.write_text(content)
    return backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--max-sensitivity", choices=SENSITIVITY_LEVELS, default=DEFAULT_MAX_SENSITIVITY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ceiling-bytes", type=int, default=CEILING_BYTES)
    parser.add_argument(
        "--inline-above-public", action="store_true",
        help="Inline full bodies of critical memories above 'public' sensitivity "
             "into the committed AGENTS.md. OFF by default: AGENTS.md is shared "
             "with external tools, so above-public inline content requires this "
             "explicit opt-in (mirrors dedup/lint local-only-by-default posture).",
    )
    args = parser.parse_args()

    claude_md_path = Path.home() / ".claude" / "CLAUDE.md"
    per_cwd_folder = find_per_cwd_memory_folder()
    # Global MEMORY folder discovered via the centralized resolver (IDE/OS-
    # neutral); the ~/.claude/CLAUDE.md voice read above stays Claude-specific.
    from memforge.paths import default_memory_paths

    _global_candidates = [p for p in default_memory_paths() if p.name == "global-memory"]
    global_memory_folder = (
        _global_candidates[0]
        if _global_candidates
        else Path.home() / ".claude" / "global-memory"
    )
    repo_claude_md_path = args.cwd / "CLAUDE.md"
    target = args.cwd / "AGENTS.md"

    claude_md_content = extract_claude_md_sections(claude_md_path, CLAUDE_MD_INCLUDE_HEADINGS)
    repo_claude_md = repo_claude_md_path.read_text(encoding="utf-8", errors="replace") if repo_claude_md_path.exists() else ""

    global_all = load_memory_folder(global_memory_folder)
    per_cwd_all = load_memory_folder(per_cwd_folder) if per_cwd_folder else []

    global_kept = filter_by_sensitivity(global_all, args.max_sensitivity)
    per_cwd_kept = filter_by_sensitivity(per_cwd_all, args.max_sensitivity)

    global_summary = summarize_filter(global_all, global_kept, args.max_sensitivity)
    per_cwd_summary = summarize_filter(per_cwd_all, per_cwd_kept, args.max_sensitivity)

    rendered = render_agents_md(
        claude_md_content=claude_md_content,
        repo_claude_md=repo_claude_md,
        per_cwd_entries=per_cwd_kept,
        global_entries=global_kept,
        per_cwd_summary=per_cwd_summary,
        global_summary=global_summary,
        max_sensitivity=args.max_sensitivity,
        ceiling=args.ceiling_bytes,
        inline_above_public=args.inline_above_public,
    )

    # DLP pre-scan: AGENTS.md is committed and shared with external tools, so it
    # MUST pass a secret scan before any write (agentsmd-01). Refuse on BLOCKER.
    secret_findings = scan_rendered_for_secrets(rendered)
    if secret_findings:
        print(
            f"error: refusing to write {target}: DLP scan found "
            f"{len(secret_findings)} BLOCKER finding(s) in the rendered AGENTS.md "
            "(AGENTS.md is committed/shared; it must not carry secrets):",
            file=sys.stderr,
        )
        for f in secret_findings:
            print(f"  - {f}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(rendered)
        return 0

    try:
        backup = back_up_if_foreign(target)
        target.write_text(rendered, encoding="utf-8")
    except Exception as e:
        print(f"error: could not write {target}: {e}", file=sys.stderr)
        return 2

    size = len(rendered.encode("utf-8"))
    print(f"wrote {target} ({size} bytes, ceiling {args.ceiling_bytes})")
    print(f"  global: {global_summary}")
    print(f"  per-cwd: {per_cwd_summary}")
    if backup:
        print(f"  foreign AGENTS.md detected; backed up to {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
