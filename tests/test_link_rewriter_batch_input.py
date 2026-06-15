"""Tests for memory-link-rewriter rename-batch malformed-stdin handling.

Regression for sec-fswrite/rename-batch-input-01: a JSON payload that parses
but has the wrong shape must error cleanly (rc=2) rather than crash with an
unhandled KeyError/TypeError traceback.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from memforge.cli import link_rewriter


def _run_batch(monkeypatch, folder: Path, raw: str) -> int:
    monkeypatch.setattr(
        "sys.argv", ["memory-link-rewriter", "--path", str(folder), "rename-batch"]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    return link_rewriter.main()


@pytest.mark.parametrize(
    "raw",
    [
        '["a", "b"]',          # list of strings, not dicts
        '{"src": 1}',          # top-level dict, not a list
        '[{"src": "a.md"}]',   # item missing "dst"
        '[{"dst": "b.md"}]',   # item missing "src"
        '[{"src": 1, "dst": 2}]',  # non-string src/dst
        '"just a string"',     # parses to a str
    ],
)
def test_rename_batch_malformed_shape_errors_cleanly(monkeypatch, tmp_path: Path, raw, capsys):
    folder = tmp_path / "memory"
    folder.mkdir()
    rc = _run_batch(monkeypatch, folder, raw)
    assert rc == 2
    err = capsys.readouterr().err
    assert "rename-batch" in err  # a clean, named error, not a traceback


def test_rename_batch_invalid_json_still_errors(monkeypatch, tmp_path: Path, capsys):
    folder = tmp_path / "memory"
    folder.mkdir()
    rc = _run_batch(monkeypatch, folder, "{not json")
    assert rc == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_rename_batch_well_formed_empty_list_is_noop(monkeypatch, tmp_path: Path):
    folder = tmp_path / "memory"
    folder.mkdir()
    rc = _run_batch(monkeypatch, folder, "[]")
    assert rc == 0
