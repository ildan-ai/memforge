"""The git program Tier 2 runs, and the answers it gives, are checked before
the commit log is judged.

A memory folder can sit below the top of the repository it belongs to, and
anyone who can commit anywhere in that repository can place an executable
named `git` beside it. And a git whose answers do not describe the audited
folder (no top level, a relative one, one that does not hold the folder, a
log line that is not a commit record) must not read as a clean commit log.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from memforge.cli._concurrency_audit import _load_waivers, tier2_findings

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None,
                                reason="planted programs are POSIX scripts; needs git")

GIT = shutil.which("git") or "git"
REAL_PATH = os.environ.get("PATH", os.defpath)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=60).stdout


@pytest.fixture
def lab(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """A repository whose memory folder `notes/memory` is a subfolder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    store = repo / "notes" / "memory"
    store.mkdir(parents=True)
    (store / "m.md").write_text("---\nname: m\ndescription: d\ntype: feedback\n---\nbody\n",
                                encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return tmp_path, repo, store


def _plant(folder: Path, marker: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "git"
    script.write_text(f'#!/bin/sh\necho "$*" >> "{marker}"\nexit 0\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _fake_git(folder: Path, *, toplevel: str, log: str = "") -> None:
    """A git outside the repository that answers `rev-parse --show-toplevel`
    with `toplevel`, `log` with `log`, and every other command with nothing."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "git"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"if 'rev-parse' in args:\n    print({toplevel!r})\n"
        f"elif 'log' in args:\n    sys.stdout.write({log!r})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _not_evaluated(findings) -> list[str]:
    return [m for s, m in findings if s == "WARN" and "NOT evaluated" in m]


def test_a_git_elsewhere_in_the_enclosing_repository_is_refused(lab, monkeypatch):
    tmp_path, repo, store = lab
    marker = tmp_path / "ran"
    _plant(repo / "bin", marker)
    monkeypatch.setenv("PATH", os.pathsep.join([str(repo / "bin"), REAL_PATH]))
    monkeypatch.chdir(store)

    findings = tier2_findings(store)
    waivers = _load_waivers(store)

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert waivers.commits == frozenset()
    warns = _not_evaluated(findings)
    assert len(warns) == 1, findings
    assert f"git resolves to {repo / 'bin' / 'git'}, inside {repo}" in warns[0]


def test_a_git_in_the_own_python_environment_inside_the_repository_is_used(lab, monkeypatch):
    tmp_path, repo, store = lab
    scripts = repo / "venv" / "bin"
    scripts.mkdir(parents=True)
    (scripts / "git").write_text(f'#!/bin/sh\nexec "{GIT}" "$@"\n', encoding="utf-8")
    (scripts / "git").chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(repo / "venv"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(scripts), REAL_PATH]))

    assert _not_evaluated(tier2_findings(store)) == []


@pytest.mark.parametrize("answer", ["", ".", "notes/memory", "elsewhere"])
def test_a_top_level_that_does_not_hold_the_store_is_a_warn(lab, monkeypatch, answer):
    tmp_path, repo, store = lab
    if answer == "elsewhere":
        (tmp_path / "elsewhere").mkdir()
        answer = str(tmp_path / "elsewhere")
    _fake_git(tmp_path / "tools", toplevel=answer)
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))
    monkeypatch.chdir(store if answer != "notes/memory" else repo)

    findings = tier2_findings(store)

    warns = _not_evaluated(findings)
    assert len(warns) == 1, findings
    assert "Tier 2 commit-log audit SKIPPED" in warns[0]
    assert _load_waivers(store).commits == frozenset()


def test_a_log_line_that_is_not_a_commit_record_is_a_warn(lab, monkeypatch):
    tmp_path, repo, store = lab
    _fake_git(tmp_path / "tools", toplevel=str(repo), log="not a commit record\n")
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))

    findings = tier2_findings(store)

    warns = _not_evaluated(findings)
    assert len(warns) == 1, findings
    assert "not a commit record" in warns[0]
