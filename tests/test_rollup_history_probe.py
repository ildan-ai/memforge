"""The write check on an existing rollup history folder must not touch a file
it did not create, must not forge or inject terminal output, and must not
leave its own file behind."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from memforge.cli import rollup

_BAD = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def _mem(folder: Path, name: str) -> None:
    (folder / name).write_text(
        f"---\nname: {name[:-3]}\ndescription: fixture {name}\ntype: project\n"
        f"uid: mem-{name[:-3]}\nstatus: active\n---\n\nBody.\n",
        encoding="utf-8",
    )


@pytest.fixture
def store(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(rollup, "find_link_rewriter", lambda: None)
    folder = tmp_path / "store"
    folder.mkdir()
    _mem(folder, "a.md")
    _mem(folder, "b.md")
    return folder


def test_a_file_at_the_probe_name_is_neither_emptied_nor_removed(store, monkeypatch, capsys):
    history = store / rollup.HISTORY_DIRNAME
    history.mkdir()
    planted = [history / f".write-check-{os.getpid()}", history / ".write-check-fixed"]
    for p in planted:
        p.write_text("keep me\n", encoding="utf-8")
    monkeypatch.setattr(rollup, "_probe_name", lambda: ".write-check-fixed", raising=False)

    ok = rollup._history_takes_a_record(store, history)

    assert [p.read_text(encoding="utf-8") for p in planted] == ["keep me\n", "keep me\n"]
    assert ok is False
    assert "nothing was moved" in capsys.readouterr().err


def test_create_proceeds_when_only_the_old_predictable_name_is_taken(store, capsys):
    history = store / rollup.HISTORY_DIRNAME
    history.mkdir()
    old = history / f".write-check-{os.getpid()}"
    old.write_text("keep me\n", encoding="utf-8")

    rc = rollup.cmd_create(store, "ops", [Path("a.md"), Path("b.md")], None, None, False)

    assert rc == 0
    assert old.read_text(encoding="utf-8") == "keep me\n"
    assert [p.name for p in history.iterdir() if p.name.startswith(".write-check")] == [old.name]


def test_create_refuses_when_the_chosen_name_is_taken(store, monkeypatch):
    history = store / rollup.HISTORY_DIRNAME
    history.mkdir()
    (history / ".write-check-fixed").write_text("keep me\n", encoding="utf-8")
    monkeypatch.setattr(rollup, "_probe_name", lambda: ".write-check-fixed", raising=False)

    rc = rollup.cmd_create(store, "ops", [Path("a.md"), Path("b.md")], None, None, False)

    assert rc == 2
    assert (store / "a.md").exists() and not (store / "ops").exists()
    assert (history / ".write-check-fixed").read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX folder names and modes")
def test_the_error_line_shows_a_hostile_history_path_as_text(tmp_path, capsys):
    hostile = tmp_path / "h\nerror: forged line\x1b]0;PWN\x07"
    hostile.mkdir()
    hostile.chmod(0o555)
    try:
        ok = rollup._history_takes_a_record(tmp_path, hostile)
    finally:
        hostile.chmod(0o755)
    if ok:  # running as a user the mode does not bind
        pytest.skip("the folder was writable")
    err = capsys.readouterr().err

    assert not _BAD.search(err)
    assert err.count("\n") == 1
    assert err.startswith("error: cannot write to the rollup history folder")
    assert "\\x1b" in err or "\\u001b" in err or "\\e" in err


def _probe_hook(monkeypatch, name, before=None, after=None):
    """Run ``before`` just ahead of, and ``after`` just after, the open that
    creates the probe; ``after`` gets the descriptor the open returned."""
    real = os.open

    def hooked(path, flags, *args, **kwargs):
        hit = os.path.basename(os.fspath(path)) == name
        if hit and before:
            before()
        fd = real(path, flags, *args, **kwargs)
        if hit and after:
            after(fd)
        return fd

    monkeypatch.setattr(os, "open", hooked)


def _history(store) -> Path:
    history = store / rollup.HISTORY_DIRNAME
    history.mkdir()
    return history


def test_a_file_created_at_the_name_after_the_name_check_is_neither_emptied_nor_removed(
    store, monkeypatch, capsys
):
    history = _history(store)
    name = ".write-check-raced"
    monkeypatch.setattr(rollup, "_probe_name", lambda: name, raising=False)
    planted = history / name
    _probe_hook(monkeypatch, name, before=lambda: planted.write_text("keep me\n", encoding="utf-8"))

    ok = rollup._history_takes_a_record(store, history)

    assert planted.read_text(encoding="utf-8") == "keep me\n"
    assert ok is False
    assert "nothing was moved" in capsys.readouterr().err


def test_a_probe_replaced_after_the_create_is_not_removed_and_is_reported(
    store, monkeypatch, capsys
):
    history = _history(store)
    name = ".write-check-swapped"
    monkeypatch.setattr(rollup, "_probe_name", lambda: name, raising=False)
    probe = history / name

    real_lstat = os.lstat

    def swap_then_lstat(path, *args, **kwargs):
        # Runs just before the identity check. If the descriptor were already
        # closed, the freed inode number could be handed to the replacement on
        # a filesystem that reuses them, and the replacement would pass.
        if os.path.basename(os.fspath(path)) == name and probe.exists() and not swapped:
            swapped.append(True)
            probe.unlink()
            probe.write_text("not yours\n", encoding="utf-8")
        return real_lstat(path, *args, **kwargs)

    swapped: list = []
    monkeypatch.setattr(os, "lstat", swap_then_lstat)

    rollup._history_takes_a_record(store, history)

    assert probe.read_text(encoding="utf-8") == "not yours\n"
    err = capsys.readouterr().err
    assert str(probe) in err


@pytest.mark.parametrize("failing", ["ftruncate", "close"])
def test_an_error_after_the_create_still_removes_the_probe(store, monkeypatch, failing):
    history = _history(store)
    name = ".write-check-late"
    monkeypatch.setattr(rollup, "_probe_name", lambda: name, raising=False)
    seen = []
    _probe_hook(monkeypatch, name, after=seen.append)
    if failing == "ftruncate":
        monkeypatch.setattr(os, "ftruncate", lambda *a: (_ for _ in ()).throw(OSError(5, "io")))
    else:
        real_close = os.close

        def close(fd):
            real_close(fd)
            if seen and fd == seen[0]:
                raise OSError(5, "io")

        monkeypatch.setattr(os, "close", close)

    rollup._history_takes_a_record(store, history)

    assert [p.name for p in history.iterdir()] == []


def test_a_probe_whose_removal_fails_once_is_removed_anyway(store, monkeypatch, capsys):
    history = _history(store)
    real = os.unlink
    calls = []

    def flaky(path, *args, **kwargs):
        if os.path.basename(os.fspath(path)).startswith(".write-check") and not calls:
            calls.append(path)
            raise PermissionError(13, "denied", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", flaky)

    ok = rollup._history_takes_a_record(store, history)

    assert calls, "the probe was never removed through unlink"
    assert [p.name for p in history.iterdir()] == []
    assert ok is False
    assert "nothing was moved" in capsys.readouterr().err


def test_a_probe_whose_removal_fails_twice_is_reported_with_its_path(store, monkeypatch, capsys):
    history = _history(store)
    name = ".write-check-stuck"
    monkeypatch.setattr(rollup, "_probe_name", lambda: name, raising=False)
    real = os.unlink

    def stuck(path, *args, **kwargs):
        if os.path.basename(os.fspath(path)) == name:
            raise PermissionError(13, "denied", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", stuck)

    rollup._history_takes_a_record(store, history)

    err = capsys.readouterr().err
    assert str(history / name) in err
    assert "left behind" in err
    assert not _BAD.search(err.replace("\n", ""))


def test_a_writable_history_folder_takes_a_record_and_keeps_no_probe(store, capsys):
    # Windows refuses to delete a file that is still open, so the check must
    # not hold its descriptor across the removal there.
    history = _history(store)

    ok = rollup._history_takes_a_record(store, history)

    assert ok is True
    assert capsys.readouterr().err == ""
    assert [p.name for p in history.iterdir()] == []


def test_create_runs_against_an_existing_history_folder(store):
    _history(store)

    rc = rollup.cmd_create(store, "ops", [Path("a.md"), Path("b.md")], None, None, False)

    assert rc == 0
    assert not [p for p in (store / rollup.HISTORY_DIRNAME).iterdir() if p.name.startswith(".write-check")]
