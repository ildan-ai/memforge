"""Program lookup on Windows tries each PATHEXT extension in one folder.

Python before 3.12 skips PATHEXT in `shutil.which` for a name with a folder
part, so a lookup through it found no `git.exe` on Windows at all. These run
on every platform; the last one needs Windows.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_find_program_tries_each_pathext_extension_on_windows(tmp_path, monkeypatch):
    # Python before 3.12 skips PATHEXT for a name with a folder part, so a
    # lookup through shutil.which found no git.exe on Windows at all.
    from memforge._programs import find_program

    program = _executable(tmp_path / "bin" / "tool.EXE")
    monkeypatch.setenv("PATH", str(program.parent))
    monkeypatch.setenv("PATHEXT", os.pathsep.join([".COM", ".EXE"]))
    monkeypatch.setattr(os, "name", "nt")

    assert find_program("tool") == str(program)


def test_find_program_takes_a_name_that_already_has_its_extension(tmp_path, monkeypatch):
    from memforge._programs import find_program

    program = _executable(tmp_path / "bin" / "tool.EXE")
    monkeypatch.setenv("PATH", str(program.parent))
    monkeypatch.setenv("PATHEXT", os.pathsep.join([".COM", ".EXE"]))
    monkeypatch.setattr(os, "name", "nt")

    assert find_program("tool.EXE") == str(program)


@pytest.mark.skipif(os.name != "nt", reason="needs the Windows file system and git.exe")
def test_find_program_finds_the_installed_git_on_windows():
    from memforge._programs import find_program

    found = find_program("git")
    assert found is not None
    assert found.lower().endswith(".exe")
