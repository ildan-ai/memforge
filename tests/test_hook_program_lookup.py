"""The Claude Code hooks never run a program planted in a memory store.

The auto-commit hook changes into the store before it runs its helpers, and
both hooks can be started from inside one. A `.`, empty or relative PATH entry
would find a program committed there; an absolute entry that points into a
store would too. Each planted program records that it ran.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "adapters" / "claude-code" / "hooks"
AUTO_COMMIT = HOOKS / "memory-auto-commit.sh"
READ_TRACKER = HOOKS / "memory-read-tracker.sh"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="bash scripts"),
    pytest.mark.skipif(any(shutil.which(t) is None for t in ("bash", "git", "jq")),
                       reason="needs bash, git and jq"),
]

AUTO_COMMIT_PROGRAMS = ("git", "tr", "memory-recall", "memory-frontmatter-backfill")
READ_TRACKER_PROGRAMS = ("jq", "date", "mktemp", "mv", "basename")


def _env(home: Path, path: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": str(home), "USER": "tester", "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "1", "PATH": path, "LC_ALL": "C"})
    return env


def _tools_path() -> list[str]:
    dirs = [str(Path(shutil.which(t)).parent) for t in ("bash", "git", "jq")] + ["/usr/bin", "/bin"]
    return list(dict.fromkeys(dirs))


def _plant(folder: Path, names: tuple[str, ...], marker: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        script = folder / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{marker}"\nexit 1\n', encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["/usr/bin/env", "git", "-C", str(repo), *args], env=env,
                          capture_output=True, text=True, check=True, timeout=60).stdout


@pytest.fixture
def store(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    folder = home / ".claude" / "projects" / "-work-notes" / "memory"
    folder.mkdir(parents=True)
    env = _env(home, os.pathsep.join(_tools_path()))
    _git(folder, env, "init", "-q")
    _git(folder, env, "config", "user.email", "test@example.com")
    _git(folder, env, "config", "user.name", "Test")
    _git(folder, env, "config", "commit.gpgsign", "false")
    (folder / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(folder, env, "add", "-A")
    _git(folder, env, "commit", "-q", "-m", "seed")
    return folder


def _home(store: Path) -> Path:
    return store.parents[3]


def _run(hook: Path, store: Path, path: str, payload: dict) -> None:
    subprocess.run(["bash", str(hook)], input=json.dumps(payload), env=_env(_home(store), path),
                   cwd=store, text=True, check=True, timeout=60)


def _write(store: Path, path: str) -> None:
    note = store / "note.md"
    note.write_text("new\n", encoding="utf-8")
    _run(AUTO_COMMIT, store, path, {"tool_name": "Write", "tool_input": {"file_path": str(note)}})


def _read(store: Path, path: str) -> None:
    _run(READ_TRACKER, store, path, {"tool_name": "Read", "tool_input": {"file_path": str(store / "seed.md")}})


def _last_subject(store: Path) -> str:
    return _git(store, _env(_home(store), os.pathsep.join(_tools_path())), "log", "-1", "--format=%s")


@pytest.mark.parametrize("entry", [".", ""])
def test_auto_commit_skips_relative_path_entries(store, tmp_path, entry):
    marker = tmp_path / "ran"
    _plant(store, AUTO_COMMIT_PROGRAMS, marker)

    _write(store, os.pathsep.join([entry, *_tools_path()]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert _last_subject(store).strip() == "memory: Write note.md"


def test_auto_commit_skips_programs_inside_the_store(store, tmp_path):
    marker = tmp_path / "ran"
    _plant(store / "bin", AUTO_COMMIT_PROGRAMS, marker)

    _write(store, os.pathsep.join([str(store / "bin"), *_tools_path()]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert _last_subject(store).strip() == "seed"


def test_auto_commit_skips_a_linked_program_whose_target_is_in_the_store(store, tmp_path):
    marker = tmp_path / "ran"
    _plant(store / "tools", ("memory-recall",), marker)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "memory-recall").symlink_to(store / "tools" / "memory-recall")

    _write(store, os.pathsep.join([str(linked), *_tools_path()]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert _last_subject(store).strip() == "memory: Write note.md"


@pytest.mark.parametrize("entry", [".", ""])
def test_read_tracker_skips_relative_path_entries(store, tmp_path, entry):
    marker = tmp_path / "ran"
    _plant(store, READ_TRACKER_PROGRAMS, marker)

    _read(store, os.pathsep.join([entry, *_tools_path()]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert list(json.loads((store / ".last_used.json").read_text(encoding="utf-8"))) == ["seed.md"]


def test_read_tracker_skips_programs_inside_the_store(store, tmp_path):
    marker = tmp_path / "ran"
    _plant(store / "bin", READ_TRACKER_PROGRAMS, marker)

    _read(store, os.pathsep.join([str(store / "bin"), *_tools_path()]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert not (store / ".last_used.json").exists()
