"""The generators and the watcher print store-derived paths and exception
text; none of it may reach a terminal as a control character or start a
forged line.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from memforge._terminal import stream_safe
from tests.terminal_support import hostile_name, lf, symlink_or_skip

HOSTILE_DIR = hostile_name("st\x1b]0;PWN\x07\nFORGED-DIR\x85")
HOSTILE_FILE = hostile_name("e\x1b]52;c;cHduZWQ=\x07\nFORGED-FILE.md")
GOOD = (
    "---\nname: good\ndescription: fine one\ntype: reference\ntags: [topic:x]\n"
    "uid: mem-2\nstatus: active\n---\n\nBody.\n"
)
MEMORY_MD = "# Title\n\nPreamble line one.\nPreamble line two.\n\n- [a](a.md): x\n"

import re

_BAD = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f]|\xc2[\x80-\x9f]")


def _env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMFORGE_", "GIT_"))}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra or {})
    return env


def _hostile_store(tmp_path: Path) -> Path:
    folder = tmp_path / HOSTILE_DIR
    folder.mkdir()
    (folder / "good.md").write_text(GOOD, encoding="utf-8")
    (folder / "MEMORY.md").write_text(MEMORY_MD, encoding="utf-8")
    (tmp_path / "home").mkdir(exist_ok=True)
    return folder


def _bare_symlink_in(folder: Path) -> None:
    symlink_or_skip(folder / HOSTILE_FILE, folder / "good.md")


def _unlistable(tmp_path: Path) -> Path:
    """A folder under a parent nobody can search: its access check fails."""
    parent = tmp_path / hostile_name("locked\x1b]0;PWN\x07\nFORGED-LOCK")
    parent.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    child = parent / "inner"
    child.mkdir()
    parent.chmod(0)
    return child


def _unrestore(tmp_path: Path) -> None:
    for p in tmp_path.iterdir():
        if p.is_dir() and not os.access(p, os.X_OK):
            p.chmod(stat.S_IRWXU)


def _store_with_hostile_symlink(tmp_path: Path) -> Path:
    folder = _hostile_store(tmp_path)
    _bare_symlink_in(folder)
    return folder


def _git_free(tmp_path: Path) -> Path:
    return _hostile_store(tmp_path)


def _missing(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir(exist_ok=True)
    return tmp_path / HOSTILE_DIR


def _symlinked_memory_md(tmp_path: Path) -> Path:
    folder = tmp_path / HOSTILE_DIR
    folder.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    (folder / "real.md").write_text(MEMORY_MD, encoding="utf-8")
    symlink_or_skip(folder / "MEMORY.md", folder / "real.md")
    return folder


def _bad_frontmatter_store(tmp_path: Path) -> Path:
    folder = _hostile_store(tmp_path)
    (folder / HOSTILE_FILE).write_bytes(b"---\nname: [unclosed\n---\n")
    return folder


# (id, module, args with {p}, build the folder, extra env or None)
CASES: list[tuple[str, str, list[str], Callable[[Path], Path]]] = [
    ("index-write", "memforge.cli.index_gen", ["--path", "{p}", "--write"], _hostile_store),
    ("index-check", "memforge.cli.index_gen", ["--path", "{p}", "--check"], _hostile_store),
    ("index-print", "memforge.cli.index_gen", ["--path", "{p}", "--print"], _hostile_store),
    ("index-write-recall", "memforge.cli.index_gen", ["--path", "{p}", "--write", "--with-recall-index"], _hostile_store),
    ("index-refused-file", "memforge.cli.index_gen", ["--path", "{p}", "--write"], _store_with_hostile_symlink),
    ("index-missing", "memforge.cli.index_gen", ["--path", "{p}", "--write"], _missing),
    ("index-unlistable", "memforge.cli.index_gen", ["--path", "{p}", "--write"], _unlistable),
    ("preamble-dry", "memforge.cli.preamble_extract", ["--path", "{p}", "--dry-run"], _hostile_store),
    ("preamble-apply", "memforge.cli.preamble_extract", ["--path", "{p}", "--apply"], _hostile_store),
    ("preamble-missing", "memforge.cli.preamble_extract", ["--path", "{p}"], _missing),
    ("preamble-unlistable", "memforge.cli.preamble_extract", ["--path", "{p}"], _unlistable),
    ("preamble-symlink", "memforge.cli.preamble_extract", ["--path", "{p}"], _symlinked_memory_md),
    ("watch-missing", "memforge.cli.watch", ["--path", "{p}"], _missing),
    ("watch-not-git", "memforge.cli.watch", ["--path", "{p}"], _git_free),
    ("watch-unreadable", "memforge.cli.watch", ["--path", "{p}"], _unlistable),
]


def _run(module: str, args: list[str], cwd: Path, home: Path, extra=None):
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        env=_env(home, extra),
        capture_output=True,
        timeout=60,
    )


def _check(done: subprocess.CompletedProcess, label: str) -> None:
    for name, stream in (("stdout", done.stdout), ("stderr", done.stderr)):
        assert not _BAD.search(lf(stream)), (label, name, stream)
        for line in stream.decode("utf-8", "replace").splitlines():
            assert not line.lstrip(" -*=").startswith("FORGED"), (label, name, line)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_a_hostile_path_cannot_reach_the_terminal(tmp_path, case):
    cid, module, args, build = case
    folder = build(tmp_path)
    try:
        done = _run(module, [a.format(p=folder) for a in args], tmp_path, tmp_path / "home")
    finally:
        _unrestore(tmp_path)
    assert done.returncode in (0, 1, 2), done.stderr
    _check(done, cid)


def test_the_unreadable_branches_actually_print_the_path(tmp_path):
    """Guards the guard: the cases above must reach the print sites."""
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("permission bits do not stop this user")
    folder = _unlistable(tmp_path)
    try:
        done = _run("memforge.cli.watch", ["--path", str(folder)], tmp_path, tmp_path / "home")
    finally:
        _unrestore(tmp_path)
    assert b"cannot read it" in done.stderr
    assert b"\\x1b]0;PWN\\x07\\x0aFORGED-LOCK" in done.stderr


def test_agents_md_gen_hides_the_hostile_names(tmp_path):
    repo = tmp_path / HOSTILE_DIR
    repo.mkdir()
    (tmp_path / "home").mkdir()
    (repo / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
    memory = tmp_path / hostile_name("mem\x1b]0;PWN\x07\nFORGED-MEM")
    memory.mkdir()
    (memory / "good.md").write_text(GOOD, encoding="utf-8")
    (memory / HOSTILE_FILE).write_text(
        GOOD.replace("sensitivity", "x") .replace("status: active", "status: active\nsensitivity: [1]"),
        encoding="utf-8",
    )
    (memory / hostile_name("b\x1b[2J\nFORGED-BAD.md")).write_bytes(b"\xff\xfe\x00bad")
    done = _run(
        "memforge.cli.agents_md_gen",
        ["--cwd", str(repo), "--dry-run"],
        tmp_path,
        tmp_path / "home",
        {"MEMFORGE_MEMORY_PATH": str(memory)},
    )
    assert done.returncode in (0, 1, 2), done.stderr
    # The rendered AGENTS.md goes to stdout in a dry run; only its lines about
    # this run (warnings on stderr, the target path) are checked for forging.
    assert not _BAD.search(lf(done.stderr)), done.stderr
    for line in done.stderr.decode("utf-8", "replace").splitlines():
        assert not line.lstrip(" -*").startswith("FORGED"), line
    assert not _BAD.search(lf(done.stdout)), done.stdout


def test_agents_md_gen_write_line_hides_the_hostile_target(tmp_path):
    repo = tmp_path / HOSTILE_DIR
    repo.mkdir()
    (tmp_path / "home").mkdir()
    (repo / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
    memory = tmp_path / "mem"
    memory.mkdir()
    (memory / "good.md").write_text(GOOD, encoding="utf-8")
    done = _run(
        "memforge.cli.agents_md_gen",
        ["--cwd", str(repo)],
        tmp_path,
        tmp_path / "home",
        {"MEMFORGE_MEMORY_PATH": str(memory)},
    )
    assert done.returncode == 0, done.stderr
    _check(done, "agents-write")
    assert b"wrote " in done.stdout


def test_a_well_formed_store_prints_the_same_text(tmp_path):
    folder = tmp_path / "store"
    folder.mkdir()
    (tmp_path / "home").mkdir()
    (folder / "good.md").write_text(GOOD, encoding="utf-8")
    (folder / "MEMORY.md").write_text(MEMORY_MD, encoding="utf-8")
    for module, args in (
        ("memforge.cli.index_gen", ["--path", str(folder), "--write"]),
        ("memforge.cli.index_gen", ["--path", str(folder), "--check"]),
        ("memforge.cli.preamble_extract", ["--path", str(folder)]),
        ("memforge.cli.watch", ["--path", str(folder)]),
    ):
        done = _run(module, args, tmp_path, tmp_path / "home")
        text = lf(done.stdout + done.stderr).decode("utf-8")
        assert stream_safe(text) == text
