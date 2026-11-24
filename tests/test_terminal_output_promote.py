"""memory-promote shows store text as visible escapes, never raw control bytes.

The file name, the MEMORY.md pointer line, the folder names and the linking
files named in its warnings all come from the store. Run as a subprocess, as
the console entry point runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from memforge._terminal import visible
from tests.terminal_support import hostile_name, lf

HOSTILE_NAME = hostile_name("e\x1b]0;PWN\x07x.md")
HOSTILE_POINTER = f"- [e]({HOSTILE_NAME}) - d \x1b]52;c;cHduZWQ=\x07 \x7f \x9b"
_BAD = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f]|\xc2[\x80-\x9f]")
BODY = "---\nname: n\ndescription: d\ntype: reference\n---\n\nBody.\n"


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMFORGE_", "GIT_"))}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    env["PYTHONIOENCODING"] = "utf-8"
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@example.invalid"
    return env


def _make(tmp_path: Path, filename: str, pointer: str, src_name: str = "src",
          git: bool = False) -> tuple[Path, Path]:
    src, tgt = tmp_path / src_name, tmp_path / "tgt"
    src.mkdir()
    tgt.mkdir()
    (tmp_path / "home").mkdir()
    (src / filename).write_text(BODY, encoding="utf-8")
    (src / "MEMORY.md").write_text(f"# Memory Index\n\n{pointer}\n", encoding="utf-8")
    (tgt / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    if git:
        for folder in (src, tgt):
            subprocess.run(["git", "init", "-q"], cwd=folder, env=_env(tmp_path / "home"), check=True)
            subprocess.run(["git", "add", "-A"], cwd=folder, env=_env(tmp_path / "home"), check=True)
            subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=folder,
                           env=_env(tmp_path / "home"), check=True)
    return src, tgt


def _run(tmp_path: Path, src: Path, tgt: Path, filename: str, *flags: str):
    return subprocess.run(
        [sys.executable, "-m", "memforge.cli.promote", filename,
         "--source", str(src), "--target", str(tgt), *flags],
        cwd=tmp_path, env=_env(tmp_path / "home"), capture_output=True, timeout=60,
    )


def _assert_clean(done) -> None:
    for name, stream in (("stdout", done.stdout), ("stderr", done.stderr)):
        assert not _BAD.search(lf(stream)), (name, stream)


def test_dry_run_shows_hostile_name_pointer_and_folder_as_escapes(tmp_path):
    src, tgt = _make(tmp_path, HOSTILE_NAME, HOSTILE_POINTER, src_name=hostile_name("s\x1b]0;P\x07rc"))
    done = _run(tmp_path, src, tgt, HOSTILE_NAME, "--dry-run")

    assert done.returncode == 0, done.stderr
    _assert_clean(done)
    assert visible(HOSTILE_NAME).encode() in done.stdout
    assert b"\\x1b]52;c;cHduZWQ=\\x07 \\x7f \\x9b" in done.stdout


def test_full_run_and_commit_lines_carry_no_control_byte(tmp_path):
    src, tgt = _make(tmp_path, HOSTILE_NAME, HOSTILE_POINTER, git=True)
    done = _run(tmp_path, src, tgt, HOSTILE_NAME, "--yes")

    assert done.returncode == 0, done.stderr
    _assert_clean(done)
    assert b"committed in" in done.stdout
    assert f"Promoted {visible(HOSTILE_NAME)}.".encode() in done.stdout


def test_dangling_link_warning_carries_no_control_byte(tmp_path):
    src, tgt = _make(tmp_path, HOSTILE_NAME, HOSTILE_POINTER)
    (src / hostile_name("l\x1b]0;PWN\x07k.md")).write_text(
        BODY + f"\n[x]({HOSTILE_NAME})\n", encoding="utf-8")
    done = _run(tmp_path, src, tgt, HOSTILE_NAME, "--yes", "--no-commit")

    assert done.returncode == 3, done.stderr
    _assert_clean(done)
    assert visible(hostile_name("l\x1b]0;PWN\x07k.md")).encode() in done.stderr


@pytest.mark.parametrize("flags", [(), ("--dry-run",), ("--yes", "--no-commit")])
def test_error_lines_carry_no_control_byte(tmp_path, flags):
    src, tgt = _make(tmp_path, "plain.md", "- [p](plain.md) - d")
    done = _run(tmp_path, src, tgt, HOSTILE_NAME, *flags)

    assert done.returncode == 2
    _assert_clean(done)
    assert visible(HOSTILE_NAME).encode() in done.stderr


def test_a_file_name_with_a_line_break_cannot_forge_a_line(tmp_path):
    src, tgt = _make(tmp_path, "plain.md", "- [p](plain.md) - d")
    done = _run(tmp_path, src, tgt, "a\nFORGED-NAME.md", "--yes")

    assert done.returncode == 2
    for stream in (done.stdout, done.stderr):
        for line in stream.decode().splitlines():
            assert not line.lstrip(" -*").startswith("FORGED"), line


def test_a_pointer_with_an_erase_sequence_is_shown_not_sent(tmp_path):
    src, tgt = _make(tmp_path, "plain.md", "- [p](plain.md) - d \x1b[2K")
    done = _run(tmp_path, src, tgt, "plain.md", "--dry-run")

    assert done.returncode == 0, done.stderr
    _assert_clean(done)
    assert b"\\x1b[2K" in done.stdout


def test_a_well_formed_store_prints_the_same_text(tmp_path):
    src, tgt = _make(tmp_path, "good.md", "- [good](good.md) - fine one")
    done = _run(tmp_path, src, tgt, "good.md", "--dry-run")

    assert done.returncode == 0
    assert lf(done.stdout).decode() == (
        "Planned promotion:\n"
        "  file:           good.md\n"
        f"  from:           {src.resolve()}\n"
        f"  to:             {tgt.resolve()}\n"
        "  pointer:        - [good](good.md) - fine one\n"
        "  src MEMORY.md:  remove line 3\n"
        "  tgt MEMORY.md:  append pointer\n"
        "  src git:        not a git repo\n"
        "  tgt git:        not a git repo\n"
        "  commits:        yes\n"
        "\n"
        "--dry-run: no changes made.\n"
    )
    assert done.stderr == b""
