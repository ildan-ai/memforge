"""Every program a tool starts is found only through absolute PATH entries,
and is refused when it resolves inside the folder the tool works on.

The tools run from inside memory stores, where anyone who can commit can
place an executable named `git` or `detect-secrets`. A `.`, empty or relative
PATH entry finds it there, and so does the current-folder search Windows does
for a bare program name. Each test plants such a program and fails if it ever
runs.

Windows is simulated the way tests/test_sync_list_and_tools.py does it; the
bare-name case is checked by recording the program each tool starts, since a
bare name is what Windows looks up in the current folder first.
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

from memforge.cli import dedup, detect, dlp_scan, promote, resolve, rollup, watch
from memforge.sync import run_sync

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None,
                                reason="planted programs are POSIX scripts; needs git")

REAL_PATH = os.environ.get("PATH", os.defpath)
# The tests' own git, found before any test plants one.
GIT = shutil.which("git") or "git"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=60).stdout


def _plant(folder: Path, name: str, marker: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / name
    script.write_text(f'#!/bin/sh\necho "$0 $*" >> "{marker}"\nexit 1\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _member(folder: Path, name: str, uid: str) -> Path:
    path = folder / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: fixture {name}\ntype: feedback\nuid: {uid}\n"
        "status: active\ndecision_topic: the-topic\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def lab(tmp_path: Path, monkeypatch):
    """A committed store that is also the current folder, and a scratch HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    store = tmp_path / "store"
    store.mkdir()
    _git(store, "init", "-q")
    _git(store, "config", "user.email", "test@example.com")
    _git(store, "config", "user.name", "Test")
    _git(store, "config", "commit.gpgsign", "false")
    (store / "MEMORY.md").write_text("# Memory Index\n\n- [A](feedback_a.md): a\n", encoding="utf-8")
    _member(store, "feedback_a", "mem-a")
    _member(store, "feedback_b", "mem-b")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "seed")
    monkeypatch.chdir(store)
    return tmp_path, store


def _stub(folder: Path, name: str, body: str) -> Path:
    """A stand-in for an optional helper program, on an absolute PATH entry
    outside the store."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / name
    script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    script.chmod(0o755)
    return script


_DETECT_SECRETS = 'import json\nprint(json.dumps({"results": {}}))\n'
_LINK_REWRITER = (
    "import json, os, sys\n"
    "for pair in json.load(sys.stdin):\n"
    "    os.rename(pair['src'], pair['dst'])\n"
)


# ---------- one scenario per tool that starts a program ----------

def _dlp(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["memory-dlp-scan", *argv])
    return dlp_scan.main()


def run_dlp_staged(tmp_path, store, monkeypatch):
    (store / "new.md").write_text("plain\n", encoding="utf-8")
    _git(store, "add", "new.md")
    return _dlp(monkeypatch, "--staged", "--no-detect-secrets")


def run_dlp_deep(tmp_path, store, monkeypatch):
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(store))
    return _dlp(monkeypatch, "--memory-folders")


def run_watch(tmp_path, store, monkeypatch):
    (store / "feedback_a.md").write_text("changed\n", encoding="utf-8")
    watch.CommitDebouncer(store, debounce_ms=10, quiet=True)._commit()


def run_resolve(tmp_path, store, monkeypatch):
    return resolve.main(["the-topic", "--memory-root", str(store), "--winner-uid", "mem-b"])


def run_promote(tmp_path, store, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "-q")
    _git(target, "config", "user.email", "test@example.com")
    _git(target, "config", "user.name", "Test")
    return promote.main(["feedback_a.md", "--source", str(store), "--target", str(target), "--yes"])


def run_rollup(tmp_path, store, monkeypatch):
    return rollup.cmd_create(store, "cluster", [store / "feedback_a.md", store / "feedback_b.md"],
                             None, None, dry_run=False)


SCENARIOS = {
    "dlp-staged": (run_dlp_staged, "git"),
    "dlp-deep": (run_dlp_deep, "detect-secrets"),
    "watch": (run_watch, "git"),
    "resolve": (run_resolve, "git"),
    "promote": (run_promote, "git"),
    "rollup": (run_rollup, "memory-link-rewriter"),
}


def _outside_stubs(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "detect-secrets", _DETECT_SECRETS)
    _stub(bin_dir, "memory-link-rewriter", _LINK_REWRITER)
    return bin_dir


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("entry", [".", ""])
def test_a_relative_path_entry_never_runs_a_program_planted_in_the_store(lab, monkeypatch, scenario, entry):
    tmp_path, store = lab
    run, name = SCENARIOS[scenario]
    marker = tmp_path / "ran"
    _plant(store, name, marker)
    bin_dir = _outside_stubs(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([entry, str(bin_dir), REAL_PATH]))

    run(tmp_path, store, monkeypatch)

    assert not marker.exists(), marker.read_text(encoding="utf-8")


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_program_inside_the_store_is_refused_before_it_runs(lab, monkeypatch, capsys, scenario):
    tmp_path, store = lab
    run, name = SCENARIOS[scenario]
    marker = tmp_path / "ran"
    _plant(store / "bin", name, marker)
    bin_dir = _outside_stubs(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([str(store / "bin"), str(bin_dir), REAL_PATH]))

    rc = run(tmp_path, store, monkeypatch)

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert f"{name} resolves to {store / 'bin' / name}, inside {store}" in captured.err + captured.out
    if scenario == "dlp-deep":
        assert "[deep_scan_failed]" in captured.out
    elif scenario != "watch":
        assert rc == 2


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_windows_starts_every_program_by_its_absolute_path(lab, monkeypatch, scenario):
    """On Windows a bare name runs a same-named program in the current folder
    first, and the tools run from inside the store."""
    tmp_path, store = lab
    run, name = SCENARIOS[scenario]
    marker = tmp_path / "ran"
    _plant(store, f"{name}.CMD", marker)
    bin_dir = _outside_stubs(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), REAL_PATH]))
    monkeypatch.setenv("PATHEXT", os.pathsep.join([".CMD", "."]))
    started: list[str] = []
    real_popen = subprocess.Popen

    class Recording(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args, *rest, **kwargs):
            started.append(args if isinstance(args, str) else os.fspath(args[0]))
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    monkeypatch.setattr(shutil, "_win_path_needs_curdir", lambda cmd, mode: True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    run(tmp_path, store, monkeypatch)

    assert started, "the scenario started no program"
    for program in started:
        assert os.path.isabs(program), started
        assert not Path(program).resolve().is_relative_to(store.resolve()), started
    assert not marker.exists()


# ---------- local-model dispatchers probed on PATH ----------

def _dispatchers(store: Path) -> dict[str, object]:
    return {
        "dedup": lambda: dedup.default_dispatcher(store),
        "detect": lambda: detect._default_dispatcher([store]),
    }


@pytest.mark.parametrize("tool", ["dedup", "detect"])
def test_a_probed_dispatcher_is_never_one_planted_in_the_store(lab, monkeypatch, tool):
    tmp_path, store = lab
    _plant(store, "ollama", tmp_path / "ran")
    monkeypatch.setenv("PATH", os.pathsep.join([".", REAL_PATH]))

    command = _dispatchers(store)[tool]()

    program = (command or "").split(" ")[0]
    # With no local model runner installed the probe names no program; only
    # a program it names can lie in the store.
    if program not in ("", "no-local-dispatcher-found"):
        assert os.path.isabs(program), command
        assert not Path(program).resolve().is_relative_to(store.resolve()), command


@pytest.mark.parametrize("tool", ["dedup", "detect"])
def test_a_probed_dispatcher_outside_the_store_wins_over_one_planted_in_it(lab, monkeypatch, tool):
    tmp_path, store = lab
    _plant(store, "ollama", tmp_path / "ran")
    outside = _plant(tmp_path / "runner", "ollama", tmp_path / "ran")
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(outside.parent)]))

    command = _dispatchers(store)[tool]()

    assert (command or "").split(" ")[0] == str(outside), command


@pytest.mark.parametrize("tool", ["dedup", "detect"])
def test_windows_dispatcher_probe_skips_the_current_folder(lab, monkeypatch, tool):
    tmp_path, store = lab
    _plant(store, "ollama.CMD", tmp_path / "ran")
    monkeypatch.setenv("PATH", REAL_PATH)
    monkeypatch.setenv("PATHEXT", os.pathsep.join([".CMD", "."]))
    monkeypatch.setattr(shutil, "_win_path_needs_curdir", lambda cmd, mode: True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    command = _dispatchers(store)[tool]()

    assert "ollama.CMD" not in (command or ""), command


def test_dedup_refuses_a_probed_dispatcher_inside_the_folder(lab, monkeypatch, capsys):
    tmp_path, store = lab
    marker = tmp_path / "ran"
    _plant(store / "bin", "ollama", marker)
    monkeypatch.setenv("PATH", os.pathsep.join([str(store / "bin"), REAL_PATH]))
    monkeypatch.delenv("MEMORY_DEDUP_DISPATCHER", raising=False)
    monkeypatch.setattr("sys.argv", ["memory-dedup", "--path", str(store)])

    rc = dedup.main()

    assert rc == 2
    assert f"ollama resolves to {store / 'bin' / 'ollama'}, inside {store}" in capsys.readouterr().err
    assert not marker.exists()


def test_detect_skips_a_probed_dispatcher_inside_the_folder(lab, monkeypatch, capsys):
    tmp_path, store = lab
    _plant(store / "bin", "ollama", tmp_path / "ran")
    monkeypatch.setenv("PATH", os.pathsep.join([str(store / "bin"), REAL_PATH]))

    assert detect._default_dispatcher([store]) is None
    assert f"ollama resolves to {store / 'bin' / 'ollama'}, inside {store}" in capsys.readouterr().err


# ---------- memory-watch startup ----------

def test_watch_refuses_to_start_with_a_git_inside_the_folder(lab, monkeypatch, capsys):
    tmp_path, store = lab
    _plant(store / "bin", "git", tmp_path / "ran")
    monkeypatch.setenv("PATH", os.pathsep.join([str(store / "bin"), REAL_PATH]))

    def no_observer():
        raise AssertionError("the watcher started")

    monkeypatch.setattr(watch, "Observer", no_observer)
    monkeypatch.setattr("sys.argv", ["memory-watch", "--path", str(store)])

    assert watch.main() == 2
    assert f"git resolves to {store / 'bin' / 'git'}, inside {store}" in capsys.readouterr().err


# ---------- memforge sync checks its root before any tool runs ----------

def test_sync_refuses_a_root_below_the_top_level_before_any_tool_runs(lab, monkeypatch, capsys):
    tmp_path, store = lab
    marker = tmp_path / "ran"
    _plant(store / "bin", "git", marker)
    sub = store / "sub"
    sub.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(store / "bin"), REAL_PATH]))
    monkeypatch.chdir(sub)

    assert run_sync(sub) == 1

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "root_not_toplevel" in err
    assert str(store) in err


def test_sync_refuses_a_folder_outside_any_repository_before_any_tool_runs(tmp_path, monkeypatch, capsys):
    loose = tmp_path / "loose"
    marker = tmp_path / "ran"
    _plant(tmp_path / "bin", "git", marker)
    loose.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "bin"), REAL_PATH]))

    assert run_sync(loose) == 1

    assert not marker.exists()
    assert "not_a_repository" in capsys.readouterr().err


def test_dlp_paths_scan_still_runs_detect_secrets_from_an_absolute_path_entry(lab, monkeypatch, capsys):
    """A well-formed setup: the deep layer runs as before."""
    tmp_path, store = lab
    record = tmp_path / "calls.json"
    _stub(tmp_path / "bin", "detect-secrets",
          f"import json, os, sys\nopen({str(record)!r}, 'w').write(json.dumps(sys.argv))\n"
          'print(json.dumps({"results": {}}))\n')
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "bin"), REAL_PATH]))

    rc = _dlp(monkeypatch, "--paths", str(store / "feedback_a.md"), "--strict")

    assert rc == 0
    assert json.loads(record.read_text(encoding="utf-8"))[0] == str(tmp_path / "bin" / "detect-secrets")
    assert "DLP: clean" in capsys.readouterr().out
