"""Automatic commits must not hide files from a user's pre-commit hook.

git hands its environment to hooks. A committing tool that exports
GIT_LITERAL_PATHSPECS makes the documented memory-validate hook, which selects
files with the pattern '*.md', match nothing and let a broken memory in. The
tools read their own names literally with the `:(literal)` pathspec magic
instead, and the variable never reaches the hook.
"""

from __future__ import annotations

import importlib.util
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
WATCHER_SH = REPO / "examples" / "scripts" / "memforge-auto-commit-watcher.sh"
WATCHER_PS1 = REPO / "examples" / "scripts" / "memforge-auto-commit-watcher.ps1"

# The pre-commit hook text the quickstart tells a user to install.
VALIDATE_HOOK = """#!/usr/bin/env bash
staged=$(git diff --cached --name-only --diff-filter=ACMT -- '*.md')
[ -z "$staged" ] && exit 0
git diff --cached --name-only -z --diff-filter=ACMT -- '*.md' | xargs -0 memory-validate -- || {
  echo "memory-validate: blocked commit; fix the frontmatter above and re-stage" >&2
  exit 1
}
"""

BROKEN = "---\nname: bad\ndescription: this breaks: here\n---\nbody\n"


def _env(home: Path, bindir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    src = str(REPO / "src")
    env.update({
        "HOME": str(home), "USER": "tester",
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONPATH": src, "PYTHONDONTWRITEBYTECODE": "1",
    })
    dirs = [str(bindir)] + [str(Path(shutil.which(t)).parent) for t in ("bash", "git", "jq", "python3")
                            if shutil.which(t)] + ["/usr/bin", "/bin"]
    env["PATH"] = os.pathsep.join(dict.fromkeys(dirs))
    return env


def _bin(path: Path) -> Path:
    """A folder holding a `memory-validate` that runs this tree's validator."""
    path.mkdir(parents=True, exist_ok=True)
    tool = path / "memory-validate"
    tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I -c '
                    '"import sys; sys.path.insert(0, sys.argv.pop(1)); '
                    'from memforge.cli.validate import main; sys.exit(main())" '
                    f'"{REPO / "src"}" "$@"\n', encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return path


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True,
                          text=True, check=True).stdout


def _store(path: Path, env: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, env, "init", "-q")
    _git(path, env, "config", "user.email", "test@example.com")
    _git(path, env, "config", "user.name", "Test")
    (path / "seed.md").write_text("---\nname: seed\ndescription: seed\n---\nseed\n", encoding="utf-8")
    _git(path, env, "add", "-A")
    _git(path, env, "commit", "-q", "-m", "seed")
    hook = path / ".git" / "hooks" / "pre-commit"
    hook.write_text(VALIDATE_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    return path


def _history(store: Path, env: dict[str, str]) -> list[str]:
    return _git(store, env, "log", "--format=%s").splitlines()


def _setup(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home, _bin(tmp_path / "bin"))
    return home, env


def test_the_documented_hook_blocks_a_broken_memory_on_a_plain_commit(tmp_path: Path):
    """Control: the hook text itself works when nothing hides files from it."""
    _home, env = _setup(tmp_path)
    store = _store(tmp_path / "store", env)
    (store / "bad.md").write_text(BROKEN, encoding="utf-8")
    _git(store, env, "add", "bad.md")
    res = subprocess.run(["git", "-C", str(store), "commit", "-q", "-m", "x"], env=env,
                         capture_output=True, text=True)
    assert res.returncode != 0
    assert _history(store, env) == ["seed"]


def test_memory_watch_commit_is_refused_by_the_validate_hook(tmp_path: Path):
    if importlib.util.find_spec("watchdog") is None:
        pytest.skip("watchdog not installed")
    from memforge.cli import watch
    _home, env = _setup(tmp_path)
    store = _store(tmp_path / "store", env)
    (store / "bad.md").write_text(BROKEN, encoding="utf-8")
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        watch.CommitDebouncer(store, debounce_ms=10, quiet=True)._commit()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert _history(store, env) == ["seed"]
    assert "bad.md" not in _git(store, env, "ls-tree", "-r", "--name-only", "HEAD")


def test_memory_watch_strips_an_inherited_literal_pathspecs_variable(tmp_path: Path):
    if importlib.util.find_spec("watchdog") is None:
        pytest.skip("watchdog not installed")
    from memforge.cli import watch
    _home, env = _setup(tmp_path)
    store = _store(tmp_path / "store", env)
    (store / "bad.md").write_text(BROKEN, encoding="utf-8")
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({**env, "GIT_LITERAL_PATHSPECS": "1"})
        watch.CommitDebouncer(store, debounce_ms=10, quiet=True)._commit()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert _history(store, env) == ["seed"]


@pytest.mark.parametrize("name", [
    pytest.param("*.md", marks=pytest.mark.skipif(
        os.name == "nt", reason="a Windows file name cannot hold an asterisk; the bracket name still runs")),
    "[a].md",
])
def test_memory_watch_still_reads_file_names_literally(tmp_path: Path, name: str):
    if importlib.util.find_spec("watchdog") is None:
        pytest.skip("watchdog not installed")
    from memforge.cli import watch
    _home, env = _setup(tmp_path)
    store = _store(tmp_path / "store", env)
    good = "---\nname: n\ndescription: fine\n---\nbody\n"
    (store / name).write_text(good, encoding="utf-8")
    (store / "a.md").write_text(good, encoding="utf-8")
    (store / "other.md").write_text(good, encoding="utf-8")
    _git(store, env, "add", "other.md")
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        watch.CommitDebouncer(store, debounce_ms=10, quiet=True)._commit()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    committed = _git(store, env, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert name in committed


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
@pytest.mark.skipif(sys.platform == "win32", reason="bash scripts")
@pytest.mark.parametrize("inherit", [False, True])
def test_auto_commit_hook_is_refused_by_the_validate_hook(tmp_path: Path, inherit: bool):
    home, env = _setup(tmp_path)
    store = _store(home / ".claude" / "global-memory", env)
    bad = store / "bad.md"
    bad.write_text(BROKEN, encoding="utf-8")
    run_env = {**env, "GIT_LITERAL_PATHSPECS": "1"} if inherit else env
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(bad)}})
    subprocess.run(["bash", str(HOOK)], input=payload, env=run_env, text=True, check=True, timeout=60)
    assert _history(store, env) == ["seed"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")
@pytest.mark.skipif(sys.platform == "win32", reason="bash scripts")
@pytest.mark.parametrize("name", ["*.md", "[a].md"])
def test_auto_commit_hook_still_reads_the_written_name_literally(tmp_path: Path, name: str):
    home, env = _setup(tmp_path)
    store = _store(home / ".claude" / "global-memory", env)
    good = "---\nname: n\ndescription: fine\n---\nbody\n"
    odd = store / name
    odd.write_text(good, encoding="utf-8")
    (store / "a.md").write_text(good, encoding="utf-8")
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(odd)}})
    subprocess.run(["bash", str(HOOK)], input=payload, env=env, text=True, check=True, timeout=60)
    assert name in _git(store, env, "show", "--name-only", "--format=", "HEAD").splitlines()


def _run_watcher_once(store: Path, env: dict[str, str], changed: Path, tmp_path: Path) -> None:
    bindir = tmp_path / "fsbin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "fswatch"
    fake.write_text("#!/bin/sh\nprintf '%s\\0' \"$FAKE_CHANGED\"\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    path = os.pathsep.join([str(bindir), env["PATH"]])
    subprocess.run(["bash", str(WATCHER_SH), str(store)], env={**env, "PATH": path, "FAKE_CHANGED": str(changed)},
                   check=True, timeout=60)


@pytest.mark.skipif(sys.platform == "win32", reason="bash scripts")
def test_shell_watcher_commit_is_refused_by_the_validate_hook(tmp_path: Path):
    _home, env = _setup(tmp_path)
    store = _store(tmp_path / "store", env)
    bad = store / "bad.md"
    bad.write_text(BROKEN, encoding="utf-8")
    _run_watcher_once(store, env, bad, tmp_path)
    assert _history(store, env) == ["seed"]


@pytest.mark.skipif(sys.platform == "win32", reason="bash scripts")
@pytest.mark.parametrize("name", ["*.md", "[a].md"])
def test_shell_watcher_still_reads_file_names_literally(tmp_path: Path, name: str):
    _home, env = _setup(tmp_path)
    store = _store(tmp_path / "store", env)
    good = "---\nname: n\ndescription: fine\n---\nbody\n"
    odd = store / name
    odd.write_text(good, encoding="utf-8")
    (store / "a.md").write_text(good, encoding="utf-8")
    _run_watcher_once(store, env, odd, tmp_path)
    assert _git(store, env, "show", "--name-only", "--format=", "HEAD").splitlines() == [name]


def test_powershell_watcher_does_not_export_the_variable_and_reads_names_literally():
    """Source-level check: the PowerShell watcher cannot run on every host."""
    text = WATCHER_PS1.read_text(encoding="utf-8")
    assert "$env:GIT_LITERAL_PATHSPECS" not in text
    assert ":(literal)$rel" in text
    # An inherited variable is removed from the environment git hands to hooks.
    assert "'GIT_LITERAL_PATHSPECS'" in text


def test_memory_watch_without_watchdog_does_not_tell_the_user_to_install_it_alone(tmp_path: Path):
    """watchdog is a dependency of the package, so a missing one means a broken
    install of the package, not a separate step."""
    code = (f"import sys; sys.path.insert(0, {str(REPO / 'src')!r}); "
            "sys.modules['watchdog'] = None; import memforge.cli.watch")
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    res = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True,
                         cwd=tmp_path, timeout=60)
    assert res.returncode == 2
    assert "pip install watchdog" not in res.stderr
    assert "ildan-memforge" in res.stderr
