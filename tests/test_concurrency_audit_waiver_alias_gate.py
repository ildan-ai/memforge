"""Tier 2 (commit-log) gates on the audit-waiver file and on `topic_aliases`
(SPEC.md §"Audit waivers", §"Multi-agent concurrency: competing claims",
§"Audit invariants for competing claims")."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memforge.cli._concurrency_audit import _load_waivers, tier2_findings


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _member(uid: str, *, topic: str | None, status: str = "active", extra: str = "") -> str:
    lines = [
        "---",
        f"name: {uid}",
        f"description: {uid} fixture",
        "type: feedback",
        f"uid: {uid}",
        f"status: {status}",
    ]
    if topic is not None:
        lines.append(f"decision_topic: {topic}")
    return "\n".join(lines) + "\n" + extra + "---\n\nBody.\n"


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose memory folder holds two one-member groups, `db-choice`
    (a.md, its anchor) and `cache` (d.md, its anchor), and an unrelated c.md."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "a.md").write_text(_member("mem-a", topic="db-choice"), encoding="utf-8")
    (memory / "d.md").write_text(_member("mem-d", topic="cache"), encoding="utf-8")
    (memory / "c.md").write_text(_member("mem-c", topic=None), encoding="utf-8")
    _commit(tmp_path, "seed")
    return tmp_path, memory


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _waiver_file(memory: Path) -> Path:
    return memory / ".memforge" / "audit-waivers.yaml"


def _write_waiver(memory: Path, shas: list[str]) -> None:
    path = _waiver_file(memory)
    path.parent.mkdir(parents=True, exist_ok=True)
    listed = "".join(f"\n  - {s}" for s in shas) or " []"
    path.write_text(f"reason: migration-era commits\nwaived_commits:{listed}\n", encoding="utf-8")


def _supersede_a(memory: Path) -> None:
    (memory / "a.md").write_text(
        _member("mem-a", topic="db-choice", status="superseded",
                extra="superseded_by: [mem-x]\n"),
        encoding="utf-8",
    )


def _edit_c(memory: Path) -> None:
    (memory / "c.md").write_text(
        _member("mem-c", topic=None).replace("Body.", "Edited."), encoding="utf-8"
    )


def _severity(findings, severity: str) -> list[str]:
    return [m for s, m in findings if s == severity]


# ---------- waiver-file commits ----------


def test_waiver_added_without_prefix_is_a_blocker(store):
    repo, memory = store
    _write_waiver(memory, [])
    _commit(repo, "memory: filesystem write")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("waiver edit" in m and "audit-waivers.yaml" in m for m in blockers)


def test_waiver_changed_and_deleted_without_prefix_are_blockers(store):
    repo, memory = store
    _write_waiver(memory, [])
    _commit(repo, "memforge: waiver")
    _write_waiver(memory, ["0000000deadbeef"])
    changed = _commit(repo, "update waivers")
    _waiver_file(memory).unlink()
    deleted = _commit(repo, "memforge: config")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("waiver edit" in m and changed[:8] in m for m in blockers)
    assert any("waiver edit" in m and deleted[:8] in m for m in blockers)


def test_waiver_commit_with_another_file_is_a_blocker(store):
    repo, memory = store
    _write_waiver(memory, [])
    _edit_c(memory)
    _commit(repo, "memforge: waiver")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("waiver edit" in m and "audit-waivers.yaml" in m for m in blockers)


def test_waiver_prefix_needs_a_word_boundary(store):
    repo, memory = store
    _write_waiver(memory, [])
    _commit(repo, "memforge: waivers")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("waiver edit" in m for m in blockers)


def test_waiver_only_commit_with_prefix_passes(store):
    repo, memory = store
    _write_waiver(memory, [])
    _commit(repo, "memforge: waiver")
    _write_waiver(memory, ["0000000deadbeef"])
    _commit(repo, "memforge: waiver add migration commit")
    _waiver_file(memory).unlink()
    _commit(repo, "memforge: waiver")

    findings = tier2_findings(memory)
    assert _severity(findings, "BLOCKER") == []
    assert _severity(findings, "MAJOR") == []


def test_waiver_cannot_waive_the_commit_that_introduced_it(store):
    """An unprefixed commit adds the waiver file and supersedes a.md; a later,
    well-formed waiver commit lists it. Neither of its findings is waived."""
    repo, memory = store
    _write_waiver(memory, [])
    _supersede_a(memory)
    evil = _commit(repo, "bulk edit")
    _write_waiver(memory, [evil])
    _commit(repo, "memforge: waiver")

    findings = tier2_findings(memory)
    blockers = _severity(findings, "BLOCKER")
    assert any("waiver edit" in m and evil[:8] in m for m in blockers)
    assert any("status->superseded transition" in m and evil[:8] in m for m in blockers)


def test_waiver_cannot_waive_a_later_waiver_commit(store):
    """A later commit that changes the waiver file is never waivable, even when
    the waiver at HEAD lists it and its other findings."""
    repo, memory = store
    _write_waiver(memory, [])
    _commit(repo, "memforge: waiver")
    _write_waiver(memory, ["0000000deadbeef"])
    _supersede_a(memory)
    later = _commit(repo, "tidy")
    _write_waiver(memory, [later])
    _commit(repo, "memforge: waiver")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("waiver edit" in m and later[:8] in m for m in blockers)
    assert any("superseded_by write" in m and later[:8] in m for m in blockers)


def test_committed_waiver_still_waives_an_ordinary_commit(store):
    repo, memory = store
    _supersede_a(memory)
    evil = _commit(repo, "migration")
    _write_waiver(memory, [evil[:8]])
    _commit(repo, "memforge: waiver")

    findings = tier2_findings(memory)
    assert _severity(findings, "BLOCKER") == []
    assert any("waived" in m and evil[:8] in m for m in _severity(findings, "WARN"))
    assert not any("work-tree" in m for m in _severity(findings, "WARN"))


# ---------- only the waiver committed at HEAD counts ----------


def test_untracked_waiver_waives_nothing(store):
    repo, memory = store
    _supersede_a(memory)
    evil = _commit(repo, "migration")
    _write_waiver(memory, [evil])

    assert _load_waivers(memory).commits == frozenset()
    findings = tier2_findings(memory)
    assert any("status->superseded transition" in m for m in _severity(findings, "BLOCKER"))
    assert any("work-tree" in m and "audit-waivers.yaml" in m for m in _severity(findings, "WARN"))


def test_locally_edited_waiver_waives_nothing(store):
    repo, memory = store
    _supersede_a(memory)
    evil = _commit(repo, "migration")
    _write_waiver(memory, ["0000000deadbeef"])
    _commit(repo, "memforge: waiver")
    _write_waiver(memory, [evil])

    assert _load_waivers(memory).commits == frozenset({"0000000deadbeef"})
    findings = tier2_findings(memory)
    assert any("status->superseded transition" in m for m in _severity(findings, "BLOCKER"))
    assert any("work-tree" in m and "audit-waivers.yaml" in m for m in _severity(findings, "WARN"))


def test_committed_link_as_waiver_file_waives_nothing(store, tmp_path_factory):
    repo, memory = store
    _supersede_a(memory)
    evil = _commit(repo, "migration")
    outside = tmp_path_factory.mktemp("outside") / "w.yaml"
    outside.write_text(f"waived_commits:\n  - {evil}\n", encoding="utf-8")
    _waiver_file(memory).parent.mkdir(parents=True)
    _waiver_file(memory).symlink_to(outside)
    _commit(repo, "memforge: waiver")

    assert _load_waivers(memory).commits == frozenset()
    assert any("status->superseded transition" in m
               for m in _severity(tier2_findings(memory), "BLOCKER"))


def test_locally_deleted_waiver_is_reported(store):
    repo, memory = store
    _write_waiver(memory, ["0000000deadbeef"])
    _commit(repo, "memforge: waiver")
    _waiver_file(memory).unlink()

    findings = tier2_findings(memory)
    assert any("work-tree" in m and "audit-waivers.yaml" in m for m in _severity(findings, "WARN"))


# ---------- topic_aliases on a current anchor ----------


def _alias_a(memory: Path, aliases: str = "[cache]") -> None:
    (memory / "a.md").write_text(
        _member("mem-a", topic="db-choice", extra=f"topic_aliases: {aliases}\n"),
        encoding="utf-8",
    )


def test_alias_added_without_prefix_is_a_major(store):
    repo, memory = store
    _alias_a(memory)
    _commit(repo, "link the cache topic")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert any("alias edit" in m and "a.md" in m for m in majors)


def test_alias_removed_in_block_list_spelling_without_prefix_is_a_major(store):
    repo, memory = store
    _alias_a(memory, "\n  - cache")
    _commit(repo, "memforge: alias db-choice")
    _alias_a(memory, "[]")
    _commit(repo, "cleanup")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert len([m for m in majors if "alias edit" in m]) == 1


def test_alias_commit_with_another_file_is_a_major(store):
    repo, memory = store
    _alias_a(memory)
    _edit_c(memory)
    _commit(repo, "memforge: alias db-choice")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert any("alias edit" in m and "a.md" in m for m in majors)


def test_alias_commit_for_another_topic_is_a_major(store):
    repo, memory = store
    _alias_a(memory)
    _commit(repo, "memforge: alias cache")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert any("alias edit" in m and "a.md" in m for m in majors)


def test_alias_single_file_commit_for_its_topic_passes(store):
    repo, memory = store
    _alias_a(memory)
    _commit(repo, "memforge: alias db-choice")
    (memory / "d.md").write_text(
        _member("mem-d", topic="cache", extra="topic_aliases: [db-choice]\n"),
        encoding="utf-8",
    )
    _commit(repo, "memforge: alias cache")

    findings = tier2_findings(memory)
    assert _severity(findings, "MAJOR") == []
    assert _severity(findings, "BLOCKER") == []


def test_alias_in_the_resolve_commit_for_its_topic_passes(store):
    repo, memory = store
    _alias_a(memory)
    _commit(repo, "memforge: resolve db-choice")

    findings = tier2_findings(memory)
    assert _severity(findings, "MAJOR") == []
    assert _severity(findings, "BLOCKER") == []


def test_alias_on_a_memory_that_is_not_a_current_anchor_is_not_flagged(store):
    repo, memory = store
    (memory / "c.md").write_text(
        _member("mem-c", topic=None, extra="topic_aliases: [cache]\n"), encoding="utf-8"
    )
    _commit(repo, "notes")

    assert not any("alias edit" in m for m in _severity(tier2_findings(memory), "MAJOR"))
