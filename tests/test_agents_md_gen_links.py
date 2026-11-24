"""agents-md-gen runs in a repo it did not write, so it never reads or writes
through a link there.

A cloned repo can commit `AGENTS.md`, `CLAUDE.md` or a backup name as a
symlink to a file outside the clone. Following one would overwrite that file
with the rendered AGENTS.md, or copy its text into files the user commits.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from memforge.cli import agents_md_gen as amg

STAMP = "20260101-000000"
OUTSIDE_TEXT = "save-exact=true\n"


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setattr(amg, "datetime", _FixedClock)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# Project notes\n\nregistry=https://example.invalid/\n")
    return repo


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    target = tmp_path / "outside.txt"
    target.write_text(OUTSIDE_TEXT)
    return target


def _run(repo: Path, monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["agents-md-gen", "--cwd", str(repo)])
    return amg.main()


def _repo_text(repo: Path) -> str:
    return "".join(
        p.read_text(errors="replace") for p in repo.iterdir() if p.is_file() and not p.is_symlink()
    )


def test_symlinked_agents_md_is_refused_and_its_target_left_alone(
    repo, outside, monkeypatch, capsys
):
    (repo / "AGENTS.md").symlink_to(outside)

    rc = _run(repo, monkeypatch)

    assert rc == 2
    assert outside.read_text() == OUTSIDE_TEXT
    assert "symlink" in capsys.readouterr().err
    assert "save-exact" not in _repo_text(repo)
    assert sorted(p.name for p in repo.iterdir()) == ["AGENTS.md", "CLAUDE.md"]


def test_symlinked_backup_name_is_refused_and_its_target_left_alone(
    repo, outside, monkeypatch, capsys
):
    (repo / "AGENTS.md").write_text("hand-written rules\n")
    (repo / f"AGENTS.bak-{STAMP}.md").symlink_to(outside)

    rc = _run(repo, monkeypatch)

    assert rc == 2
    assert outside.read_text() == OUTSIDE_TEXT
    assert (repo / "AGENTS.md").read_text() == "hand-written rules\n"
    assert "symlink" in capsys.readouterr().err


def test_symlinked_claude_md_is_refused_and_its_text_not_copied(
    repo, outside, monkeypatch, capsys
):
    (repo / "CLAUDE.md").unlink()
    (repo / "CLAUDE.md").symlink_to(outside)

    rc = _run(repo, monkeypatch)

    assert rc == 2
    assert not (repo / "AGENTS.md").exists()
    assert "symlink" in capsys.readouterr().err


def test_regular_files_still_render_and_back_up(repo, monkeypatch):
    (repo / "AGENTS.md").write_text("hand-written rules\n")

    rc = _run(repo, monkeypatch)

    assert rc == 0
    assert (repo / f"AGENTS.bak-{STAMP}.md").read_text() == "hand-written rules\n"
    rendered = (repo / "AGENTS.md").read_text()
    assert amg.MARKER in rendered
    assert "registry=https://example.invalid/" in rendered
