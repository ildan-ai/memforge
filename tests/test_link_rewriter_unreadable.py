"""memory-link-rewriter stops before it changes anything when a file in the
folder cannot be read.

A file the walk finds but cannot read may hold a link to the file being
renamed. Skipping it would move the file and leave that link pointing at the
old name, so every subcommand refuses with exit 2 and names the file.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from memforge.cli import link_rewriter

TARGET = "---\nuid: mem-target\nname: target\n---\nThe bun rule.\n"
LINKER = "---\nuid: mem-linker\nname: linker\n---\nSee [the bun rule](target.md).\n"
UNREADABLE_BODY = "---\nuid: mem-hidden\nname: hidden\n---\nSee [the bun rule](target.md).\n"


def _no_permission(path: Path) -> None:
    path.write_text(UNREADABLE_BODY, encoding="utf-8")
    path.chmod(0)


def _not_utf8(path: Path) -> None:
    path.write_bytes(UNREADABLE_BODY.encode("utf-8") + b"\xff\xfe\n")


def _named_pipe(path: Path) -> None:
    os.mkfifo(path)


KINDS = [
    pytest.param(
        _no_permission,
        id="no-permission",
        marks=pytest.mark.skipif(
            os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permission bits"
        ),
    ),
    pytest.param(_not_utf8, id="not-utf8"),
    pytest.param(
        _named_pipe,
        id="named-pipe",
        marks=pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo"),
    ),
]


@pytest.fixture
def folder(tmp_path: Path):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "target.md").write_text(TARGET, encoding="utf-8")
    (root / "linker.md").write_text(LINKER, encoding="utf-8")
    yield root
    for path in root.iterdir():
        if not path.is_symlink():
            path.chmod(0o644)


def _run(monkeypatch, folder: Path, *args: str, stdin: str = "") -> int:
    monkeypatch.setattr("sys.argv", ["memory-link-rewriter", "--path", str(folder), *args])
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return link_rewriter.main()


def _assert_nothing_changed(folder: Path) -> None:
    assert (folder / "target.md").read_text(encoding="utf-8") == TARGET
    assert not (folder / "moved.md").exists()
    assert (folder / "linker.md").read_text(encoding="utf-8") == LINKER


@pytest.mark.parametrize("make_unreadable", KINDS)
def test_check_reports_the_unreadable_file(monkeypatch, folder, make_unreadable, capsys):
    make_unreadable(folder / "hidden.md")

    rc = _run(monkeypatch, folder, "check")

    assert rc == 2
    assert "cannot read hidden.md" in capsys.readouterr().err


@pytest.mark.parametrize("make_unreadable", KINDS)
def test_rename_refuses_and_moves_nothing(monkeypatch, folder, make_unreadable, capsys):
    make_unreadable(folder / "hidden.md")

    rc = _run(monkeypatch, folder, "rename", str(folder / "target.md"), str(folder / "moved.md"))

    assert rc == 2
    assert "cannot read hidden.md" in capsys.readouterr().err
    _assert_nothing_changed(folder)


@pytest.mark.parametrize("make_unreadable", KINDS)
def test_rename_batch_refuses_and_moves_nothing(monkeypatch, folder, make_unreadable, capsys):
    make_unreadable(folder / "hidden.md")
    payload = json.dumps([{"src": str(folder / "target.md"), "dst": str(folder / "moved.md")}])

    rc = _run(monkeypatch, folder, "rename-batch", stdin=payload)

    assert rc == 2
    assert "cannot read hidden.md" in capsys.readouterr().err
    _assert_nothing_changed(folder)


@pytest.mark.parametrize("make_unreadable", KINDS)
def test_upgrade_refuses_and_writes_nothing(monkeypatch, folder, make_unreadable, capsys):
    make_unreadable(folder / "hidden.md")

    rc = _run(monkeypatch, folder, "upgrade")

    assert rc == 2
    assert "cannot read hidden.md" in capsys.readouterr().err
    _assert_nothing_changed(folder)


def test_check_names_every_unreadable_file(monkeypatch, folder, capsys):
    _not_utf8(folder / "first.md")
    _not_utf8(folder / "second.md")

    rc = _run(monkeypatch, folder, "check")

    err = capsys.readouterr().err
    assert rc == 2
    assert "cannot read first.md" in err
    assert "cannot read second.md" in err


def test_readable_folder_still_renames(monkeypatch, folder):
    rc = _run(monkeypatch, folder, "rename", str(folder / "target.md"), str(folder / "moved.md"))

    assert rc == 0
    assert (folder / "moved.md").exists()
    assert "(moved.md)" in (folder / "linker.md").read_text(encoding="utf-8")
