"""Tests for memforge.discovery (walk_memory_files, is_memory_file)."""

from __future__ import annotations

from pathlib import Path

from memforge.discovery import is_memory_file, walk_memory_files


def _touch(p: Path, body: str = "stub\n") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ---------- is_memory_file ----------


def test_is_memory_file_accepts_md(tmp_path):
    p = _touch(tmp_path / "feedback_rule.md")
    assert is_memory_file(p) is True


def test_is_memory_file_rejects_memory_md_index(tmp_path):
    p = _touch(tmp_path / "MEMORY.md")
    assert is_memory_file(p) is False


def test_is_memory_file_rejects_non_md(tmp_path):
    p = _touch(tmp_path / "notes.txt", "x")
    assert is_memory_file(p) is False


def test_is_memory_file_rejects_path_under_archive(tmp_path):
    p = _touch(tmp_path / "archive" / "feedback_old.md")
    assert is_memory_file(p) is False


def test_is_memory_file_accepts_subfolder_md(tmp_path):
    """Rollup subfolders (auth/, infra/, etc.) hold memory files."""
    p = _touch(tmp_path / "auth" / "project_x.md")
    assert is_memory_file(p) is True


# ---------- walk_memory_files ----------


def test_walk_returns_empty_for_missing_root(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list(walk_memory_files(missing)) == []


def test_walk_returns_empty_for_a_file(tmp_path):
    """walk_memory_files only walks dirs; passing a file returns nothing."""
    f = _touch(tmp_path / "a.md")
    assert list(walk_memory_files(f)) == []


def test_walk_yields_md_files_in_root(tmp_path):
    _touch(tmp_path / "a.md")
    _touch(tmp_path / "b.md")
    out = sorted(p.name for p in walk_memory_files(tmp_path))
    assert out == ["a.md", "b.md"]


def test_walk_skips_memory_md_index(tmp_path):
    _touch(tmp_path / "MEMORY.md", "# index\n")
    _touch(tmp_path / "a.md")
    assert [p.name for p in walk_memory_files(tmp_path)] == ["a.md"]


def test_walk_skips_non_md(tmp_path):
    _touch(tmp_path / "a.md")
    _touch(tmp_path / "notes.txt", "ignore me")
    _touch(tmp_path / "config.yaml", "k: v")
    assert [p.name for p in walk_memory_files(tmp_path)] == ["a.md"]


def test_walk_prunes_archive_subtree(tmp_path):
    _touch(tmp_path / "a.md")
    _touch(tmp_path / "archive" / "old1.md")
    _touch(tmp_path / "archive" / "deeper" / "old2.md")
    out = sorted(p.name for p in walk_memory_files(tmp_path))
    assert out == ["a.md"]


def test_walk_descends_into_rollup_subfolders(tmp_path):
    _touch(tmp_path / "root.md")
    _touch(tmp_path / "auth" / "a1.md")
    _touch(tmp_path / "auth" / "a2.md")
    _touch(tmp_path / "infra" / "i1.md")
    out = sorted(p.name for p in walk_memory_files(tmp_path))
    assert out == ["a1.md", "a2.md", "i1.md", "root.md"]


def test_walk_yields_filenames_in_sorted_order_per_directory(tmp_path):
    """Documented contract: sorted within a directory (stable for diffs).

    Cross-directory ordering depends on os.walk traversal; we only assert
    the per-directory invariant.
    """
    _touch(tmp_path / "c.md")
    _touch(tmp_path / "a.md")
    _touch(tmp_path / "b.md")
    names = [p.name for p in walk_memory_files(tmp_path)]
    assert names == ["a.md", "b.md", "c.md"]
