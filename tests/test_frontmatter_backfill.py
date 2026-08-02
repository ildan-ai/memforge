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
from memforge.frontmatter import parse as _mf_parse


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


# ---------- cmd_run summary count (MINOR backfill-01) ----------


def test_cmd_run_summary_matches_planned_count(tmp_path: Path, capsys, monkeypatch):
    """The final summary 'would change' count is tracked from the single
    plan_change pass, not recomputed by re-invoking plan_change three more
    times per file. Regression for lifecycle/backfill-01 (redundant I/O +
    latent None-deref). We assert the printed count matches the number of
    files that need additions and that plan_change is not called again after
    the planning loop."""
    from memforge.cli import frontmatter_backfill as fb

    folder = tmp_path / "memory"
    folder.mkdir()
    # Two minimal-frontmatter files that will need additions (no uid/tier/etc).
    for n in ("a", "b"):
        (folder / f"{n}.md").write_text(
            f"---\nname: {n}\ndescription: d\n---\n\nBody.\n", encoding="utf-8"
        )

    # Count plan_change invocations; wrap the real function.
    calls = {"n": 0}
    real_plan = fb.plan_change

    def counting_plan(path, root):
        calls["n"] += 1
        return real_plan(path, root)

    monkeypatch.setattr(fb, "plan_change", counting_plan)

    rc = fb.cmd_run([folder], apply=False, limit=0)
    assert rc == 0
    out = capsys.readouterr().out
    # Both files need additions; summary reports 2.
    assert "would change: 2" in out
    # plan_change called exactly once per file (the planning loop), NOT 4x.
    assert calls["n"] == 2, f"plan_change invoked {calls['n']} times (expected 2)"


# ---- access default must not contradict operator-set sensitivity ---------


def _write(p, sensitivity=None):
    lines = ["---", "name: n", "description: d", "type: reference"]
    if sensitivity:
        lines.append(f"sensitivity: {sensitivity}")
    lines += ["---", "", "body", ""]
    p.write_text("\n".join(lines), encoding="utf-8")


def test_backfill_does_not_fabricate_access_on_restricted(tmp_path):
    """Backfill must not invent an OPEN access label on a file whose
    operator-set sensitivity is restrictive.

    Previously `access: internal` was written unconditionally, so a file
    declaring `sensitivity: restricted` came out asserting both -- two
    classification fields disagreeing, one of them fabricated by tooling
    rather than chosen by the operator.

    Not a live exposure today: `_access_ok` treats an absent access list and
    an `internal` label identically, and sensitivity is enforced by a separate
    ANDed gate. It becomes one the moment the no-label default is changed to
    fail-closed, at which point every restricted file carrying a fabricated
    open label would stay visible while appearing gated.
    """
    from memforge.cli.frontmatter_backfill import plan_change

    for sens in ("restricted", "privileged"):
        f = tmp_path / f"{sens}.md"
        _write(f, sens)
        change = plan_change(f, tmp_path)
        assert change is not None
        assert "access" not in change.additions, (
            f"backfill fabricated access={change.additions.get('access')!r} "
            f"on a sensitivity:{sens} file"
        )


def test_backfill_still_defaults_access_on_non_restrictive(tmp_path):
    """Non-regression: the common path keeps its `access: internal` default.

    The fix must narrow the behaviour for restrictive sensitivities only. A
    fix that stopped defaulting access everywhere would change classification
    for every ordinary memory in the store.
    """
    from memforge.cli.frontmatter_backfill import plan_change

    f = tmp_path / "internal.md"
    _write(f, "internal")
    assert plan_change(f, tmp_path).additions.get("access") == "internal"

    g = tmp_path / "unset.md"
    _write(g, None)
    add = plan_change(g, tmp_path).additions
    assert add.get("sensitivity") == "internal"
    assert add.get("access") == "internal"


def test_backfill_access_respects_sensitivity_it_adds_in_same_pass(tmp_path):
    """When backfill itself supplies `sensitivity` in the same pass, the
    access decision must read that value, not the absent original.

    Guards the ordering dependency: `sensitivity` is defaulted a few lines
    above `access`, so reading only `fm` would miss it.
    """
    from memforge.cli import frontmatter_backfill as fb

    f = tmp_path / "x.md"
    _write(f, None)
    add = fb.plan_change(f, tmp_path).additions
    # default sensitivity is non-restrictive, so access is still set
    assert add.get("sensitivity") == "internal"
    assert add.get("access") == "internal"


# ---------- nested metadata.type hoist ----------


def _write_memory(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_backfill_hoists_nested_metadata_type(tmp_path):
    """Lossless promotion: the value is already in the file, so no guessing.

    The harness-written `metadata.type` shape parses cleanly and therefore
    survives a parse-only gate, but it is not the spec's top-level `type`.
    """
    p = _write_memory(
        tmp_path,
        "nested.md",
        "---\nname: nested\ndescription: d\nmetadata:\n"
        "  type: feedback\n  node_type: memory\n---\n\nbody\n",
    )
    plan = plan_change(p, tmp_path)
    assert plan is not None
    assert plan.additions.get("type") == "feedback"

    apply_change(p, plan.additions)
    fm, _ = _mf_parse(p.read_text(encoding="utf-8"))
    assert fm["type"] == "feedback"
    # Additive only: the metadata block and its other keys survive.
    assert fm["metadata"]["node_type"] == "memory"
    assert fm["metadata"]["type"] == "feedback"


def test_backfill_does_not_invent_a_type_when_none_exists(tmp_path):
    """`type` is a semantic classification; guessing one is worse than absent.

    Files using a third shape (a metadata block with node_type but no type at
    all) must stay untouched for operator judgement.
    """
    p = _write_memory(
        tmp_path,
        "notype.md",
        "---\nname: notype\ndescription: d\nmetadata:\n  node_type: memory\n---\n\nbody\n",
    )
    plan = plan_change(p, tmp_path)
    additions = plan.additions if plan is not None else {}
    assert "type" not in additions


def test_backfill_ignores_a_non_spec_nested_type(tmp_path):
    """A nested value outside the spec's four types is not hoisted."""
    p = _write_memory(
        tmp_path,
        "bogus.md",
        "---\nname: bogus\ndescription: d\nmetadata:\n  type: notathing\n---\n\nbody\n",
    )
    plan = plan_change(p, tmp_path)
    additions = plan.additions if plan is not None else {}
    assert "type" not in additions


def test_backfill_leaves_an_existing_top_level_type_alone(tmp_path):
    p = _write_memory(
        tmp_path,
        "already.md",
        "---\nname: already\ndescription: d\ntype: project\nmetadata:\n"
        "  type: feedback\n---\n\nbody\n",
    )
    plan = plan_change(p, tmp_path)
    additions = plan.additions if plan is not None else {}
    assert "type" not in additions
    if plan is not None:
        apply_change(p, plan.additions)
    fm, _ = _mf_parse(p.read_text(encoding="utf-8"))
    assert fm["type"] == "project"
