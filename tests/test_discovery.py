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


# ---------- discovery-01: is_memory_file / walk_memory_files consistency ----------


def test_is_memory_file_consistent_with_walk_when_root_under_archive(tmp_path):
    """Regression for discovery-01.

    When the memory root itself sits under a directory named 'archive'
    (e.g. data-archive/memory or .../archive/memory), walk_memory_files
    yields the files (it prunes archive/ only BELOW root) but the old
    is_memory_file rejected every one (matched 'archive' anywhere in the
    absolute path), silently dropping every memory for a consumer that
    filtered walk output through is_memory_file. Passing the root makes the
    two agree.
    """
    root = tmp_path / "archive" / "memory"
    f = _touch(root / "feedback_x.md")

    walked = list(walk_memory_files(root))
    assert [p.name for p in walked] == ["feedback_x.md"]

    # With root supplied, is_memory_file agrees with walk_memory_files.
    assert is_memory_file(f, root=root) is True
    for p in walked:
        assert is_memory_file(p, root=root) is True


def test_is_memory_file_rejects_archive_below_root_even_with_root(tmp_path):
    """A genuine archive/ subtree BELOW the root is still rejected when root
    is supplied (the consistency fix must not stop excluding real archives)."""
    root = tmp_path / "archive" / "memory"
    archived = _touch(root / "archive" / "old.md")
    assert is_memory_file(archived, root=root) is False


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
