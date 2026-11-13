"""Tests for .github/scripts/check_dco.py, the required DCO sign-off check.

Run as a subprocess against real throwaway git repos, because the contract is
the exit code CI reads, not an internal function.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check_dco.py"


def _git(repo: Path, *args: str, email: str = "dev@example.org") -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": email}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, message: str, email: str = "dev@example.org", name: str = "f.txt") -> str:
    (repo / name).write_text(message)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message, email=email)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "base")
    return tmp_path


def _check(repo: Path, base: str, head: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), base, head], cwd=repo,
                          capture_output=True, text=True, timeout=30)


def test_signed_commits_pass(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "fix: a\n\nSigned-off-by: Dev <dev@example.org>")
    assert _check(repo, base, head).returncode == 0


def test_unsigned_commit_fails_and_names_it(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "fix: a\n\nSigned-off-by: Dev <dev@example.org>")
    bad = _commit(repo, "fix: b")
    proc = _check(repo, base, bad)
    assert proc.returncode == 1
    assert bad[:12] in proc.stdout


def test_signoff_for_another_email_fails(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "fix: a\n\nSigned-off-by: Someone <other@example.org>")
    assert _check(repo, base, head).returncode == 1


def test_email_match_ignores_case(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "fix: a\n\nSigned-off-by: Dev <DEV@Example.org>")
    assert _check(repo, base, head).returncode == 0


def test_signoff_text_mid_line_does_not_count(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "fix: a\n\nsee Signed-off-by: Dev <dev@example.org> above")
    assert _check(repo, base, head).returncode == 1


def test_merge_commits_are_skipped(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "side")
    _commit(repo, "side\n\nSigned-off-by: Dev <dev@example.org>", name="side.txt")
    _git(repo, "switch", "-q", "main")
    _commit(repo, "main\n\nSigned-off-by: Dev <dev@example.org>")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    assert _check(repo, base, _git(repo, "rev-parse", "HEAD")).returncode == 0


def test_wrong_argument_count_is_usage_error(repo):
    proc =subprocess.run([sys.executable, str(SCRIPT)], cwd=repo,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
