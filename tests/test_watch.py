"""Tests for memforge.cli.watch (CommitDebouncer repo targeting)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import importlib.util

import pytest

# watch.py imports watchdog at module import time and sys.exit(2)s (raises
# SystemExit, not ImportError) when it is missing, so importorskip cannot catch
# it. Probe for watchdog first and skip the whole module cleanly if absent.
if importlib.util.find_spec("watchdog") is None:
    pytest.skip("watchdog not installed", allow_module_level=True)

from memforge.cli import watch  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")


def test_commit_targets_own_repo_not_process_cwd(tmp_path: Path, monkeypatch):
    """_commit must commit into its own repo even when the process cwd points at
    a DIFFERENT repo. Regression for lifecycle/watch-01 (os.chdir race)."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_repo(repo_a)
    _init_repo(repo_b)

    # Make a pending change in repo_a only.
    (repo_a / "new_a.md").write_text("change in A\n", encoding="utf-8")

    # Point the process cwd at repo_b to simulate a concurrent debouncer that
    # has chdir'd elsewhere. The old os.chdir(self.repo) code would still work
    # in isolation, but with `-C <repo>` the cwd is irrelevant; assert that.
    monkeypatch.chdir(repo_b)

    deb = watch.CommitDebouncer(repo_a, debounce_ms=10, quiet=True)
    deb._commit()

    # repo_a got the commit.
    log_a = _git(repo_a, "log", "--format=%s").stdout.splitlines()
    assert len(log_a) == 2
    assert log_a[0].startswith("memory: filesystem write")
    files_a = _git(repo_a, "show", "--name-only", "--format=", "HEAD").stdout
    assert "new_a.md" in files_a

    # repo_b is untouched (no stray commit landed there).
    log_b = _git(repo_b, "log", "--format=%s").stdout.splitlines()
    assert len(log_b) == 1


def test_commit_noop_when_clean(tmp_path: Path):
    """A clean repo produces no commit."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    deb = watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)
    deb._commit()
    log = _git(repo, "log", "--format=%s").stdout.splitlines()
    assert len(log) == 1


def test_commit_does_not_change_process_cwd(tmp_path: Path, monkeypatch):
    """_commit must not mutate the process working directory (no os.chdir)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "x.md").write_text("x\n", encoding="utf-8")

    start = tmp_path / "elsewhere"
    start.mkdir()
    monkeypatch.chdir(start)
    before = os.getcwd()

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    assert os.getcwd() == before


def test_commit_scoped_to_md_excludes_stray_files(tmp_path: Path):
    """watch-01: a stray non-.md file (editor swap, .DS_Store) must NOT ride
    along in the scoped `memory: filesystem write` commit. Only *.md and
    .memforge/ content is staged."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "real.md").write_text("real memory\n", encoding="utf-8")
    (repo / ".DS_Store").write_text("junk\n", encoding="utf-8")
    (repo / "scratch.swp").write_text("swap\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "real.md" in committed
    assert ".DS_Store" not in committed
    assert "scratch.swp" not in committed
    # The stray files remain untracked, not committed.
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout
    assert ".DS_Store" in untracked
    assert "scratch.swp" in untracked


def test_commit_stages_memforge_dir(tmp_path: Path):
    """Content under .memforge/ (recall index, snoozes) is in scope."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    mf = repo / ".memforge"
    mf.mkdir()
    (mf / "recall-index.json").write_text("{}\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert ".memforge/recall-index.json" in committed
