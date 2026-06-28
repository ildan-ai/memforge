"""memory-validate - the syntax-aware write-boundary gate for MemForge.

The agent-neutral primitive an adapter calls BEFORE accepting or committing a
memory write, so a malformed file is rejected at the write boundary instead of
surfacing later as a memory-audit failure. No adapter reimplements YAML parsing;
they all shell out to (or import) this one operation. See
``docs/adapter-implementation-guide.md`` §"Write-boundary gate" for the per-IDE
wiring (git pre-commit, Claude Code PreToolUse shim, editor-on-save, etc.).

Contract (deliberately a FAST single-file subset of memory-audit, not a clone):

  HARD checks (always exit nonzero on failure -- this is the gate):
    - the frontmatter block parses as a YAML *mapping*. This is the recurring
      unquoted-colon break, promoted to a numbered integrity invariant in
      spec/SPEC.md. An adapter wires `memory-validate <file> || reject`.

  SOFT checks (reported always; only exit nonzero under --strict):
    - MEMORY.md pointer lines within the byte cap + index within the line cap
    - memory files carry the required v0.4 frontmatter fields
    - tier / status values are in their enum

memory-audit remains the full-corpus conformance pass (orphans, supersession
graph, sensitivity gates, commit-log invariants); validate is the latency-bound
single-file pre-write check. The two share the caps + enums via
``memforge.constants`` and the parser via ``memforge.frontmatter`` so they
cannot disagree about what "valid" means.

Read-only. Never mutates a file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from memforge.constants import (
    MEMORY_MD_LINE_CAP,
    POINTER_LINE_BYTE_CAP,
    VALID_STATUSES,
    VALID_TIERS,
)
from memforge.frontmatter import parse, validate_frontmatter


# Required v0.4 frontmatter fields on a memory file (not the MEMORY.md index or
# rollup README.md). Mirrors the set memory-frontmatter-backfill fills in.
REQUIRED_FIELDS = ("uid", "tier", "tags", "owner", "status", "created")

# Same pointer-line shape memory-audit + index-gen key on.
_BULLET_POINTER_RE = re.compile(r"^- \[")

# Files that are NOT memory files and so skip the required-field / enum checks.
_NON_MEMORY_BASENAMES = {"MEMORY.md", "README.md"}


class Finding:
    """One validation result. level is 'error' (hard) or 'warn' (soft)."""

    __slots__ = ("file", "level", "code", "message")

    def __init__(self, file: str, level: str, code: str, message: str) -> None:
        self.file = file
        self.level = level
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"file": self.file, "level": self.level, "code": self.code,
                "message": self.message}


def _is_memory_md(path: Path) -> bool:
    return path.name == "MEMORY.md"


def _is_memory_file(path: Path) -> bool:
    """A memory file carries frontmatter + required fields. The index and rollup
    README.md indexes do not, and anything under archive/ is exempt."""
    if path.name in _NON_MEMORY_BASENAMES:
        return False
    if "archive" in path.parts:
        return False
    return path.suffix == ".md"


def _check_index_caps(path: Path, text: str, findings: list[Finding]) -> None:
    """Soft cap checks that apply only to the MEMORY.md index."""
    line_count = len(text.splitlines())
    if line_count > MEMORY_MD_LINE_CAP:
        findings.append(Finding(
            str(path), "warn", "memory_md_line_cap",
            f"MEMORY.md is {line_count} lines (SHOULD stay <= {MEMORY_MD_LINE_CAP}; "
            "roll detail memories into topic subfolders)",
        ))
    over = 0
    for line in text.splitlines():
        if _BULLET_POINTER_RE.match(line) and len(line.encode("utf-8")) > POINTER_LINE_BYTE_CAP:
            over += 1
    if over:
        findings.append(Finding(
            str(path), "warn", "pointer_byte_cap",
            f"{over} pointer line(s) exceed {POINTER_LINE_BYTE_CAP} bytes "
            "(em-dashes cost 3 bytes each; prefer colons)",
        ))


def _check_required_fields(path: Path, fm: dict[str, Any], findings: list[Finding]) -> None:
    """Soft frontmatter-completeness checks on a memory file."""
    missing = [f for f in REQUIRED_FIELDS if f not in fm or fm[f] in (None, "", [])]
    if missing:
        findings.append(Finding(
            str(path), "warn", "missing_field",
            f"missing required v0.4 field(s): {', '.join(missing)} "
            "(run memory-frontmatter-backfill to fill them)",
        ))
    tier = fm.get("tier")
    if tier is not None and tier not in VALID_TIERS:
        findings.append(Finding(
            str(path), "warn", "bad_tier",
            f"tier '{tier}' is not one of {sorted(VALID_TIERS)}",
        ))
    status = fm.get("status")
    if status is not None and status not in VALID_STATUSES:
        findings.append(Finding(
            str(path), "warn", "bad_status",
            f"status '{status}' is not one of {sorted(VALID_STATUSES)}",
        ))


def validate_file(path: Path) -> list[Finding]:
    """Validate a single file. Returns the findings (possibly empty)."""
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [Finding(str(path), "error", "not_found", "file does not exist")]
    except OSError as e:
        return [Finding(str(path), "error", "read_error", f"could not read file: {e}")]

    # HARD: frontmatter must parse as a mapping (the colon-break gate).
    ok, reason = validate_frontmatter(text)
    if not ok:
        findings.append(Finding(str(path), "error", "frontmatter_parse", reason or "invalid frontmatter"))
        # Frontmatter is unparseable; downstream field/enum checks would be
        # meaningless, so stop here for this file.
        return findings

    # SOFT checks.
    if _is_memory_md(path):
        _check_index_caps(path, text, findings)
    elif _is_memory_file(path):
        fm, _body = parse(text)
        _check_required_fields(path, fm, findings)

    return findings


def _iter_target_files(paths: list[Path], explicit_files: list[Path]) -> list[Path]:
    """Resolve the set of files to validate.

    Explicit FILE positionals win (validate exactly those). Otherwise walk each
    --path dir (or the default memory roots) for *.md, skipping archive/."""
    if explicit_files:
        return [f.expanduser() for f in explicit_files]

    roots = [p.expanduser().resolve() for p in paths]
    if not roots:
        from memforge.paths import default_memory_paths
        roots = default_memory_paths()

    out: list[Path] = []
    for root in roots:
        if root.is_file():
            out.append(root)
            continue
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.md")):
            if "archive" in f.parts:
                continue
            out.append(f)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="memory-validate",
        description=(
            "Syntax-aware write-boundary gate for MemForge memory files. HARD "
            "check (always fails): frontmatter parses as a YAML mapping. SOFT "
            "checks (fail only with --strict): pointer/line caps, required v0.4 "
            "fields, tier/status enums. Read-only; never mutates."
        ),
    )
    p.add_argument("files", nargs="*", type=Path,
                   help="Specific file(s) to validate. Overrides --path/defaults.")
    p.add_argument("--path", action="append", type=Path, default=[],
                   help="Validate every *.md under this dir (repeatable). "
                        "Ignored when FILE positionals are given.")
    p.add_argument("--strict", action="store_true",
                   help="Exit nonzero on SOFT (warn) findings too, not just HARD errors.")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="Emit machine-readable JSON.")
    args = p.parse_args(argv)

    targets = _iter_target_files(args.path, args.files)

    all_findings: list[Finding] = []
    for t in targets:
        all_findings.extend(validate_file(t))

    errors = [f for f in all_findings if f.level == "error"]
    warns = [f for f in all_findings if f.level == "warn"]

    if args.json_out:
        print(json.dumps({
            "files_checked": len(targets),
            "errors": len(errors),
            "warnings": len(warns),
            "findings": [f.as_dict() for f in all_findings],
        }, indent=2))
    else:
        for f in all_findings:
            marker = "ERROR" if f.level == "error" else "warn "
            print(f"  [{marker}] {f.file}: {f.message}", file=sys.stderr)
        if not all_findings:
            print(f"memory-validate: {len(targets)} file(s) OK", file=sys.stderr)
        else:
            print(
                f"memory-validate: {len(targets)} file(s) checked, "
                f"{len(errors)} error(s), {len(warns)} warning(s)",
                file=sys.stderr,
            )

    # Exit nonzero on any HARD error always; on SOFT warnings only with --strict.
    if errors:
        return 1
    if warns and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
