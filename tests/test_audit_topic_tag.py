"""Tests for the missing-topic-tag HEALTH check in memory-audit.

`tags` is load-bearing in two places: `recall` folds it into the trigger set
alongside name and description, and `index_gen.extract_topic` derives the
MEMORY.md topic section from it. A file with no `topic:` tag is therefore both
harder for recall to surface and lands in a flat "(no topic)" dump.

Nothing detected that before. `memory-frontmatter-backfill` infers a topic only
from a named subfolder or a filename matching its inline KNOWN_TOPICS set, so a
top-level file with an unrecognized name gets nothing and no tool objects. 59
untagged files accumulated in a real 834-memory corpus that way, putting 46 of
87 per-cwd pointers into "(no topic)", before an audit surfaced the class.

Reported, never repaired: choosing the topic is a semantic call, and a wrong
topic is worse than none because it files the memory where the operator will not
look.
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.audit import audit_target, _has_topic_tag


def _write(folder: Path, name: str, tags_block: str) -> None:
    (folder / name).write_text(
        "---\n"
        "name: Test memory\n"
        "description: A test memory for the topic-tag check\n"
        "type: reference\n"
        "sensitivity: internal\n"
        f"{tags_block}"
        "---\n\n"
        "Body text.\n",
        encoding="utf-8",
    )


def _run(folder: Path) -> list[str]:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _count, blob = audit_target(
            folder, stale_days=9999, fix=False, add_defaults=False, json_out=True
        )
    assert blob is not None
    return blob["health"]


def _hits(health: list[str]) -> list[str]:
    return [h for h in health if "no topic: tag" in h]


# ---- helper unit coverage ----

def test_has_topic_tag_accepts_list_form() -> None:
    assert _has_topic_tag({"tags": ["topic:forge", "terraform"]}) is True


def test_has_topic_tag_accepts_scalar_form() -> None:
    """`tags: topic:forge` is valid YAML and appears in real corpora."""
    assert _has_topic_tag({"tags": "topic:forge"}) is True


def test_has_topic_tag_rejects_non_topic_tags() -> None:
    assert _has_topic_tag({"tags": ["terraform", "oauth"]}) is False


def test_has_topic_tag_handles_missing_and_malformed() -> None:
    for fm in ({}, {"tags": None}, {"tags": {"a": 1}}):
        assert _has_topic_tag(fm) is False, fm


# ---- audit integration ----

def test_untagged_file_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "loose.md", "")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](loose.md): hook\n", encoding="utf-8"
    )
    hits = _hits(_run(tmp_path))
    assert len(hits) == 1, f"expected 1 topic-tag finding, got: {hits}"
    assert "loose.md" in hits[0]


def test_tagged_file_is_not_reported(tmp_path: Path) -> None:
    _write(tmp_path, "tagged.md", "tags:\n- topic:forge\n")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](tagged.md): hook\n", encoding="utf-8"
    )
    assert _hits(_run(tmp_path)) == []


def test_non_topic_tags_alone_still_report(tmp_path: Path) -> None:
    """Having tags is not enough; the check is specifically for a topic: tag,
    because that is what index-gen reads to place the memory in a section."""
    _write(tmp_path, "sideways.md", "tags:\n- terraform\n- oauth\n")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](sideways.md): hook\n", encoding="utf-8"
    )
    hits = _hits(_run(tmp_path))
    assert len(hits) == 1, f"expected 1 finding for non-topic tags, got: {hits}"


def test_reported_as_health_never_as_violation(tmp_path: Path) -> None:
    """This is a quality signal, not a conformance failure. An untagged memory is
    still well-formed and still reachable; it is just harder to find."""
    _write(tmp_path, "loose.md", "")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](loose.md): hook\n", encoding="utf-8"
    )
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _count, blob = audit_target(
            tmp_path, stale_days=9999, fix=False, add_defaults=False, json_out=True
        )
    assert blob is not None
    assert not any("no topic: tag" in v for v in blob["violations"])
    assert _hits(blob["health"])
