"""Tests for memforge.cli.audit.

Regression coverage: rollup-subfolder README.md files must be treated as
pointer-comparable (per spec §"Rollup subfolders") so that legitimate
parent-MEMORY.md pointers like `[Forge state](forge/README.md)` do not
fire `Orphan pointer (no file)`.
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.audit import _disk_md_files


def _seed_top_level(folder: Path, name: str = "feedback_x.md") -> None:
    (folder / name).write_text(
        "---\n"
        "name: Top level\n"
        "description: Top-level seed\n"
        "type: feedback\n"
        "---\n\n"
        "Body.\n"
        "**Why:** test.\n"
        "**How to apply:** test.\n",
        encoding="utf-8",
    )


def _seed_rollup_readme(folder: Path, topic: str) -> None:
    sub = folder / topic
    sub.mkdir()
    (sub / "README.md").write_text(
        "---\n"
        f"name: {topic} rollup\n"
        f"description: Rollup README for {topic}\n"
        "type: reference\n"
        "tier: index\n"
        "---\n\n"
        f"Rollup body for {topic}.\n",
        encoding="utf-8",
    )


def test_disk_md_files_returns_top_level(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_top_level(tmp_path, "feedback_b.md")
    (tmp_path / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    assert _disk_md_files(tmp_path) == ["feedback_a.md", "feedback_b.md"]


def test_disk_md_files_includes_rollup_readmes(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme(tmp_path, "forge")
    _seed_rollup_readme(tmp_path, "ildan")

    assert _disk_md_files(tmp_path) == [
        "feedback_a.md",
        "forge/README.md",
        "ildan/README.md",
    ]


def test_disk_md_files_excludes_archive(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "README.md").write_text(
        "---\nname: archived\ndescription: x\ntype: reference\n---\n",
        encoding="utf-8",
    )

    assert _disk_md_files(tmp_path) == ["feedback_a.md"]


def test_disk_md_files_skips_subfolders_without_readme(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    sub = tmp_path / "forge"
    sub.mkdir()
    (sub / "feedback_detail.md").write_text(
        "---\nname: detail\ndescription: x\ntype: feedback\ntier: detail\n---\n",
        encoding="utf-8",
    )

    assert _disk_md_files(tmp_path) == ["feedback_a.md"]


def test_no_orphan_pointer_for_rollup_readme(tmp_path: Path) -> None:
    """End-to-end: a MEMORY.md pointer at a rollup README must not produce
    an `Orphan pointer (no file)` violation."""
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme(tmp_path, "forge")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [Forge state](forge/README.md) - rollup\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violations, _ = audit_target(
            tmp_path,
            stale_days=365,
            strict=False,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "Orphan pointer (no file): forge/README.md" not in output
    assert "Orphan file (no pointer): forge/README.md" not in output
