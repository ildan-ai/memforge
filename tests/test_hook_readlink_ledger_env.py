"""The Claude Code hooks and the example watcher: how they follow links, what
they write, and which repository their git acts on.

- Both hooks follow every program's links with `readlink` to see where it
  really lies. A `readlink` that is itself a link is never run: its target
  could be a program planted in a store.
- The read tracker never reads or writes its `.last_used.json` ledger through
  a link, or when it is not a regular file. A committed link would make it
  copy any JSON file of the reader's into the store, or create a file outside
  the store.
- git in the auto-commit hook and the example watcher acts on the store,
  whatever repository-local variables (`GIT_DIR` and the rest) the caller's
  environment carries.
"""

# The shared `store` fixture is imported, so every test that takes it reads as
# a redefinition to the linter.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_hook_program_lookup import (  # noqa: F401  (store is a fixture)
    AUTO_COMMIT,
    READ_TRACKER,
    _env,
    _git,
    _home,
    _plant,
    _read,
    _run,
    _tools_path,
    store,
)
from tests.test_watcher_control_files import _env as _watcher_env
from tests.test_watcher_control_files import _git as _watcher_git
from tests.test_watcher_control_files import _run_watcher_once

pytestmark = [
    pytest.mark.skipif(os.name == "nt", reason="bash scripts"),
    pytest.mark.skipif(any(shutil.which(t) is None for t in ("bash", "git", "jq", "python3")),
                       reason="needs bash, git, jq and python3"),
]


def _linked_tools(tmp_path: Path, store: Path, marker: Path) -> Path:
    """A folder outside the store whose `readlink` links to one planted in the
    store, and whose `jq` is a link to the real one, so following `jq` needs
    `readlink`."""
    _plant(store / "bin", ("readlink",), marker)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "readlink").symlink_to(store / "bin" / "readlink")
    (linked / "jq").symlink_to(shutil.which("jq"))
    return linked


def _subjects(repo: Path) -> list[str]:
    return _git(repo, _env(_home(repo), os.pathsep.join(_tools_path())), "log", "--format=%s").splitlines()


@pytest.mark.parametrize("hook", ["auto-commit", "read-tracker"])
def test_a_readlink_that_is_a_link_is_never_run(store, tmp_path, hook):
    marker = tmp_path / "ran"
    linked = _linked_tools(tmp_path, store, marker)
    path = os.pathsep.join([str(linked), *_tools_path()])

    if hook == "auto-commit":
        note = store / "note.md"
        note.write_text("new\n", encoding="utf-8")
        _run(AUTO_COMMIT, store, path, {"tool_name": "Write", "tool_input": {"file_path": str(note)}})
    else:
        _read(store, path)

    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_the_hooks_still_act_with_a_linked_jq_and_a_real_readlink(store, tmp_path):
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "jq").symlink_to(shutil.which("jq"))
    path = os.pathsep.join([str(linked), *_tools_path()])
    note = store / "note.md"
    note.write_text("new\n", encoding="utf-8")

    _run(AUTO_COMMIT, store, path, {"tool_name": "Write", "tool_input": {"file_path": str(note)}})
    _read(store, path)

    assert _subjects(store)[0] == "memory: Write note.md"
    assert list(json.loads((store / ".last_used.json").read_text(encoding="utf-8"))) == ["seed.md"]


def test_the_read_tracker_never_reads_a_linked_ledger(store, tmp_path):
    secret = tmp_path / "home" / ".docker" / "config.json"
    secret.parent.mkdir(parents=True)
    secret.write_text('{"auths": {"registry.example.invalid": {"auth": "FAKE"}}}\n', encoding="utf-8")
    ledger = store / ".last_used.json"
    ledger.symlink_to(os.path.relpath(secret, store))

    _read(store, os.pathsep.join(_tools_path()))

    assert ledger.is_symlink()
    assert json.loads(secret.read_text(encoding="utf-8")) == {
        "auths": {"registry.example.invalid": {"auth": "FAKE"}}}


def test_the_read_tracker_never_creates_a_file_through_a_dangling_link(store, tmp_path):
    outside = tmp_path / "created-outside"
    ledger = store / ".last_used.json"
    ledger.symlink_to(outside)

    _read(store, os.pathsep.join(_tools_path()))

    assert not outside.exists()
    assert ledger.is_symlink()


def test_the_read_tracker_leaves_a_ledger_that_is_not_a_file(store, tmp_path):
    (store / ".last_used.json").mkdir()

    _read(store, os.pathsep.join(_tools_path()))

    assert (store / ".last_used.json").is_dir()


def _decoy(tmp_path: Path, env: dict[str, str]) -> tuple[Path, dict[str, str]]:
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, env, "init", "-q")
    _git(decoy, env, "config", "user.email", "test@example.com")
    _git(decoy, env, "config", "user.name", "Test")
    (decoy / "x.md").write_text("x\n", encoding="utf-8")
    _git(decoy, env, "add", "-A")
    _git(decoy, env, "commit", "-q", "-m", "decoy seed")
    return decoy, {"GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy),
                   "GIT_INDEX_FILE": str(decoy / ".git" / "index")}


def test_the_auto_commit_hook_commits_to_the_store_whatever_git_dir_says(store, tmp_path):
    env = _env(_home(store), os.pathsep.join(_tools_path()))
    decoy, local = _decoy(tmp_path, env)
    note = store / "note.md"
    note.write_text("new\n", encoding="utf-8")

    subprocess.run(["bash", str(AUTO_COMMIT)],
                   input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(note)}}),
                   env={**env, **local}, cwd=store, text=True, check=True, timeout=60)

    assert _subjects(store)[0] == "memory: Write note.md"
    assert _git(decoy, env, "log", "--format=%s").splitlines() == ["decoy seed"]


def test_the_example_watcher_commits_to_the_store_whatever_git_dir_says(tmp_path):
    env = _watcher_env(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    _watcher_git(store, env, "init", "-q")
    _watcher_git(store, env, "config", "user.email", "test@example.com")
    _watcher_git(store, env, "config", "user.name", "Test")
    (store / "seed.md").write_text("seed\n", encoding="utf-8")
    _watcher_git(store, env, "add", "-A")
    _watcher_git(store, env, "commit", "-q", "-m", "seed")
    decoy, local = _decoy(tmp_path, env)
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_watcher_once(store, {**env, **local}, [note], tmp_path)

    assert _watcher_git(store, env, "log", "--format=%s").splitlines() == ["memory: auto-commit note.md", "seed"]
    assert _watcher_git(decoy, env, "log", "--format=%s").splitlines() == ["decoy seed"]


@pytest.mark.parametrize("layout", ["linked-readlink-first", "linked-folder"])
def test_the_hooks_find_the_system_readlink(store, tmp_path, layout):
    """A readlink that is a link outside the stores (Homebrew coreutils puts
    one first on PATH) is passed over for the regular one after it; a PATH
    folder that is itself a link (a merged /bin -> usr/bin) is followed."""
    system = Path(shutil.which("readlink", path="/usr/bin:/bin") or "/usr/bin/readlink")
    linked = tmp_path / "linked"
    if layout == "linked-readlink-first":
        linked.mkdir()
        (linked / "readlink").symlink_to(system)
        (linked / "jq").symlink_to(shutil.which("jq"))
    else:
        linked.symlink_to(system.parent, target_is_directory=True)
    path = os.pathsep.join([str(linked), *_tools_path()])
    note = store / "note.md"
    note.write_text("new\n", encoding="utf-8")

    _run(AUTO_COMMIT, store, path, {"tool_name": "Write", "tool_input": {"file_path": str(note)}})
    _read(store, path)

    assert _subjects(store)[0] == "memory: Write note.md"
    assert list(json.loads((store / ".last_used.json").read_text(encoding="utf-8"))) == ["seed.md"]
