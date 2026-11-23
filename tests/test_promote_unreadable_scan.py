"""memory-promote refuses, before anything moves, when it cannot read every
file in the source folder that could link to the memory it moves.

A file it cannot read may hold a link to that memory. Moving anyway would
leave the link pointing at the old place with no warning and a clean exit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from memforge.cli import promote

pytestmark = pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                                reason="needs POSIX permissions that bind the current user")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")


@pytest.fixture
def folders(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    source, target = tmp_path / "src", tmp_path / "tgt"
    _init(source)
    _init(target)
    (source / "MEMORY.md").write_text("# Memory Index\n\n- [Thing](feedback_thing.md): a thing\n",
                                      encoding="utf-8")
    (source / "feedback_thing.md").write_text(
        "---\nname: thing\nuid: mem-thing\nstatus: active\n---\n\nBody.\n", encoding="utf-8")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "seed")
    return source, target


def _promote(source: Path, target: Path) -> int:
    return promote.main(["feedback_thing.md", "--source", str(source), "--target", str(target), "--yes"])


def _unchanged(source: Path, target: Path) -> None:
    assert (source / "feedback_thing.md").exists()
    assert not (target / "feedback_thing.md").exists()
    assert "feedback_thing.md" in (source / "MEMORY.md").read_text(encoding="utf-8")


def test_an_unreadable_file_stops_the_promote_before_anything_moves(folders, capsys):
    source, target = folders
    other = source / "other.md"
    other.write_text("see [thing](feedback_thing.md)\n", encoding="utf-8")
    other.chmod(0)
    try:
        rc = _promote(source, target)
    finally:
        other.chmod(0o644)

    assert rc == 2
    _unchanged(source, target)
    assert "other.md" in capsys.readouterr().err


def test_an_unlistable_folder_stops_the_promote_before_anything_moves(folders, capsys):
    source, target = folders
    sub = source / "topic"
    sub.mkdir()
    (sub / "other.md").write_text("see [thing](../feedback_thing.md)\n", encoding="utf-8")
    sub.chmod(0)
    try:
        rc = _promote(source, target)
    finally:
        sub.chmod(0o755)

    assert rc == 2
    _unchanged(source, target)
    assert "topic" in capsys.readouterr().err


def test_a_file_that_is_not_utf8_stops_the_promote_before_anything_moves(folders, capsys):
    source, target = folders
    (source / "other.md").write_bytes(b"see [thing](feedback_thing.md) \xff\n")

    rc = _promote(source, target)

    assert rc == 2
    _unchanged(source, target)
    assert "other.md" in capsys.readouterr().err


def test_a_readable_folder_still_reports_inbound_links_after_the_move(folders, capsys):
    source, target = folders
    (source / "other.md").write_text("see [thing](feedback_thing.md)\n", encoding="utf-8")

    rc = _promote(source, target)

    assert rc == promote.EXIT_UNRECONCILED_LINKS
    assert (target / "feedback_thing.md").exists()
    assert "other.md: feedback_thing.md" in capsys.readouterr().err
