"""A folder the walk cannot list is reported, never passed over in silence.

A memory in a folder the walk cannot list may link to a file being renamed,
or be missing from the audit and the index. Each tool reports such a folder
as it reports a file it cannot read.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from memforge.cli import audit, index_gen, link_rewriter, migrate_claim_block
from memforge.discovery import read_text_no_follow, walk_md_files, walk_memory_files

pytestmark = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="needs POSIX permission bits that the user cannot bypass",
)

TOP = "---\nname: m1\ndescription: the bun rule\ntype: feedback\nuid: mem-m1\n---\nbody\n"
ROLLUP = (
    "---\nname: sub\ndescription: the sub rollup\ntype: reference\nuid: mem-sub\n---\n"
    "- [m2](m2.md)\n"
)
DETAIL = (
    "---\nname: m2\ndescription: d\ntype: feedback\nuid: mem-m2\ntier: detail\n---\n"
    "See [m1](../m1.md).\n"
)


@pytest.fixture
def store(tmp_path: Path):
    """A store whose rollup subfolder ``sub/`` cannot be listed."""
    root = tmp_path / "memory"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "m1.md").write_text(TOP, encoding="utf-8")
    (sub / "README.md").write_text(ROLLUP, encoding="utf-8")
    (sub / "m2.md").write_text(DETAIL, encoding="utf-8")
    sub.chmod(0)
    yield root
    sub.chmod(0o755)


def test_walk_yields_the_folder_so_reading_it_fails(store: Path):
    found = list(walk_md_files(store))

    assert found == [store / "m1.md", store / "sub"]
    with pytest.raises(OSError):
        read_text_no_follow(store / "sub", root=store)


def test_walk_memory_files_yields_the_folder(store: Path):
    assert store / "sub" in list(walk_memory_files(store))


def test_walk_does_not_report_a_pruned_folder(store: Path):
    assert list(walk_md_files(store, skip_dirs=("sub",))) == [store / "m1.md"]


def _rewriter(monkeypatch, folder: Path, *args: str) -> int:
    monkeypatch.setattr("sys.argv", ["memory-link-rewriter", "--path", str(folder), *args])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    return link_rewriter.main()


def test_link_rewriter_check_reports_the_folder(monkeypatch, store: Path, capsys):
    rc = _rewriter(monkeypatch, store, "check")

    assert rc == 2
    assert "cannot read sub" in capsys.readouterr().err


def test_link_rewriter_rename_refuses_and_moves_nothing(monkeypatch, store: Path, capsys):
    rc = _rewriter(monkeypatch, store, "rename", str(store / "m1.md"), str(store / "moved.md"))

    assert rc == 2
    assert "cannot read sub" in capsys.readouterr().err
    assert (store / "m1.md").read_text(encoding="utf-8") == TOP
    assert not (store / "moved.md").exists()


def test_audit_reports_the_folder_as_a_violation(store: Path, capsys):
    rc = audit.main(["--path", str(store), "--strict"])

    assert rc == 1
    assert "    - sub: cannot read file" in capsys.readouterr().out


def test_audit_reports_a_folder_two_levels_down(tmp_path: Path, capsys):
    root = tmp_path / "memory"
    deeper = root / "sub" / "deeper"
    deeper.mkdir(parents=True)
    (root / "m1.md").write_text(TOP, encoding="utf-8")
    (root / "sub" / "README.md").write_text(ROLLUP, encoding="utf-8")
    deeper.chmod(0)
    try:
        audit.main(["--path", str(root), "--strict"])
    finally:
        deeper.chmod(0o755)

    assert "    - sub/deeper: cannot read file" in capsys.readouterr().out


def test_index_gen_names_the_folder_and_fails(store: Path, capsys):
    rc = index_gen.process(store, "print")

    captured = capsys.readouterr()
    assert rc == 1
    assert f"error: {store / 'sub'}: cannot list the folder" in captured.err
    assert "left out of MEMORY.md" in captured.err
    assert "- [m1](m1.md)" in captured.out


def test_index_gen_names_a_file_it_cannot_read_and_fails(tmp_path: Path, capsys):
    (tmp_path / "m1.md").write_text(TOP, encoding="utf-8")
    (tmp_path / "m1.md").chmod(0)
    try:
        rc = index_gen.process(tmp_path, "print")
    finally:
        (tmp_path / "m1.md").chmod(0o644)

    assert rc == 1
    assert f"error: {tmp_path / 'm1.md'}: cannot read file" in capsys.readouterr().err


def test_migrate_claim_block_warns_about_the_folder(store: Path, capsys):
    rc = migrate_claim_block.main(["--memory-root", str(store), "--dry-run"])

    assert rc == 0
    assert f"warn: cannot list {store / 'sub'}" in capsys.readouterr().err
