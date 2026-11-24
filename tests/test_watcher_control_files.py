"""The bash example watcher commits memory files only, never .memforge/ files.

Edits under .memforge/ (config, snoozes, waivers) belong in their own
`memforge:` commits, so an automatic commit must leave every one of them out.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WATCHER = REPO / "examples" / "scripts" / "memforge-auto-commit-watcher.sh"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="bash script"),
    pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None
                       or shutil.which("python3") is None, reason="needs bash, git and python3"),
    pytest.mark.skipif(not WATCHER.exists(), reason="script not in this layout"),
]


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True,
                          check=True).stdout


def _run_watcher_once(store: Path, env: dict[str, str], changed: list[Path], tmp_path: Path) -> None:
    """Run the watcher with a stand-in `fswatch` that reports the paths and exits."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "fswatch"
    fake.write_text("#!/bin/sh\ncat \"$FAKE_CHANGED\"\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    tools = [str(Path(shutil.which(t)).parent) for t in ("bash", "git", "python3")]
    path = os.pathsep.join(dict.fromkeys([str(bindir), *tools, "/usr/bin", "/bin"]))
    listed = tmp_path / "changed"
    listed.write_bytes(b"".join(os.fsencode(p) + b"\0" for p in changed))
    subprocess.run(["bash", str(WATCHER), str(store)], env={**env, "PATH": path, "FAKE_CHANGED": str(listed)},
                   check=True, timeout=60)


@pytest.mark.parametrize("name", [
    ".memforge/config.yaml",
    ".memforge/snoozes/t.yaml",
    ".memforge/audit-waivers.yaml",
    ".memforge/notes.md",
    ".memforge/snoozes/t.md",
])
def test_watcher_leaves_memforge_files_out(tmp_path: Path, name: str):
    env = _env(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    _git(store, env, "init", "-q")
    _git(store, env, "config", "user.email", "test@example.com")
    _git(store, env, "config", "user.name", "Test")
    (store / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(store, env, "add", "-A")
    _git(store, env, "commit", "-q", "-m", "seed")
    f = store / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("k: 1\n", encoding="utf-8")
    note = store / "note.md"
    note.write_text("note\n", encoding="utf-8")

    _run_watcher_once(store, env, [f, note], tmp_path)

    assert _git(store, env, "log", "--format=%s").splitlines() == ["memory: auto-commit note.md", "seed"]
    assert _git(store, env, "ls-files", "--", name) == ""
