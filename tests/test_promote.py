"""Tests for memforge.cli.promote (move + operator advisories)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from memforge.cli import promote


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _seed(folder: Path) -> None:
    (folder / "MEMORY.md").write_text(
        "# Memory Index\n\n- [Thing](feedback_thing.md): a thing\n",
        encoding="utf-8",
    )
    (folder / "feedback_thing.md").write_text(
        "---\nname: thing\nuid: mem-thing\nstatus: active\n---\n\nBody.\n",
        encoding="utf-8",
    )


def test_promote_moves_file_and_updates_indexes(tmp_path: Path, capsys):
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    _init_repo(source)
    _init_repo(target)
    _seed(source)
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "seed")

    rc = promote.main([
        "feedback_thing.md",
        "--source", str(source),
        "--target", str(target),
        "--yes",
    ])
    assert rc == 0
    assert not (source / "feedback_thing.md").exists()
    assert (target / "feedback_thing.md").exists()
    # Pointer removed from source index, added to target index.
    assert "feedback_thing.md" not in (source / "MEMORY.md").read_text(encoding="utf-8")
    assert "feedback_thing.md" in (target / "MEMORY.md").read_text(encoding="utf-8")


def test_promote_emits_link_rewriter_advisory(tmp_path: Path, capsys):
    """promote must tell the operator to run memory-link-rewriter, since it does
    not rewrite inbound links or cross-folder mirror fields. Regression for
    lifecycle/promote-01 (documented gap)."""
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    _init_repo(source)
    _init_repo(target)
    _seed(source)
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "seed")

    rc = promote.main([
        "feedback_thing.md",
        "--source", str(source),
        "--target", str(target),
        "--yes",
        "--no-commit",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "memory-link-rewriter check" in out
    assert "does not rewrite inbound links" in out


def test_promote_exits_nonzero_on_inbound_dangling_link(tmp_path: Path, capsys):
    """When another file in the source folder links to the moved memory by
    path, promote leaves that link dangling and must exit with the distinct
    EXIT_UNRECONCILED_LINKS code so scripted callers detect the unreconciled
    state. Regression for lifecycle/promote-01."""
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    _init_repo(source)
    _init_repo(target)
    _seed(source)
    # A second source file with an inbound path-form link to the moved memory.
    (source / "feedback_other.md").write_text(
        "---\nname: other\nuid: mem-other\nstatus: active\n---\n\n"
        "See [the thing](feedback_thing.md) for context.\n",
        encoding="utf-8",
    )
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "seed")

    rc = promote.main([
        "feedback_thing.md",
        "--source", str(source),
        "--target", str(target),
        "--yes",
        "--no-commit",
    ])
    assert rc == promote.EXIT_UNRECONCILED_LINKS
    # The move still completed (this is "succeeded but unreconciled", not a hard
    # failure).
    assert not (source / "feedback_thing.md").exists()
    assert (target / "feedback_thing.md").exists()
    err = capsys.readouterr().err
    assert "dangling" in err
    assert "feedback_other.md" in err


def test_promote_clean_when_no_inbound_links(tmp_path: Path):
    """No inbound links -> clean exit (the EXIT_UNRECONCILED_LINKS path must not
    fire for the common case)."""
    source = tmp_path / "src"
    target = tmp_path / "tgt"
    _init_repo(source)
    _init_repo(target)
    _seed(source)
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "seed")

    rc = promote.main([
        "feedback_thing.md",
        "--source", str(source),
        "--target", str(target),
        "--yes",
    ])
    assert rc == 0
