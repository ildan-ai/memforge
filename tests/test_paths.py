"""Tests for memforge.paths.default_memory_paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from memforge.paths import ARCHIVE_DIRNAME, default_memory_paths


def _project_folder(home: Path) -> Path:
    """Claude Code's projects/ entry for the current folder (short ASCII paths only)."""
    return home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))


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
    """When the Claude Code layout for the working folder exists, it is used."""
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    home = tmp_path / "home"
    work = tmp_path / "work" / "notes"
    work.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(work)

    cc_global = home / ".claude" / "global-memory"
    cc_percwd = _project_folder(home) / "memory"
    cc_global.mkdir(parents=True)
    cc_percwd.mkdir(parents=True)

    per_cwd, global_mem = default_memory_paths()
    assert per_cwd == cc_percwd
    assert global_mem == cc_global


def test_per_cwd_layout_alone_selects_claude_layout(monkeypatch, tmp_path):
    """The per-project folder on its own is enough to select the Claude Code layout."""
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    home = tmp_path / "home"
    work = tmp_path / "work" / "notes"
    work.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(work)
    cc_percwd = _project_folder(home) / "memory"
    cc_percwd.mkdir(parents=True)

    per_cwd, global_mem = default_memory_paths()
    assert per_cwd == cc_percwd
    assert global_mem == home / ".claude" / "global-memory"


def test_env_override_wins_over_claude_layout(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)
    (_project_folder(home) / "memory").mkdir(parents=True)
    store = tmp_path / "store"
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(store))

    assert default_memory_paths() == [store]


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ("/srv/Alice/notes", "-srv-Alice-notes"),
        ("/home/alice/my notes_v1.2", "-home-alice-my-notes-v1-2"),
        ("C:\\Users\\alice\\notes", "C--Users-alice-notes"),
        # One dash per UTF-16 code unit: the emoji is a surrogate pair.
        ("/home/alice/\u00e9\U0001f600", "-home-alice----"),
    ],
)
def test_claude_code_project_dirname(project, expected):
    from memforge.paths import claude_code_project_dirname

    assert claude_code_project_dirname(project) == expected


def test_claude_code_project_dirname_takes_a_name_with_undecodable_bytes():
    # A POSIX folder name that is not valid UTF-8 reaches Python with
    # surrogate escapes; each such byte is one character that is not an ASCII
    # letter or digit.
    from memforge.paths import claude_code_project_dirname

    assert claude_code_project_dirname("/tmp/a" + "\udcff" + "b") == "-tmp-a-b"


@pytest.mark.parametrize(
    ("project", "suffix"),
    [("/home/alice/" + "a" * 200, "h0xe7k"), ("/home/alice/" + "b" * 300, "styp80")],
)
def test_long_project_dirname_is_cut_and_hashed(project, suffix):
    from memforge.paths import claude_code_project_dirname

    name = claude_code_project_dirname(project)
    assert name == project.replace("/", "-")[:200] + "-" + suffix


def _store_for(home: Path, folder: Path) -> Path:
    name = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(folder))
    return home / ".claude" / "projects" / name / "memory"


def _per_cwd_from(monkeypatch, home: Path, cwd: Path) -> Path:
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(cwd)
    (home / ".claude" / "global-memory").mkdir(parents=True, exist_ok=True)
    return default_memory_paths()[0]


def test_subfolder_of_a_repository_uses_the_git_root(monkeypatch, tmp_path):
    """Claude Code keys auto-memory by the repository, not the folder you stand in."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)

    assert _per_cwd_from(monkeypatch, tmp_path / "home", sub) == _store_for(tmp_path / "home", repo)


def test_linked_worktree_uses_the_folder_holding_the_git_file(monkeypatch, tmp_path):
    worktree = tmp_path / "wt"
    sub = worktree / "docs"
    sub.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")

    assert _per_cwd_from(monkeypatch, tmp_path / "home", sub) == _store_for(tmp_path / "home", worktree)


def test_outside_a_repository_uses_the_working_folder(monkeypatch, tmp_path):
    work = tmp_path / "work" / "notes"
    work.mkdir(parents=True)

    assert _per_cwd_from(monkeypatch, tmp_path / "home", work) == _store_for(tmp_path / "home", work)


def test_env_override_wins_over_the_git_root(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src"
    sub.mkdir()
    home = tmp_path / "home"
    _store_for(home, repo).mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(sub)
    store = tmp_path / "store"
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(store))

    assert default_memory_paths() == [store]


def test_git_entry_above_the_home_folder_is_honored(monkeypatch, tmp_path):
    """git itself finds a repository above $HOME, so the walk does not stop at home."""
    (tmp_path / ".git").mkdir()
    home = tmp_path / "home"
    project = home / "proj"
    project.mkdir(parents=True)

    assert _per_cwd_from(monkeypatch, home, project) == _store_for(home, tmp_path)


def test_ide_neutral_default_when_no_claude_layout(monkeypatch, tmp_path):
    """No env override and no Claude layout on disk -> ~/.memforge default."""
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)

    per_cwd, global_mem = default_memory_paths()
    assert per_cwd == tmp_path / ".memforge" / "memory"
    assert global_mem == tmp_path / ".memforge" / "global-memory"


def test_archive_dirname_is_archive():
    """Discovery and audit tooling key off this constant; pin its value."""
    assert ARCHIVE_DIRNAME == "archive"
