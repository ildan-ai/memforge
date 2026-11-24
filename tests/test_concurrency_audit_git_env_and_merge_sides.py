"""Tier 2 (commit-log) audit: the repository git is pointed at, and what a
merge may leave to the commits of the branch it merges (SPEC.md §"Audit
invariants for competing claims", "Merge commits (Tier 2)")."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memforge.cli import resolve
from memforge.cli._concurrency_audit import _load_waivers, tier2_findings


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, env=env,
    ).stdout.strip()


def _member(uid: str, *, topic: str | None, status: str = "active",
            extra: str = "", description: str = "") -> str:
    lines = [
        "---",
        f"name: {uid}",
        f"description: {description or uid + ' fixture'}",
        "type: feedback",
        f"uid: {uid}",
        f"status: {status}",
    ]
    if topic is not None:
        lines.append(f"decision_topic: {topic}")
    return "\n".join(lines) + "\n" + extra + "---\n\nBody.\n"


def _commit(repo: Path, message: str, *, days_ago: int = 0) -> str:
    env = None
    if days_ago:
        stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, env=env)
    return _git(repo, "rev-parse", "HEAD")


def _repo(path: Path) -> tuple[Path, Path]:
    """A repo whose memory folder holds a two-member group `db-choice`
    (a.md, b.md) and an unrelated c.md, committed as the seed on `main`."""
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    memory = path / "memory"
    memory.mkdir()
    (memory / "a.md").write_text(_member("mem-a", topic="db-choice"), encoding="utf-8")
    (memory / "b.md").write_text(_member("mem-b", topic="db-choice"), encoding="utf-8")
    (memory / "c.md").write_text(_member("mem-c", topic=None), encoding="utf-8")
    _commit(path, "seed")
    return path, memory


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    return _repo(tmp_path / "repo")


_SUPERSEDED_B = _member(
    "mem-b", topic="db-choice", status="superseded", extra="superseded_by: [mem-a]\n"
)


def _hand_supersede(repo: Path, memory: Path, message: str = "edit b", **kw) -> str:
    (memory / "b.md").write_text(_SUPERSEDED_B, encoding="utf-8")
    return _commit(repo, message, **kw)


def _blockers(findings) -> list[str]:
    return [m for s, m in findings if s == "BLOCKER"]


# ---------- the audit reads the repository it names ----------


@pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_WORK_TREE"])
def test_a_git_variable_naming_another_repository_does_not_hide_a_violation(
    store, tmp_path, monkeypatch, variable
):
    repo, memory = store
    _hand_supersede(repo, memory)
    other, _ = _repo(tmp_path / "other")
    monkeypatch.setenv(variable, str(other / ".git" if variable == "GIT_DIR" else other))

    blockers = _blockers(tier2_findings(memory))

    assert any("edit b" in m and "status->superseded transition" in m for m in blockers)


def test_a_git_dir_naming_a_violating_repository_does_not_flag_a_clean_one(
    store, tmp_path, monkeypatch
):
    _, memory = store
    other, other_memory = _repo(tmp_path / "other")
    _hand_supersede(other, other_memory)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    assert _blockers(tier2_findings(memory)) == []


def test_a_git_index_file_does_not_change_the_waiver_state(store, tmp_path, monkeypatch):
    repo, memory = store
    (memory / ".memforge").mkdir()
    (memory / ".memforge" / "audit-waivers.yaml").write_text(
        "waived_commits: []\n", encoding="utf-8"
    )
    _commit(repo, "memforge: waiver")
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "no-such-index"))

    warns = [m for s, m in tier2_findings(memory) if s == "WARN"]

    assert not [m for m in warns if "audit-waivers.yaml" in m]


def test_a_git_dir_naming_another_repository_does_not_supply_waivers(
    store, tmp_path, monkeypatch
):
    _, memory = store
    other, _ = _repo(tmp_path / "other")
    # At the other repository's top: where git, pointed there, would look for
    # the store's waiver file.
    (other / ".memforge").mkdir()
    (other / ".memforge" / "audit-waivers.yaml").write_text(
        "waived_commits: [0123456789abcdef]\n", encoding="utf-8"
    )
    _commit(other, "memforge: waiver")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    assert _load_waivers(memory).commits == frozenset()


# ---------- a merge result that is not the branch's own change ----------


def _branch_resolve_then_merge(repo: Path, memory: Path) -> None:
    """`side` resolves db-choice in its own resolve commit; `main` moves on;
    the merge is left uncommitted for the test to finish."""
    _git(repo, "branch", "side")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-a"]) == 0
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-commit", "--no-ff", "side")


def test_merge_that_writes_another_superseded_by_than_the_branch_is_a_blocker(store):
    repo, memory = store
    _branch_resolve_then_merge(repo, memory)
    text = (memory / "b.md").read_text(encoding="utf-8")
    assert "mem-a" in text
    (memory / "b.md").write_text(text.replace("mem-a", "mem-c"), encoding="utf-8")
    _commit(repo, "Merge branch side")

    blockers = _blockers(tier2_findings(memory))

    assert any(
        "Merge branch side" in m and "superseded_by write" in m and "b.md" in m
        for m in blockers
    )


def test_merge_that_keeps_the_branch_resolve_beside_a_main_description_edit_audits_clean(store):
    repo, memory = store
    _git(repo, "branch", "side")
    (memory / "b.md").write_text(
        _member("mem-b", topic="db-choice", description="clarified on main"), encoding="utf-8"
    )
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-a"]) == 0
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")
    merged = (memory / "b.md").read_text(encoding="utf-8")
    assert "clarified on main" in merged and "status: superseded" in merged

    findings = tier2_findings(memory)

    assert _blockers(findings) == []
    assert [m for s, m in findings if s == "MAJOR"] == []


# ---------- the branch's commits must be inside the audited window ----------


def test_merge_of_a_branch_change_older_than_the_window_is_a_blocker(store):
    repo, memory = store
    _git(repo, "branch", "side")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    _hand_supersede(repo, memory, "old edit b", days_ago=60)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")

    blockers = _blockers(tier2_findings(memory, audit_window_days=30))

    assert not any("old edit b" in m for m in blockers)
    assert any(
        "Merge branch" in m and "status->superseded transition" in m and "b.md" in m
        for m in blockers
    )


def test_merge_of_a_branch_with_an_old_unrelated_commit_and_a_new_resolve_audits_clean(store):
    repo, memory = store
    _git(repo, "branch", "side")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    (repo / "notes.txt").write_text("old\n", encoding="utf-8")
    _commit(repo, "old notes", days_ago=60)
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-a"]) == 0
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")

    findings = tier2_findings(memory, audit_window_days=30)

    assert _blockers(findings) == []
    assert [m for s, m in findings if s == "MAJOR"] == []
