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
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from memforge._terminal import terminal_safe, visible
from memforge.constants import (
    MEMORY_MD_LINE_CAP,
    POINTER_LINE_BYTE_CAP,
    VALID_STATUSES,
    VALID_TIERS,
)
from memforge.discovery import access_error, read_text_no_follow, walk_md_files
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
    # A value that is not text cannot be looked up in an enum or shown as the
    # memory's index line; name the field instead.
    not_text = [
        f for f in ("name", "description", "tier", "status")
        if fm.get(f) is not None and not isinstance(fm[f], str)
    ]
    for f in not_text:
        findings.append(Finding(
            str(path), "warn", "bad_field_type", f"field '{f}' must be text",
        ))
    tier = fm.get("tier")
    if tier is not None and "tier" not in not_text and tier not in VALID_TIERS:
        findings.append(Finding(
            str(path), "warn", "bad_tier",
            f"tier '{tier}' is not one of {sorted(VALID_TIERS)}",
        ))
    status = fm.get("status")
    if status is not None and "status" not in not_text and status not in VALID_STATUSES:
        findings.append(Finding(
            str(path), "warn", "bad_status",
            f"status '{status}' is not one of {sorted(VALID_STATUSES)}",
        ))


def validate_file(path: Path, root: Optional[Path] = None) -> list[Finding]:
    """Validate a single file. Returns the findings (possibly empty).

    Give ``root`` for a file found by walking a folder, so a link there is not
    read through; a file named on the command line is read as named.
    """
    findings: list[Finding] = []
    try:
        if root is None:
            text = path.read_text(encoding="utf-8")
        else:
            text = read_text_no_follow(path, root=root)
    except FileNotFoundError:
        return [Finding(str(path), "error", "not_found", "file does not exist")]
    except (OSError, UnicodeDecodeError) as e:
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


def _iter_target_files(
    paths: list[Path], explicit_files: list[Path]
) -> list[tuple[Path, Optional[Path]]]:
    """Resolve the set of files to validate, each with the folder it was
    found by walking (None for a file named on the command line).

    Explicit FILE positionals win (validate exactly those). Otherwise walk each
    --path dir (or the default memory roots) for *.md, skipping archive/."""
    if explicit_files:
        return [(f.expanduser(), None) for f in explicit_files]

    roots = [p.expanduser().resolve() for p in paths]
    if not roots:
        from memforge.paths import default_memory_paths
        # Only folders: a file at a default folder's path is not a store. One
        # that cannot be looked at is walked, so it is reported.
        roots = [r for r in default_memory_paths() if os.path.isdir(r) or access_error(r)]

    out: list[tuple[Path, Optional[Path]]] = []
    for root in roots:
        if os.path.isfile(root):
            out.append((root, None))
            continue
        if not os.path.isdir(root) and access_error(root) is None:
            continue
        for f in sorted(walk_md_files(root)):
            if "archive" in f.parts:
                continue
            out.append((f, root))
    return out


@terminal_safe
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

    not_a_folder: list[Path] = []
    if not args.files and not args.path:
        from memforge.paths import default_memory_paths
        defaults = default_memory_paths()
        # A default memory folder is a folder. Something else at that path is
        # not a store, and validating it as one memory file would report a
        # pass for a store this run never opened.
        not_a_folder = [d for d in defaults if os.path.exists(d) and not os.path.isdir(d)]
        for d in not_a_folder:
            print(f"memory-validate: {d} is not a folder; not validated.", file=sys.stderr)
        if not any(os.path.isdir(d) or access_error(d) for d in defaults) and not_a_folder:
            return 2 if args.strict else 0
        if not any(os.path.exists(d) or access_error(d) for d in defaults):
            # "0 file(s) OK" would read as a pass for a store this run never
            # opened. A strict gate that checked nothing has not passed.
            looked_for = ", ".join(str(d) for d in defaults)
            print(
                f"memory-validate: nothing validated. No FILE or --path was "
                f"given and none of the default memory folders exists "
                f"({looked_for}). Pass --path <folder> to validate another folder.",
                file=sys.stderr,
            )
            return 2 if args.strict else 0

    # A named --path that is neither a folder nor a regular file (missing, or
    # a pipe or device) was not validated. Same rule as above: say so, print no
    # summary when nothing at all was validated, fail --strict.
    missing = [] if args.files else [
        p for p in args.path
        if not (
            os.path.isdir(p.expanduser())
            or os.path.isfile(p.expanduser())
            or access_error(p.expanduser())
        )
    ]
    for p_missing in missing:
        print(
            f"memory-validate: {p_missing} does not exist or is not a folder or "
            "file; not validated.",
            file=sys.stderr,
        )
    if missing and len(missing) == len(args.path):
        return 2 if args.strict else 0
    missing += not_a_folder

    targets = _iter_target_files(args.path, args.files)

    all_findings: list[Finding] = []
    for t, root in targets:
        all_findings.extend(validate_file(t, root))

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
            print(f"  [{marker}] {visible(f.file)}: {visible(f.message)}", file=sys.stderr)
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
    if missing and args.strict:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
