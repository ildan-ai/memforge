"""A program inside the repository that holds the folder a tool works on is
refused, not only one inside that folder.

A memory folder can sit below the top of the repository it belongs to. git
then acts on that whole repository, and anyone who can commit anywhere in it
can place an executable named `git` or `detect-secrets` beside the folder. An
absolute PATH entry that points there would find it. Each test plants such a
program outside the folder, inside its repository, and fails if it ever runs.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from memforge._programs import ProgramInFolder, find_program_outside
from memforge.cli import dlp_scan, promote, resolve, rollup, watch

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None,
                                reason="planted programs are POSIX scripts; needs git")

REAL_PATH = os.environ.get("PATH", os.defpath)
GIT = shutil.which("git") or "git"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=60).stdout


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _plant(folder: Path, name: str, marker: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / name
    script.write_text(f'#!/bin/sh\necho "$0 $*" >> "{marker}"\nexit 1\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _stub(folder: Path, name: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / name
    script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    script.chmod(0o755)


def _member(folder: Path, name: str, uid: str) -> None:
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: fixture {name}\ntype: feedback\nuid: {uid}\n"
        "status: active\ndecision_topic: the-topic\n---\n\nBody.\n",
        encoding="utf-8",
    )


@pytest.fixture
def lab(tmp_path: Path, monkeypatch):
    """A repository whose memory folder is a subfolder, the current folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    repo = tmp_path / "repo"
    _init(repo)
    store = repo / "notes" / "memory"
    store.mkdir(parents=True)
    (store / "MEMORY.md").write_text("# Memory Index\n\n- [A](feedback_a.md): a\n", encoding="utf-8")
    _member(store, "feedback_a", "mem-a")
    _member(store, "feedback_b", "mem-b")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    monkeypatch.chdir(store)
    return tmp_path, repo, store


def _dlp(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["memory-dlp-scan", *argv])
    return dlp_scan.main()


def run_dlp_staged(tmp_path, repo, store, monkeypatch):
    (store / "new.md").write_text("plain\n", encoding="utf-8")
    _git(repo, "add", "-A")
    return _dlp(monkeypatch, "--staged", "--no-detect-secrets")


def run_dlp_staged_deep(tmp_path, repo, store, monkeypatch):
    (store / "new.md").write_text("plain\n", encoding="utf-8")
    _git(repo, "add", "-A")
    return _dlp(monkeypatch, "--staged")


def run_dlp_deep(tmp_path, repo, store, monkeypatch):
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(store))
    return _dlp(monkeypatch, "--memory-folders")


def run_watch(tmp_path, repo, store, monkeypatch):
    (store / "feedback_a.md").write_text("changed\n", encoding="utf-8")
    watch.CommitDebouncer(store, debounce_ms=10, quiet=True)._commit()


def run_resolve(tmp_path, repo, store, monkeypatch):
    return resolve.main(["the-topic", "--memory-root", str(store), "--winner-uid", "mem-b"])


def run_promote(tmp_path, repo, store, monkeypatch):
    target = tmp_path / "target"
    _init(target)
    return promote.main(["feedback_a.md", "--source", str(store), "--target", str(target), "--yes"])


def run_rollup(tmp_path, repo, store, monkeypatch):
    return rollup.cmd_create(store, "cluster", [store / "feedback_a.md", store / "feedback_b.md"],
                             None, None, dry_run=False)


SCENARIOS = {
    "dlp-staged": (run_dlp_staged, "git"),
    "dlp-staged-deep": (run_dlp_staged_deep, "detect-secrets"),
    "dlp-deep": (run_dlp_deep, "detect-secrets"),
    "watch": (run_watch, "git"),
    "resolve": (run_resolve, "git"),
    "promote": (run_promote, "git"),
    "rollup": (run_rollup, "memory-link-rewriter"),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_program_elsewhere_in_the_enclosing_repository_is_refused(lab, monkeypatch, capsys, scenario):
    tmp_path, repo, store = lab
    run, name = SCENARIOS[scenario]
    marker = tmp_path / "ran"
    _plant(repo / "bin", name, marker)
    outside = tmp_path / "outside-bin"
    _stub(outside, "detect-secrets", 'import json\nprint(json.dumps({"results": {}}))\n')
    monkeypatch.setenv("PATH", os.pathsep.join([str(repo / "bin"), str(outside), REAL_PATH]))
    if scenario == "rollup":
        # The checkout's own rewriter is found first; make the PATH one the only one.
        monkeypatch.setattr(rollup, "find_link_rewriter", lambda: repo / "bin" / name)

    rc = run(tmp_path, repo, store, monkeypatch)

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert f"{name} resolves to {repo / 'bin' / name}, inside {repo}" in captured.err + captured.out
    if scenario in ("dlp-deep", "dlp-staged-deep"):
        assert "[deep_scan_failed]" in captured.out
    elif scenario != "watch":
        assert rc == 2


def test_a_nested_repository_is_its_own_top(tmp_path, monkeypatch):
    """A store with its own .git entry inside a larger checkout: git acts on
    the store's repository, so a program beside it in the parent is not
    inside the repository the tool works on."""
    parent = tmp_path / "parent"
    store = parent / "memory"
    (store / ".git").mkdir(parents=True)
    (parent / ".git").mkdir()
    _plant(parent / "bin", "tool", tmp_path / "ran")
    monkeypatch.setenv("PATH", str(parent / "bin"))

    assert find_program_outside("tool", store) == str(parent / "bin" / "tool")


def test_the_repository_top_is_found_by_its_git_entry_without_running_git(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "a" / "b").mkdir(parents=True)
    (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    _plant(repo / "bin", "tool", tmp_path / "ran")
    monkeypatch.setenv("PATH", str(repo / "bin"))

    with pytest.raises(ProgramInFolder) as caught:
        find_program_outside("tool", repo / "a" / "b")

    assert caught.value.folder == repo


def test_a_program_outside_the_repository_is_still_found(lab, monkeypatch):
    tmp_path, repo, store = lab
    _plant(tmp_path / "tools", "tool", tmp_path / "ran")
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))

    assert find_program_outside("tool", store) == str(tmp_path / "tools" / "tool")


def test_init_store_never_runs_a_git_elsewhere_in_the_repository(lab, monkeypatch, capsys):
    from memforge.cli import _dispatch

    tmp_path, repo, store = lab
    marker = tmp_path / "ran"
    _plant(repo / "bin", "git", marker)
    monkeypatch.setenv("PATH", os.pathsep.join([str(repo / "bin"), REAL_PATH]))

    assert _dispatch.main(["init-store", "--memory-root", str(store), "--mode", "ssh"]) == 0

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert f"inside {repo}" in capsys.readouterr().err


def _project_with_venv(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A project repository whose memory folder is a subfolder, with a
    virtual environment inside the repository."""
    repo = tmp_path / "project"
    _init(repo)
    store = repo / "memory"
    store.mkdir()
    _member(store, "feedback_a", "mem-a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    venv = repo / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    return repo, store, venv


def _dlp_from(venv: Path, store: Path, tmp_path: Path, path: str) -> subprocess.CompletedProcess:
    """memory-dlp-scan run by the venv's own interpreter, so sys.prefix is the venv."""
    import sysconfig

    src = Path(__file__).resolve().parents[1] / "src"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": str(home), "PATH": path, "MEMFORGE_MEMORY_PATH": str(store),
                "PYTHONPATH": os.pathsep.join([str(src), sysconfig.get_paths()["purelib"]])})
    return subprocess.run([str(venv / "bin" / "python"), "-m", "memforge.cli.dlp_scan", "--memory-folders"],
                          cwd=store, env=env, capture_output=True, text=True, timeout=120)


def test_the_environment_memforge_runs_from_is_trusted_inside_the_repository(tmp_path):
    """Whoever can write to the scripts folder of memforge's own environment
    can already change memforge's code, so a program there is run even when
    that environment sits inside the repository holding the memory folder."""
    repo, store, venv = _project_with_venv(tmp_path)
    marker = tmp_path / "ran"
    _stub(venv / "bin", "detect-secrets",
          f'import json, pathlib\npathlib.Path({str(marker)!r}).write_text("ran")\n'
          'print(json.dumps({"results": {}}))\n')

    proc = _dlp_from(venv, store, tmp_path, os.pathsep.join([str(venv / "bin"), REAL_PATH]))

    assert marker.exists(), proc.stdout + proc.stderr
    assert "deep_scan_failed" not in proc.stdout + proc.stderr


def test_a_program_elsewhere_in_the_repository_is_still_refused_from_that_environment(tmp_path):
    repo, store, venv = _project_with_venv(tmp_path)
    marker = tmp_path / "ran"
    _plant(repo / "bin", "detect-secrets", marker)

    proc = _dlp_from(venv, store, tmp_path, os.pathsep.join([str(repo / "bin"), str(venv / "bin"), REAL_PATH]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert f"detect-secrets resolves to {repo / 'bin' / 'detect-secrets'}, inside {repo}" in proc.stdout


def test_the_environment_memforge_runs_from_is_still_refused_inside_the_memory_folder(tmp_path):
    """The exemption covers only the enclosing repository: a store that holds
    the environment is refused as before."""
    repo = tmp_path / "store"
    _init(repo)
    _member(repo, "feedback_a", "mem-a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    venv = repo / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    marker = tmp_path / "ran"
    _plant(venv / "bin", "detect-secrets", marker)

    proc = _dlp_from(venv, repo, tmp_path, os.pathsep.join([str(venv / "bin"), REAL_PATH]))

    assert not marker.exists(), marker.read_text(encoding="utf-8")
    assert "[deep_scan_failed]" in proc.stdout
