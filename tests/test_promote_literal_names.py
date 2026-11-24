"""memory-promote commits exactly the files it changed, also when the promoted
file's name is one git would read as a wildcard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from memforge.cli import promote

# A name git reads as a wildcard. `*` is not a legal Windows file name, so
# there a bracket pattern, which git also reads as a wildcard, stands in.
WILD_FILE = "[a-z].md" if os.name == "nt" else "*.md"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def _memory(uid: str) -> str:
    return f"---\nname: {uid}\nuid: {uid}\nstatus: active\n---\n\nBody.\n"


def test_wildcard_file_name_commits_only_the_promoted_files(tmp_path: Path):
    source = _repo(tmp_path / "src")
    target = _repo(tmp_path / "tgt")
    (source / "MEMORY.md").write_text(f"# Memory Index\n\n- [Star]({WILD_FILE}): a star\n",
                                      encoding="utf-8")
    (source / WILD_FILE).write_text(_memory("mem-star"), encoding="utf-8")
    (source / "c.md").write_text(_memory("mem-c"), encoding="utf-8")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "seed")
    (source / "c.md").write_text(_memory("mem-c") + "Staged edit.\n", encoding="utf-8")
    _git(source, "add", "c.md")
    (source / "notes.md").write_text("untracked\n", encoding="utf-8")
    (target / "MEMORY.md").write_text("# Memory Index\n\n", encoding="utf-8")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "seed")
    (target / "other.md").write_text("untracked\n", encoding="utf-8")

    rc = promote.main([WILD_FILE, "--source", str(source), "--target", str(target), "--yes"])

    assert rc == 0
    for repo in (source, target):
        files = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert sorted(files) == sorted([WILD_FILE, "MEMORY.md"])
    assert _git(source, "diff", "--cached", "--name-only").split() == ["c.md"]
    assert _git(source, "ls-files", "--others").split() == ["notes.md"]
    assert _git(target, "ls-files", "--others").split() == ["other.md"]
