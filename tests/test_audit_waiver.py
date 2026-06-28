"""Tests for the v0.7.0 audit-waiver mechanism (R2): the
.memforge/audit-waivers.yaml allowlist that suppresses immutable migration-era
Tier 2 commit-log findings while REPORTING that a waiver was applied."""

from __future__ import annotations

import subprocess
from pathlib import Path

from memforge.cli._concurrency_audit import (
    WaiverSet,
    _is_waived,
    _load_waivers,
    run_concurrency_audit,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _write_waivers(memory: Path, body: str) -> None:
    (memory / ".memforge").mkdir(parents=True, exist_ok=True)
    (memory / ".memforge" / "audit-waivers.yaml").write_text(body, encoding="utf-8")


# ---------- _load_waivers ----------


def test_load_missing_is_empty(tmp_path: Path):
    assert _load_waivers(tmp_path) == WaiverSet(frozenset(), None)


def test_load_malformed_is_empty(tmp_path: Path):
    _write_waivers(tmp_path, "this: is: not: valid:\n  - [\n")
    assert _load_waivers(tmp_path) == WaiverSet(frozenset(), None)


def test_load_non_mapping_is_empty(tmp_path: Path):
    _write_waivers(tmp_path, "- just\n- a\n- list\n")
    assert _load_waivers(tmp_path) == WaiverSet(frozenset(), None)


def test_load_parses_commits_and_cutoff(tmp_path: Path):
    _write_waivers(tmp_path,
                   "waived_commits:\n  - de61c940\n  - 958cdcdb\n"
                   "superseded_transition_waived_before: 2026-01-01\n")
    w = _load_waivers(tmp_path)
    assert w.commits == frozenset({"de61c940", "958cdcdb"})
    assert w.superseded_before == "2026-01-01"


def test_load_short_token_guarded(tmp_path: Path):
    # entries < 7 chars are ignored so a stray token cannot waive by prefix-match
    _write_waivers(tmp_path, "waived_commits: ['abc', 'de61c940']\n")
    assert _load_waivers(tmp_path).commits == frozenset({"de61c940"})


# --- regressions: panel MAJOR findings (gemini-pro threat-model, 2026-06-28) ---


def test_load_scalar_waived_commits_fail_closed(tmp_path: Path):
    # MAJOR-1: a scalar (non-list) waived_commits must NOT raise TypeError; it
    # fails closed to an empty commit set.
    _write_waivers(tmp_path, "waived_commits: 1234567\n")
    assert _load_waivers(tmp_path).commits == frozenset()


def test_load_string_waived_commits_not_iterated_per_char(tmp_path: Path):
    # A bare string (not a list) must not be iterated character-by-character.
    _write_waivers(tmp_path, "waived_commits: de61c940\n")
    assert _load_waivers(tmp_path).commits == frozenset()


def test_load_bad_date_cutoff_rejected(tmp_path: Path):
    # MAJOR-2: a non-date cutoff (bool/int/garbage) must be rejected to None, not
    # stringified into a value that over-waives via string comparison.
    for bad in ("true", "1", "not-a-date", "2026-13-99"):
        _write_waivers(tmp_path, f"superseded_transition_waived_before: {bad}\n")
        assert _load_waivers(tmp_path).superseded_before is None, bad


def test_load_valid_date_cutoff_quoted_and_unquoted(tmp_path: Path):
    _write_waivers(tmp_path, "superseded_transition_waived_before: '2026-01-01'\n")
    assert _load_waivers(tmp_path).superseded_before == "2026-01-01"
    # unquoted ISO date -> safe_load yields a datetime.date; must canonicalize
    _write_waivers(tmp_path, "superseded_transition_waived_before: 2026-01-01\n")
    assert _load_waivers(tmp_path).superseded_before == "2026-01-01"


def test_bad_date_does_not_overspread(tmp_path: Path):
    # End-to-end of MAJOR-2: a garbage cutoff must not waive a real superseded
    # transition (because it coerced to None, not to an over-permissive string).
    memory, _sha = _seed_superseded_transition(tmp_path)
    _write_waivers(memory, "superseded_transition_waived_before: true\n")
    blockers, _, _ = run_concurrency_audit(memory, audit_window_days=3650)
    assert any("transition" in m or "superseded_by write" in m for _, m in blockers)


# ---------- _is_waived ----------


def test_is_waived_explicit_short_prefix():
    w = WaiverSet(frozenset({"de61c940"}), None)
    assert _is_waived("de61c9401122deadbeef", "2026-06-27T00:00:00-04:00",
                      "status:superseded transition", w)


def test_is_waived_non_match():
    w = WaiverSet(frozenset({"de61c940"}), None)
    assert not _is_waived("ffffffffdead", "2026-06-27T00:00:00-04:00",
                          "status:superseded transition", w)


def test_is_waived_cutoff_only_superseded_before_date():
    w = WaiverSet(frozenset(), "2026-01-01")
    # superseded transition before cutoff -> waived
    assert _is_waived("aaaa1111", "2025-12-01T00:00:00-04:00",
                      "status:superseded transition", w)
    # same commit but a non-superseded kind -> NOT waived by the cutoff
    assert not _is_waived("aaaa1111", "2025-12-01T00:00:00-04:00",
                          "decision_topic mutation", w)
    # superseded transition AFTER cutoff -> NOT waived
    assert not _is_waived("aaaa1111", "2026-03-01T00:00:00-04:00",
                          "status:superseded transition", w)


# ---------- integration against a real git repo ----------


def _seed_superseded_transition(tmp_path: Path) -> tuple[Path, str]:
    """Build a repo whose HEAD has a status->superseded transition in a commit
    LACKING a memforge: prefix. Returns (memory_root, evil_commit_sha)."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    memory = tmp_path / "memory"
    memory.mkdir()
    p = memory / "loser.md"
    p.write_text(
        "---\nname: loser\ndescription: loser fixture\ntype: feedback\n"
        "uid: mem-loser\nstatus: active\ndecision_topic: t1\n---\n\nBody.\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    p.write_text(
        p.read_text(encoding="utf-8").replace("status: active", "status: superseded")
        + "superseded_by: [mem-fake]\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "evil: bypass resolve")
    sha = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    return memory, sha


def test_waiver_suppresses_blocker_and_reports(tmp_path: Path):
    memory, sha = _seed_superseded_transition(tmp_path)

    # No waiver yet: the Tier 2 BLOCKER fires.
    blockers, _, warns = run_concurrency_audit(memory, audit_window_days=3650)
    assert any("transition" in m or "superseded_by write" in m for _, m in blockers)

    # Add a waiver for the evil commit (short SHA).
    _write_waivers(memory, f"waived_commits:\n  - {sha[:8]}\n")
    blockers2, _, warns2 = run_concurrency_audit(memory, audit_window_days=3650)

    # BLOCKER gone, and a visible "waived" WARN now reports it.
    assert not any("transition" in m or "superseded_by write" in m for _, m in blockers2)
    assert any("waived" in m for _, m in warns2)
    assert any(sha[:8] in m for _, m in warns2)


def test_waiver_fail_closed_unrelated_sha_does_not_suppress(tmp_path: Path):
    memory, _sha = _seed_superseded_transition(tmp_path)
    _write_waivers(memory, "waived_commits:\n  - 0000000deadbeef\n")
    blockers, _, _ = run_concurrency_audit(memory, audit_window_days=3650)
    # Unrelated waiver entry: the real BLOCKER still fires.
    assert any("transition" in m or "superseded_by write" in m for _, m in blockers)
