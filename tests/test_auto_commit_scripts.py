"""The shipped auto-commit scripts: what they may and may not commit.

Covers the Claude Code PostToolUse hook and the bash example watcher. Both
run with the user's rights on files someone else may have written.
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
HOOK = REPO / "adapters" / "claude-code" / "hooks" / "memory-auto-commit.sh"
WATCHER = REPO / "examples" / "scripts" / "memforge-auto-commit-watcher.sh"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="bash scripts"),
    pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"),
    pytest.mark.skipif(not HOOK.exists() or not WATCHER.exists(), reason="scripts not in this layout"),
]

SIGNER_LISTS = [
    ".memforge/allowed_signers",
    ".memforge/allowed_gpg_fingerprints",
    ".memforge/allowed_identities",
]


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": str(home), "USER": "tester", "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "1"})
    return env


def _tool_path(*tools: str) -> str:
    """A PATH with the named tools and the base system utilities, and nothing
    from the developer's own environment."""
    dirs = [str(Path(shutil.which(t)).parent) for t in tools] + ["/usr/bin", "/bin"]
    return os.pathsep.join(dict.fromkeys(dirs))


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True, check=True).stdout


def _store(path: Path, env: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, env, "init", "-q")
    _git(path, env, "config", "user.email", "test@example.com")
    _git(path, env, "config", "user.name", "Test")
    (path / "seed.md").write_text("seed\n", encoding="utf-8")
    (path / ".memforge").mkdir()
    for name in SIGNER_LISTS:
        (path / name).write_text("owner key\n", encoding="utf-8")
    _git(path, env, "add", "-A")
    _git(path, env, "commit", "-q", "-m", "seed")
    return path


def _run_hook(store: Path, env: dict[str, str], file_path: Path | str, tool_name: str = "Write") -> None:
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": str(file_path)}})
    # Keep the hook's optional helpers (backfill, recall) off the PATH so the
    # test exercises the commit step alone.
    env = {**env, "PATH": _tool_path("bash", "git", "jq")}
    subprocess.run(["bash", str(HOOK)], input=payload, env=env, text=True, check=True, timeout=60)


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
@pytest.mark.parametrize("name", SIGNER_LISTS)
def test_hook_never_commits_a_signer_list(tmp_path: Path, name: str):
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "global-memory", env)
    (store / name).write_text("owner key\nplanted key\n", encoding="utf-8")
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_hook(store, env, note)

    assert _git(store, env, "show", "--name-only", "--format=", "HEAD").split() == ["note.md"]
    assert _git(store, env, "show", f"HEAD:{name}") == "owner key\n"


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_does_not_commit_a_signer_list_that_is_the_written_file(tmp_path: Path):
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "global-memory", env)
    lst = store / SIGNER_LISTS[0]
    lst.write_text("owner key\nplanted key\n", encoding="utf-8")

    _run_hook(store, env, lst)

    assert _git(store, env, "log", "--format=%s").splitlines() == ["seed"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_leaves_files_outside_its_scope_staged(tmp_path: Path):
    """The hook commits the written memory file and what it regenerates. A
    file staged by hand, and the config and snooze files that only land in a
    deliberate commit, stay out of the automatic commit."""
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "global-memory", env)
    (store / "settings.env").write_text("token\n", encoding="utf-8")
    _git(store, env, "add", "settings.env")
    (store / ".memforge" / "config.yaml").write_text("recall: {}\n", encoding="utf-8")
    (store / ".memforge" / "snoozes").mkdir()
    (store / ".memforge" / "snoozes" / "s.json").write_text("{}\n", encoding="utf-8")
    (store / ".memforge" / "recall-index.json").write_text("{}\n", encoding="utf-8")
    (store / "MEMORY.md").write_text("index\n", encoding="utf-8")
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_hook(store, env, note)

    committed = sorted(_git(store, env, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == ["MEMORY.md", "note.md"]
    status = _git(store, env, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert "A  settings.env" in status
    assert "?? .memforge/config.yaml" in status
    assert "?? .memforge/snoozes/s.json" in status


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
@pytest.mark.parametrize("rel", [
    ".memforge/audit-waivers.yaml",
    ".memforge/recall-synonyms.yaml",
    ".memforge/notes.md",
])
def test_hook_commits_no_memforge_control_file(tmp_path: Path, rel: str):
    """Nothing under `.memforge/` rides along in an automatic commit, the recall
    index (a local cache) included, whether it is the written file or only lies
    beside it: a waiver, for one, may land only in its own `memforge: waiver`
    commit."""
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "global-memory", env)
    control = store / rel
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text("planted\n", encoding="utf-8")
    (store / ".memforge" / "recall-index.json").write_text("{}\n", encoding="utf-8")
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_hook(store, env, control)
    _run_hook(store, env, note)

    committed = set(_git(store, env, "log", "--name-only", "--format=").split())
    assert rel not in committed
    assert ".memforge/recall-index.json" not in committed
    assert "note.md" in committed


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_commits_a_written_file_whose_name_is_a_pathspec_pattern(tmp_path: Path):
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "global-memory", env)
    (store / "private").mkdir()
    (store / "private" / "draft.txt").write_text("draft\n", encoding="utf-8")
    odd = store / ":(top)*.txt"
    odd.write_text("x\n", encoding="utf-8")

    _run_hook(store, env, odd)

    assert _git(store, env, "show", "--name-only", "--format=", "HEAD").splitlines() == [":(top)*.txt"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_commit_message_is_one_line_without_trailers(tmp_path: Path):
    """The file name and tool name come from the tool call. A newline in
    either must not add lines, and so trailers, to a commit the store owner
    signs."""
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "global-memory", env)
    (store / "d.md").write_text("d\n", encoding="utf-8")
    injected = f"{store}/d.md\n\nSigned-off-by: someone <x@example.invalid>"

    _run_hook(store, env, injected, tool_name="Write\n\nReviewed-by: someone <x@example.invalid>")

    body = _git(store, env, "log", "-1", "--format=%B").rstrip("\n")
    assert "\n" not in body
    assert body.startswith("memory: Write")
    assert _git(store, env, "log", "-1", "--format=%(trailers)").strip() == ""


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_ignores_an_empty_project_segment(tmp_path: Path):
    """`projects//memory/` has no project name, so `projects/memory` is not a
    project memory folder."""
    env = _env(tmp_path)
    outside = _store(tmp_path / ".claude" / "projects" / "memory", env)
    (outside / "x.md").write_text("x\n", encoding="utf-8")

    _run_hook(outside, env, f"{tmp_path}/.claude/projects//memory/x.md")

    assert _git(outside, env, "log", "--format=%s").splitlines() == ["seed"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_commits_in_a_claude_code_project_memory_folder(tmp_path: Path):
    """Claude Code names each project folder after the project path, so the
    hook matches any folder under projects/, not one named after the user."""
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "projects" / "-work-alice-notes" / "memory", env)
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_hook(store, env, note)

    assert _git(store, env, "log", "--format=%s").splitlines() == ["memory: Write note.md", "seed"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
def test_hook_ignores_a_folder_that_is_not_a_project_memory_root(tmp_path: Path):
    env = _env(tmp_path)
    store = _store(tmp_path / ".claude" / "projects" / "-work-alice-notes" / "scratch" / "memory", env)
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_hook(store, env, note)

    assert _git(store, env, "log", "--format=%s").splitlines() == ["seed"]


def _run_watcher_once(store: Path, env: dict[str, str], changed: Path, tmp_path: Path) -> None:
    """Run the watcher with a stand-in `fswatch` that reports one path and exits."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "fswatch"
    fake.write_text("#!/bin/sh\nprintf '%s\\0' \"$FAKE_CHANGED\"\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    path = os.pathsep.join([str(bindir), _tool_path("bash", "git", "python3")])
    subprocess.run(["bash", str(WATCHER), str(store)], env={**env, "PATH": path, "FAKE_CHANGED": str(changed)},
                   check=True, timeout=60)


def test_watcher_commits_only_the_changed_memory_file(tmp_path: Path):
    env = _env(tmp_path)
    store = _store(tmp_path / "store", env)
    (store / "settings.env").write_text("token\n", encoding="utf-8")
    _git(store, env, "add", "settings.env")
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_watcher_once(store, env, note, tmp_path)

    assert _git(store, env, "show", "--name-only", "--format=", "HEAD").split() == ["note.md"]
    assert "A  settings.env" in _git(store, env, "status", "--porcelain")


def test_watcher_reads_file_names_literally(tmp_path: Path):
    env = _env(tmp_path)
    store = _store(tmp_path / "store", env)
    (store / "private").mkdir()
    (store / "private" / "draft.md").write_text("draft\n", encoding="utf-8")
    odd = store / ":(top)*.md"
    odd.write_text("x\n", encoding="utf-8")

    _run_watcher_once(store, env, odd, tmp_path)

    committed = _git(store, env, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "private/draft.md" not in committed


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
@pytest.mark.parametrize("rel", ["projects/../memory/note.md", "projects/./memory/note.md",
                                 "projects/p/memory/../../../memory/note.md"])
def test_hook_ignores_a_path_with_dot_segments(tmp_path: Path, rel: str):
    """A path that names a folder outside projects/ through `.` or `..` must
    not pick a repository there."""
    env = _env(tmp_path)
    outside = _store(tmp_path / ".claude" / "memory", env)
    _store(tmp_path / ".claude" / "projects" / "p" / "memory", env)
    (outside / "note.md").write_text("note\n", encoding="utf-8")

    _run_hook(outside, env, tmp_path / ".claude" / rel)

    assert _git(outside, env, "log", "--format=%s").splitlines() == ["seed"]
