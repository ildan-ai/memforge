"""Tests for memory-index-gen's v0.4 competing-claim block emission."""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli.index_gen import (
    _truncate_first_line,
    _yaml_escape,
    render_competing_claims_block,
)


def _write(folder: Path, name: str, fm_extra: str, body: str = "Body line.\n") -> None:
    text = (
        "---\n"
        f"name: {name}\n"
        f"description: fixture {name}\n"
        "type: feedback\n"
        f"{fm_extra}"
        "---\n\n"
        f"{body}"
    )
    (folder / f"{name}.md").write_text(text, encoding="utf-8")


# ---------- _yaml_escape ----------


def test_escape_plain_string():
    assert _yaml_escape("plain") == "plain"


def test_escape_string_with_colon():
    assert _yaml_escape("key: value") == "'key: value'"


def test_escape_string_with_hash():
    assert _yaml_escape("a # comment") == "'a # comment'"


def test_escape_string_starting_with_dash():
    assert _yaml_escape("-leading") == "'-leading'"


def test_escape_string_starting_with_bracket():
    assert _yaml_escape("[bracket") == "'[bracket'"


def test_escape_empty_string():
    assert _yaml_escape("") == "''"


def test_escape_string_with_leading_space():
    assert _yaml_escape(" leading") == "' leading'"


def test_escape_single_quote_doubled():
    assert _yaml_escape("can't") == "'can''t'"


def test_escape_bool_lowercase():
    assert _yaml_escape(True) == "true"
    assert _yaml_escape(False) == "false"


# ---------- _truncate_first_line ----------


def test_truncate_short_unchanged():
    assert _truncate_first_line("short body line\n") == "short body line"


def test_truncate_skips_empty_lines():
    body = "\n\n  \n  Real first line\nNext line\n"
    assert _truncate_first_line(body) == "Real first line"


def test_truncate_long_appends_ellipsis():
    long = "a" * 200 + "\n"
    out = _truncate_first_line(long)
    assert out.endswith("...")
    # 117 ascii bytes + "..." == 120 chars
    assert len(out) == 120


def test_truncate_empty_body_returns_empty():
    assert _truncate_first_line("") == ""
    assert _truncate_first_line("\n\n  \n") == ""


def test_truncate_unicode_safe_boundary():
    """A 4-byte emoji at exactly the cap boundary must not produce invalid UTF-8."""
    body = "a" * 116 + "\U0001F600" + "\n"  # 116 ascii + 4-byte emoji = 120 bytes
    out = _truncate_first_line(body)
    # Must be valid UTF-8; either includes the emoji whole or truncates before it.
    out.encode("utf-8")  # raises if invalid


# ---------- render_competing_claims_block ----------


def test_no_block_when_zero_or_one_member(tmp_path: Path):
    _write(tmp_path, "lone",
           "uid: mem-lone\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: solo-topic\n")
    assert render_competing_claims_block(tmp_path) == ""


def test_block_emitted_for_two_members(tmp_path: Path):
    _write(tmp_path, "a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: t1\n")
    _write(tmp_path, "b",
           "uid: mem-b\nstatus: proposed\n"
           "owner: mike\nupdated: 2026-05-09\n"
           "decision_topic: t1\n")
    out = render_competing_claims_block(tmp_path)
    assert out.startswith("# memforge:competing-claims:begin")
    assert out.rstrip().endswith("# memforge:competing-claims:end")
    assert "decision_topic: t1" in out
    assert "state: competing" in out
    assert "uid: mem-a" in out
    assert "uid: mem-b" in out


def test_member_sort_by_updated_desc_then_uid_asc(tmp_path: Path):
    _write(tmp_path, "older_a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: t1\n")
    _write(tmp_path, "newer_b",
           "uid: mem-b\nstatus: proposed\n"
           "owner: mike\nupdated: 2026-05-09\n"
           "decision_topic: t1\n")
    out = render_competing_claims_block(tmp_path)
    # Newer (b) appears before older (a)
    pos_a = out.find("uid: mem-a")
    pos_b = out.find("uid: mem-b")
    assert 0 < pos_b < pos_a


def test_excludes_superseded_dropped_archived(tmp_path: Path):
    _write(tmp_path, "live",
           "uid: mem-live\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: t1\n")
    for status in ("superseded", "dropped", "archived"):
        _write(tmp_path, f"dead_{status}",
               f"uid: mem-{status}\nstatus: {status}\n"
               "owner: mike\nupdated: 2026-05-08\n"
               "decision_topic: t1\n")
    # Only one live member; block not emitted.
    assert render_competing_claims_block(tmp_path) == ""


def test_groups_sorted_alphabetically(tmp_path: Path):
    for topic in ("zebra", "alpha"):
        _write(tmp_path, f"{topic}_1",
               f"uid: mem-{topic}-1\nstatus: active\n"
               f"owner: mike\nupdated: 2026-05-08\n"
               f"decision_topic: {topic}\n")
        _write(tmp_path, f"{topic}_2",
               f"uid: mem-{topic}-2\nstatus: proposed\n"
               f"owner: mike\nupdated: 2026-05-08\n"
               f"decision_topic: {topic}\n")
    out = render_competing_claims_block(tmp_path)
    pos_alpha = out.find("decision_topic: alpha")
    pos_zebra = out.find("decision_topic: zebra")
    assert 0 < pos_alpha < pos_zebra


def test_snooze_in_effect_emits_snoozed_state(tmp_path: Path):
    _write(tmp_path, "a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: snoozed-topic\n")
    _write(tmp_path, "b",
           "uid: mem-b\nstatus: proposed\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: snoozed-topic\n")
    snooze_dir = tmp_path / ".memforge" / "snoozes"
    snooze_dir.mkdir(parents=True)
    (snooze_dir / "snoozed-topic.yaml").write_text(
        "decision_topic: snoozed-topic\n"
        "snoozed_until: 2099-12-31\n"  # far future
        "snooze_reason: testing snooze surface\n"
        "assignee: mike\n"
        "created: 2026-05-08\n"
        "created_by: mike\n",
        encoding="utf-8",
    )
    out = render_competing_claims_block(tmp_path)
    assert "state: snoozed" in out
    assert "snoozed_until:" in out
    assert "snooze_reason:" in out


def test_expired_snooze_treated_as_active_collision(tmp_path: Path):
    _write(tmp_path, "a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: expired-topic\n")
    _write(tmp_path, "b",
           "uid: mem-b\nstatus: proposed\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: expired-topic\n")
    snooze_dir = tmp_path / ".memforge" / "snoozes"
    snooze_dir.mkdir(parents=True)
    (snooze_dir / "expired-topic.yaml").write_text(
        "decision_topic: expired-topic\n"
        "snoozed_until: 2020-01-01\n"  # already past
        "snooze_reason: this snooze is dead\n"
        "assignee: mike\n"
        "created: 2020-01-01\n"
        "created_by: mike\n",
        encoding="utf-8",
    )
    out = render_competing_claims_block(tmp_path)
    # Expired snooze is ignored; block emitted as competing.
    assert "state: competing" in out
    assert "state: snoozed" not in out


def test_canonical_key_order(tmp_path: Path):
    """Per-member field order must match the spec: uid, owner, status, updated,
    first_line, file_path."""
    _write(tmp_path, "a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: t1\n")
    _write(tmp_path, "b",
           "uid: mem-b\nstatus: proposed\n"
           "owner: mike\nupdated: 2026-05-09\n"
           "decision_topic: t1\n")
    out = render_competing_claims_block(tmp_path)
    # Find the first member's fields and check ordering
    first_block = out.split("- uid:", 2)[1]
    keys_in_order = ["uid:", "owner:", "status:", "updated:", "first_line:", "file_path:"]
    # The first key (`uid:`) was already consumed by split; check the remainder
    last_pos = -1
    for k in keys_in_order[1:]:  # skip uid (already split)
        pos = first_block.find(k)
        assert pos > last_pos, f"key {k} out of order"
        last_pos = pos
