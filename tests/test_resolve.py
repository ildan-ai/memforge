"""Tests for memforge.cli.resolve (the v0.4 reference resolve operation)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from memforge.cli import resolve
from memforge.frontmatter import parse


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _make_member(folder: Path, name: str, *, uid: str, topic: str | None = "the-topic",
                 status: str = "active") -> Path:
    fm = [
        "---",
        f"name: {name}",
        f"description: fixture {name}",
        "type: feedback",
        f"uid: {uid}",
        f"status: {status}",
    ]
    if topic is not None:
        fm.append(f"decision_topic: {topic}")
    fm.append("---")
    body = f"\nBody for {name}.\n"
    text = "\n".join(fm) + body
    p = folder / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def two_member_repo(tmp_path: Path) -> tuple[Path, Path, list[Path]]:
    """Build a tmp git repo with a memory folder and two competing live members."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()
    a = _make_member(memory, "feedback_a", uid="mem-a")
    b = _make_member(memory, "feedback_b", uid="mem-b")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path, memory, [a, b]


def test_resolve_happy_path_winner_uid(two_member_repo, monkeypatch):
    repo, memory, paths = two_member_repo
    a, b = paths

    rc = resolve.main([
        "the-topic",
        "--memory-root", str(memory),
        "--winner-uid", "mem-b",
    ])
    assert rc == 0

    a_fm, _ = parse(a.read_text(encoding="utf-8"))
    b_fm, _ = parse(b.read_text(encoding="utf-8"))

    # Winner: status active, replaces lists loser, ever_multi_member true
    assert b_fm["status"] == "active"
    assert b_fm["replaces"] == ["mem-a"]
    assert b_fm["ever_multi_member"] is True

    # Loser: status superseded, superseded_by points to winner
    assert a_fm["status"] == "superseded"
    assert a_fm["superseded_by"] == ["mem-b"]
    assert a_fm["ever_multi_member"] is True

    # Atomic resolve commit landed
    log = _git(repo, "log", "--format=%s", "-1").stdout.strip()
    assert log == "memforge: resolve the-topic"


def test_resolve_single_member_is_noop(tmp_path: Path, capsys):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()
    _make_member(memory, "lone", uid="mem-lone")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")

    rc = resolve.main(["the-topic", "--memory-root", str(memory)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "only one live member" in captured.out

    # No new commit
    log = _git(tmp_path, "log", "--oneline").stdout.splitlines()
    assert len(log) == 1


def test_resolve_zero_members_is_noop(tmp_path: Path, capsys):
    memory = tmp_path / "memory"
    memory.mkdir()
    rc = resolve.main(["nonexistent-topic", "--memory-root", str(memory)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "no live members" in captured.out


def test_resolve_winner_uid_mismatch_errors(two_member_repo, capsys):
    _, memory, _ = two_member_repo
    rc = resolve.main([
        "the-topic",
        "--memory-root", str(memory),
        "--winner-uid", "mem-does-not-exist",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "does not match any live member" in captured.err


def test_resolve_dry_run_does_not_mutate(two_member_repo):
    repo, memory, paths = two_member_repo
    a, b = paths
    a_before = a.read_text(encoding="utf-8")
    b_before = b.read_text(encoding="utf-8")

    rc = resolve.main([
        "the-topic",
        "--memory-root", str(memory),
        "--winner-uid", "mem-b",
        "--dry-run",
    ])
    assert rc == 0

    # Files unchanged
    assert a.read_text(encoding="utf-8") == a_before
    assert b.read_text(encoding="utf-8") == b_before

    # No new commit
    log = _git(repo, "log", "--oneline").stdout.splitlines()
    assert len(log) == 1


def test_resolve_deletes_snooze_file(two_member_repo):
    repo, memory, _ = two_member_repo
    snooze_dir = memory / ".memforge" / "snoozes"
    snooze_dir.mkdir(parents=True)
    snooze_file = snooze_dir / "the-topic.yaml"
    snooze_file.write_text(
        "decision_topic: the-topic\n"
        "snoozed_until: 2026-12-31\n"
        "snooze_reason: testing snooze deletion\n"
        "assignee: mike\n"
        "created: 2026-05-08\n"
        "created_by: mike\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed snooze")

    rc = resolve.main([
        "the-topic",
        "--memory-root", str(memory),
        "--winner-uid", "mem-b",
    ])
    assert rc == 0
    assert not snooze_file.exists()
