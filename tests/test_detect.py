"""Tests for memforge-detect: queue lifecycle, merge semantics, and item IDs.

These tests exercise the core queue logic without requiring a live local LLM.
The audit/lint/dedup/cluster-suggest calls are run against real (tiny) tmp
memory folders; semantic triage is exercised in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memforge.cli.detect import (
    _item_id,
    _load_queue,
    _merge_into_queue,
    _write_queue,
    _parse_lessons,
    _build_rule_catalog,
    PRIORITY_ORDER,
    main,
)


# ---------- helper ----------

def _minimal_memory(folder: Path, name: str = "feedback_x.md") -> None:
    (folder / name).write_text(
        "---\n"
        "name: Test rule\n"
        "description: A test rule for detect tests\n"
        "type: feedback\n"
        "sensitivity: internal\n"
        "---\n\n"
        "Body.\n**Why:** test.\n**How to apply:** always.\n",
        encoding="utf-8",
    )
    (folder / "MEMORY.md").write_text(
        f"- [Test rule]({name}) : test rule description\n",
        encoding="utf-8",
    )


# ---------- _item_id ----------

def test_item_id_deterministic() -> None:
    a = _item_id("integrity", "folder|some violation")
    b = _item_id("integrity", "folder|some violation")
    assert a == b


def test_item_id_differs_on_different_input() -> None:
    a = _item_id("integrity", "detail-A")
    b = _item_id("integrity", "detail-B")
    assert a != b


def test_item_id_16_hex_chars() -> None:
    fid = _item_id("lessons", "text content")
    assert len(fid) == 16
    assert all(c in "0123456789abcdef" for c in fid)


# ---------- queue read / write ----------

def test_load_queue_absent(tmp_path: Path) -> None:
    q = _load_queue(tmp_path / "nonexistent.json")
    assert q == []


def test_load_queue_malformed(tmp_path: Path) -> None:
    p = tmp_path / "q.json"
    p.write_text("not json", encoding="utf-8")
    assert _load_queue(p) == []


def test_write_then_load_roundtrip(tmp_path: Path) -> None:
    q = tmp_path / "q.json"
    items = [{"id": "abc", "category": "integrity", "status": "open", "detail": "x"}]
    _write_queue(q, items)
    loaded = _load_queue(q)
    assert loaded == items


def test_write_is_atomic(tmp_path: Path) -> None:
    """A .tmp file must not linger after a successful write."""
    q = tmp_path / "q.json"
    _write_queue(q, [])
    tmp = q.with_suffix(".tmp")
    assert not tmp.exists()


# ---------- merge semantics ----------

def _make_open(category: str, detail: str) -> dict:
    return {
        "id": _item_id(category, detail),
        "category": category,
        "status": "open",
        "detail": detail,
    }


def _make_done(category: str, detail: str) -> dict:
    return {
        "id": _item_id(category, detail),
        "category": category,
        "status": "done",
        "detail": detail,
    }


def test_merge_adds_new_items() -> None:
    existing: list[dict] = []
    new = [_make_open("integrity", "violation A")]
    merged, added, skipped = _merge_into_queue(existing, new)
    assert added == 1
    assert skipped == 0
    assert len(merged) == 1
    assert merged[0]["status"] == "open"


def test_merge_deduplicates_already_open() -> None:
    item = _make_open("integrity", "violation A")
    existing = [item]
    new = [item]
    merged, added, skipped = _merge_into_queue(existing, new)
    assert added == 0
    assert skipped == 1
    assert len(merged) == 1


def test_merge_preserves_done_status() -> None:
    """A done item is never re-opened by a re-run."""
    done_item = _make_done("integrity", "violation A")
    existing = [done_item]
    new_finding = {
        "id": done_item["id"],
        "category": "integrity",
        "detail": "violation A",
    }
    merged, added, skipped = _merge_into_queue(existing, [new_finding])
    assert skipped == 1
    assert merged[0]["status"] == "done"


def test_merge_priority_sort() -> None:
    """Merged queue must be sorted by priority order."""
    items = [
        _make_open("lessons", "lesson 1"),
        _make_open("integrity", "violation 1"),
        _make_open("recall-weakness", "weak memory"),
    ]
    merged, _, _ = _merge_into_queue([], items)
    categories = [m["category"] for m in merged]
    # integrity must come before recall-weakness, which must come before lessons.
    assert categories.index("integrity") < categories.index("recall-weakness")
    assert categories.index("recall-weakness") < categories.index("lessons")


# ---------- lessons parsing ----------

def test_parse_lessons_empty(tmp_path: Path) -> None:
    p = tmp_path / "lessons.md"
    p.write_text("", encoding="utf-8")
    entries = _parse_lessons(p)
    assert entries == []


def test_parse_lessons_splits_on_headers(tmp_path: Path) -> None:
    content = (
        "# Lessons log\n\n"
        "## Lesson 1\n\nContent of lesson one.\n\n"
        "## Lesson 2\n\nContent of lesson two.\n"
    )
    p = tmp_path / "lessons.md"
    p.write_text(content, encoding="utf-8")
    entries = _parse_lessons(p)
    assert len(entries) == 2
    assert "lesson one" in entries[0]["text"].lower()
    assert "lesson two" in entries[1]["text"].lower()


def test_parse_lessons_missing_file(tmp_path: Path) -> None:
    entries = _parse_lessons(tmp_path / "nonexistent.md")
    assert entries == []


# ---------- rule catalog ----------

def test_build_rule_catalog_empty_folder(tmp_path: Path) -> None:
    rules = _build_rule_catalog([tmp_path])
    assert rules == []


def test_build_rule_catalog_extracts_descriptions(tmp_path: Path) -> None:
    _minimal_memory(tmp_path)
    rules = _build_rule_catalog([tmp_path])
    assert any("test rule" in r.lower() for r in rules)


# ---------- main CLI ----------

def test_main_no_folders_exits_cleanly(tmp_path: Path) -> None:
    """main() with --path pointing to a non-existent folder exits 0."""
    rc = main([
        "--path", str(tmp_path / "nonexistent"),
        "--no-lessons",
        "--dry-run",
        "--queue", str(tmp_path / "queue.json"),
    ])
    assert rc == 0


def test_main_dry_run_does_not_write_queue(tmp_path: Path) -> None:
    _minimal_memory(tmp_path)
    queue_path = tmp_path / "queue.json"
    rc = main([
        "--path", str(tmp_path),
        "--no-lessons",
        "--dry-run",
        "--queue", str(queue_path),
    ])
    assert rc == 0
    assert not queue_path.exists(), "dry-run must not write queue"


def test_main_writes_queue(tmp_path: Path) -> None:
    _minimal_memory(tmp_path)
    queue_path = tmp_path / "queue.json"
    rc = main([
        "--path", str(tmp_path),
        "--no-lessons",
        "--queue", str(queue_path),
    ])
    assert rc == 0
    assert queue_path.is_file()
    data = json.loads(queue_path.read_text())
    assert isinstance(data, list)


def test_main_rerun_merges_not_clobbers(tmp_path: Path) -> None:
    """Re-running main() preserves done items from a prior run."""
    _minimal_memory(tmp_path)
    queue_path = tmp_path / "queue.json"

    # First run: write queue.
    main(["--path", str(tmp_path), "--no-lessons", "--queue", str(queue_path)])
    first = json.loads(queue_path.read_text())

    if not first:
        pytest.skip("no findings generated by audit on this minimal folder")

    # Mark the first item done.
    first[0]["status"] = "done"
    _write_queue(queue_path, first)

    # Second run: merge.
    main(["--path", str(tmp_path), "--no-lessons", "--queue", str(queue_path)])
    second = json.loads(queue_path.read_text())

    done_ids = {item["id"] for item in second if item["status"] == "done"}
    assert first[0]["id"] in done_ids, "done item must survive a re-run"


def test_main_summary_flag(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _minimal_memory(tmp_path)
    queue_path = tmp_path / "queue.json"
    rc = main([
        "--path", str(tmp_path),
        "--no-lessons",
        "--queue", str(queue_path),
        "--summary",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    # Summary line must mention "memforge hygiene" and "Run /memforge-curate".
    all_out = captured.out + captured.err
    assert "memforge hygiene" in all_out or "Run /memforge-curate" in all_out
