"""Tests for tools/memory-audit-log.

Covers the BLOCKER-closer behavior (tail_record reads only the last record
without re-walking the file) and the chain-integrity contract (verify_chain
detects any tamper).
"""

from __future__ import annotations

import json
from pathlib import Path


# ---------- tail_record: O(1) append support ----------


def test_tail_record_returns_none_for_missing_log(tmp_path: Path, audit_log_module):
    assert audit_log_module.tail_record(tmp_path) is None


def test_tail_record_returns_none_for_empty_log(tmp_path: Path, audit_log_module):
    (tmp_path / audit_log_module.LOG_FILENAME).write_text("", encoding="utf-8")
    assert audit_log_module.tail_record(tmp_path) is None


def test_tail_record_reads_last_of_many(tmp_path: Path, audit_log_module):
    audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)
    audit_log_module.append_record(tmp_path, op="edit", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)
    audit_log_module.append_record(tmp_path, op="move", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)

    last = audit_log_module.tail_record(tmp_path)
    assert last is not None
    assert last["seq"] == 3
    assert last["op"] == "move"


def test_tail_record_reads_single_record_log(tmp_path: Path, audit_log_module):
    audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)
    last = audit_log_module.tail_record(tmp_path)
    assert last is not None
    assert last["seq"] == 1


def test_tail_record_handles_log_larger_than_one_chunk(tmp_path: Path, audit_log_module):
    """The tail-read seeks in 8KB chunks; ensure correctness when the log
    spans many chunks (catches off-by-one in the seek/find loop)."""
    for i in range(50):
        audit_log_module.append_record(
            tmp_path, op="write", file=None, before_sha256=None, after_sha256=None,
            operator=f"op-{i}",
            meta={"padding": "x" * 200},
        )
    last = audit_log_module.tail_record(tmp_path)
    assert last is not None
    assert last["seq"] == 50
    assert last["operator"] == "op-49"


def test_tail_record_handles_garbage_trailing_bytes(tmp_path: Path, audit_log_module):
    """A truncated half-line at EOF must not crash the reader."""
    log_path = tmp_path / audit_log_module.LOG_FILENAME
    audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)
    with log_path.open("ab") as f:
        f.write(b"{partial_garbage_no_newline")
    # Should return None on garbage decode rather than raise.
    out = audit_log_module.tail_record(tmp_path)
    assert out is None


# ---------- append + verify: chain integrity ----------


def test_verify_empty_log_is_ok(tmp_path: Path, audit_log_module):
    ok, errors = audit_log_module.verify_chain(tmp_path)
    assert ok is True
    assert errors == []


def test_verify_after_appends_is_ok(tmp_path: Path, audit_log_module):
    for op in ("write", "edit", "edit", "move"):
        audit_log_module.append_record(tmp_path, op=op, file=None, before_sha256=None,
                                       after_sha256=None, operator="test", meta=None)
    ok, errors = audit_log_module.verify_chain(tmp_path)
    assert ok is True, errors
    assert errors == []


def test_verify_detects_chain_break_when_record_body_tampered(tmp_path: Path, audit_log_module):
    audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                   after_sha256=None, operator="alice", meta=None)
    audit_log_module.append_record(tmp_path, op="edit", file=None, before_sha256=None,
                                   after_sha256=None, operator="alice", meta=None)

    log_path = tmp_path / audit_log_module.LOG_FILENAME
    lines = log_path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["operator"] = "mallory"
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)
    assert ok is False
    assert any("chain hash mismatch" in e for e in errors)


def test_verify_detects_seq_skip(tmp_path: Path, audit_log_module):
    audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)
    audit_log_module.append_record(tmp_path, op="edit", file=None, before_sha256=None,
                                   after_sha256=None, operator="test", meta=None)

    log_path = tmp_path / audit_log_module.LOG_FILENAME
    lines = log_path.read_text(encoding="utf-8").splitlines()

    rec = json.loads(lines[1])
    rec["seq"] = 99
    rec["chain_sha256"] = audit_log_module.compute_chain_hash(
        rec.get("prev_chain_sha256", ""),
        {k: v for k, v in rec.items() if k != "chain_sha256"},
    )
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)
    assert ok is False
    assert any("expected 2" in e for e in errors)


def test_verify_detects_dropped_record(tmp_path: Path, audit_log_module):
    """Removing a middle record breaks the chain at the next record."""
    for _ in range(3):
        audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                       after_sha256=None, operator="test", meta=None)
    log_path = tmp_path / audit_log_module.LOG_FILENAME
    lines = log_path.read_text(encoding="utf-8").splitlines()
    log_path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    ok, errors = audit_log_module.verify_chain(tmp_path)
    assert ok is False
    assert errors


def test_append_increments_seq_and_chains_prev(tmp_path: Path, audit_log_module):
    r1 = audit_log_module.append_record(tmp_path, op="write", file=None, before_sha256=None,
                                        after_sha256=None, operator="test", meta=None)
    r2 = audit_log_module.append_record(tmp_path, op="edit", file=None, before_sha256=None,
                                        after_sha256=None, operator="test", meta=None)

    assert r1["seq"] == 1
    assert r1["prev_chain_sha256"] == ""
    assert r2["seq"] == 2
    assert r2["prev_chain_sha256"] == r1["chain_sha256"]
    assert r1["chain_sha256"] != r2["chain_sha256"]
