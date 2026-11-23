"""The frontmatter gate still allows the write on an internal error, and now
says so on stderr.

A gate that fails open in silence looks the same as one that checked and
passed. One stderr line naming the error class makes the failure visible
without ever changing the allow decision.
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
NESTED_ONLY = "---\nname: x\ndescription: y\nmetadata:\n  type: feedback\n---\n\nbody\n"

pytestmark = pytest.mark.skipif(not GATE.exists(), reason="CC adapter gate not present in this checkout")


def _shadow(tmp_path: Path, files: dict[str, str]) -> Path:
    """A memforge package placed ahead of the real one, with the given modules."""
    shadow = tmp_path / "shadow"
    for name, body in files.items():
        path = shadow / "memforge" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return shadow


def _gate(tmp_path: Path, stdin: str, pythonpath: Path, roots: Path | None) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "MEMFORGE_MEMORY_ROOTS"}
    env.update({"HOME": str(home), "PYTHONPATH": str(pythonpath)})
    if roots is not None:
        env["MEMFORGE_MEMORY_ROOTS"] = str(roots)
    proc = subprocess.run([sys.executable, str(GATE)], input=stdin, capture_output=True,
                          text=True, env=env, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "an internal error must ALLOW, not deny"
    return proc


def _errors(proc: subprocess.CompletedProcess) -> list[str]:
    return [line for line in proc.stderr.splitlines() if "internal error" in line]


def _write(tmp_path: Path) -> str:
    return json.dumps({"tool_name": "Write",
                       "tool_input": {"file_path": str(tmp_path / "m.md"), "content": NESTED_ONLY}})


def test_an_unimportable_package_allows_and_names_the_error(tmp_path):
    shadow = _shadow(tmp_path, {"__init__.py": "raise ImportError('simulated broken install')\n"})

    proc = _gate(tmp_path, _write(tmp_path), shadow, roots=tmp_path)

    assert len(_errors(proc)) == 1
    assert "ImportError" in _errors(proc)[0]


def test_a_validator_that_raises_allows_and_names_the_error(tmp_path):
    shadow = _shadow(tmp_path, {
        "__init__.py": "",
        "frontmatter.py": ("def validate_frontmatter(text):\n    raise RuntimeError('boom')\n"
                           "def validate_required_fields(text):\n    return True, ''\n"),
    })

    proc = _gate(tmp_path, _write(tmp_path), shadow, roots=tmp_path)

    assert len(_errors(proc)) == 1
    assert "RuntimeError" in _errors(proc)[0]


def test_unreadable_default_roots_allow_and_name_the_error(tmp_path):
    shadow = _shadow(tmp_path, {"__init__.py": "", "paths.py": "raise OSError('simulated')\n"})

    proc = _gate(tmp_path, _write(tmp_path), shadow, roots=None)

    assert len(_errors(proc)) == 1
    assert "OSError" in _errors(proc)[0]


def test_a_payload_that_is_not_json_allows_and_names_the_error(tmp_path):
    proc = _gate(tmp_path, "{not json", REPO_ROOT / "src", roots=tmp_path)

    assert len(_errors(proc)) == 1
    assert "JSONDecodeError" in _errors(proc)[0]


def test_a_checked_write_prints_no_error(tmp_path):
    proc = _gate(tmp_path, json.dumps({"tool_name": "Write", "tool_input": {
        "file_path": str(tmp_path / "m.md"),
        "content": "---\nname: x\ndescription: y\ntype: feedback\n---\n\nbody\n"}}),
        REPO_ROOT / "src", roots=tmp_path)

    assert _errors(proc) == []
