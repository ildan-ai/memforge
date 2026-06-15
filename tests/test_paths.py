"""Tests for memforge.paths.default_memory_paths."""

from __future__ import annotations

from pathlib import Path

from memforge.paths import ARCHIVE_DIRNAME, default_memory_paths


def test_returns_two_paths_in_documented_order():
    paths = default_memory_paths()
    assert len(paths) == 2, "per-cwd then global, two entries"
    per_cwd, global_mem = paths
    assert per_cwd.name == "memory"
    # Either the grandfathered Claude-Code layout (if present on disk) or the
    # IDE-neutral ~/.memforge default; both end the global path at global-memory.
    assert global_mem.name == "global-memory"


def test_env_override_takes_priority(monkeypatch, tmp_path):
    """MEMFORGE_MEMORY_PATH (os.pathsep-separated) overrides everything."""
    import os

    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", f"{a}{os.pathsep}{b}")
    roots = default_memory_paths()
    assert roots == [a, b]


def test_grandfather_claude_layout_when_it_exists(monkeypatch, tmp_path):
    """When the Claude Code layout already exists on disk, it is used."""
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    cc_global = tmp_path / ".claude" / "global-memory"
    cc_percwd = tmp_path / ".claude" / "projects" / "alice-claude-projects" / "memory"
    cc_global.mkdir(parents=True)
    cc_percwd.mkdir(parents=True)

    per_cwd, global_mem = default_memory_paths()
    assert per_cwd == cc_percwd
    assert global_mem == cc_global


def test_ide_neutral_default_when_no_claude_layout(monkeypatch, tmp_path):
    """No env override and no Claude layout on disk -> ~/.memforge default."""
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    per_cwd, global_mem = default_memory_paths()
    assert per_cwd == tmp_path / ".memforge" / "memory"
    assert global_mem == tmp_path / ".memforge" / "global-memory"


def test_archive_dirname_is_archive():
    """Discovery and audit tooling key off this constant; pin its value."""
    assert ARCHIVE_DIRNAME == "archive"
