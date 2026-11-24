"""The recall cache must not trust a file edited in the same clock tick as the
build it came from.

A file clock ticks in steps (about 15 ms on NTFS), so a same-size rewrite made
right after a build can keep the size and mtime the build saw. The index
records when it was built; a file whose mtime is within the racy window of that
time is treated as changed, as git treats a racy index entry.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from memforge import recall

BUILT_NS = 1_700_000_000_000_000_000
HOUR_NS = 3_600 * 1_000_000_000


def _mem(folder: Path, desc: str, mtime_ns: int | None = None) -> Path:
    path = folder / "mem-a.md"
    path.write_text(
        f"---\nname: mem-a\ndescription: {desc}\ntype: project\nstatus: active\n---\nbody\n",
        encoding="utf-8")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _desc(folder: Path) -> str:
    index, _ = recall.current_index(folder)
    assert index is not None
    return next(iter(index["entries"].values()))["desc"]


def test_a_same_size_edit_in_the_tick_of_the_build_is_seen(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS - 1_000_000)
    recall.write_index(tmp_path, recall.build_index(tmp_path))

    # Same size, same mtime: only the clock tick hides the edit.
    _mem(tmp_path, "alpha-two", BUILT_NS - 1_000_000)

    assert _desc(tmp_path) == "alpha-two"


def test_an_edit_made_right_after_a_real_build_is_seen(tmp_path: Path):
    for i in range(30):
        _mem(tmp_path, f"first-{i:02d}")
        recall.write_index(tmp_path, recall.build_index(tmp_path))
        _mem(tmp_path, f"later-{i:02d}")
        assert _desc(tmp_path) == f"later-{i:02d}", i


def test_a_quiet_store_is_served_from_the_cache_every_time(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS - HOUR_NS)
    recall.write_index(tmp_path, recall.build_index(tmp_path))

    def boom(*a, **k):
        raise AssertionError("rebuilt a quiet store")
    monkeypatch.setattr(recall, "build_index", boom)

    for _ in range(3):
        assert _desc(tmp_path) == "alpha-one"


def test_an_index_without_a_build_time_is_rebuilt_once(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS - HOUR_NS)
    recall.write_index(tmp_path, recall.build_index(tmp_path))
    index = tmp_path / ".memforge" / "recall-index.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    data.pop("built_ns", None)
    index.write_text(json.dumps(data), encoding="utf-8")

    real = recall.build_index
    calls = []
    monkeypatch.setattr(recall, "build_index", lambda *a, **k: calls.append(1) or real(*a, **k))

    assert _desc(tmp_path) == "alpha-one"
    assert _desc(tmp_path) == "alpha-one"
    assert len(calls) == 1
    assert json.loads(index.read_text(encoding="utf-8"))["built_ns"] == BUILT_NS


def test_a_non_integer_build_time_counts_as_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS - HOUR_NS)
    recall.write_index(tmp_path, recall.build_index(tmp_path))
    index = tmp_path / ".memforge" / "recall-index.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    data["built_ns"] = "soon"
    index.write_text(json.dumps(data), encoding="utf-8")

    assert recall.load_index(tmp_path)["built_ns"] is None


def _fail_on_build(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("rebuilt a store whose files did not change")
    monkeypatch.setattr(recall, "build_index", boom)


def test_a_file_dated_far_in_the_future_is_served_from_the_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS + HOUR_NS)

    assert _desc(tmp_path) == "alpha-one"  # first query builds and writes the index
    _fail_on_build(monkeypatch)
    for _ in range(3):
        assert _desc(tmp_path) == "alpha-one"


def test_an_edit_to_a_future_dated_file_is_still_seen(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS + HOUR_NS)
    assert _desc(tmp_path) == "alpha-one"

    # Same size, new mtime: the fingerprint moves, so the cache is dropped.
    _mem(tmp_path, "alpha-two", BUILT_NS - HOUR_NS)

    assert _desc(tmp_path) == "alpha-two"


def test_a_same_size_edit_to_a_future_dated_file_inside_the_window_is_seen(
        tmp_path: Path, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", BUILT_NS + 1_000_000)
    recall.write_index(tmp_path, recall.build_index(tmp_path))

    # A file a moment ahead of the build is within one tick of it: racy.
    _mem(tmp_path, "alpha-two", BUILT_NS + 1_000_000)

    assert _desc(tmp_path) == "alpha-two"


def test_a_same_size_edit_exactly_one_window_after_the_build_is_seen(
        tmp_path: Path, monkeypatch):
    # A clock that rounds write times up to 2 s can date an edit exactly one
    # window after the build; the boundary itself is racy.
    at = BUILT_NS + recall.RACY_WINDOW_NS
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", at)
    recall.write_index(tmp_path, recall.build_index(tmp_path))

    _mem(tmp_path, "alpha-two", at)

    assert _desc(tmp_path) == "alpha-two"


def test_a_same_size_edit_exactly_one_window_before_the_build_is_seen(
        tmp_path: Path, monkeypatch):
    at = BUILT_NS - recall.RACY_WINDOW_NS
    monkeypatch.setattr(time, "time_ns", lambda: BUILT_NS)
    _mem(tmp_path, "alpha-one", at)
    recall.write_index(tmp_path, recall.build_index(tmp_path))

    _mem(tmp_path, "alpha-two", at)

    assert _desc(tmp_path) == "alpha-two"
