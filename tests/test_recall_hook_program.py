"""The Claude Code recall hook starts `memory-recall` by an absolute path found
the way every memforge tool finds a program.

The hook runs in the folder Claude Code was opened in, where anyone who can
commit can place a program named `memory-recall`. Started by bare name, it is
found there through a `.` PATH entry, and on Windows through the current-folder
search that needs no PATH entry at all. Each planted program records that it
ran. Windows itself is not run here: the POSIX `.` entry is the same lookup.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "adapters" / "claude-code" / "hooks" / "memory_recall_hook.py"
README = REPO / "adapters" / "claude-code" / "README.md"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="planted programs are POSIX scripts")


def _plant(folder: Path, marker: Path, said: str = "PLANTED") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "memory-recall"
    script.write_text(f'#!/bin/sh\necho "$0" >> "{marker}"\necho {said}\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _hook_module():
    spec = importlib.util.spec_from_file_location("memory_recall_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "store"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(folder))
    return folder


def test_a_memory_recall_in_the_working_folder_never_runs(tmp_path, store):
    project = tmp_path / "project"
    marker = tmp_path / "ran.log"
    _plant(project, marker)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([".", *os.environ.get("PATH", os.defpath).split(os.pathsep)])
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"prompt": "hello"}),
                          cwd=project, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "PLANTED" not in proc.stdout
    assert not marker.exists()


def test_a_memory_recall_on_an_absolute_path_entry_into_the_store_is_refused(
        tmp_path, store, monkeypatch):
    marker = tmp_path / "ran.log"
    _plant(store / "bin", marker)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env-without-memforge"))
    monkeypatch.setenv("PATH", str(store / "bin"))
    hook = _hook_module()
    with pytest.raises(hook.RecallOff, match="inside"):
        hook._recall_program()
    assert not marker.exists()


def test_a_memory_recall_on_an_absolute_path_entry_outside_the_store_is_used(
        tmp_path, store, monkeypatch):
    planted = _plant(tmp_path / "tools", tmp_path / "ran.log")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env-without-memforge"))
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))
    assert _hook_module()._recall_program() == str(planted)


def test_the_memory_recall_of_the_hook_s_own_environment_comes_first(
        tmp_path, store, monkeypatch):
    own = _plant(tmp_path / "env" / "bin", tmp_path / "own.log")
    _plant(tmp_path / "tools", tmp_path / "ran.log")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env"))
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))
    assert _hook_module()._recall_program() == str(own)


# Without memforge on its path: `-S` skips site-packages, where an installed
# memforge would be found, and `-I` ignores PYTHONPATH. This is a system
# python3 running the hook while memforge lives in a pipx environment.
STANDALONE = (sys.executable, "-I", "-S")


def _run_hook(interpreter, cwd: Path, path: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PATH=path, PYTHONPATH=str(REPO / "src"))
    return subprocess.run([*interpreter, str(HOOK)], input=json.dumps({"prompt": "hello"}),
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path) -> Path:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "sub").mkdir()
    return project


def test_without_memforge_a_memory_recall_on_an_absolute_path_entry_recalls(tmp_path, store, repo):
    _plant(tmp_path / "pipx-bin", tmp_path / "ran.log", said="RECALLED")
    proc = _run_hook(STANDALONE, repo / "sub", os.pathsep.join([str(tmp_path / "pipx-bin"), "/usr/bin", "/bin"]))
    assert proc.returncode == 0
    assert proc.stdout == "RECALLED\n"
    assert proc.stderr == ""


@pytest.mark.parametrize("where", ["repository", "memory folder", "working folder"])
def test_without_memforge_a_planted_memory_recall_is_refused_and_recall_is_off(tmp_path, store, repo, where):
    marker = tmp_path / "ran.log"
    folder = {"repository": repo / "tools", "memory folder": store / "bin", "working folder": repo / "sub"}[where]
    _plant(folder, marker)
    entry = "." if where == "working folder" else str(folder)
    proc = _run_hook(STANDALONE, repo / "sub", os.pathsep.join([entry, "/usr/bin", "/bin"]))
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not marker.exists()
    assert proc.stderr.startswith("memory_recall_hook: recall is off: ")
    assert len(proc.stderr.splitlines()) == 1
    if where != "working folder":
        assert "inside" in proc.stderr


def test_without_memforge_an_absolute_path_entry_naming_the_working_folder_is_refused(tmp_path, store):
    """Outside any repository the working folder itself is still refused."""
    folder = tmp_path / "not-a-repo"
    marker = tmp_path / "ran.log"
    _plant(folder, marker)
    proc = _run_hook(STANDALONE, folder, os.pathsep.join([str(folder), "/usr/bin", "/bin"]))
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not marker.exists()
    assert "inside" in proc.stderr


@pytest.mark.parametrize("where", ["working folder", "repository"])
def test_with_memforge_a_memory_recall_in_the_working_folder_or_its_repository_is_refused(
        tmp_path, store, repo, monkeypatch, where):
    folder = {"working folder": tmp_path / "not-a-repo", "repository": repo / "tools"}[where]
    cwd = folder if where == "working folder" else repo / "sub"
    marker = tmp_path / "ran.log"
    _plant(folder, marker)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env-without-memforge"))
    monkeypatch.setenv("PATH", str(folder))
    hook = _hook_module()
    with pytest.raises(hook.RecallOff, match="inside"):
        hook._recall_program()
    assert not marker.exists()


def test_the_hook_s_own_environment_inside_the_repository_stays_trusted(tmp_path, store, repo, monkeypatch):
    own = _plant(repo / ".venv" / "bin", tmp_path / "own.log")
    monkeypatch.chdir(repo / "sub")
    monkeypatch.setattr(sys, "prefix", str(repo / ".venv"))
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    assert _hook_module()._recall_program() == str(own)


def test_without_memforge_no_memory_recall_found_says_recall_is_off(tmp_path, store, repo):
    proc = _run_hook(STANDALONE, repo / "sub", os.pathsep.join(["/usr/bin", "/bin"]))
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == "memory_recall_hook: recall is off: no memory-recall on an absolute PATH entry\n"


def test_with_memforge_no_memory_recall_found_is_refused(tmp_path, store, monkeypatch):
    hook = _hook_module()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env-without-memforge"))
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    with pytest.raises(hook.RecallOff, match="no memory-recall on an absolute PATH entry"):
        hook._recall_program()


def _layout(tmp_path: Path, where: str) -> tuple[Path, Path, str | None, str]:
    """One fixture for the two lookups: (working folder, folder holding the
    planted memory-recall, MEMFORGE_MEMORY_PATH or None, expected outcome)."""
    from memforge.paths import claude_code_project_dirname

    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    repo = tmp_path / "repo"
    for folder in (home, elsewhere, repo / ".git", repo / "sub"):
        folder.mkdir(parents=True, exist_ok=True)
    env_store = tmp_path / "store"
    env_store.mkdir()
    if where == "repository of a MEMFORGE_MEMORY_PATH store":
        (tmp_path / "team" / ".git").mkdir(parents=True)
        (tmp_path / "team" / "mem").mkdir()
        return elsewhere, tmp_path / "team" / "tools", str(tmp_path / "team" / "mem"), "off"
    if where == "MEMFORGE_MEMORY_PATH store":
        return elsewhere, env_store / "bin", str(env_store), "off"
    if where == "outside every folder":
        return elsewhere, tmp_path / "tools", str(env_store), "found"
    if where == "repository of the working folder":
        return repo / "sub", repo / "tools", str(env_store), "off"
    if where == "working folder":
        return elsewhere, elsewhere, str(env_store), "off"
    if where == "home folder while MEMFORGE_MEMORY_PATH is set":
        (home / ".memforge" / "memory").mkdir(parents=True)
        return elsewhere, home / ".memforge" / "bin", str(env_store), "off"
    if where == "Claude Code global memory while MEMFORGE_MEMORY_PATH is set":
        (home / ".claude" / "global-memory").mkdir(parents=True)
        return elsewhere, home / ".claude" / "global-memory" / "bin", str(env_store), "off"
    if where == "Claude Code projects folder while MEMFORGE_MEMORY_PATH is set":
        (home / ".claude" / "projects").mkdir(parents=True)
        return elsewhere, home / ".claude" / "projects" / "-other" / "bin", str(env_store), "off"
    if where == "outside every folder with the default store":
        (home / ".memforge" / "memory").mkdir(parents=True)
        return elsewhere, home / ".local" / "bin", None, "found"
    if where == "repository of the default store":
        (home / ".git").mkdir()
        (home / ".memforge" / "memory").mkdir(parents=True)
        return elsewhere, home / ".local" / "bin", None, "off"
    if where == "memforge home beside the default store":
        (home / ".memforge" / "memory").mkdir(parents=True)
        return elsewhere, home / ".memforge" / "bin", None, "off"
    if where == "Claude Code project store":
        (home / ".claude" / "global-memory").mkdir(parents=True)
        memory = home / ".claude" / "projects" / claude_code_project_dirname(str(repo)) / "memory"
        memory.mkdir(parents=True)
        return repo / "sub", memory / "bin", None, "off"
    if where == "Claude Code project store of a long path":
        long_cwd = tmp_path / ("p" * 230)
        long_cwd.mkdir()
        memory = home / ".claude" / "projects" / claude_code_project_dirname(str(long_cwd)) / "memory"
        memory.mkdir(parents=True)
        return long_cwd, memory / "bin", None, "off"
    if where == "another project's Claude Code store":
        (home / ".claude" / "global-memory").mkdir(parents=True)
        return repo / "sub", home / ".claude" / "projects" / "-other" / "memory" / "bin", None, "off"
    raise AssertionError(where)


LAYOUTS = (
    "repository of a MEMFORGE_MEMORY_PATH store", "MEMFORGE_MEMORY_PATH store", "outside every folder",
    "repository of the working folder", "working folder", "home folder while MEMFORGE_MEMORY_PATH is set",
    "repository of the default store", "memforge home beside the default store", "Claude Code project store",
    "Claude Code project store of a long path", "another project's Claude Code store",
    "Claude Code global memory while MEMFORGE_MEMORY_PATH is set",
    "Claude Code projects folder while MEMFORGE_MEMORY_PATH is set",
    "outside every folder with the default store",
)

# The folders the fallback refused before the memforge lookup was brought into
# agreement with it: each stays refused by both lookups.
STILL_REFUSED = (
    "home folder while MEMFORGE_MEMORY_PATH is set", "memforge home beside the default store",
    "another project's Claude Code store", "Claude Code global memory while MEMFORGE_MEMORY_PATH is set",
    "Claude Code projects folder while MEMFORGE_MEMORY_PATH is set",
)


@pytest.mark.parametrize("where", STILL_REFUSED)
def test_without_memforge_a_memory_recall_in_a_folder_it_refused_before_is_still_refused(
        tmp_path, monkeypatch, where):
    cwd, folder, memory_path, expected = _layout(tmp_path, where)
    assert expected == "off"
    marker = tmp_path / "ran.log"
    _plant(folder, marker)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if memory_path is None:
        monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    else:
        monkeypatch.setenv("MEMFORGE_MEMORY_PATH", memory_path)
    monkeypatch.setenv("PATH", str(folder))
    monkeypatch.chdir(cwd)
    hook = _hook_module()
    with pytest.raises(hook.RecallOff, match="inside"):
        hook._recall_program_without_memforge()
    assert not marker.exists()


def test_with_memforge_the_hook_s_own_environment_inside_the_memforge_home_stays_trusted(
        tmp_path, monkeypatch):
    """The folders added to the refused set apply to a program found on PATH,
    not to the environment the hook itself runs from."""
    home = tmp_path / "home"
    (home / ".memforge" / "memory").mkdir(parents=True)
    own = _plant(home / ".memforge" / "venv" / "bin", tmp_path / "own.log")
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.chdir(tmp_path / "elsewhere")
    monkeypatch.setattr(sys, "prefix", str(home / ".memforge" / "venv"))
    assert _hook_module()._recall_program() == str(own)


@pytest.mark.parametrize("where", LAYOUTS)
def test_the_lookup_without_memforge_refuses_exactly_what_the_memforge_lookup_refuses(
        tmp_path, monkeypatch, where):
    cwd, folder, memory_path, expected = _layout(tmp_path, where)
    planted = _plant(folder, tmp_path / "ran.log")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if memory_path is None:
        monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    else:
        monkeypatch.setenv("MEMFORGE_MEMORY_PATH", memory_path)
    monkeypatch.setenv("PATH", str(folder))
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env-without-memforge"))
    hook = _hook_module()

    def outcome(lookup) -> tuple[str, str]:
        try:
            return "found", lookup()
        except hook.RecallOff as exc:
            return "off", str(exc)

    with_memforge = outcome(hook._recall_program)
    without_memforge = outcome(hook._recall_program_without_memforge)
    assert with_memforge == without_memforge
    assert with_memforge[0] == expected
    if expected == "found":
        assert with_memforge[1] == str(planted)


def test_without_memforge_a_memory_recall_in_the_repository_of_a_store_is_refused(tmp_path, monkeypatch):
    team = tmp_path / "team"
    (team / ".git").mkdir(parents=True)
    (team / "mem").mkdir()
    (tmp_path / "elsewhere").mkdir()
    marker = tmp_path / "ran.log"
    _plant(team / "tools", marker)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(team / "mem"))
    proc = _run_hook(STANDALONE, tmp_path / "elsewhere", os.pathsep.join([str(team / "tools"), "/usr/bin", "/bin"]))
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not marker.exists()
    assert proc.stderr.startswith("memory_recall_hook: recall is off: ")
    assert f"inside {team}" in proc.stderr


@pytest.mark.parametrize("project", ["/home/alice/notes", "/tmp/" + "x" * 240, "/home/zoë/日本", "C:\\Users\\a b"])
def test_the_hook_names_a_claude_code_project_folder_as_memforge_does(project):
    from memforge.paths import claude_code_project_dirname

    assert _hook_module()._claude_code_project_dirname(project) == claude_code_project_dirname(project)


def test_the_readme_registers_the_hook_with_an_absolute_interpreter():
    commands = re.findall(r'"command":\s*"([^"]*memory_recall_hook\.py)"', README.read_text(encoding="utf-8"))
    assert commands
    for command in commands:
        interpreter = command.split(" ", 1)[0]
        assert os.path.isabs(interpreter), command
