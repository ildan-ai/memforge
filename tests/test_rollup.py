"""Tests for memforge.cli.rollup (create + undo containment / rollback)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memforge.cli import rollup


def _write_memory(folder: Path, name: str) -> Path:
    p = folder / f"{name}.md"
    p.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: fixture {name}\n"
        "type: project\n"
        f"uid: mem-{name}\n"
        "status: active\n"
        "---\n\n"
        f"Body for {name}.\n",
        encoding="utf-8",
    )
    return p


# ---------- security: undo containment ----------


def test_undo_refuses_readme_path_outside_folder(tmp_path: Path, capsys):
    """A tampered history record naming an out-of-folder readme must be refused
    before anything is unlinked. Regression for sec-fswrite/rollup-01."""
    folder = tmp_path / "memory"
    folder.mkdir()
    history_dir = folder / rollup.HISTORY_DIRNAME
    history_dir.mkdir()

    # A victim file that lives OUTSIDE the memory folder.
    victim = tmp_path / "id_ed25519"
    victim.write_text("PRIVATE KEY MATERIAL\n", encoding="utf-8")

    record = {
        "schema": "memforge-rollup-history/v1",
        "operation": "create",
        "slug": "evil",
        "timestamp": "20260101T000000Z",
        "folder": str(folder),
        "moved": [],
        "readme": str(victim),
    }
    (history_dir / "20260101T000000Z-evil.json").write_text(
        json.dumps(record), encoding="utf-8"
    )

    rc = rollup.cmd_undo(folder, "evil", dry_run=False)
    assert rc == 2
    assert victim.exists()  # not deleted
    captured = capsys.readouterr()
    assert "outside the memory folder" in captured.err


def test_undo_refuses_move_target_outside_folder(tmp_path: Path):
    """A tampered 'to' path that escapes the memory folder must be refused.
    Regression for sec-fswrite/rollup-01 (move-loop branch)."""
    folder = tmp_path / "memory"
    folder.mkdir()
    history_dir = folder / rollup.HISTORY_DIRNAME
    history_dir.mkdir()

    inside = folder / "evil" / "moved.md"
    inside.parent.mkdir()
    inside.write_text("content\n", encoding="utf-8")

    outside_target = tmp_path / "escaped.md"

    record = {
        "operation": "create",
        "slug": "evil",
        "moved": [{"from": str(outside_target), "to": str(inside)}],
        "readme": str(folder / "evil" / "README.md"),
    }
    (history_dir / "20260101T000000Z-evil.json").write_text(
        json.dumps(record), encoding="utf-8"
    )

    rc = rollup.cmd_undo(folder, "evil", dry_run=False)
    assert rc == 2
    assert not outside_target.exists()
    assert inside.exists()  # nothing moved


def test_undo_happy_path_within_folder(tmp_path: Path, monkeypatch):
    """A legitimate in-folder undo still works after the containment guard."""
    # Force the no-rewriter fallback path (plain shutil.move).
    monkeypatch.setattr(rollup, "find_link_rewriter", lambda: None)

    folder = tmp_path / "memory"
    folder.mkdir()
    f1 = _write_memory(folder, "alpha")
    f2 = _write_memory(folder, "beta")

    rc = rollup.cmd_create(folder, "cluster", [f1, f2], None, None, dry_run=False)
    assert rc == 0
    assert (folder / "cluster" / "alpha.md").exists()
    assert not f1.exists()

    rc = rollup.cmd_undo(folder, "cluster", dry_run=False)
    assert rc == 0
    assert f1.exists()
    assert f2.exists()
    assert not (folder / "cluster").exists()


# ---------- rollback on partial create ----------


def test_create_rolls_back_on_move_failure(tmp_path: Path, monkeypatch):
    """If a fallback move fails partway, already-moved files roll back and no
    half-rollup / orphaned target dir is left. Regression for
    lifecycle/rollup-01."""
    monkeypatch.setattr(rollup, "find_link_rewriter", lambda: None)

    folder = tmp_path / "memory"
    folder.mkdir()
    f1 = _write_memory(folder, "alpha")
    f2 = _write_memory(folder, "beta")
    f3 = _write_memory(folder, "gamma")

    real_move = rollup.shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # fail on the second file
            raise OSError("simulated move failure")
        return real_move(src, dst)

    monkeypatch.setattr(rollup.shutil, "move", flaky_move)

    rc = rollup.cmd_create(folder, "cluster", [f1, f2, f3], None, None, dry_run=False)
    assert rc == 2

    # All originals restored; no orphaned target dir; no history record written.
    assert f1.exists() and f2.exists() and f3.exists()
    assert not (folder / "cluster").exists()
    history_dir = folder / rollup.HISTORY_DIRNAME
    assert not history_dir.exists() or not list(history_dir.glob("*.json"))


# ---------- security: --slug traversal rejection (MAJOR rollup-slug-01) ----------


def test_create_rejects_traversal_slug(tmp_path: Path, capsys):
    """A traversal slug must be rejected before any mkdir/move so it cannot
    escape the memory folder. Regression for sec-fswrite/rollup-slug-01."""
    folder = tmp_path / "memory"
    folder.mkdir()
    # A sibling dir that a `../evil` slug would land in if unvalidated.
    (tmp_path / "evil").mkdir()
    f1 = _write_memory(folder, "a")

    rc = rollup.cmd_create(folder, "../evil", [f1], None, None, dry_run=False)
    assert rc == 2
    # The escaped target dir must not have gained a README.
    assert not (tmp_path / "evil" / "README.md").exists()
    # The source file must be untouched (not moved out).
    assert f1.exists()


def test_undo_rejects_traversal_slug(tmp_path: Path):
    folder = tmp_path / "memory"
    folder.mkdir()
    (folder / rollup.HISTORY_DIRNAME).mkdir()
    rc = rollup.cmd_undo(folder, "../../etc", dry_run=False)
    assert rc == 2


def test_create_rejects_absolute_slug(tmp_path: Path):
    folder = tmp_path / "memory"
    folder.mkdir()
    f1 = _write_memory(folder, "a")
    rc = rollup.cmd_create(folder, "/etc/passwd", [f1], None, None, dry_run=False)
    assert rc == 2
    assert f1.exists()


def test_create_rejects_control_char_topic(tmp_path: Path):
    """A --topic containing a newline must be rejected so it cannot inject into
    the generated README YAML frontmatter."""
    folder = tmp_path / "memory"
    folder.mkdir()
    f1 = _write_memory(folder, "a")
    rc = rollup.cmd_create(
        folder, "cluster", [f1], "evil\nname: injected", None, dry_run=False
    )
    assert rc == 2
    assert not (folder / "cluster").exists()
