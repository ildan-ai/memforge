"""Tests for memforge.frontmatter (parse, has_frontmatter, render).

Covers the BLOCKER-closer module: every tool now imports parse() from here.
The contract is documented in src/memforge/frontmatter.py.
"""

from __future__ import annotations

from memforge.frontmatter import has_frontmatter, parse, render


# ---------- has_frontmatter ----------


def test_has_frontmatter_true_for_well_formed():
    text = "---\nname: x\ntype: user\n---\nbody\n"
    assert has_frontmatter(text) is True


def test_has_frontmatter_false_for_empty():
    assert has_frontmatter("") is False


def test_has_frontmatter_false_for_body_only():
    assert has_frontmatter("just a body, no fences\n") is False


def test_has_frontmatter_false_when_open_present_but_no_close():
    assert has_frontmatter("---\nname: x\nbody-without-close\n") is False


def test_has_frontmatter_false_when_open_indented():
    """`---` is only a fence at column 0."""
    assert has_frontmatter("  ---\nname: x\n---\nbody\n") is False


# ---------- parse: happy path ----------


def test_parse_returns_dict_and_body():
    text = "---\nname: rule\ndescription: short\ntype: feedback\n---\nbody line 1\nbody line 2\n"
    fm, body = parse(text)
    assert fm == {"name": "rule", "description": "short", "type": "feedback"}
    assert body == "body line 1\nbody line 2\n"


def test_parse_empty_body():
    text = "---\nname: x\ntype: user\n---\n"
    fm, body = parse(text)
    assert fm == {"name": "x", "type": "user"}
    assert body == ""


def test_parse_no_frontmatter_returns_full_text_as_body():
    text = "no frontmatter here\nsecond line\n"
    fm, body = parse(text)
    assert fm == {}
    assert body == text


# ---------- parse: type quirks ----------


def test_parse_stringifies_iso_dates():
    """yaml.safe_load decodes 2026-05-07 to a date; we want the ISO string back."""
    text = "---\nname: x\nlast_reviewed: 2026-05-07\n---\nbody\n"
    fm, _ = parse(text)
    assert fm["last_reviewed"] == "2026-05-07"
    assert isinstance(fm["last_reviewed"], str)


def test_parse_stringifies_dates_in_nested_dicts_and_lists():
    text = (
        "---\n"
        "name: x\n"
        "audit:\n"
        "  last: 2026-05-01\n"
        "history:\n"
        "  - 2026-04-15\n"
        "  - 2026-04-22\n"
        "---\nbody\n"
    )
    fm, _ = parse(text)
    assert fm["audit"]["last"] == "2026-05-01"
    assert fm["history"] == ["2026-04-15", "2026-04-22"]


def test_parse_preserves_list_values():
    text = (
        "---\n"
        "name: x\n"
        "tags: [topic:routing, topic:cost-posture]\n"
        "access_labels:\n"
        "  - public\n"
        "  - operator\n"
        "---\nbody\n"
    )
    fm, _ = parse(text)
    assert fm["tags"] == ["topic:routing", "topic:cost-posture"]
    assert fm["access_labels"] == ["public", "operator"]


def test_parse_skips_yaml_comments():
    text = (
        "---\n"
        "# comment line ignored\n"
        "name: x   # inline ignored\n"
        "type: user\n"
        "---\n"
        "body\n"
    )
    fm, body = parse(text)
    assert fm == {"name": "x", "type": "user"}
    assert body == "body\n"


# ---------- parse: error / fallback paths ----------


def test_parse_malformed_yaml_returns_empty_dict_and_full_text():
    """Per docstring, parse() falls back to ({}, original_text) on YAML errors."""
    text = "---\nname: x\n  : bad indentation: : :\n---\nbody\n"
    fm, body = parse(text)
    assert fm == {}
    assert body == text


def test_parse_non_dict_yaml_top_level_returns_empty_dict():
    """A YAML list at the top of the frontmatter block is not a frontmatter dict."""
    text = "---\n- one\n- two\n---\nbody\n"
    fm, body = parse(text)
    assert fm == {}
    assert body == text


def test_parse_unclosed_frontmatter_treats_input_as_no_frontmatter():
    """When the opening `---\\n` has no closing `\\n---`, parse() falls back
    to ({}, original_text). The literal `---\\n---\\nbody` is one such case
    (the second fence is on the line immediately after the opener; no
    `\\n---` separator exists between them)."""
    text = "---\n---\nbody only\n"
    fm, body = parse(text)
    assert fm == {}
    assert body == text


# ---------- render ----------


def test_render_round_trip_preserves_simple_fields():
    fm = {"name": "rule", "description": "short", "type": "feedback"}
    body = "rule body\n"
    out = render(fm, body)
    fm2, body2 = parse(out)
    assert fm2 == fm
    assert body2 == body


def test_render_scalar_only_mapping_is_block_style():
    """frontmatter-render-01/02: a list-less (scalar-only) frontmatter mapping
    must render block style (one key per line), NOT collapse to an inline
    flow-style brace line ({name: x, ...}). Round-trip equality alone does NOT
    catch the regression because the flow form round-trips fine; assert on the
    on-disk LAYOUT directly."""
    fm = {
        "name": "rule-x",
        "description": "a short hook",
        "type": "feedback",
        "uid": "mem-00001",
        "tier": "index",
    }
    out = render(fm, "body\n")
    assert "\nname:" in out
    assert "\ntype:" in out
    assert "{name:" not in out
    # No flow-style braces anywhere in the frontmatter block.
    fm_block = out.split("---", 2)[1]
    assert "{" not in fm_block


def test_render_round_trip_preserves_list_values():
    fm = {"name": "rule", "tags": ["topic:routing", "topic:cost"]}
    body = "body\n"
    out = render(fm, body)
    fm2, _ = parse(out)
    assert fm2["tags"] == ["topic:routing", "topic:cost"]


def test_render_round_trip_after_date_stringification():
    """After parse stringifies dates, render must keep them as strings on disk
    so a second parse cycle is idempotent."""
    text = "---\nname: x\nlast_reviewed: 2026-05-07\n---\nbody\n"
    fm, body = parse(text)
    rendered = render(fm, body)
    fm2, body2 = parse(rendered)
    assert fm2 == fm
    assert body2 == body


def test_render_with_empty_frontmatter_returns_body_only():
    assert render({}, "just body\n") == "just body\n"


# ---------- frontmatter-01: close fence must be a full `---` line ----------


def test_parse_trailing_text_on_fence_line_is_not_a_fence():
    """Regression for frontmatter-01.

    `--- not a real fence` is NOT a close fence (the delimiter is `---` alone on
    its own line per SPEC). The old bare-substring match split here and leaked
    ' not a real fence\\nbody' into the body. With no valid close fence in this
    text, parse returns no frontmatter rather than a mis-split.
    """
    text = "---\nname: z\ntype: user\n--- not a real fence\nbody\n"
    assert has_frontmatter(text) is False
    fm, body = parse(text)
    assert fm == {}
    assert body == text


def test_parse_four_dash_line_is_not_a_fence():
    """A `----` line is not a `---` fence; it must not be consumed as one."""
    text = "---\nname: z\ntype: user\n----\nbody\n"
    assert has_frontmatter(text) is False
    fm, body = parse(text)
    assert fm == {}
    assert body == text


def test_parse_skips_non_fence_dash_line_to_real_fence():
    """The close-fence search must skip a `--- trailing text` line and stop at
    the first true `---`-on-its-own-line fence. The skipped line and what
    follows the real fence land in the body, not the YAML block."""
    text = "---\nname: z\ntype: user\n---\n--- still in body\nrest\n"
    assert has_frontmatter(text) is True
    fm, body = parse(text)
    assert fm.get("name") == "z"
    assert body == "--- still in body\nrest\n"


def test_parse_fence_at_eof_without_trailing_newline():
    """A file ending exactly with `\\n---` (no trailing newline, empty body)
    is a valid close fence."""
    text = "---\nname: z\ntype: user\n---"
    assert has_frontmatter(text) is True
    fm, body = parse(text)
    assert fm.get("name") == "z"
    assert body == ""
