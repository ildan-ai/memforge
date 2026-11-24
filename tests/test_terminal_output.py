"""Store text must not reach a terminal as control characters.

A memory file name, a frontmatter field or a quoted body line is chosen by
whoever can commit to the store. Every tool that prints such text in its
human output shows escape sequences as visible text instead.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from memforge._terminal import stream_safe, terminal_safe, visible
from tests.terminal_support import hostile_name, lf

HOSTILE_NAME = hostile_name("e\x1b]0;PWN\x07x.md")
HOSTILE_FM = (
    "---\n"
    'name: "N\\e]0;PWN\\a"\n'
    'description: "D\\e]52;c;cHduZWQ=\\a desc \\x85 words \\x7f here"\n'
    "type: reference\n"
    "tags: [topic:x]\n"
    "uid: mem-1\nstatus: active\ndecision_topic: db\n"
    "---\n\nBody \x1b[2J line.\n"
)
GOOD = "---\nname: good\ndescription: fine one\ntype: reference\ntags: [topic:x]\nuid: mem-2\nstatus: active\ndecision_topic: db\n---\n\nBody.\n"

_BAD = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f]|\xc2[\x80-\x9f]")


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMFORGE_", "GIT_"))}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.fixture
def store(tmp_path: Path) -> Path:
    folder = tmp_path / "store"
    folder.mkdir()
    (folder / HOSTILE_NAME).write_bytes(HOSTILE_FM.encode("utf-8"))
    (folder / "good.md").write_text(GOOD, encoding="utf-8")
    (tmp_path / "home").mkdir()
    return folder


def _run(module: str, args: list[str], store: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=store,
        env=_env(store.parent / "home"),
        capture_output=True,
        timeout=60,
    )


TOOLS = [
    ("memforge.cli.query", ["--path", "{p}", "--pinned"]),
    ("memforge.cli.query", ["--path", "{p}"]),
    ("memforge.cli.query", ["--path", "{p}", "--format", "json"]),
    ("memforge.cli.audit", ["--path", "{p}", "--strict"]),
    ("memforge.cli.validate", ["--path", "{p}", "--strict"]),
    ("memforge.cli.audit_deep", ["--path", "{p}"]),
    ("memforge.cli.resolve", ["db", "--memory-root", "{p}", "--dry-run"]),
    ("memforge.cli.frontmatter_backfill", ["--path", "{p}"]),
    ("memforge.cli.lint", ["--path", "{p}"]),
    ("memforge.cli.dlp_scan", ["--path", "{p}"]),
    ("memforge.cli.dedup", ["--path", "{p}"]),
    ("memforge.cli.cluster_suggest", ["--path", "{p}"]),
    ("memforge.cli.link_rewriter", ["--path", "{p}", "check"]),
    ("memforge.cli.migrate_claim_block", ["--memory-root", "{p}", "--dry-run"]),
    ("memforge.cli.rollup", ["--path", "{p}", "list"]),
    ("memforge.cli.detect", ["--path", "{p}", "--dry-run", "--no-lessons"]),
]


@pytest.mark.parametrize("module,args", TOOLS, ids=[f"{m.rsplit('.', 1)[1]}-{i}" for i, (m, _) in enumerate(TOOLS)])
def test_no_control_byte_reaches_stdout_or_stderr(store, module, args):
    done = _run(module, [a.format(p=store) for a in args], store)

    assert done.returncode in (0, 1, 2), done.stderr
    for name, stream in (("stdout", done.stdout), ("stderr", done.stderr)):
        assert not _BAD.search(lf(stream)), (module, name, stream)


def test_query_shows_the_hostile_name_as_visible_escapes(store):
    done = _run("memforge.cli.query", ["--path", str(store)], store)

    assert visible(HOSTILE_NAME).encode() in done.stdout
    assert b"\\x1b]52;c;cHduZWQ=\\x07 desc \\x85 words \\x7f here" in done.stdout


def test_json_output_keeps_its_own_escaping_without_doubling(store):
    done = _run("memforge.cli.query", ["--path", str(store), "--format", "json"], store)

    assert not _BAD.search(lf(done.stdout))
    records = json.loads(done.stdout)
    names = {r["frontmatter"].get("name") for r in records}
    assert "N\x1b]0;PWN\x07" in names
    assert b"\\\\u001b" not in done.stdout and b"\\\\x1b" not in done.stdout


@pytest.mark.parametrize("module,args", TOOLS, ids=[f"{m.rsplit('.', 1)[1]}-{i}" for i, (m, _) in enumerate(TOOLS)])
def test_a_name_with_a_line_break_cannot_forge_a_line(tmp_path, module, args):
    folder = tmp_path / "store"
    folder.mkdir()
    (tmp_path / "home").mkdir()
    forged = GOOD.replace("name: good", 'name: "n\\nFORGED-NAME"').replace(
        "fine one", "fine\\nFORGED-DESC"
    )
    (folder / hostile_name("a\nFORGED-FILE.md")).write_text(forged, encoding="utf-8")
    (folder / "b.md").write_text(GOOD, encoding="utf-8")
    done = _run(module, [a.format(p=folder) for a in args], folder)

    for stream in (done.stdout, done.stderr):
        for line in stream.decode().splitlines():
            assert not line.lstrip(" -*").startswith("FORGED"), (module, line)


def test_a_well_formed_store_prints_the_same_text(tmp_path):
    folder = tmp_path / "store"
    folder.mkdir()
    (tmp_path / "home").mkdir()
    (folder / "good.md").write_text(GOOD, encoding="utf-8")
    (folder / "b.md").write_text(GOOD.replace("good", "bee"), encoding="utf-8")
    for module, args in TOOLS:
        done = _run(module, [a.format(p=folder) for a in args], folder)
        text = done.stdout + done.stderr
        assert stream_safe(lf(text).decode("utf-8")) == lf(text).decode("utf-8")


def test_visible_and_stream_safe_rules():
    assert visible("a\tb\nc\x1bd\x7fe\x9bf\u2028") == "a\\x09b\\x0ac\\x1bd\\x7fe\\x9bf\\u2028"
    assert stream_safe("a\tb\nc\x1bd\x7fe\x9bf\r") == "a\tb\nc\\x1bd\\x7fe\\x9bf\\x0d"
    assert visible("plain text é ü") == "plain text é ü"


def test_terminal_safe_wraps_both_streams_and_restores_them(capsys):
    @terminal_safe
    def tool() -> int:
        print("out \x1b[2J")
        print("err \x07", file=sys.stderr)
        return 3

    assert tool() == 3
    captured = capsys.readouterr()
    assert captured.out == "out \\x1b[2J\n"
    assert captured.err == "err \\x07\n"
    print("\x1b", end="")
    assert capsys.readouterr().out == "\x1b"
