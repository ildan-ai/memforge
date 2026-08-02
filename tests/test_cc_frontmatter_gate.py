"""Tests for the Claude Code PreToolUse write-boundary gate.

The gate is FAIL-OPEN by construction: any unexpected error allows the write.
That is the correct posture for something sitting in front of every editor
write, and it is also why it needs tests more than a fail-closed gate would.
A fail-closed gate announces its own breakage on the next call; a fail-open one
goes silently permissive and nothing reports it. Untested, that is the same
"green and dark" shape the gate exists to prevent.

Exercised by subprocess against the real script, because the contract under
test is the hook wire protocol (a JSON payload on stdin, an optional
permissionDecision JSON on stdout, always exit 0), not any internal function.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "adapters" / "claude-code" / "hooks" / "gate-memory-frontmatter.py"

NESTED_ONLY = (
    "---\nname: x\ndescription: y\nmetadata:\n  type: feedback\n---\n\nbody\n"
)
TOP_LEVEL = "---\nname: x\ndescription: y\ntype: feedback\n---\n\nbody\n"


pytestmark = pytest.mark.skipif(
    not GATE.exists(), reason="CC adapter gate not present in this checkout"
)


def _run(payload: dict, roots: Path) -> dict | None:
    """Invoke the gate; return the parsed hook output, or None when allowed."""
    env = dict(os.environ)
    env["MEMFORGE_MEMORY_ROOTS"] = str(roots)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, "the gate must ALWAYS exit 0"
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def _decision(result: dict | None) -> str:
    return "allow" if result is None else result["hookSpecificOutput"]["permissionDecision"]


def test_denies_nested_metadata_type(tmp_path):
    """The regression this gate exists for: `metadata.type` parses fine but is
    not the spec's top-level `type`, so a parse-only gate lets it through."""
    result = _run(
        {"tool_name": "Write",
         "tool_input": {"file_path": str(tmp_path / "m.md"), "content": NESTED_ONLY}},
        tmp_path,
    )
    assert _decision(result) == "deny"
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    # The message must be actionable, naming the value already in the file.
    assert "metadata.type: feedback" in reason


def test_allows_top_level_type(tmp_path):
    result = _run(
        {"tool_name": "Write",
         "tool_input": {"file_path": str(tmp_path / "m.md"), "content": TOP_LEVEL}},
        tmp_path,
    )
    assert _decision(result) == "allow"


def test_ignores_files_outside_a_memory_root(tmp_path):
    """Scope discipline: a malformed memory shape elsewhere is not this gate's
    business, and denying there would break unrelated editing."""
    outside = tmp_path / "not-memory"
    outside.mkdir()
    result = _run(
        {"tool_name": "Write",
         "tool_input": {"file_path": str(outside / "m.md"), "content": NESTED_ONLY}},
        tmp_path / "memory-root-that-does-not-contain-it",
    )
    assert _decision(result) == "allow"


def test_skips_the_folder_index(tmp_path):
    """MEMORY.md is the folder index, not a memory; it carries no frontmatter
    by spec, so requiring `type` of it would be wrong."""
    result = _run(
        {"tool_name": "Write",
         "tool_input": {"file_path": str(tmp_path / "MEMORY.md"),
                        "content": "- [a](a.md): hook\n"}},
        tmp_path,
    )
    assert _decision(result) == "allow"


def test_edit_is_validated_against_the_reconstructed_file(tmp_path):
    """An Edit that REMOVES the top-level type must be caught.

    Validating `new_string` alone would miss this entirely: the removal is only
    visible once the substitution is applied to what is already on disk.
    """
    target = tmp_path / "m.md"
    target.write_text(TOP_LEVEL, encoding="utf-8")
    result = _run(
        {"tool_name": "Edit",
         "tool_input": {"file_path": str(target),
                        "old_string": "type: feedback\n", "new_string": ""}},
        tmp_path,
    )
    assert _decision(result) == "deny"


def test_edit_touching_only_the_body_is_allowed(tmp_path):
    target = tmp_path / "m.md"
    target.write_text(TOP_LEVEL, encoding="utf-8")
    result = _run(
        {"tool_name": "Edit",
         "tool_input": {"file_path": str(target),
                        "old_string": "body", "new_string": "new body"}},
        tmp_path,
    )
    assert _decision(result) == "allow"


def test_edit_with_absent_old_string_defers_to_the_edit_tool(tmp_path):
    target = tmp_path / "m.md"
    target.write_text(TOP_LEVEL, encoding="utf-8")
    result = _run(
        {"tool_name": "Edit",
         "tool_input": {"file_path": str(target),
                        "old_string": "NOT PRESENT", "new_string": "x"}},
        tmp_path,
    )
    assert _decision(result) == "allow"


def test_non_write_tools_are_ignored(tmp_path):
    result = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}}, tmp_path)
    assert _decision(result) == "allow"


@pytest.mark.parametrize("payload", [{}, {"tool_name": "Write"}, {"tool_input": {}}])
def test_fails_open_on_degenerate_payloads(payload, tmp_path):
    """Every malformed-input path must allow, and must still exit 0."""
    assert _decision(_run(payload, tmp_path)) == "allow"


def test_fails_open_when_the_package_is_unimportable(tmp_path):
    """If memforge cannot be imported the gate must allow, never wedge.

    Shadowed with a stub package that raises on import, placed on PYTHONPATH so
    it precedes site-packages. Setting PYTHONPATH to a bogus directory would
    NOT work: an installed memforge would still resolve and the test would pass
    for the wrong reason.
    """
    shadow = tmp_path / "shadow"
    (shadow / "memforge").mkdir(parents=True)
    (shadow / "memforge" / "__init__.py").write_text(
        "raise ImportError('simulated broken install')\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env["MEMFORGE_MEMORY_ROOTS"] = str(tmp_path)
    env["PYTHONPATH"] = str(shadow)
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "m.md"), "content": NESTED_ONLY},
        }),
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "a broken install must ALLOW, not deny"


def test_env_override_announces_itself_on_stderr(tmp_path):
    """An override must never disarm the gate silently.

    Precedence is correct, silence is not: an operator who sets this once for a
    non-standard layout and forgets would otherwise have a permanently
    disarmed gate that reports nothing.
    """
    env = dict(os.environ)
    env["MEMFORGE_MEMORY_ROOTS"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "m.md"), "content": TOP_LEVEL},
        }),
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0
    assert "MEMFORGE_MEMORY_ROOTS" in proc.stderr


def test_oversized_file_is_declined_not_read(tmp_path):
    """A pathological file must not become a memory-exhaustion vector."""
    import importlib.util
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader("cc_gate_under_test", str(GATE))
    spec = importlib.util.spec_from_loader("cc_gate_under_test", loader)
    gate = importlib.util.module_from_spec(spec)
    loader.exec_module(gate)
    target = tmp_path / "huge.md"
    target.write_text("x" * 128, encoding="utf-8")

    original = gate.MAX_VALIDATED_BYTES
    try:
        gate.MAX_VALIDATED_BYTES = 16  # smaller than the file
        out = gate._post_write_text(
            "Edit",
            {"file_path": str(target), "old_string": "x", "new_string": "y"},
        )
        assert out is None, "over-cap files must be declined, not read"
    finally:
        gate.MAX_VALIDATED_BYTES = original
