"""The documented `memory-validate` pre-commit hook, as a reader copies it.

Several tools and a user's own shell may commit with `GIT_LITERAL_PATHSPECS=1`,
which turns the hook's `'*.md'` pathspec into a literal file name that matches
nothing. A copy of the hook that does not clear the variable lets a malformed
memory into history without a word.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("docs/quickstart.md", "docs/adapter-implementation-guide.md")

pytestmark = [
    pytest.mark.skipif(os.name == "nt", reason="bash hook"),
    pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                       reason="needs git and bash"),
]

_FENCE = re.compile(r"```bash\n(.*?)```", re.S)


def _hook_copies(doc: str) -> list[str]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    return [b for b in _FENCE.findall(text) if "xargs -0 memory-validate" in b]


@pytest.mark.parametrize("doc", DOCS)
def test_each_doc_ships_the_hook_with_the_unset_line(doc):
    copies = _hook_copies(doc)
    assert copies, f"no hook copy found in {doc}"
    for hook in copies:
        assert hook.splitlines()[1] == "unset GIT_LITERAL_PATHSPECS"
    assert "Keep the `unset` line" in (ROOT / doc).read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", DOCS)
def test_documented_hook_refuses_a_broken_memory_under_literal_pathspecs(doc, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "memory-validate"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m memforge.cli.validate "$@"\n')
    shim.chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "PYTHONPATH": str(ROOT / "src"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }

    def git(*args, extra=None):
        return subprocess.run(["git", "-C", str(repo), *args], env={**env, **(extra or {})},
                              capture_output=True, text=True)

    assert git("init", "-q").returncode == 0
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(_hook_copies(doc)[0])
    hook.chmod(0o755)
    (repo / "bad.md").write_text("---\nname: bad\ndescription: this breaks: here\n---\nbody\n")
    git("add", "bad.md")
    plain = git("commit", "-m", "plain")
    literal = git("commit", "-m", "literal", extra={"GIT_LITERAL_PATHSPECS": "1"})
    assert plain.returncode != 0, plain.stderr
    assert literal.returncode != 0, literal.stdout + literal.stderr


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["adapters/aider/README.md", "adapters/cursor/README.md",
                                 "adapters/codex/README.md", "adapters/vscode-copilot/README.md",
                                 "adapters/README.md"])
def test_watcher_is_the_installed_console_script(rel):
    text = _read(rel)
    assert "tools/memory-watch" not in text
    assert "pip install watchdog" not in text


def test_aider_recipe_uses_a_private_temp_file():
    text = _read("adapters/aider/README.md")
    assert "/tmp/aider-memory.md" not in text
    assert "out=$(mktemp)" in text


def test_claim_block_migration_names_its_store():
    for rel in ("docs/adapter-implementation-guide.md", "README.md"):
        for line in _read(rel).splitlines():
            if "memforge-migrate-claim-block" in line and "Run" in line:
                assert "--memory-root" in line
    assert "memforge-migrate-claim-block --memory-root <store>" in _read(
        "docs/adapter-implementation-guide.md")


def test_audit_scenarios_name_their_store():
    text = _read("docs/adapter-implementation-guide.md")
    assert text.count("Run `memory-audit --path <store>`") == 2
