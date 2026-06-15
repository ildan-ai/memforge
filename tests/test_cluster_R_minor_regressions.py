"""Regression tests for selected iteration-3 cluster R MINOR findings.

Covers the MINORs with a clear behavioral assertion:
- config-02: a dict-typed config section set to a scalar warns (not silent).
- frontmatter-01: CRLF-terminated frontmatter parses.
- idxgen-02: duplicate-uid disambiguation never overwrites a real #N entry.
- adv-03: plain-scalar emission quotes values that re-resolve to non-strings.
- adv-04: promote refuses a symlinked source.
- query-tag-substring-01: emit_markdown labels the matched topic.
- recall-04: privileged export-gate block is annotated even when enforce=True.
- resolve-02: resolve imports the shared status set (no dead local constant).
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


def test_config_scalar_section_warns(capsys):
    from memforge.cli import _config

    merged = _config._merge_defaults({"audit": 7})
    # Section reverts to defaults...
    assert isinstance(merged["audit"], dict)
    assert merged["audit"]["enforce_sensitivity_export_gate"] is True
    # ...but it is no longer silent.
    err = capsys.readouterr().err
    assert "audit" in err and "not a mapping" in err


def test_frontmatter_parses_crlf():
    from memforge.frontmatter import has_frontmatter, parse

    text = "---\r\nname: A\r\ntype: user\r\n---\r\nbody line\r\n"
    assert has_frontmatter(text)
    fm, body = parse(text)
    assert fm.get("name") == "A"
    assert "body line" in body


def test_recall_dup_uid_does_not_overwrite_real_suffixed(tmp_path: Path):
    from memforge import recall

    mem = tmp_path / "mem"
    mem.mkdir()

    def w(name, uid):
        (mem / name).write_text(
            f"---\nname: {name}\ndescription: d\ntype: reference\n"
            f"uid: {uid}\nstatus: active\n---\nbody\n",
            encoding="utf-8",
        )

    # A real A#1, plus two colliding base-A memories. The disambiguation must
    # not collapse onto the real A#1.
    w("a1.md", "A#1")
    w("a2.md", "A")
    w("a3.md", "A")
    payload = recall.build_index(mem)
    uids = set(payload["entries"].keys())
    # All three live memories must survive in the index (none silently dropped).
    assert len(payload["entries"]) == 3
    assert "A#1" in uids  # the genuine suffixed entry is intact


@pytest.mark.parametrize("hostile", ["no", "0x10", "null", "yes", "123"])
def test_yaml_escape_retyping_values_roundtrip(hostile):
    from memforge.cli.index_gen import _yaml_escape

    emitted = _yaml_escape(hostile)
    parsed = yaml.safe_load(f"owner: {emitted}\n")["owner"]
    assert parsed == hostile  # stays a string, not retyped to bool/int/None


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="symlinks unsupported")
def test_promote_refuses_symlinked_source(tmp_path: Path):
    import os

    from memforge.cli import promote

    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "tgt"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    real = outside / "secret.md"
    real.write_text("---\nname: secret\ntype: user\n---\nx\n", encoding="utf-8")
    os.symlink(real, source / "mem.md")
    (source / "MEMORY.md").write_text("- [mem](mem.md)\n", encoding="utf-8")

    rc = promote.main([
        "mem.md", "--source", str(source), "--target", str(target),
        "--yes", "--no-commit",
    ])
    assert rc == 2  # refused
    assert not (target / "mem.md").exists()


def test_query_emit_markdown_labels_matched_topic():
    from memforge.cli.query import emit_markdown, Hit

    fm = {"name": "X", "description": "d", "tags": ["topic:aws", "topic:security"]}
    hit = Hit(path=Path("x.md"), folder=Path("."), rel="x.md", fm=fm, body="")
    out = emit_markdown([hit], matched_topic="security")
    assert "topic:security" in out


def test_export_gate_privileged_annotated_when_enforced():
    from memforge.cli.audit import _export_tier_gate

    msg = _export_tier_gate("privileged", "restricted", enforce=True)
    assert msg is not None
    assert "privileged hard-floor" in msg


def test_resolve_imports_shared_status_set():
    import memforge.cli.resolve as resolve
    import memforge.cli._concurrency_audit as ca

    assert resolve.LIVE_STATUSES is ca.LIVE_STATUSES
    assert not hasattr(resolve, "EXIT_STATUSES")  # dead local constant removed
