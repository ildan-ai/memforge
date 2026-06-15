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


def test_escape_date_typed_string_round_trips_as_str():
    # A bare ISO date (the normal form of `updated`/`created`) must be quoted so
    # a reader's yaml.safe_load does NOT retype it to datetime.date. These member
    # fields are semantically strings.
    import yaml

    for value in ("2026-05-09", "2026-1-1", "2026-12-31T23:59:59Z", "2026-05-09 10:00:00"):
        esc = _yaml_escape(value)
        parsed = yaml.safe_load(f"k: {esc}")["k"]
        assert isinstance(parsed, str), f"{value!r} retyped to {type(parsed).__name__}"
        assert parsed == value
    # a non-date string stays unquoted (no over-quoting)
    assert _yaml_escape("owner-name") == "owner-name"


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


# ---------- control-char / newline escaping (BLOCKER claimblock-01 +
#            MAJOR yaml-escape-newline-02) ----------

import yaml  # noqa: E402


def _reparse_block(block: str) -> list:
    """Strip the BEGIN/END comment markers and yaml.safe_load the body.
    Returns the parsed list-of-mappings. Raises if the block is unparseable."""
    inner_lines = []
    for line in block.splitlines():
        if line.strip() in (
            "# memforge:competing-claims:begin",
            "# memforge:competing-claims:end",
        ):
            continue
        inner_lines.append(line)
    return yaml.safe_load("\n".join(inner_lines))


def test_yaml_escape_forces_double_quote_on_newline():
    """A value with an interior newline must emit a single-line double-quoted
    scalar with the newline escaped as \\n, not a bare multi-line value."""
    out = _yaml_escape("ev\nil.md")
    assert "\n" not in out, "escaped value must be a single physical line"
    assert out == '"ev\\nil.md"'
    # And it must round-trip back to the original under yaml.safe_load.
    assert yaml.safe_load(out) == "ev\nil.md"


def test_yaml_escape_handles_cr_tab_nul():
    for raw in ("a\rb", "a\tb", "a\x00b", "a\x1bb"):
        out = _yaml_escape(raw)
        assert "\n" not in out and "\r" not in out
        assert yaml.safe_load(out) == raw


def test_block_reparses_with_newline_filename(tmp_path: Path):
    """BLOCKER claimblock-01: a hostile filename containing a newline must NOT
    corrupt the whole competing-claims block. The full block must still
    yaml.safe_load into a well-formed list, and the legit member survives."""
    # Legit competing member.
    _write(tmp_path, "good_a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: t1\n")
    # Hostile file whose NAME contains a literal newline, same topic so it joins
    # the group as a second live member.
    hostile_name = "ev\nil"
    hostile_text = (
        "---\n"
        "name: hostile\n"
        "description: fixture\n"
        "type: feedback\n"
        "uid: mem-b\nstatus: proposed\nowner: mike\n"
        "updated: 2026-05-09\ndecision_topic: t1\n"
        "---\n\nBody.\n"
    )
    (tmp_path / f"{hostile_name}.md").write_text(hostile_text, encoding="utf-8")

    out = render_competing_claims_block(tmp_path)
    # The whole block must re-parse cleanly (no ScannerError).
    parsed = _reparse_block(out)
    assert isinstance(parsed, list) and len(parsed) == 1
    group = parsed[0]
    assert group["decision_topic"] == "t1"
    member_uids = {m["uid"] for m in group["members"]}
    assert {"mem-a", "mem-b"} <= member_uids
    # The newline-bearing file_path round-trips intact for the hostile member.
    fps = [m["file_path"] for m in group["members"]]
    assert any("\n" in fp for fp in fps), "newline must survive inside the scalar"


def test_block_reparses_with_multiline_owner(tmp_path: Path):
    """MAJOR yaml-escape-newline-02: a multiline `owner` (YAML block scalar in
    hostile frontmatter) must not break the one-field-per-line block."""
    _write(tmp_path, "a",
           "uid: mem-a\nstatus: active\n"
           "owner: mike\nupdated: 2026-05-08\n"
           "decision_topic: t1\n")
    # owner as a YAML block scalar -> parses to a string containing a newline.
    _write(tmp_path, "b",
           "uid: mem-b\nstatus: proposed\n"
           "owner: |\n  line one\n  line two\n"
           "updated: 2026-05-09\n"
           "decision_topic: t1\n")
    out = render_competing_claims_block(tmp_path)
    parsed = _reparse_block(out)
    assert isinstance(parsed, list) and len(parsed) == 1
    owners = {m["uid"]: m["owner"] for m in parsed[0]["members"]}
    assert "\n" in owners["mem-b"], "multiline owner must round-trip with its newline"
    # The raw block must keep one field per physical line: each member's uid
    # line (`    - uid: ...`) appears once, so the count equals the member count.
    uid_lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("- uid:")]
    assert len(uid_lines) == 2
