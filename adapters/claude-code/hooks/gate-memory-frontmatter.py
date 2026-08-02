#!/usr/bin/env python3
"""PreToolUse write-boundary gate: reject a malformed memory write.

Claude Code is the one adapter that can reject a bad memory write BEFORE the
bytes hit disk (Tier A in the adapter guide). This is the shim the CC adapter
README has specified; it reconstructs the post-write content and pipes it to
the installed `memforge.frontmatter` primitives.

Why it exists, concretely: an agent harness may write frontmatter of the shape

    metadata:
      type: feedback

which is NOT the spec's top-level `type:` key. That block parses as valid YAML,
so a parse-only gate passes it, and no backfill can invent a semantic type, so
the files accumulate unnoticed until an audit run finds them. The only thing
that ever detected them was `memory-audit`, whose findings land in a log. This
gate moves the detection to the write itself.

Two checks, in order:
  1. `validate_frontmatter`  -- does the block parse (invariant 27).
  2. `validate_required_fields` -- are the NON-DERIVABLE fields present. That
     set is deliberately narrow (name, description, type). Everything else the
     spec requires (uid, tier, tags, owner, status, created) is derivable later
     by `memory-frontmatter-backfill`, and denying a write for a field a tool
     will fill in would be noise. A gate people switch off protects nothing.

FAIL-OPEN is mandatory and unconditional: any unexpected error, an
un-importable package, an unreadable file, a surprising payload shape, all
return 0 and allow the write. A memory gate that wedges the editor is worse
than the drift it prevents.

Scope: `Write` and `Edit` on `*.md` under a memory root only. `MEMORY.md` is
skipped by spec (it is the folder index, not a memory, and carries no
frontmatter); its own pointer-length rule is a separate gate's job.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Upper bound on a file this gate will read and validate. Memory files are
#: prose with a small frontmatter block; a few megabytes is already far past
#: anything legitimate.
MAX_VALIDATED_BYTES = 4 * 1024 * 1024


def _memory_roots() -> list[Path]:
    """Memory roots. An explicit env override WINS over the package default.

    Override-first, not fallback-only: an operator with a non-standard layout,
    and any test harness, needs a way to say where the roots are that does not
    depend on the package returning nothing. Fallback-only ordering silently
    ignores the override on every machine where the package resolves, which
    makes the gate untestable and surprises the operator who set the variable.
    """
    env = os.environ.get("MEMFORGE_MEMORY_ROOTS", "")
    roots = [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    if roots:
        # Announce the override. Precedence is right, silence is not: an
        # operator who sets this once for a non-standard layout and forgets
        # would otherwise have a permanently disarmed gate reporting nothing,
        # which is the failure mode this gate exists to end. Panel finding
        # 2026-08-02 (threat-modeler). stderr only; it never changes the
        # allow/deny decision.
        print(
            "gate-memory-frontmatter: memory roots overridden by "
            f"MEMFORGE_MEMORY_ROOTS ({len(roots)} path(s)); the package "
            "default is NOT in effect",
            file=sys.stderr,
        )
        return roots
    try:
        from memforge.paths import default_memory_paths  # type: ignore

        return [Path(p) for p in default_memory_paths()]
    except Exception:
        return []


def _under_memory_root(file_path: str) -> bool:
    if not file_path.endswith(".md"):
        return False
    if os.path.basename(file_path) == "MEMORY.md":
        return False  # folder index, not a memory; no frontmatter by spec
    try:
        target = Path(file_path).expanduser().resolve()
    except Exception:
        return False
    for root in _memory_roots():
        try:
            target.relative_to(root.expanduser().resolve())
            return True
        except Exception:
            continue
    return False


def _post_write_text(tool: str, tool_input: dict) -> str | None:
    """Reconstruct what the file WILL contain if this call is allowed."""
    if tool == "Write":
        return tool_input.get("content") or ""
    # Edit: apply the substitution to the file as it stands. Validating
    # new_string alone would be wrong twice over: an edit to the body cannot
    # break frontmatter, and an edit that DOES break it usually does so in
    # combination with the text already on disk.
    path = tool_input.get("file_path") or ""
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if old is None or new is None:
        return None
    try:
        target = Path(path).expanduser()
        # Bound the read. A memory file is prose; anything past this is not one,
        # and a hook that sits in front of every write must not be turned into a
        # memory-exhaustion vector by one pathological file. Over the cap we
        # decline to judge (return None -> allow) rather than read it: the gate
        # is a correctness guardrail, not a security boundary, and refusing to
        # read is the conservative move on both axes. Panel finding 2026-08-02.
        if target.stat().st_size > MAX_VALIDATED_BYTES:
            return None
        current = target.read_text(encoding="utf-8")
    except Exception:
        return None
    if old not in current:
        return None  # let the Edit tool report its own mismatch
    if tool_input.get("replace_all"):
        return current.replace(old, new)
    return current.replace(old, new, 1)


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0  # fail open

    try:
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        file_path = tool_input.get("file_path") or ""

        if tool not in ("Write", "Edit"):
            return 0
        if not _under_memory_root(file_path):
            return 0

        text = _post_write_text(tool, tool_input)
        if text is None:
            return 0

        from memforge.frontmatter import (  # noqa: WPS433
            validate_frontmatter,
            validate_required_fields,
        )

        ok, why = validate_frontmatter(text)
        if ok:
            ok, why = validate_required_fields(text)
        if ok:
            return 0

        msg = (
            f"gate-memory-frontmatter: {file_path}\n{why}\n\n"
            "Fix the frontmatter in this same write and retry. Nothing was "
            "written. If the memory is genuinely not a MemForge memory, it "
            "does not belong under a memory root."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg,
            }
        }))
        return 0
    except Exception:
        return 0  # fail open, unconditionally


if __name__ == "__main__":
    sys.exit(main())
