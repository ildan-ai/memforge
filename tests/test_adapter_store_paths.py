"""The shipped shell adapters find the Claude Code project memory folder the
way Claude Code names it: the project path with every character other than an
ASCII letter or digit replaced by "-"."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from memforge.paths import claude_code_project_dirname

REPO = Path(__file__).resolve().parents[1]
READ_TRACKER = REPO / "adapters" / "claude-code" / "hooks" / "memory-read-tracker.sh"
AIDER_CMD = REPO / "adapters" / "aider" / "aider-memory-cmd.sh"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="bash scripts"),
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
]


def _env(home: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(home), "USER": "alice", "PATH": "/usr/bin:/bin", "LC_ALL": "C", **extra}


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_read_tracker_records_a_read_in_a_project_memory_folder(tmp_path: Path):
    folder = tmp_path / ".claude" / "projects" / "-work-alice-notes" / "memory"
    folder.mkdir(parents=True)
    memory = folder / "feedback_x.md"
    memory.write_text("x\n", encoding="utf-8")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(memory)}})
    jq_dir = str(Path(shutil.which("jq")).parent)

    subprocess.run(["bash", str(READ_TRACKER)], input=payload, text=True, check=True, timeout=60,
                   env=_env(tmp_path, PATH=f"{jq_dir}:/usr/bin:/bin"))

    ledger = json.loads((folder / ".last_used.json").read_text(encoding="utf-8"))
    assert list(ledger) == ["feedback_x.md"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_read_tracker_ignores_an_empty_project_segment(tmp_path: Path):
    """`projects//memory/` has no project name, so `projects/memory` is not a
    project memory folder."""
    folder = tmp_path / ".claude" / "projects" / "memory"
    folder.mkdir(parents=True)
    (folder / "x.md").write_text("x\n", encoding="utf-8")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": f"{tmp_path}/.claude/projects//memory/x.md"}})
    jq_dir = str(Path(shutil.which("jq")).parent)

    subprocess.run(["bash", str(READ_TRACKER)], input=payload, text=True, check=True, timeout=60,
                   env=_env(tmp_path, PATH=f"{jq_dir}:/usr/bin:/bin"))

    assert not (folder / ".last_used.json").exists()


def _script(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _aider_mem(home: Path, cwd: Path, *, with_memforge: bool = True) -> subprocess.CompletedProcess:
    """Source the aider wrapper and run it with a stand-in aider that prints its arguments.

    The `memforge` on PATH runs this checkout's package, so the wrapper gets the
    package's own answer.
    """
    bindir = home / "bin"
    bindir.mkdir(parents=True)
    _script(bindir / "aider", '#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done\n')
    if with_memforge:
        _script(bindir / "memforge",
                f'#!/bin/sh\nPYTHONPATH="{REPO / "src"}" exec "{sys.executable}" -m memforge.cli._dispatch "$@"\n')
    return subprocess.run(
        ["bash", "-c", 'source "$1" && aider-mem --yes', "bash", str(AIDER_CMD)],
        cwd=cwd, env=_env(home, PATH=f"{bindir}:/usr/bin:/bin"),
        capture_output=True, text=True, timeout=60,
    )


def _run_aider_mem(home: Path, cwd: Path) -> list[str]:
    out = _aider_mem(home, cwd)
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_aider_wrapper_reads_the_project_memory_of_the_working_folder(tmp_path: Path):
    home = tmp_path / "home"
    work = tmp_path / "work" / "my notes_v1.2"
    work.mkdir(parents=True)
    cwd = os.path.realpath(work)
    name = "".join(c if c.isascii() and c.isalnum() else "-" for c in cwd)
    index = home / ".claude" / "projects" / name / "memory" / "MEMORY.md"
    index.parent.mkdir(parents=True)
    index.write_text("# index\n", encoding="utf-8")

    assert _run_aider_mem(home, work) == ["--read", str(index), "--yes"]


def _project_index(home: Path, project: Path) -> Path:
    name = "".join(c if c.isascii() and c.isalnum() else "-" for c in os.path.realpath(project))
    index = home / ".claude" / "projects" / name / "memory" / "MEMORY.md"
    index.parent.mkdir(parents=True)
    index.write_text("# index\n", encoding="utf-8")
    return index


def test_aider_wrapper_reads_the_git_root_memory_from_a_subfolder(tmp_path: Path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    index = _project_index(home, repo)

    assert _run_aider_mem(home, sub) == ["--read", str(index), "--yes"]


def test_aider_wrapper_treats_a_git_file_as_the_worktree_root(tmp_path: Path):
    home = tmp_path / "home"
    worktree = tmp_path / "wt"
    sub = worktree / "docs"
    sub.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
    index = _project_index(home, worktree)

    assert _run_aider_mem(home, sub) == ["--read", str(index), "--yes"]


# A folder name over the 200-character cut, made of short segments so each stays a legal name.
_LONG = "/".join(["long-folder-name-for-the-hashed-cut"] * 7)


@pytest.mark.parametrize("relative", ["plain", "café notes", "emoji-\U0001f600", _LONG],
                         ids=["ordinary", "non-ascii", "astral", "over-200"])
def test_aider_wrapper_reads_the_store_the_package_names(tmp_path: Path, relative: str):
    home = tmp_path / "home"
    work = tmp_path / "w" / relative
    work.mkdir(parents=True)
    name = claude_code_project_dirname(os.path.realpath(work))
    index = home / ".claude" / "projects" / name / "memory" / "MEMORY.md"
    index.parent.mkdir(parents=True)
    index.write_text("# index\n", encoding="utf-8")

    assert _run_aider_mem(home, work) == ["--read", str(index), "--yes"]


def test_aider_wrapper_refuses_without_memforge_on_path(tmp_path: Path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()

    out = _aider_mem(home, work, with_memforge=False)

    assert out.returncode != 0
    assert out.stdout == ""
    assert "memforge" in out.stderr and "MEMFORGE_MEMORY_PATH" in out.stderr
