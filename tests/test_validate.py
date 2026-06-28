"""Tests for memory-validate (the v0.7.0 write-boundary gate) + the
memforge.frontmatter.validate_frontmatter primitive it shares with audit."""

from __future__ import annotations

import json
from pathlib import Path

from memforge.frontmatter import validate_frontmatter
from memforge.cli.validate import main, validate_file


# A complete, well-formed memory file (all required v0.4 fields present).
GOOD = (
    "---\n"
    "name: foo-bar\n"
    "description: a clean one-line description\n"
    "type: feedback\n"
    "uid: mem-2026-06-28-foo\n"
    "tier: detail\n"
    "tags: [topic:x]\n"
    "owner: mike\n"
    "status: active\n"
    "created: 2026-06-28\n"
    "---\n\n"
    "Body.\n"
)


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


# ---------- the shared primitive ----------


def test_primitive_valid_mapping():
    ok, reason = validate_frontmatter(GOOD)
    assert ok and reason is None


def test_primitive_colon_break():
    text = "---\ndescription: this breaks: on the colon\n---\nbody\n"
    ok, reason = validate_frontmatter(text)
    assert not ok and "COLON" in reason.upper()


def test_primitive_non_mapping_scalar():
    ok, reason = validate_frontmatter("---\njust a bare scalar\n---\nbody\n")
    assert not ok and "mapping" in reason


def test_primitive_non_mapping_list():
    ok, reason = validate_frontmatter("---\n- a\n- b\n---\nbody\n")
    assert not ok and "mapping" in reason


def test_primitive_unclosed_fence_is_lenient():
    # An opening --- with no recognized closing fence is treated as no
    # frontmatter (matching parse()'s leniency), NOT a parse violation. Presence
    # of a valid block on a memory file is invariant 1's (audit's) concern.
    ok, reason = validate_frontmatter("---\nname: x\nno closing fence here\n")
    assert ok and reason is None


def test_primitive_no_frontmatter_is_ok():
    ok, reason = validate_frontmatter("plain markdown, no fence\n")
    assert ok and reason is None


# --- panel BLOCKER regression (grok-reasoning, 2026-06-28): empty/degenerate
#     fences must NOT HARD-fail. ---


def test_primitive_empty_closed_fence_no_blank_line_is_ok():
    # `---\n---\n`: the parser does not recognize this as a closed fence, so it
    # is treated as no-frontmatter (lenient), not a parse failure.
    ok, reason = validate_frontmatter("---\n---\n# Title\n\nBody.\n")
    assert ok and reason is None


def test_primitive_empty_recognized_fence_is_ok():
    # `---\n\n---\n`: a recognized but empty fence -> safe_load yields None ->
    # treated as the benign empty mapping, NOT a non-mapping failure.
    ok, reason = validate_frontmatter("---\n\n---\n# Title\n\nBody.\n")
    assert ok and reason is None


def test_primitive_thematic_break_markdown_not_false_positive():
    # A plain markdown doc that opens with a `---` thematic break must not be
    # flagged (it has no closing fence -> no frontmatter).
    ok, reason = validate_frontmatter("---\n\nSome prose under a horizontal rule.\n")
    assert ok and reason is None


def test_primitive_crlf_normalized():
    # CRLF-terminated valid frontmatter must still validate.
    ok, _ = validate_frontmatter("---\r\nname: x\r\ndescription: y\r\n---\r\nbody\r\n")
    assert ok


# ---------- validate_file ----------


def test_file_good_no_findings(tmp_path: Path):
    p = _write(tmp_path / "feedback_good.md", GOOD)
    assert validate_file(p) == []


def test_file_colon_break_is_error(tmp_path: Path):
    p = _write(tmp_path / "feedback_bad.md",
               "---\ndescription: breaks: here\ntype: feedback\n---\nbody\n")
    findings = validate_file(p)
    assert len(findings) == 1
    assert findings[0].level == "error"
    assert findings[0].code == "frontmatter_parse"


def test_file_missing_fields_is_warn_not_error(tmp_path: Path):
    # Parses fine, but missing required v0.4 fields -> SOFT warn, not a hard error.
    p = _write(tmp_path / "feedback_thin.md",
               "---\nname: x\ndescription: y\ntype: feedback\n---\nbody\n")
    findings = validate_file(p)
    assert [f.level for f in findings] == ["warn"]
    assert findings[0].code == "missing_field"


def test_file_bad_enum_warns(tmp_path: Path):
    fm = GOOD.replace("tier: detail", "tier: bogus").replace("status: active", "status: nope")
    p = _write(tmp_path / "feedback_enum.md", fm)
    codes = {f.code for f in validate_file(p)}
    assert "bad_tier" in codes and "bad_status" in codes
    assert all(f.level == "warn" for f in validate_file(p))


def test_file_nonexistent_is_error(tmp_path: Path):
    findings = validate_file(tmp_path / "nope.md")
    assert findings and findings[0].code == "not_found" and findings[0].level == "error"


def test_memory_md_pointer_cap_warn(tmp_path: Path):
    over = "- [Title](x.md): " + "x" * 200  # > 180 bytes
    p = _write(tmp_path / "MEMORY.md", "# Index\n" + over + "\n")
    findings = validate_file(p)
    assert [f.code for f in findings] == ["pointer_byte_cap"]
    assert findings[0].level == "warn"


def test_memory_md_no_required_field_check(tmp_path: Path):
    # MEMORY.md is the index, not a memory file: it must NOT be flagged for
    # missing v0.4 fields.
    p = _write(tmp_path / "MEMORY.md", "# Index\n- [a](a.md): hook\n")
    assert validate_file(p) == []


def test_archive_file_skips_field_check(tmp_path: Path):
    arc = tmp_path / "archive"
    arc.mkdir()
    p = _write(arc / "old.md", "---\nname: x\n---\nbody\n")
    # under archive/ -> not treated as a memory file, no missing-field warn
    assert validate_file(p) == []


# ---------- the CLI (exit codes + json) ----------


def test_cli_exit_zero_on_good(tmp_path: Path, capsys):
    p = _write(tmp_path / "feedback_good.md", GOOD)
    assert main([str(p)]) == 0


def test_cli_exit_one_on_hard_error(tmp_path: Path, capsys):
    p = _write(tmp_path / "feedback_bad.md",
               "---\ndescription: breaks: here\n---\nbody\n")
    assert main([str(p)]) == 1


def test_cli_warn_passes_without_strict_fails_with_strict(tmp_path: Path, capsys):
    p = _write(tmp_path / "feedback_thin.md",
               "---\nname: x\ndescription: y\ntype: feedback\n---\nbody\n")
    assert main([str(p)]) == 0            # SOFT warning -> exit 0
    assert main([str(p), "--strict"]) == 1  # --strict escalates


def test_cli_json_shape(tmp_path: Path, capsys):
    p = _write(tmp_path / "feedback_bad.md",
               "---\ndescription: breaks: here\n---\nbody\n")
    main(["--json", str(p)])
    out = json.loads(capsys.readouterr().out)
    assert out["files_checked"] == 1
    assert out["errors"] == 1
    assert out["findings"][0]["code"] == "frontmatter_parse"


def test_cli_path_walk_skips_archive(tmp_path: Path, capsys):
    _write(tmp_path / "feedback_good.md", GOOD)
    arc = tmp_path / "archive"
    arc.mkdir()
    # A malformed file UNDER archive/ must not cause a failure (archive excluded).
    _write(arc / "broken.md", "---\nbreaks: here: now\n---\nbody\n")
    assert main(["--path", str(tmp_path)]) == 0
