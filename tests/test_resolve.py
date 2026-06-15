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


# ---------- security: path traversal via decision_topic ----------


def test_resolve_rejects_traversal_topic_argument(tmp_path: Path, capsys):
    """A traversal-shaped topic must be refused before any filesystem or git
    operation. Regression for sec-fswrite/resolve-01."""
    memory = tmp_path / "memory"
    memory.mkdir()
    rc = resolve.main([
        "../../secret/victim",
        "--memory-root", str(memory),
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "fails the slug pipeline" in captured.err


def test_resolve_traversal_topic_does_not_delete_outside_folder(tmp_path: Path):
    """Even if a malformed topic reached the group walk, a snooze path that
    escapes the snoozes dir must never be unlinked. Builds two live members
    whose decision_topic is a traversal string and a victim file outside the
    memory folder, then asserts the victim survives. Regression for
    sec-fswrite/resolve-01."""
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    memory = repo / "memory"
    memory.mkdir()
    traversal = "../../secret/victim"
    _make_member(memory, "feedback_a", uid="mem-a", topic=traversal)
    _make_member(memory, "feedback_b", uid="mem-b", topic=traversal)

    # The would-be deletion target, outside the memory folder.
    victim_dir = repo / "secret"
    victim_dir.mkdir(parents=True)
    victim = victim_dir / "victim.yaml"
    victim.write_text("important: keep me\n", encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    # main() validates the topic argument up front and refuses it.
    rc = resolve.main([traversal, "--memory-root", str(memory), "--winner-uid", "mem-b"])
    assert rc == 2
    assert victim.exists()

    # Defense in depth: _delete_snooze must also refuse an out-of-folder path
    # directly, regardless of how the topic got there.
    with pytest.raises(SystemExit) as exc:
        resolve._delete_snooze(memory, traversal, dry_run=False)
    assert exc.value.code == 2
    assert victim.exists()


def test_resolve_commit_does_not_sweep_unrelated_staged_change(two_member_repo):
    """The scope-locked resolve commit must not include a pre-staged unrelated
    change. Regression for lifecycle/resolve-01."""
    repo, memory, paths = two_member_repo

    # Stage an unrelated change before resolving.
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("not part of the resolve\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")

    rc = resolve.main([
        "the-topic",
        "--memory-root", str(memory),
        "--winner-uid", "mem-b",
    ])
    assert rc == 0

    # The resolve commit must touch only the two member files.
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "unrelated.txt" not in files
    assert any("feedback_a.md" in f for f in files)
    assert any("feedback_b.md" in f for f in files)

    # The unrelated change is still staged (not committed away).
    staged = _git(repo, "diff", "--cached", "--name-only").stdout.split()
    assert "unrelated.txt" in staged


# ---------- re-resolve: prior superseded members carried (MAJOR resolve-01) ----------


def test_reresolve_carries_prior_superseded_into_winner_replaces(tmp_path: Path):
    """A group resolved once, then a new competing member added and resolved
    again, must end with the new winner's `replaces` listing EVERY historical
    superseded UID (SPEC post-condition 3, 'no fewer') and every superseded
    member's superseded_by re-pointed at the new winner, so the concurrency
    audit reports zero findings. Regression for lifecycle/resolve-01.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()

    a = _make_member(memory, "feedback_a", uid="mem-a")
    b = _make_member(memory, "feedback_b", uid="mem-b")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")

    # First resolve: A wins, B superseded.
    rc = resolve.main(["the-topic", "--memory-root", str(memory), "--winner-uid", "mem-a"])
    assert rc == 0
    a_fm, _ = parse(a.read_text(encoding="utf-8"))
    b_fm, _ = parse(b.read_text(encoding="utf-8"))
    assert a_fm["status"] == "active" and a_fm["replaces"] == ["mem-b"]
    assert b_fm["status"] == "superseded" and b_fm["superseded_by"] == ["mem-a"]

    # A new competing member C is added later (same topic, live).
    c = _make_member(memory, "feedback_c", uid="mem-c")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add c")

    # Second resolve: C wins. A becomes superseded; B (already superseded) must
    # be carried forward, not dropped.
    rc = resolve.main(["the-topic", "--memory-root", str(memory), "--winner-uid", "mem-c"])
    assert rc == 0

    a_fm, _ = parse(a.read_text(encoding="utf-8"))
    b_fm, _ = parse(b.read_text(encoding="utf-8"))
    c_fm, _ = parse(c.read_text(encoding="utf-8"))

    # Winner C: replaces lists BOTH historical losers (no fewer).
    assert c_fm["status"] == "active"
    assert set(c_fm["replaces"]) == {"mem-a", "mem-b"}
    assert c_fm["ever_multi_member"] is True

    # Both prior/current losers point at the new winner.
    assert a_fm["status"] == "superseded" and a_fm["superseded_by"] == ["mem-c"]
    assert b_fm["status"] == "superseded" and b_fm["superseded_by"] == ["mem-c"]

    # The concurrency audit (tier 1: HEAD-pure symmetry invariants) is clean.
    from memforge.cli._concurrency_audit import run_concurrency_audit
    blockers, majors, _warns = run_concurrency_audit(memory, skip_tier2=True)
    assert blockers == [], f"unexpected BLOCKER findings: {blockers}"
    assert majors == [], f"unexpected MAJOR findings: {majors}"
