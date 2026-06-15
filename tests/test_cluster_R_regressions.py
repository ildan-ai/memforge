"""Regression tests for the iteration-3 cluster R MAJOR findings.

One test per MAJOR finding:

- models-01 / discovery-02: link_rewriter uses the canonical shared models +
  sorted discovery walk (no forked dataclasses, deterministic order).
- idxgen-01: _collect_decision_groups does not descend symlinked directories.
- recall-02: audit-log append is serialized by an exclusive lock (chain stays
  intact under concurrent appends).
- promote-01: promote's pointer matcher is anchored to the link basename and
  does not delete a substring-colliding sibling's pointer.
- adv-01: a hostile first_line beginning with ',' or '? ' round-trips through
  yaml.safe_load (competing-claims block stays parseable).
- adv-02: rollup --topic/--title are YAML-escaped (no frontmatter injection).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


# ---------- models-01 / discovery-02 ----------


def test_link_rewriter_uses_canonical_models_and_sorted_walk():
    """link_rewriter must import Memory/FolderIndex/Link from memforge.models
    and walk_memory_files from memforge.discovery (closes models-01 /
    discovery-02), not define its own divergent copies."""
    import memforge.cli.link_rewriter as lr
    import memforge.models as models
    import memforge.discovery as discovery

    assert lr.Memory is models.Memory
    assert lr.FolderIndex is models.FolderIndex
    assert lr.Link is models.Link
    assert lr.walk_memory_files is discovery.walk_memory_files
    # The canonical Memory carries the `frontmatter` field the fork lacked.
    assert "frontmatter" in models.Memory.__dataclass_fields__


def test_link_rewriter_walk_is_sorted(tmp_path: Path):
    """The canonical discovery walk yields filenames in sorted order."""
    import memforge.cli.link_rewriter as lr

    for name in ["c.md", "a.md", "b.md"]:
        (tmp_path / name).write_text("---\nname: x\ntype: user\n---\nbody\n", encoding="utf-8")
    got = [p.name for p in lr.walk_memory_files(tmp_path)]
    assert got == sorted(got)


# ---------- idxgen-01 ----------


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_collect_decision_groups_skips_symlinked_directory(tmp_path: Path):
    """A symlinked DIRECTORY under the memory root must not be descended, so an
    out-of-root memory's body cannot reach the competing-claims block
    (closes idxgen-01)."""
    from memforge.cli.index_gen import _collect_decision_groups

    mem = tmp_path / "mem"
    mem.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text(
        "---\nname: Leak\ntype: feedback\ndecision_topic: shared-topic\n"
        "uid: mem-leak\nstatus: active\nowner: attacker\nupdated: 2026-01-01\n"
        "---\nSECRET out-of-root body content\n",
        encoding="utf-8",
    )
    # A legit in-root member of the same topic, so the topic would render.
    (mem / "real.md").write_text(
        "---\nname: Real\ntype: feedback\ndecision_topic: shared-topic\n"
        "uid: mem-real\nstatus: active\nowner: operator\nupdated: 2026-01-02\n"
        "---\nIn-root body\n",
        encoding="utf-8",
    )
    os.symlink(outside, mem / "linkdir", target_is_directory=True)

    groups = _collect_decision_groups(mem)
    members = groups.get("shared-topic", [])
    uids = {m["uid"] for m in members}
    assert "mem-real" in uids
    assert "mem-leak" not in uids  # out-of-root member must not be ingested
    first_lines = " ".join(m["first_line"] for m in members)
    assert "SECRET" not in first_lines


# ---------- recall-02 ----------


def test_audit_log_concurrent_appends_keep_chain_intact(tmp_path: Path):
    """Concurrent appends serialized by the exclusive lock produce a valid,
    non-forked hash chain (closes recall-02)."""
    import threading

    from memforge.cli import audit_log

    folder = tmp_path / "mem"
    folder.mkdir()

    def _append(i: int) -> None:
        audit_log.append_record(
            folder, op="write", file=None,
            before_sha256=None, after_sha256=None,
            operator=f"agent-{i}", meta={"i": i},
        )

    threads = [threading.Thread(target=_append, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok, errors = audit_log.verify_chain(folder)
    assert ok, f"chain forked under concurrency: {errors}"
    records = audit_log.read_log(folder)
    assert len(records) == 16
    seqs = sorted(r["seq"] for r in records)
    assert seqs == list(range(1, 17))  # no duplicate seqs


# ---------- promote-01 ----------


def test_find_pointer_line_anchors_to_basename():
    """The pointer matcher must match the link target's basename exactly, not a
    substring, so promoting 'auth.md' never matches 'prior_auth.md' or
    'oauth.md' (closes promote-01)."""
    from memforge.cli.promote import _find_pointer_line

    index = (
        "# Memory Index\n"
        "\n"
        "- [OAuth notes](feedback_oauth.md): a\n"
        "- [Prior auth](prior_auth.md): b\n"
        "- [Auth rule](auth.md): c\n"
    )
    lineno, line, count = _find_pointer_line(index, "auth.md")
    assert count == 1
    assert "(auth.md)" in line
    assert "oauth" not in line and "prior_auth" not in line


def test_find_pointer_line_strips_fragment():
    from memforge.cli.promote import _find_pointer_line

    index = "- [x](sub/auth.md#section): c\n"
    lineno, line, count = _find_pointer_line(index, "auth.md")
    assert count == 1 and lineno == 1


# ---------- adv-01 ----------


@pytest.mark.parametrize("hostile", [",foo", "? key", ",", "?  spaced"])
def test_yaml_escape_leading_indicator_roundtrips(hostile: str):
    """A first_line beginning with ',' or '? ' must round-trip through
    yaml.safe_load (the competing-claims block stays parseable) (closes
    adv-01)."""
    from memforge.cli.index_gen import _yaml_escape

    emitted = _yaml_escape(hostile)
    parsed = yaml.safe_load(f"first_line: {emitted}\n")["first_line"]
    assert parsed == hostile


def test_competing_claims_block_parseable_with_hostile_first_line(tmp_path: Path):
    from memforge.cli.index_gen import render_competing_claims_block

    for i, body in enumerate([",hostile flow entry", "? hostile complex key"]):
        (tmp_path / f"m{i}.md").write_text(
            "---\nname: M{0}\ntype: feedback\ndecision_topic: t\n"
            "uid: mem-{0}\nstatus: active\nowner: o\nupdated: 2026-01-0{1}\n"
            "---\n{2}\n".format(i, i + 1, body),
            encoding="utf-8",
        )
    block = render_competing_claims_block(tmp_path)
    assert block  # two live members -> block emitted
    # The fenced block body must parse cleanly as YAML.
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, list)


# ---------- adv-02 ----------


def test_rollup_readme_frontmatter_not_injectable(tmp_path: Path):
    """A crafted --topic/--title must not inject or corrupt the generated README
    frontmatter (closes adv-02)."""
    from memforge.cli import rollup
    from memforge.frontmatter import parse

    folder = tmp_path / "mem"
    folder.mkdir()
    for n in ("a", "b"):
        (folder / f"{n}.md").write_text(
            f"---\nname: {n}\ntype: user\n---\nbody\n", encoding="utf-8"
        )
    rc = rollup.cmd_create(
        folder, "myslug",
        [Path("a.md"), Path("b.md")],
        topic="evil] injected: true [",
        title="x'y",
        dry_run=False,
    )
    assert rc == 0
    readme = (folder / "myslug" / "README.md").read_text(encoding="utf-8")
    fm, _ = parse(readme)
    assert "injected" not in fm  # no smuggled key
    assert fm["name"] == "x'y"
    assert fm["tags"] == ["topic:evil] injected: true ["]
