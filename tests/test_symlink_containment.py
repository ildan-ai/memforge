"""A symlink planted in a memory folder must not carry a tool outside it.

Writers refuse to write through a symlink; readers skip symlinked files and do
not descend symlinked directories. Each test plants a link to a file outside
the memory folder and asserts the outside file is neither changed nor read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from memforge import recall
from memforge.cli import (
    audit,
    frontmatter_backfill,
    index_gen,
    link_rewriter,
    migrate_claim_block,
    preamble_extract,
    query,
    resolve,
    validate,
)
from memforge.cli import recall as recall_cli
from memforge.discovery import walk_memory_files

OUTSIDE_SECRET = "outside-secret-value"

OUTSIDE_MEMORY = (
    "---\n"
    "name: outside\n"
    f"description: {OUTSIDE_SECRET}\n"
    "type: reference\n"
    "---\n\n"
    "Outside body.\n"
)

INSIDE_MEMORY = (
    "---\n"
    "name: inside\n"
    "description: a memory that lives in the folder\n"
    "type: reference\n"
    "---\n\n"
    "Inside body.\n"
)


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks are not available on this platform")


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    """(memory folder, outside folder) as siblings, one real memory inside."""
    folder = tmp_path / "store"
    outside = tmp_path / "outside"
    folder.mkdir()
    outside.mkdir()
    (folder / "inside.md").write_text(INSIDE_MEMORY, encoding="utf-8")
    return folder, outside


@pytest.fixture
def linked_file(store: tuple[Path, Path]) -> tuple[Path, Path, Path]:
    """(folder, link inside the folder, its target outside the folder)."""
    folder, outside = store
    target = outside / "victim.md"
    target.write_text(OUTSIDE_MEMORY, encoding="utf-8")
    link = folder / "link.md"
    _symlink(link, target)
    return folder, link, target


@pytest.fixture
def linked_dir(store: tuple[Path, Path]) -> tuple[Path, Path]:
    """(folder, outside file reachable through a symlinked subfolder)."""
    folder, outside = store
    target = outside / "victim.md"
    target.write_text(OUTSIDE_MEMORY, encoding="utf-8")
    _symlink(folder / "linked", outside)
    return folder, target


# ---------- writers ----------


def test_backfill_apply_leaves_a_symlinked_memory_untouched(linked_file, capsys):
    folder, _link, target = linked_file

    rc = frontmatter_backfill.cmd_run([folder], apply=True, limit=0)

    assert rc == 0
    assert target.read_text(encoding="utf-8") == OUTSIDE_MEMORY
    assert "uid:" in (folder / "inside.md").read_text(encoding="utf-8")


def test_backfill_apply_change_refuses_a_symlink(linked_file, capsys):
    _folder, link, target = linked_file

    frontmatter_backfill.apply_change(link, {"owner": "operator"})

    assert target.read_text(encoding="utf-8") == OUTSIDE_MEMORY
    assert "symlink" in capsys.readouterr().err


def test_index_gen_refuses_a_symlinked_memory_md(store, capsys):
    folder, outside = store
    target = outside / "startup.sh"
    target.write_text("echo original\n", encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)

    rc = index_gen.process(folder, "write")

    assert rc == 1
    assert target.read_text(encoding="utf-8") == "echo original\n"
    assert "symlink" in capsys.readouterr().err


def test_index_gen_continues_with_the_next_folder(store, tmp_path, monkeypatch, capsys):
    folder, outside = store
    target = outside / "startup.sh"
    target.write_text("echo original\n", encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "inside.md").write_text(INSIDE_MEMORY, encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["memory-index-gen", "--path", str(folder), "--path", str(clean)]
    )

    rc = index_gen.main()

    assert rc == 1
    assert target.read_text(encoding="utf-8") == "echo original\n"
    assert "inside" in (clean / "MEMORY.md").read_text(encoding="utf-8")


def test_preamble_extract_refuses_a_symlinked_config(store, capsys):
    folder, outside = store
    (folder / "MEMORY.md").write_text("# Title\n\nOperator note.\n", encoding="utf-8")
    target = outside / "newfile.sh"
    _symlink(folder / "_memforge.yaml", target)

    rc = preamble_extract.cmd_run([folder], apply=True)

    assert rc == 1
    assert not target.exists()
    assert "symlink" in capsys.readouterr().err


def test_recall_write_index_refuses_a_planted_tmp_symlink(store):
    folder, outside = store
    target = outside / "precious.txt"
    target.write_text("precious\n", encoding="utf-8")
    (folder / ".memforge").mkdir()
    _symlink(folder / ".memforge" / "recall-index.json.tmp", target)

    with pytest.raises(OSError, match="symlink"):
        recall.write_index(folder, recall.build_index(folder))

    assert target.read_text(encoding="utf-8") == "precious\n"


def test_recall_write_index_refuses_a_symlinked_state_folder(store):
    folder, outside = store
    _symlink(folder / ".memforge", outside)

    with pytest.raises(OSError, match="symlink"):
        recall.write_index(folder, recall.build_index(folder))

    assert list(outside.iterdir()) == []


def test_recall_rebuild_reports_the_refusal_and_stays_fail_open(store, capsys):
    folder, outside = store
    target = outside / "precious.txt"
    target.write_text("precious\n", encoding="utf-8")
    (folder / ".memforge").mkdir()
    _symlink(folder / ".memforge" / "recall-index.json.tmp", target)

    rc = recall_cli.main(["--path", str(folder), "--rebuild"])

    assert rc == 0
    assert target.read_text(encoding="utf-8") == "precious\n"
    assert "symlink" in capsys.readouterr().err


def test_audit_add_defaults_leaves_a_symlinked_memory_untouched(linked_file, monkeypatch, capsys):
    folder, _link, target = linked_file
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    audit.audit_target(folder, stale_days=90, fix=False, add_defaults=True, json_out=False)

    assert target.read_text(encoding="utf-8") == OUTSIDE_MEMORY
    assert "sensitivity: internal" in (folder / "inside.md").read_text(encoding="utf-8")


def test_audit_add_defaults_does_not_enter_a_symlinked_subfolder(linked_dir, monkeypatch, capsys):
    folder, target = linked_dir
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    audit.audit_target(folder, stale_days=90, fix=False, add_defaults=True, json_out=False)

    assert target.read_text(encoding="utf-8") == OUTSIDE_MEMORY


def test_audit_fix_refuses_a_symlinked_memory_md(store, monkeypatch, capsys):
    folder, outside = store
    index_text = "# Index\n\n- [inside](inside.md): here\n- [gone](gone.md): orphan pointer\n"
    target = outside / "index-elsewhere.md"
    target.write_text(index_text, encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    audit.audit_target(folder, stale_days=90, fix=True, add_defaults=False, json_out=False)

    assert target.read_text(encoding="utf-8") == index_text
    assert "symlink" in capsys.readouterr().out


def test_migrate_claim_block_refuses_a_symlinked_memory_md(store, capsys):
    folder, outside = store
    legacy = (
        "# Index\n\n```yaml\n"
        "# memforge:competing-claims:begin\n"
        "- decision_topic: the-topic\n"
        "  status: competing\n"
        "# memforge:competing-claims:end\n"
        "```\n"
    )
    target = outside / "index-elsewhere.md"
    target.write_text(legacy, encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)

    count = migrate_claim_block.migrate_file(folder / "MEMORY.md", dry_run=False)

    assert count == 0
    assert target.read_text(encoding="utf-8") == legacy
    assert "symlink" in capsys.readouterr().err


def test_resolve_leaves_a_symlinked_member_untouched(store):
    folder, outside = store
    member = (
        "---\nname: {name}\ndescription: fixture\ntype: feedback\nuid: {uid}\n"
        "status: active\ndecision_topic: the-topic\n---\n\nBody for {name}.\n"
    )
    (folder / "a.md").write_text(member.format(name="a", uid="mem-a"), encoding="utf-8")
    (folder / "b.md").write_text(member.format(name="b", uid="mem-b"), encoding="utf-8")
    outside_text = member.format(name="c", uid="mem-c")
    target = outside / "c.md"
    target.write_text(outside_text, encoding="utf-8")
    _symlink(folder / "c.md", target)

    rc = resolve.main([
        "the-topic", "--memory-root", str(folder), "--winner-uid", "mem-a",
    ])

    assert rc == 0
    assert target.read_text(encoding="utf-8") == outside_text
    assert "status: superseded" in (folder / "b.md").read_text(encoding="utf-8")


def test_link_upgrade_leaves_a_symlinked_memory_untouched(store):
    folder, outside = store
    (folder / "inside.md").write_text(
        INSIDE_MEMORY.replace("type: reference\n", "type: reference\nuid: mem-inside\n"),
        encoding="utf-8",
    )
    outside_text = OUTSIDE_MEMORY + "See [inside](inside.md).\n"
    target = outside / "victim.md"
    target.write_text(outside_text, encoding="utf-8")
    _symlink(folder / "link.md", target)

    rc = link_rewriter.cmd_upgrade(link_rewriter.index_folder(folder), dry_run=False)

    assert rc == 0
    assert target.read_text(encoding="utf-8") == outside_text


# ---------- readers ----------


def test_walk_memory_files_skips_a_symlinked_file(linked_file):
    folder, _link, _target = linked_file

    assert [p.name for p in walk_memory_files(folder)] == ["inside.md"]


def test_walk_memory_files_does_not_descend_a_symlinked_folder(linked_dir):
    folder, _target = linked_dir

    assert [p.name for p in walk_memory_files(folder)] == ["inside.md"]


def test_query_does_not_read_through_a_symlink(linked_file):
    folder, _link, _target = linked_file

    hits = query.discover(folder)

    assert [h.rel for h in hits] == ["inside.md"]
    assert all(OUTSIDE_SECRET not in str(h.fm) for h in hits)


def test_index_gen_does_not_read_a_symlinked_config(store, capsys):
    folder, outside = store
    target = outside / "config.yaml"
    target.write_text(f"title: T\npreamble: {OUTSIDE_SECRET}\n", encoding="utf-8")
    _symlink(folder / "_memforge.yaml", target)

    rc = index_gen.process(folder, "print")

    captured = capsys.readouterr()
    assert rc == 0
    assert OUTSIDE_SECRET not in captured.out
    assert "symlink" in captured.err


def test_preamble_extract_does_not_read_a_symlinked_memory_md(store, capsys):
    folder, outside = store
    target = outside / "notes.md"
    target.write_text(f"# Title\n\n{OUTSIDE_SECRET}\n", encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)

    rc = preamble_extract.cmd_run([folder], apply=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert OUTSIDE_SECRET not in captured.out
    assert not (folder / "_memforge.yaml").exists()
    assert "symlink" in captured.err


def test_validate_walk_skips_symlinks(linked_file):
    folder, _link, _target = linked_file

    assert [p.name for p, _root in validate._iter_target_files([folder], [])] == ["inside.md"]


def test_backfill_discovery_skips_symlinks(linked_file):
    folder, _link, _target = linked_file

    assert [p.name for p in frontmatter_backfill.discover_files(folder)] == ["inside.md"]


def test_resolve_walk_skips_symlinks(linked_file):
    folder, _link, _target = linked_file

    assert [p.name for p, _fm, _body in resolve._walk_memory(folder)] == ["inside.md"]


def test_audit_file_lists_skip_symlinked_files(linked_file):
    folder, _link, _target = linked_file

    assert audit._files_to_audit(folder) == ["inside.md"]
    assert audit._disk_md_files(folder) == ["inside.md"]
    assert audit._all_md_files_recursive(folder) == {"inside.md"}


def test_audit_file_lists_skip_symlinked_folders(linked_dir):
    folder, target = linked_dir
    (target.parent / "README.md").write_text(OUTSIDE_MEMORY, encoding="utf-8")

    assert audit._files_to_audit(folder) == ["inside.md"]
    assert audit._disk_md_files(folder) == ["inside.md"]
    assert audit._all_md_files_recursive(folder) == {"inside.md"}
