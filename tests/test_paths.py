"""Tests for memforge.paths.default_memory_paths."""

from __future__ import annotations

from pathlib import Path

from memforge.paths import ARCHIVE_DIRNAME, default_memory_paths


def test_returns_two_paths_in_documented_order():
    paths = default_memory_paths()
    assert len(paths) == 2, "per-cwd then global, two entries"
    per_cwd, global_mem = paths
    assert per_cwd.name == "memory"
    assert per_cwd.parent.name.endswith("-claude-projects")
    assert global_mem.name == "global-memory"


def test_per_cwd_path_uses_user_env(monkeypatch, tmp_path):
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    per_cwd, global_mem = default_memory_paths()

    assert per_cwd == tmp_path / ".claude" / "projects" / "alice-claude-projects" / "memory"
    assert global_mem == tmp_path / ".claude" / "global-memory"


def test_per_cwd_path_falls_back_when_user_unset(monkeypatch, tmp_path):
    """Documented behavior: USER unset -> empty string in the path component."""
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    per_cwd, _ = default_memory_paths()
    assert per_cwd.name == "memory"
    assert per_cwd.parent.name == "-claude-projects"


def test_archive_dirname_is_archive():
    """Discovery and audit tooling key off this constant; pin its value."""
    assert ARCHIVE_DIRNAME == "archive"
