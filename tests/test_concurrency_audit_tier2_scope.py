"""Tier 2 (commit-log) checks: prefix scope, parsed authority fields, and the
config, snooze and member-deletion gates (SPEC.md §"Audit invariants for
competing claims", §"The resolve operation", §"Snooze record",
§"Config file")."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memforge.cli import resolve
from memforge.cli._concurrency_audit import run_concurrency_audit, tier2_findings


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
    """A repo whose memory folder holds a two-member group `db-choice`
    (a.md, b.md) and an unrelated c.md, committed as the seed."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "a.md").write_text(_member("mem-a", topic="db-choice"), encoding="utf-8")
    (memory / "b.md").write_text(_member("mem-b", topic="db-choice"), encoding="utf-8")
    (memory / "c.md").write_text(_member("mem-c", topic=None), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path, memory


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _supersede_a(memory: Path) -> None:
    """Resolve the group by hand: a loses to b, residue consistent for Tier 1."""
    (memory / "a.md").write_text(
        _member("mem-a", topic="db-choice", status="superseded",
                extra="superseded_by: [mem-b]\never_multi_member: true\n"),
        encoding="utf-8",
    )
    (memory / "b.md").write_text(
        _member("mem-b", topic="db-choice",
                extra="replaces: [mem-a]\never_multi_member: true\n"),
        encoding="utf-8",
    )


def _severity(findings, severity: str) -> list[str]:
    return [m for s, m in findings if s == severity]


# ---------- prefix scope ----------


def test_resolve_prefix_for_another_topic_is_a_scope_blocker(store):
    repo, memory = store
    _supersede_a(memory)
    _commit(repo, "memforge: resolve unrelated-topic")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("resolve-commit scope violation" in m and "a.md" in m for m in blockers)
    assert any("status->superseded transition" in m and "a.md" in m for m in blockers)


def test_resolve_commit_touching_an_unrelated_file_is_a_scope_blocker(store):
    repo, memory = store
    _supersede_a(memory)
    (memory / "c.md").write_text(
        _member("mem-c", topic=None).replace("Body.", "Edited."), encoding="utf-8"
    )
    _commit(repo, "memforge: resolve db-choice")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("resolve-commit scope violation" in m and "c.md" in m for m in blockers)
    assert not any("a.md" in m or "b.md" in m for m in blockers)


def test_resolve_commit_touching_a_file_outside_the_store_is_a_scope_blocker(store):
    repo, memory = store
    _supersede_a(memory)
    (repo / "README.txt").write_text("unrelated\n", encoding="utf-8")
    _commit(repo, "memforge: resolve db-choice")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("resolve-commit scope violation" in m and "README.txt" in m for m in blockers)


def test_snooze_prefix_does_not_exempt_a_supersede(store):
    repo, memory = store
    _supersede_a(memory)
    _commit(repo, "memforge: snooze db-choice")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("status->superseded transition" in m and "a.md" in m for m in blockers)
    assert any("superseded_by write" in m and "a.md" in m for m in blockers)


def test_reference_resolve_and_snooze_history_audits_clean(store):
    repo, memory = store
    snoozes = memory / ".memforge" / "snoozes"
    snoozes.mkdir(parents=True)
    (snoozes / "db-choice.yaml").write_text(
        "decision_topic: db-choice\nsnoozed_until: 2099-01-01\n"
        "snooze_reason: waiting on the benchmark\nassignee: ops\n"
        "created: 2026-01-01\ncreated_by: ops\n",
        encoding="utf-8",
    )
    _commit(repo, "memforge: snooze db-choice")
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-b"]) == 0
    assert not (snoozes / "db-choice.yaml").exists()

    findings = tier2_findings(memory)
    assert _severity(findings, "BLOCKER") == []
    assert _severity(findings, "MAJOR") == []
    blockers, majors, _ = run_concurrency_audit(memory)
    assert blockers == []


# ---------- authority fields compared as parsed values ----------


@pytest.mark.parametrize("status_line, superseded_by", [
    ("status: 'superseded'", "superseded_by:\n  - mem-b\n"),
    ('status: "superseded"', "superseded_by: [mem-b]\n"),
    ("status: superseded  # resolved in chat", "superseded_by: [ mem-b ]\n"),
    ("status:   superseded", "superseded_by: ['mem-b']\n"),
])
def test_superseded_spellings_without_prefix_are_blockers(store, status_line, superseded_by):
    repo, memory = store
    text = _member("mem-a", topic="db-choice", extra=superseded_by)
    (memory / "a.md").write_text(text.replace("status: active", status_line), encoding="utf-8")
    _commit(repo, "tidy notes")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("status->superseded transition" in m and "a.md" in m for m in blockers)
    assert any("superseded_by write" in m and "a.md" in m for m in blockers)


def test_decision_topic_moved_line_without_prefix_is_a_blocker(store):
    repo, memory = store
    (memory / "a.md").write_text(
        "---\ndecision_topic: other-choice\nname: mem-a\ndescription: mem-a fixture\n"
        "type: feedback\nuid: mem-a\nstatus: active\n---\n\nBody.\n",
        encoding="utf-8",
    )
    _commit(repo, "reorder keys")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("decision_topic mutation" in m and "a.md" in m for m in blockers)


def test_unparseable_frontmatter_after_a_commit_fails_closed(store):
    repo, memory = store
    # The YAML no longer loads, so the member's topic can no longer be read.
    (memory / "a.md").write_text(
        _member("mem-a", topic="db-choice", status="superseded", extra="bad: [unclosed\n"),
        encoding="utf-8",
    )
    _commit(repo, "edit a")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("decision_topic mutation" in m and "a.md" in m for m in blockers)


def test_body_text_that_looks_like_frontmatter_is_not_an_authority_change(store):
    repo, memory = store
    (memory / "c.md").write_text(
        _member("mem-c", topic=None).replace("Body.", "status: superseded\nsuperseded_by: [x]"),
        encoding="utf-8",
    )
    _commit(repo, "notes")

    assert _severity(tier2_findings(memory), "BLOCKER") == []


# ---------- member deletion ----------


def test_group_member_deleted_without_prefix_is_a_blocker(store):
    repo, memory = store
    (memory / "b.md").unlink()
    _commit(repo, "cleanup")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("group member deletion" in m and "b.md" in m for m in blockers)


def test_non_member_deleted_without_prefix_passes(store):
    repo, memory = store
    (memory / "c.md").unlink()
    _commit(repo, "cleanup")

    assert _severity(tier2_findings(memory), "BLOCKER") == []


def test_group_member_moved_within_the_store_is_not_a_deletion(store):
    repo, memory = store
    (memory / "sub").mkdir()
    _git(repo, "mv", "memory/b.md", "memory/sub/b.md")
    _commit(repo, "move b")

    assert _severity(tier2_findings(memory), "BLOCKER") == []


# ---------- config and snooze gates ----------


@pytest.mark.parametrize("where", ["store", "repo"])
def test_config_added_without_prefix_is_a_blocker(store, where):
    repo, memory = store
    base = memory if where == "store" else repo
    (base / ".memforge").mkdir()
    (base / ".memforge" / "config.yaml").write_text("audit:\n  audit_window_days: 30\n",
                                                    encoding="utf-8")
    _commit(repo, "memory: filesystem write")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("config edit" in m and "config.yaml" in m for m in blockers)


def test_config_commit_with_another_file_is_a_blocker(store):
    repo, memory = store
    (memory / ".memforge").mkdir()
    (memory / ".memforge" / "config.yaml").write_text("audit: {}\n", encoding="utf-8")
    (memory / "c.md").write_text(
        _member("mem-c", topic=None).replace("Body.", "Edited."), encoding="utf-8"
    )
    _commit(repo, "memforge: config")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("config edit" in m and "config.yaml" in m for m in blockers)


def test_config_commit_touching_only_the_config_passes(store):
    repo, memory = store
    (memory / ".memforge").mkdir()
    (memory / ".memforge" / "config.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "memforge: config")

    findings = tier2_findings(memory)
    assert _severity(findings, "BLOCKER") == []
    assert _severity(findings, "MAJOR") == []


def test_snooze_added_without_prefix_is_a_major(store):
    repo, memory = store
    (memory / ".memforge" / "snoozes").mkdir(parents=True)
    (memory / ".memforge" / "snoozes" / "db-choice.yaml").write_text(
        "decision_topic: db-choice\n", encoding="utf-8"
    )
    _commit(repo, "memory: filesystem write")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert any("snooze edit" in m and "db-choice.yaml" in m for m in majors)


def test_snooze_commit_for_another_topic_is_a_major(store):
    repo, memory = store
    (memory / ".memforge" / "snoozes").mkdir(parents=True)
    (memory / ".memforge" / "snoozes" / "db-choice.yaml").write_text(
        "decision_topic: db-choice\n", encoding="utf-8"
    )
    _commit(repo, "memforge: snooze other-choice")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert any("snooze edit" in m and "db-choice.yaml" in m for m in majors)
