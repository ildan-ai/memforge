"""A git call that names its repository acts on that repository, whatever
repository-local variables the caller's environment carries.

A tool started from inside a git hook, or from a shell that exported
`GIT_DIR`, inherits variables that point git at another repository, index or
work tree. git honours them over `-C` and the working folder, so without
clearing them a tool would read or commit to that other repository while
reporting on this one. Each test points them at a decoy and checks that the
decoy is untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from memforge._sync_git import Store
from memforge.cli import _dispatch, promote, resolve, watch

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

GIT = shutil.which("git") or "git"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.environ["GIT_CONFIG_GLOBAL"]})
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=60, env=env).stdout


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _member(folder: Path, name: str, uid: str) -> None:
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: fixture {name}\ntype: feedback\nuid: {uid}\n"
        "status: active\ndecision_topic: the-topic\n---\n\nBody.\n",
        encoding="utf-8",
    )


def _commits(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


@pytest.fixture
def lab(tmp_path: Path, monkeypatch):
    """A committed store and a committed decoy, with the decoy in GIT_DIR."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    store = tmp_path / "store"
    _init(store)
    (store / "MEMORY.md").write_text("# Memory Index\n\n- [A](feedback_a.md): a\n", encoding="utf-8")
    _member(store, "feedback_a", "mem-a")
    _member(store, "feedback_b", "mem-b")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "seed")
    decoy = tmp_path / "decoy"
    _init(decoy)
    (decoy / "x.md").write_text("x\n", encoding="utf-8")
    _git(decoy, "add", "-A")
    _git(decoy, "commit", "-q", "-m", "decoy seed")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    monkeypatch.chdir(store)
    return tmp_path, store, decoy


def test_watch_commits_to_the_watched_repository(lab):
    tmp_path, store, decoy = lab
    (store / "feedback_a.md").write_text("changed\n", encoding="utf-8")

    watch.CommitDebouncer(store, debounce_ms=10, quiet=True)._commit()

    assert _commits(decoy) == 1
    assert _commits(store) == 2


def test_resolve_commits_to_the_memory_root(lab):
    tmp_path, store, decoy = lab

    rc = resolve.main(["the-topic", "--memory-root", str(store), "--winner-uid", "mem-b"])

    assert rc == 0
    assert _commits(decoy) == 1
    assert _commits(store) == 2


def test_promote_commits_to_source_and_target(lab):
    tmp_path, store, decoy = lab
    target = tmp_path / "target"
    _init(target)

    rc = promote.main(["feedback_a.md", "--source", str(store), "--target", str(target), "--yes"])

    assert rc == 0
    assert _commits(decoy) == 1
    assert _commits(store) == 2
    assert _commits(target) == 1


def test_sync_reads_the_store_repository(lab):
    tmp_path, store, decoy = lab

    result = Store(GIT, store).git("rev-parse", "--absolute-git-dir")

    assert result.rc == 0
    assert Path(result.out.decode().strip()).resolve() == (store / ".git").resolve()


def test_sync_keeps_the_receivers_other_variables(lab, monkeypatch):
    tmp_path, store, decoy = lab
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Receiver")

    result = Store(GIT, store).git("var", "GIT_AUTHOR_IDENT")

    assert result.out.decode().startswith("Receiver ")


def test_init_store_reads_the_store_config(lab):
    tmp_path, store, decoy = lab
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly000000000000"
    _git(store, "config", "user.signingkey", f"key::{pub}")

    assert _dispatch.main(["init-store", "--memory-root", str(store), "--mode", "ssh"]) == 0

    assert pub in (store / ".memforge" / "allowed_signers").read_text(encoding="utf-8")
