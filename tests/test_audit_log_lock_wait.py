"""The audit log's append lock holds a waiting append until the lock is free,
however long that takes, and the log reads the same whichever line ending it
was written with."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from memforge.cli import audit_log

# On Windows the operating system's own blocking lock call gives up after ten
# seconds; a wait longer than that is what this test needs there.
HOLD_SECONDS = 12 if os.name == "nt" else 1


def _append(folder: Path) -> None:
    audit_log.append_record(
        folder, op="write", file=None, before_sha256=None, after_sha256=None,
        operator="waiter", meta={},
    )


def test_an_append_waits_for_the_lock_instead_of_giving_up(tmp_path: Path):
    folder = tmp_path / "mem"
    folder.mkdir()
    waiter = threading.Thread(target=_append, args=(folder,))

    with audit_log._append_lock(folder):
        waiter.start()
        time.sleep(HOLD_SECONDS)
        written_while_locked = (folder / audit_log.LOG_FILENAME).exists()
    waiter.join()

    assert not written_while_locked
    assert audit_log.verify_chain(folder) == (True, [])
    assert [r["seq"] for r in audit_log.read_log(folder)] == [1]


def test_a_log_written_with_crlf_line_ends_verifies_and_extends(tmp_path: Path):
    _append(tmp_path)
    _append(tmp_path)
    log = tmp_path / audit_log.LOG_FILENAME
    text = log.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    log.write_bytes(text)

    assert audit_log.verify_chain(tmp_path) == (True, [])
    _append(tmp_path)

    assert audit_log.verify_chain(tmp_path) == (True, [])
    assert [r["seq"] for r in audit_log.read_log(tmp_path)] == [1, 2, 3]
