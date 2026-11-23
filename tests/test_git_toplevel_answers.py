"""A `git rev-parse --show-toplevel` answer that does not name a folder at or
above the store is not a repository top.

An empty or relative answer is read against the current folder, so a tool
that took it would commit to, or vouch for, whatever folder it was started
from. Each test answers with such a git, found outside the repository, and
checks the tool refuses instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from memforge import sync as sync_mod
from memforge.cli import resolve

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None,
                                reason="the fake git is a POSIX script; needs git")

GIT = shutil.which("git") or "git"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=60).stdout


def _member(folder: Path, name: str, uid: str) -> None:
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: fixture {name}\ntype: feedback\nuid: {uid}\n"
        "status: active\ndecision_topic: the-topic\n---\n\nBody.\n",
        encoding="utf-8",
    )


def _fake_git(folder: Path, toplevel: str, calls: Path) -> None:
    """A git that answers `rev-parse --show-toplevel` with `toplevel`, records
    every other command in `calls`, and succeeds with no output."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "git"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if '--show-toplevel' in args:\n"
        f"    print({toplevel!r})\n"
        "elif '--version' in args:\n"
        "    print('git version 0')\n"
        "else:\n"
        f"    with open({str(calls)!r}, 'a') as log:\n"
        "        log.write(' '.join(args) + '\\n')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> Path:
    """A repository whose top is the memory folder, with a two-member group."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    root = tmp_path / "store"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    _member(root, "feedback_a", "mem-a")
    _member(root, "feedback_b", "mem-b")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    monkeypatch.chdir(root)
    return root


def _answer(tmp_path: Path, answer: str) -> str:
    if answer == "elsewhere":
        (tmp_path / "elsewhere").mkdir()
        return str(tmp_path / "elsewhere")
    return answer


@pytest.mark.parametrize("answer", ["", ".", "store", "elsewhere"])
def test_resolve_refuses_a_top_level_that_does_not_hold_the_store(store, tmp_path, monkeypatch,
                                                                  capsys, answer):
    if answer == "store":
        monkeypatch.chdir(tmp_path)
    calls = tmp_path / "calls"
    _fake_git(tmp_path / "tools", _answer(tmp_path, answer), calls)
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))
    before = {p.name: p.read_bytes() for p in store.glob("*.md")}

    rc = resolve.main(["the-topic", "--memory-root", str(store), "--winner-uid", "mem-b"])

    assert rc == 2
    assert "not a folder at or above" in capsys.readouterr().err
    assert {p.name: p.read_bytes() for p in store.glob("*.md")} == before
    assert not calls.exists(), calls.read_text(encoding="utf-8")


@pytest.mark.parametrize("answer", ["", "."])
def test_sync_refuses_a_top_level_that_is_not_absolute(store, tmp_path, monkeypatch, capsys,
                                                       answer):
    _fake_git(tmp_path / "tools", answer, tmp_path / "calls")
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))

    rc = sync_mod.run_sync(store)

    assert rc == 1
    assert "memforge sync: root_not_toplevel:" in capsys.readouterr().err
