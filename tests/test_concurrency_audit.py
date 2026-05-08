"""Tests for memforge.cli._concurrency_audit (v0.4 Tier 1 + Tier 2 invariants)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memforge.cli._concurrency_audit import (
    _is_valid_slug,
    collect_state,
    run_concurrency_audit,
    tier1_findings,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _write_member(folder: Path, name: str, fm_extra: str = "") -> Path:
    p = folder / f"{name}.md"
    p.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} fixture\n"
        "type: feedback\n"
        f"{fm_extra}"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    return p


# ---------- slug pipeline ----------


def test_slug_valid():
    assert _is_valid_slug("good-slug")
    assert _is_valid_slug("a")
    assert _is_valid_slug("topic-1")


def test_slug_rejects_uppercase():
    assert not _is_valid_slug("BadSlug")


def test_slug_rejects_underscores():
    assert not _is_valid_slug("under_score")


def test_slug_rejects_reserved_names():
    for reserved in ("con", "aux", "nul", "prn", "com1", "lpt9", ".", ".."):
        assert not _is_valid_slug(reserved), f"{reserved!r} should be reserved"


def test_slug_rejects_reserved_with_extension():
    assert not _is_valid_slug("con.txt")
    assert not _is_valid_slug("nul.foo.bar")


def test_slug_rejects_overlong():
    # 65-char slug exceeds 64-byte cap
    assert not _is_valid_slug("a" + "-b" * 32 + "-cc")


# ---------- Tier 1: status enumeration ----------


def test_invalid_status_blocker(tmp_path: Path):
    _write_member(tmp_path, "rejected_status",
                  fm_extra="status: rejected\n")
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("invalid status value 'rejected'" in m for m in msgs)


def test_valid_status_no_blocker(tmp_path: Path):
    _write_member(tmp_path, "ok_status", fm_extra="status: active\n")
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert not any("invalid status" in m for m in msgs)


# ---------- Tier 1: asymmetric supersession ----------


def test_asymmetric_supersession_blocker_dangling(tmp_path: Path):
    _write_member(tmp_path, "orphan",
                  fm_extra=(
                      "uid: mem-orphan\n"
                      "status: superseded\n"
                      "superseded_by: [mem-does-not-exist]\n"
                      "decision_topic: orphan-topic\n"
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("not the sole status:active member" in m for m in msgs)


def test_asymmetric_supersession_blocker_wrong_length(tmp_path: Path):
    _write_member(tmp_path, "winner",
                  fm_extra=(
                      "uid: mem-winner\n"
                      "status: active\n"
                      "decision_topic: t1\n"
                      "replaces: [mem-loser]\n"
                  ))
    _write_member(tmp_path, "loser",
                  fm_extra=(
                      "uid: mem-loser\n"
                      "status: superseded\n"
                      "decision_topic: t1\n"
                      "superseded_by: []\n"  # length 0, should be 1
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("superseded_by has length 0" in m or "expected 1" in m for m in msgs)


def test_symmetric_supersession_passes(tmp_path: Path):
    _write_member(tmp_path, "winner",
                  fm_extra=(
                      "uid: mem-winner\n"
                      "status: active\n"
                      "decision_topic: t1\n"
                      "replaces: [mem-loser]\n"
                      "ever_multi_member: true\n"
                  ))
    _write_member(tmp_path, "loser",
                  fm_extra=(
                      "uid: mem-loser\n"
                      "status: superseded\n"
                      "decision_topic: t1\n"
                      "superseded_by: [mem-winner]\n"
                      "ever_multi_member: true\n"
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert not any("asymmetry" in m or "not the sole" in m for m in msgs)


# ---------- Tier 1: cross-topic + dangling replaces ----------


def test_cross_topic_replaces_blocker(tmp_path: Path):
    _write_member(tmp_path, "a",
                  fm_extra=("uid: mem-a\nstatus: active\ndecision_topic: topic-a\n"))
    _write_member(tmp_path, "b_cross",
                  fm_extra=(
                      "uid: mem-b\nstatus: active\ndecision_topic: topic-b\n"
                      "replaces: [mem-a]\n"  # cross-topic
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("cross-topic replaces forbidden" in m for m in msgs)


def test_dangling_replaces_blocker(tmp_path: Path):
    _write_member(tmp_path, "winner",
                  fm_extra=(
                      "uid: mem-w\nstatus: active\ndecision_topic: t1\n"
                      "replaces: [does-not-exist]\n"
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("dangling" in m for m in msgs)


# ---------- Tier 1: exactly-one-active for ever_multi_member groups ----------


def test_exactly_one_active_blocker_zero(tmp_path: Path):
    _write_member(tmp_path, "a",
                  fm_extra=(
                      "uid: mem-a\nstatus: superseded\ndecision_topic: t1\n"
                      "superseded_by: [mem-b]\never_multi_member: true\n"
                  ))
    _write_member(tmp_path, "b",
                  fm_extra=(
                      "uid: mem-b\nstatus: superseded\ndecision_topic: t1\n"
                      "superseded_by: [mem-a]\never_multi_member: true\n"
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("ever_multi_member is true but 0 active members" in m for m in msgs)


# ---------- Tier 1: replaces cardinality cap ----------


def test_replaces_cardinality_cap_major(tmp_path: Path):
    # winner with 21 members in replaces (over the cap of 20)
    refs = [f"mem-r{i}" for i in range(21)]
    fm_extra = (
        "uid: mem-w\nstatus: active\ndecision_topic: t1\n"
        f"replaces: [{', '.join(refs)}]\n"
    )
    _write_member(tmp_path, "winner", fm_extra=fm_extra)
    # also seed targets so the dangling check doesn't drown it
    for i in range(21):
        _write_member(tmp_path, f"r{i}",
                      fm_extra=(
                          f"uid: mem-r{i}\nstatus: superseded\n"
                          "decision_topic: t1\nsuperseded_by: [mem-w]\n"
                      ))
    _, majors, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in majors]
    assert any("cardinality 21 > 20 cap" in m for m in msgs)


# ---------- Tier 1: alias mutuality + cycle ----------


def test_alias_non_mutual_warn(tmp_path: Path):
    _write_member(tmp_path, "a",
                  fm_extra=(
                      "uid: mem-a\nstatus: active\ndecision_topic: topic-a\n"
                      "topic_aliases: [topic-b]\n"
                  ))
    _write_member(tmp_path, "b",
                  fm_extra=(
                      "uid: mem-b\nstatus: active\ndecision_topic: topic-b\n"
                  ))
    _, _, warns = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in warns]
    assert any("does not list 'topic-a' back" in m for m in msgs)


def test_alias_cycle_blocker(tmp_path: Path):
    # 3-way mutual cycle: a<->b, b<->c, c<->a
    _write_member(tmp_path, "a",
                  fm_extra=(
                      "uid: mem-a\nstatus: active\ndecision_topic: topic-a\n"
                      "topic_aliases: [topic-b, topic-c]\n"
                  ))
    _write_member(tmp_path, "b",
                  fm_extra=(
                      "uid: mem-b\nstatus: active\ndecision_topic: topic-b\n"
                      "topic_aliases: [topic-a, topic-c]\n"
                  ))
    _write_member(tmp_path, "c",
                  fm_extra=(
                      "uid: mem-c\nstatus: active\ndecision_topic: topic-c\n"
                      "topic_aliases: [topic-a, topic-b]\n"
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert any("alias cycle detected" in m for m in msgs)


def test_alias_two_way_mutual_no_cycle(tmp_path: Path):
    """Two-way mutual aliases (A<->B) are normal mutuality, not a cycle."""
    _write_member(tmp_path, "a",
                  fm_extra=(
                      "uid: mem-a\nstatus: active\ndecision_topic: topic-a\n"
                      "topic_aliases: [topic-b]\n"
                  ))
    _write_member(tmp_path, "b",
                  fm_extra=(
                      "uid: mem-b\nstatus: active\ndecision_topic: topic-b\n"
                      "topic_aliases: [topic-a]\n"
                  ))
    blockers, _, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in blockers]
    assert not any("alias cycle" in m for m in msgs)


def test_alias_cap_major(tmp_path: Path):
    aliases = [f"topic-{i}" for i in range(11)]  # 11 > 10 cap
    _write_member(tmp_path, "a",
                  fm_extra=(
                      "uid: mem-a\nstatus: active\ndecision_topic: topic-anchor\n"
                      f"topic_aliases: [{', '.join(aliases)}]\n"
                  ))
    _, majors, _ = run_concurrency_audit(tmp_path, skip_tier2=True)
    msgs = [m for _, m in majors]
    assert any("topic_aliases cardinality 11" in m for m in msgs)


# ---------- Tier 2: status transition outside resolve commit ----------


def test_tier2_status_transition_without_prefix_blocker(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()
    p = _write_member(memory, "loser",
                      fm_extra=(
                          "uid: mem-loser\nstatus: active\n"
                          "decision_topic: t1\n"
                      ))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    # Now flip to superseded in a non-resolve commit
    p.write_text(
        p.read_text(encoding="utf-8").replace("status: active", "status: superseded")
        + "superseded_by: [mem-fake]\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "evil: bypass resolve")

    blockers, _, _ = run_concurrency_audit(memory, audit_window_days=30)
    msgs = [m for _, m in blockers]
    # Tier 2 fires either status->superseded or superseded_by write
    assert any("transition" in m or "superseded_by write" in m for m in msgs)


def test_tier2_resolve_prefix_passes(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()
    p = _write_member(memory, "loser",
                      fm_extra=(
                          "uid: mem-loser\nstatus: active\n"
                          "decision_topic: t1\n"
                      ))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    p.write_text(
        p.read_text(encoding="utf-8").replace("status: active", "status: superseded")
        + "superseded_by: [mem-fake]\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "memforge: resolve t1")

    blockers, _, _ = run_concurrency_audit(memory, audit_window_days=30)
    msgs = [m for _, m in blockers]
    # Tier 2 should NOT fire for the recognized prefix
    assert not any("without `memforge: resolve`" in m for m in msgs)
