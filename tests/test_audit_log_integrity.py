"""Audit-log behavior on a log file the tool did not write itself.

The log sits in the memory folder, so every reader has to cope with lines and
values that someone else put there: verify fails on what it cannot check, the
SIEM export cannot be made to forge events, tail prints what is present, and
append does not write through a link to somewhere else.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def _append(module, folder: Path, **overrides) -> dict:
    fields = dict(op="write", file=None, before_sha256=None, after_sha256=None,
                  operator="tester", meta=None)
    fields.update(overrides)
    return module.append_record(folder, **fields)


def _log(module, folder: Path) -> Path:
    return folder / module.LOG_FILENAME


# ---------- verify ----------


def test_verify_fails_on_a_line_that_is_not_json(tmp_path, audit_log_module, capsys):
    _append(audit_log_module, tmp_path)
    _append(audit_log_module, tmp_path)
    log = _log(audit_log_module, tmp_path)
    log.write_text(log.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)

    assert not ok
    assert any("line 3" in e for e in errors), errors
    assert audit_log_module.cmd_verify(SimpleNamespace(path=[str(tmp_path)])) == 1


def test_verify_fails_on_a_line_that_is_not_a_record(tmp_path, audit_log_module):
    _append(audit_log_module, tmp_path)
    log = _log(audit_log_module, tmp_path)
    log.write_text(log.read_text(encoding="utf-8") + "[1, 2]\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)

    assert not ok
    assert any("line 2" in e for e in errors), errors


def test_verify_reports_a_record_without_seq_instead_of_crashing(tmp_path, audit_log_module):
    _append(audit_log_module, tmp_path)
    log = _log(audit_log_module, tmp_path)
    log.write_text(log.read_text(encoding="utf-8") + '{"op": "write"}\n', encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)

    assert not ok
    assert errors


def test_verify_output_states_what_a_passing_chain_does_not_show(
    tmp_path, audit_log_module, capsys
):
    _append(audit_log_module, tmp_path)

    rc = audit_log_module.cmd_verify(SimpleNamespace(path=[str(tmp_path)]))

    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "does not detect" in out
    assert "truncat" in out


# ---------- CEF export ----------


def _cef_lines(module, folder: Path, capsys) -> list[str]:
    capsys.readouterr()
    assert module.cmd_export(SimpleNamespace(path=str(folder), format="cef")) == 0
    return capsys.readouterr().out.splitlines()


def test_cef_export_keeps_one_event_per_record(tmp_path, audit_log_module, capsys):
    forged = "x\nCEF:0|ILDAN|MemForge|9|forged|memforge-audit|10|suser=root"
    _append(audit_log_module, tmp_path, operator=forged)
    _append(audit_log_module, tmp_path, op="edit\r\nCEF:0|forged")

    lines = _cef_lines(audit_log_module, tmp_path, capsys)

    assert len(lines) == 2
    assert all(line.startswith("CEF:0|ILDAN|MemForge|") for line in lines)


def test_cef_export_escapes_header_and_extension_values(tmp_path, audit_log_module, capsys):
    _append(
        audit_log_module,
        tmp_path,
        op="a|b\\c",
        operator="eve suser=root",
        file=Path("dir/name=1\\x.md"),
    )

    (line,) = _cef_lines(audit_log_module, tmp_path, capsys)

    header = line.split("|memforge-audit|")[0]
    assert header.endswith("a\\|b\\\\c")
    assert "suser=eve suser\\=root " in line
    assert "name\\=1\\\\x.md" in line


def test_cef_export_survives_non_string_fields(tmp_path, audit_log_module, capsys):
    record = {"seq": "one", "op": {"k": "v|w"}, "operator": None, "file": ["a=b"]}
    _log(audit_log_module, tmp_path).write_text(json.dumps(record) + "\n", encoding="utf-8")

    (line,) = _cef_lines(audit_log_module, tmp_path, capsys)

    assert line.count("|") - line.count("\\|") == 7


# ---------- tail ----------


def test_tail_prints_a_record_that_lacks_fields(tmp_path, audit_log_module, capsys):
    _log(audit_log_module, tmp_path).write_text(
        '{"op": "write"}\n{"seq": "x", "ts": 5}\n', encoding="utf-8"
    )

    rc = audit_log_module.cmd_tail(SimpleNamespace(path=str(tmp_path), n=20))

    out = capsys.readouterr().out
    assert rc == 0
    assert "write" in out
    assert len(out.splitlines()) == 2


# ---------- append through a link ----------


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_append_refuses_a_symlinked_log_file(tmp_path, audit_log_module):
    folder = tmp_path / "memory"
    folder.mkdir()
    outside = tmp_path / "outside.txt"
    _log(audit_log_module, folder).symlink_to(outside)

    with pytest.raises(audit_log_module.AuditLogError, match="symbolic link"):
        _append(audit_log_module, folder)

    assert not outside.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_append_refuses_a_symlinked_log_file_that_already_has_records(
    tmp_path, audit_log_module
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _append(audit_log_module, elsewhere)
    target = _log(audit_log_module, elsewhere)
    before = target.read_bytes()
    folder = tmp_path / "memory"
    folder.mkdir()
    _log(audit_log_module, folder).symlink_to(target)

    with pytest.raises(audit_log_module.AuditLogError, match="symbolic link"):
        _append(audit_log_module, folder)

    assert target.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_append_refuses_a_symlinked_lock_file(tmp_path, audit_log_module):
    folder = tmp_path / "memory"
    folder.mkdir()
    outside = tmp_path / "outside.lock"
    (folder / audit_log_module.LOCK_FILENAME).symlink_to(outside)

    with pytest.raises(audit_log_module.AuditLogError, match="symbolic link"):
        _append(audit_log_module, folder)

    assert not outside.exists()


# ---------- append through a hard link ----------


def _hardlink(link: Path, target: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hard links are not available on this platform")


def test_append_refuses_a_log_with_a_second_hard_link(tmp_path, audit_log_module):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _append(audit_log_module, elsewhere)
    outside = _log(audit_log_module, elsewhere)
    before = outside.read_bytes()
    folder = tmp_path / "memory"
    folder.mkdir()
    _hardlink(_log(audit_log_module, folder), outside)

    with pytest.raises(audit_log_module.AuditLogError, match="hard link"):
        _append(audit_log_module, folder)

    assert outside.read_bytes() == before


def test_append_refuses_a_lock_with_a_second_hard_link(tmp_path, audit_log_module):
    folder = tmp_path / "memory"
    folder.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"outside content\n")
    _hardlink(folder / audit_log_module.LOCK_FILENAME, outside)

    with pytest.raises(audit_log_module.AuditLogError, match="hard link"):
        _append(audit_log_module, folder)

    assert outside.read_bytes() == b"outside content\n"
    assert not _log(audit_log_module, folder).exists()


# ---------- a named pipe where the log or lock should be ----------
#
# Opening a pipe that has no other end blocks forever, so each of these would
# hang the tool if the open were not refused.


def _mkfifo(path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("needs named pipes")
    os.mkfifo(path)


def test_append_refuses_a_named_pipe_as_the_log(tmp_path, audit_log_module):
    _mkfifo(_log(audit_log_module, tmp_path))

    with pytest.raises(audit_log_module.AuditLogError, match="not a regular file"):
        _append(audit_log_module, tmp_path)


def test_write_open_of_a_named_pipe_log_is_refused(tmp_path, audit_log_module):
    # Reached when the pipe appears after the tail was read; a write-only
    # open of a pipe with no reader is the one that would wait forever.
    log = _log(audit_log_module, tmp_path)
    _mkfifo(log)

    with pytest.raises(audit_log_module.AuditLogError, match="not a regular file"):
        audit_log_module._open_for_append(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT)


def test_append_refuses_a_named_pipe_as_the_lock(tmp_path, audit_log_module):
    _mkfifo(tmp_path / audit_log_module.LOCK_FILENAME)

    with pytest.raises(audit_log_module.AuditLogError, match="not a regular file"):
        _append(audit_log_module, tmp_path)

    assert not _log(audit_log_module, tmp_path).exists()


def test_file_hash_of_a_named_pipe_does_not_wait(tmp_path, audit_log_module, monkeypatch):
    # The pipe stands in for a file swapped for one after any check on the
    # path, so the path checks are made to pass as they would before the swap.
    pipe = tmp_path / "note.md"
    _mkfifo(pipe)
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    result: list = []
    worker = threading.Thread(
        target=lambda: result.append(audit_log_module.file_sha256(pipe)), daemon=True
    )

    worker.start()
    worker.join(timeout=10)
    if worker.is_alive():
        # Give the waiting open a writer and close it, so the thread ends.
        os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(timeout=10)
        pytest.fail("file_sha256 waited on a named pipe")

    assert result == [None]


def test_file_hash_follows_a_symlink_the_operator_named(tmp_path, audit_log_module):
    target = tmp_path / "real.md"
    target.write_bytes(b"body\n")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    assert audit_log_module.file_sha256(link) == audit_log_module.file_sha256(target)
    assert audit_log_module.file_sha256(target) == hashlib.sha256(b"body\n").hexdigest()


def test_verify_fails_on_a_named_pipe_as_the_log(tmp_path, audit_log_module):
    _mkfifo(_log(audit_log_module, tmp_path))

    ok, errors = audit_log_module.verify_chain(tmp_path)

    assert not ok
    assert any("not a regular file" in e for e in errors), errors


# ---------- what the chain claims ----------


def test_help_text_does_not_claim_tamper_evidence(audit_log_module, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["memory-audit-log", "--help"])

    with pytest.raises(SystemExit):
        audit_log_module.main()

    out = " ".join(capsys.readouterr().out.split())
    assert "tamper-evident" not in out.lower()
    assert "does not detect" in out


# ---------- reading a log that is a link ----------


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
@pytest.mark.parametrize("dangling", [True, False])
def test_verify_fails_when_the_log_is_a_symlink(tmp_path, audit_log_module, capsys, dangling):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if not dangling:
        _append(audit_log_module, elsewhere)
    folder = tmp_path / "memory"
    folder.mkdir()
    _log(audit_log_module, folder).symlink_to(_log(audit_log_module, elsewhere))

    ok, errors = audit_log_module.verify_chain(folder)

    assert not ok
    assert any("symbolic link" in e for e in errors), errors
    assert audit_log_module.cmd_verify(SimpleNamespace(path=[str(folder)])) == 1
    assert "symbolic link" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
@pytest.mark.parametrize("command", ["tail", "export"])
def test_tail_and_export_refuse_a_symlinked_log(tmp_path, audit_log_module, capsys, command):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _append(audit_log_module, elsewhere, operator="someone-elsewhere")
    folder = tmp_path / "memory"
    folder.mkdir()
    _log(audit_log_module, folder).symlink_to(_log(audit_log_module, elsewhere))

    if command == "tail":
        rc = audit_log_module.cmd_tail(SimpleNamespace(path=str(folder), n=20))
    else:
        rc = audit_log_module.cmd_export(SimpleNamespace(path=str(folder), format="jsonl"))

    captured = capsys.readouterr()
    assert rc == 1
    assert "someone-elsewhere" not in captured.out
    assert "symbolic link" in captured.err


# ---------- a log swapped for a link during append ----------


def _swap_for_link_after_first_lstat(monkeypatch, path: Path, target: Path) -> None:
    """Replace `path` with a link to `target` right after it is first checked,
    the way a concurrent writer could between a check and the open."""
    real_lstat = os.lstat
    swapped: list[bool] = []

    def lstat(checked, *args, **kwargs):
        try:
            return real_lstat(checked, *args, **kwargs)
        finally:
            if not swapped and os.fspath(checked) == os.fspath(path):
                swapped.append(True)
                if os.path.lexists(path):
                    os.unlink(path)
                os.symlink(target, path)

    monkeypatch.setattr(os, "lstat", lstat)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
@pytest.mark.parametrize("platform_has_no_follow", [True, False])
@pytest.mark.parametrize("log_exists", [True, False])
def test_append_refuses_a_log_swapped_for_a_link_after_the_check(
    tmp_path, audit_log_module, monkeypatch, platform_has_no_follow, log_exists
):
    folder = tmp_path / "memory"
    folder.mkdir()
    if log_exists:
        _append(audit_log_module, folder)
    outside = tmp_path / "outside.jsonl"
    if not platform_has_no_follow:
        monkeypatch.delattr(os, "O_NOFOLLOW")
        monkeypatch.setattr(audit_log_module, "_O_NOFOLLOW", 0)
    _swap_for_link_after_first_lstat(monkeypatch, _log(audit_log_module, folder), outside)

    with pytest.raises(audit_log_module.AuditLogError):
        _append(audit_log_module, folder)

    assert _log(audit_log_module, folder).is_symlink()
    assert not os.path.lexists(outside)


def _swap_for_link_at_the_creating_open(monkeypatch, path: Path, target: Path) -> None:
    """Replace `path` with a link to `target` just before the open that may
    create it, after every check on the path has run."""
    real_open = os.open
    swapped: list[bool] = []

    def open_(opened, flags, *args, **kwargs):
        if (not swapped and flags & os.O_CREAT
                and os.fspath(opened) == os.fspath(path)):
            swapped.append(True)
            if os.path.lexists(path):
                os.unlink(path)
            os.symlink(target, path)
        return real_open(opened, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
@pytest.mark.parametrize("name", ["LOG_FILENAME", "LOCK_FILENAME"])
@pytest.mark.parametrize("log_exists", [True, False])
def test_append_without_no_follow_creates_nothing_through_a_swapped_link(
    tmp_path, audit_log_module, monkeypatch, name, log_exists
):
    folder = tmp_path / "memory"
    folder.mkdir()
    if log_exists:
        _append(audit_log_module, folder)
    outside = tmp_path / "outside"
    monkeypatch.setattr(audit_log_module, "_O_NOFOLLOW", 0)
    _swap_for_link_at_the_creating_open(
        monkeypatch, folder / getattr(audit_log_module, name), outside
    )

    with pytest.raises(audit_log_module.AuditLogError):
        _append(audit_log_module, folder)

    assert not os.path.lexists(outside)


def test_append_still_creates_a_new_log(tmp_path, audit_log_module):
    record = _append(audit_log_module, tmp_path)

    assert record["seq"] == 1
    assert audit_log_module.verify_chain(tmp_path) == (True, [])


# ---------- blank lines ----------


def test_verify_fails_on_a_blank_line_inside_the_log(tmp_path, audit_log_module):
    _append(audit_log_module, tmp_path)
    _append(audit_log_module, tmp_path)
    log = _log(audit_log_module, tmp_path)
    first, second = log.read_text(encoding="utf-8").splitlines()
    log.write_text(f"{first}\n   \n{second}\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)

    assert not ok
    assert any("line 2" in e and "blank" in e for e in errors), errors


def test_verify_fails_on_an_extra_blank_line_at_the_end(tmp_path, audit_log_module):
    _append(audit_log_module, tmp_path)
    log = _log(audit_log_module, tmp_path)
    log.write_text(log.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)

    assert not ok
    assert any("line 2" in e for e in errors), errors


def test_verify_accepts_the_single_trailing_newline(tmp_path, audit_log_module):
    _append(audit_log_module, tmp_path)
    _append(audit_log_module, tmp_path)
    # The log is written in text mode, so a line ends with the platform's own
    # newline; what matters is that exactly one ends the file.
    written = _log(audit_log_module, tmp_path).read_bytes().replace(b"\r\n", b"\n")
    assert written.endswith(b"}\n") and not written.endswith(b"\n\n")

    assert audit_log_module.verify_chain(tmp_path) == (True, [])
