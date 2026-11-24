"""The detect-secrets layer must cover every file the scan was given.

detect-secrets only scans files under the folder it runs in and says nothing
about the rest, so a file outside the working directory used to get no deep
scan at all. The stand-in executable below keeps that behavior.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from memforge.cli.dlp_scan import main

pytestmark = pytest.mark.skipif(os.name == "nt", reason="stub executable is a POSIX script")

_STUB = """#!{python}
import json, os, sys
names = sys.argv[sys.argv.index("--") + 1:]
with open({record!r}, "a") as handle:
    handle.write(json.dumps({{"cwd": os.getcwd(), "names": names}}) + "\\n")
results = {{}}
for name in names:
    outside = os.path.isabs(name) or name.split(os.sep)[0] == os.pardir
    if not outside and os.path.isfile(name):
        results[name] = [{{"type": "Stub", "line_number": 1}}]
print(json.dumps({{"results": results}}))
"""


@pytest.fixture
def calls(tmp_path: Path, monkeypatch) -> Path:
    """Put the stand-in `detect-secrets` on PATH; return its call record."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "calls.jsonl"
    stub = bin_dir / "detect-secrets"
    stub.write_text(_STUB.format(python=sys.executable, record=str(record)), encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return record


def _recorded(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def _run(monkeypatch, capsys, *argv: str) -> tuple[int, str]:
    monkeypatch.setattr("sys.argv", ["memory-dlp-scan", *argv])
    rc = main()
    return rc, capsys.readouterr().out


def test_file_outside_the_working_directory_gets_the_deep_scan(
    tmp_path, monkeypatch, capsys, calls
):
    first = tmp_path / "one" / "note.md"
    second = tmp_path / "two" / "note.md"
    for target in (first, second):
        target.parent.mkdir()
        target.write_text("plain\n", encoding="utf-8")

    rc, out = _run(monkeypatch, capsys, "--paths", str(first), str(second), "--strict")

    assert f"{first}:1  [detect-secrets:Stub]" in out
    assert f"{second}:1  [detect-secrets:Stub]" in out
    assert rc == 1
    for call in _recorded(calls):
        assert call["names"] == ["note.md"]
    assert {os.path.realpath(c["cwd"]) for c in _recorded(calls)} == {
        os.path.realpath(first.parent),
        os.path.realpath(second.parent),
    }


def test_memory_folder_mode_gets_the_deep_scan_in_subfolders(
    tmp_path, monkeypatch, capsys, calls
):
    folder = tmp_path / "memory"
    nested = folder / "topic" / "detail.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("plain\n", encoding="utf-8")
    top = folder / "rule.md"
    top.write_text("plain\n", encoding="utf-8")
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(folder))

    rc, out = _run(monkeypatch, capsys, "--memory-folders", "--strict")

    assert f"{nested}:1  [detect-secrets:Stub]" in out
    assert f"{top}:1  [detect-secrets:Stub]" in out
    assert rc == 1


def test_relative_path_is_reported_in_the_form_it_was_given(tmp_path, monkeypatch, capsys, calls):
    (Path.cwd() / "sub").mkdir()
    (Path.cwd() / "sub" / "note.md").write_text("plain\n", encoding="utf-8")

    _, out = _run(monkeypatch, capsys, "--paths", "sub/note.md", "../elsewhere/sub/note.md")

    assert "  sub/note.md:1  [detect-secrets:Stub]" in out
    assert "  ../elsewhere/sub/note.md:1  [detect-secrets:Stub]" in out
