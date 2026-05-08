"""Tests for memory-frontmatter-backfill (CLI: memforge.cli.frontmatter_backfill).

Regression coverage for the 2026-05-08 duplicate-keys bug: when a memory
file's YAML frontmatter contained an unquoted colon-space (e.g., a long
`description:` line), yaml.safe_load failed, the backfill saw an empty
fm dict, and apply_change line-appended every required field. On every
subsequent Write/Edit the auto-commit hook re-ran backfill, which kept
appending — producing growing blocks of duplicate keys.

Two-layer fix under test:

1. plan_change skips files whose frontmatter is present but unparseable
   (writes a warning to stderr).
2. apply_change uses a dict-merge + memforge.frontmatter.render round-trip
   instead of line-appending, so the duplicate-keys output is structurally
   impossible.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from memforge.cli.frontmatter_backfill import (
    _frontmatter_present_but_unparseable,
    apply_change,
    plan_change,
)


# ---------- _frontmatter_present_but_unparseable ----------


def test_unparseable_detects_unquoted_colon_space():
    text = (
        "---\n"
        "name: x\n"
        "description: A: B description with embedded colon-space\n"
        "type: feedback\n"
        "---\n"
        "body\n"
    )
    assert _frontmatter_present_but_unparseable(text) is True


def test_unparseable_false_for_duplicate_keys():
    """PyYAML's safe_load silently keeps the last value on duplicate keys
    (no error raised), so the helper correctly returns False. The protection
    against duplicate-key OUTPUT comes from apply_change's dict-merge round
    trip, not from this detector."""
    text = (
        "---\n"
        "name: x\n"
        "uid: mem-2026-05-08-a\n"
        "uid: mem-2026-05-08-b\n"
        "type: feedback\n"
        "---\n"
        "body\n"
    )
    assert _frontmatter_present_but_unparseable(text) is False


def test_unparseable_false_for_well_formed():
    text = "---\nname: x\ntype: feedback\n---\nbody\n"
    assert _frontmatter_present_but_unparseable(text) is False


def test_unparseable_false_for_no_frontmatter():
    assert _frontmatter_present_but_unparseable("just body\n") is False


def test_unparseable_false_for_empty_block():
    text = "---\n---\nbody\n"
    assert _frontmatter_present_but_unparseable(text) is False


# ---------- plan_change ----------


def test_plan_change_skips_unparseable_yaml(tmp_path: Path, capsys):
    f = tmp_path / "feedback_broken.md"
    f.write_text(
        "---\n"
        "name: broken\n"
        "description: this: has unquoted: colon-space everywhere\n"
        "type: feedback\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    result = plan_change(f, tmp_path)
    assert result is None
    captured = capsys.readouterr()
    assert "frontmatter present but YAML parse failed" in captured.err


def test_plan_change_proceeds_on_valid_yaml(tmp_path: Path):
    f = tmp_path / "feedback_valid.md"
    f.write_text(
        "---\n"
        "name: valid\n"
        "description: a clean description without embedded colon-space\n"
        "type: feedback\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    result = plan_change(f, tmp_path)
    assert result is not None
    assert "uid" in result.additions
    assert "tier" in result.additions


# ---------- apply_change ----------


def test_apply_change_round_trips_via_yaml(tmp_path: Path):
    f = tmp_path / "feedback_partial.md"
    f.write_text(
        "---\n"
        "name: partial\n"
        "type: feedback\n"
        "---\n"
        "body line 1\nbody line 2\n",
        encoding="utf-8",
    )
    additions = {
        "uid": "mem-2026-05-08-test",
        "tier": "index",
        "pinned": False,
        "status": "active",
    }
    apply_change(f, additions)
    text = f.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    fm = yaml.safe_load(text[4:end])
    assert fm["name"] == "partial"
    assert fm["uid"] == "mem-2026-05-08-test"
    assert fm["tier"] == "index"
    assert fm["status"] == "active"
    assert fm["pinned"] is False
    body = text[end + 5:]
    assert "body line 1" in body
    assert "body line 2" in body


def test_apply_change_is_idempotent(tmp_path: Path):
    """Running apply_change repeatedly must not grow the file or produce
    duplicate keys.

    This is the direct regression test for the 2026-05-08 bug where the
    auto-commit hook re-ran backfill on every Write/Edit and the output
    grew duplicate frontmatter sections each time. With round-trip render
    the merged dict is keyed (no duplicates possible) and the rendered
    bytes stabilize on the second call."""
    f = tmp_path / "feedback_partial.md"
    f.write_text(
        "---\n"
        "name: x\n"
        "type: feedback\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    additions = {
        "uid": "mem-2026-05-08-x",
        "tier": "index",
        "pinned": False,
    }
    apply_change(f, additions)
    after_first = f.read_text(encoding="utf-8")
    for _ in range(4):
        apply_change(f, additions)
    after_repeat = f.read_text(encoding="utf-8")
    assert after_first == after_repeat
    end = after_repeat.find("\n---\n", 4)
    parsed = yaml.safe_load(after_repeat[4:end])
    assert isinstance(parsed, dict)
    assert parsed["uid"] == "mem-2026-05-08-x"
    assert parsed["tier"] == "index"
    assert parsed["pinned"] is False
    assert list(parsed).count("uid") == 1


def test_apply_change_preserves_existing_fields(tmp_path: Path):
    f = tmp_path / "feedback_keep.md"
    f.write_text(
        "---\n"
        "name: keep\n"
        "uid: mem-2026-04-21-original\n"
        "type: feedback\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    additions = {
        "uid": "mem-2026-05-08-WOULD-OVERWRITE",
        "tier": "index",
    }
    apply_change(f, additions)
    text = f.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    fm = yaml.safe_load(text[4:end])
    assert fm["uid"] == "mem-2026-04-21-original"
    assert fm["tier"] == "index"


def test_apply_change_skips_unparseable_yaml(tmp_path: Path):
    """Defense in depth: even if plan_change is bypassed, apply_change
    must refuse to mutate broken YAML."""
    f = tmp_path / "feedback_broken.md"
    original = (
        "---\n"
        "name: broken\n"
        "description: this: has unquoted: colons\n"
        "type: feedback\n"
        "---\n"
        "body\n"
    )
    f.write_text(original, encoding="utf-8")
    apply_change(f, {"uid": "mem-2026-05-08-x", "tier": "index"})
    assert f.read_text(encoding="utf-8") == original
