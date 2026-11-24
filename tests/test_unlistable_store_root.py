"""A memory folder that cannot itself be listed is reported, never walked as empty.

An empty walk reads as a clean, empty store: the audit passes, the index is
rebuilt with nothing in it, and a rename finds no link to rewrite. Each tool
reports the folder as it reports a subfolder it cannot list. A folder that is
not there keeps its own message.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from memforge.cli import audit, audit_deep, dlp_scan, index_gen, link_rewriter, validate
from memforge.discovery import read_text_no_follow, walk_md_files, walk_memory_files

pytestmark = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="needs POSIX permission bits that the user cannot bypass",
)

TOP = "---\nname: m1\ndescription: the bun rule\ntype: feedback\nuid: mem-m1\n---\nbody\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Keep config discovery and the default memory folders away from the real home."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


def _locked(tmp_path: Path, mode: int = 0):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "m1.md").write_text(TOP, encoding="utf-8")
    root.chmod(mode)
    return root


@pytest.fixture
def store(tmp_path: Path, home: Path):
    """A store whose own folder cannot be listed."""
    root = _locked(tmp_path)
    yield root
    root.chmod(0o755)


@pytest.fixture
def writable_store(tmp_path: Path, home: Path):
    """A store that can be written into and searched but not listed."""
    root = _locked(tmp_path, 0o300)
    yield root
    root.chmod(0o755)


def test_walk_yields_the_root_so_reading_it_fails(store: Path):
    assert list(walk_md_files(store)) == [store]
    with pytest.raises(OSError):
        read_text_no_follow(store, root=store)


def test_walk_memory_files_yields_the_root(store: Path):
    assert list(walk_memory_files(store)) == [store]


def test_walk_of_a_missing_root_stays_empty(tmp_path: Path):
    assert list(walk_md_files(tmp_path / "absent")) == []
    assert list(walk_memory_files(tmp_path / "absent")) == []


def test_walk_of_a_file_named_as_root_stays_empty(tmp_path: Path):
    (tmp_path / "m1.md").write_text(TOP, encoding="utf-8")
    assert list(walk_md_files(tmp_path / "m1.md")) == []


def test_link_rewriter_check_reports_the_root(monkeypatch, store: Path, capsys):
    monkeypatch.setattr("sys.argv", ["memory-link-rewriter", "--path", str(store), "check"])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    rc = link_rewriter.main()

    assert rc == 2
    assert f"Permission denied: '{store}'" in capsys.readouterr().err


def test_link_rewriter_rename_refuses_and_leaves_the_link_whole(
    monkeypatch, writable_store: Path, capsys
):
    linker = "---\nname: m2\ndescription: d\ntype: feedback\nuid: mem-m2\n---\nsee [m1](m1.md)\n"
    (writable_store / "m2.md").write_text(linker, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["memory-link-rewriter", "--path", str(writable_store), "rename",
         str(writable_store / "m1.md"), str(writable_store / "moved.md")],
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    rc = link_rewriter.main()

    assert rc == 2
    assert "nothing changed" in capsys.readouterr().err
    assert (writable_store / "m1.md").read_text(encoding="utf-8") == TOP
    assert not (writable_store / "moved.md").exists()


def test_audit_reports_the_root_as_a_violation(store: Path, capsys):
    rc = audit.main(["--path", str(store), "--strict"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "    - cannot list the memory folder: " in out
    assert "Total integrity violations across targets: 1" in out


def test_audit_goes_on_to_the_next_folder(store: Path, tmp_path: Path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    (other / "m1.md").write_text(TOP, encoding="utf-8")

    audit.main(["--path", str(store), "--path", str(other)])

    assert f"====== {other} ======" in capsys.readouterr().out


def test_audit_of_a_missing_root_keeps_its_message(tmp_path: Path, home: Path, capsys):
    rc = audit.main(["--path", str(tmp_path / "absent"), "--strict"])

    assert rc == 2
    assert "does not exist or is not a folder; not audited" in capsys.readouterr().err


def test_audit_deep_reports_the_root(monkeypatch, store: Path, capsys):
    monkeypatch.setattr("sys.argv", ["memory-audit-deep", "--path", str(store), "--strict"])

    rc = audit_deep.main()

    out = capsys.readouterr().out
    assert rc == 1
    assert "cannot read file" in out


def test_validate_reports_the_root(store: Path, capsys):
    rc = validate.main(["--path", str(store)])

    assert rc == 1
    assert f"[ERROR] {store}: could not read file: " in capsys.readouterr().err


def test_index_gen_refuses_and_names_the_root(store: Path, capsys):
    rc = index_gen.process(store, "print")

    captured = capsys.readouterr()
    assert rc == 1
    assert f"error: {store}: cannot list the memory folder" in captured.err
    assert captured.out == ""


def test_index_gen_writes_no_empty_index_over_a_root_it_cannot_list(writable_store: Path, capsys):
    rc = index_gen.process(writable_store, "write")

    assert rc == 1
    assert "cannot list the memory folder" in capsys.readouterr().err
    writable_store.chmod(0o755)
    assert not (writable_store / "MEMORY.md").exists()


def test_index_gen_check_fails_on_a_root_it_cannot_list(store: Path, capsys):
    assert index_gen.process(store, "check") == 1


def test_index_gen_of_a_missing_root_keeps_its_message(tmp_path: Path, capsys):
    assert index_gen.process(tmp_path / "absent", "print") == 2
    assert "error: folder does not exist" in capsys.readouterr().err


def test_dlp_memory_folders_reports_the_root(monkeypatch, store: Path, capsys):
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(store))
    monkeypatch.setattr(
        "sys.argv", ["memory-dlp-scan", "--memory-folders", "--strict", "--no-detect-secrets"]
    )

    rc = dlp_scan.main()

    assert rc == 1
    assert f"{store}:0  [unreadable_file]" in capsys.readouterr().out
