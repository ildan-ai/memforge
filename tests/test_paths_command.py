"""`memforge paths` prints the package's default memory folders for scripts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _memforge_paths(home: Path, cwd: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "USERPROFILE": str(home), "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO / "src"), **extra}
    return subprocess.run([sys.executable, "-m", "memforge.cli._dispatch", "paths"],
                          cwd=cwd, env=env, capture_output=True, timeout=60)


def test_prints_the_default_folders_in_order_whether_or_not_they_exist(tmp_path: Path):
    present = tmp_path / "present"
    present.mkdir()
    absent = tmp_path / "absent"

    out = _memforge_paths(tmp_path, tmp_path, MEMFORGE_MEMORY_PATH=os.pathsep.join([str(absent), str(present)]))

    assert out.returncode == 0
    assert out.stdout == f"{absent}\n{present}\n".encode()
    assert out.stderr == b""


def test_prints_a_non_ascii_folder_as_its_file_system_bytes(tmp_path: Path):
    folder = tmp_path / "café"

    out = _memforge_paths(tmp_path, tmp_path, MEMFORGE_MEMORY_PATH=str(folder), LC_ALL="C")

    assert out.returncode == 0
    assert out.stdout == os.fsencode(folder) + b"\n"


def test_matches_default_memory_paths_for_the_claude_code_layout(tmp_path: Path):
    from memforge.paths import claude_code_project_dirname

    work = tmp_path / "work"
    work.mkdir()
    project = tmp_path / ".claude" / "projects" / claude_code_project_dirname(os.path.realpath(work)) / "memory"
    project.mkdir(parents=True)

    out = _memforge_paths(tmp_path, work)

    assert out.returncode == 0
    assert out.stdout.decode().splitlines() == [str(project), str(tmp_path / ".claude" / "global-memory")]


def test_refuses_a_folder_whose_name_holds_a_newline(tmp_path: Path):
    out = _memforge_paths(tmp_path, tmp_path, MEMFORGE_MEMORY_PATH=str(tmp_path / "a\nb"))

    assert out.returncode == 1
    assert out.stdout == b""
    assert b"newline" in out.stderr
