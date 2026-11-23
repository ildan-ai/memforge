"""The example bash commit-msg hook applies the waiver-file gate
(SPEC.md §"Audit waivers", rule 5) before the commit is made."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "examples" / "git-hooks" / "commit-msg"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "store"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    return repo


def _hook(repo: Path, message: str) -> subprocess.CompletedProcess:
    msg = repo.parent / "COMMIT_EDITMSG"
    msg.write_text(message + "\n", encoding="utf-8")
    return subprocess.run(
        ["bash", str(HOOK), str(msg)], cwd=repo, capture_output=True, text=True, check=False
    )


def _stage_waiver(repo: Path) -> None:
    (repo / ".memforge").mkdir()
    (repo / ".memforge" / "audit-waivers.yaml").write_text("waived_commits: []\n", encoding="utf-8")


@pytest.fixture
def waiver_only(repo: Path) -> Path:
    _git(repo, "commit", "-q", "-m", "seed")
    _stage_waiver(repo)
    _git(repo, "add", "-A")
    return repo


def test_waiver_with_its_prefix_alone_is_accepted(waiver_only):
    assert _hook(waiver_only, "memforge: waiver").returncode == 0
    assert _hook(waiver_only, "memforge: waiver add migration commit").returncode == 0


@pytest.mark.parametrize("message", ["add waivers", "memforge: config", "memforge: waivers"])
def test_waiver_without_its_prefix_is_rejected(waiver_only, message):
    result = _hook(waiver_only, message)
    assert result.returncode == 1
    assert "audit-waivers.yaml" in result.stderr


def test_waiver_with_a_second_file_is_rejected(repo):
    _stage_waiver(repo)
    _git(repo, "add", "-A")
    result = _hook(repo, "memforge: waiver")
    assert result.returncode == 1
    assert "other files" in result.stderr


def test_unrelated_commit_is_accepted(repo):
    assert _hook(repo, "seed").returncode == 0


@pytest.mark.skipif(sys.platform == "win32", reason="a newline in a file name")
def test_waiver_with_a_second_file_named_by_a_newline_is_rejected(waiver_only):
    (waiver_only / "\n").write_text("x\n", encoding="utf-8")
    _git(waiver_only, "add", "-A")
    result = _hook(waiver_only, "memforge: waiver")
    assert result.returncode == 1
    assert "other files" in result.stderr
