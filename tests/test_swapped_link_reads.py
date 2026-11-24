"""A symlink swapped in after a tool's walk has checked the path is not read.

The walks skip symlinks, but the walk and the read are two steps. Here the
check is patched to see no link, which is what a tool sees when the link
appears between the two, and each tool must still leave the outside file
unread.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from memforge import recall
from memforge.cli import (
    audit,
    audit_deep,
    frontmatter_backfill,
    index_gen,
    link_rewriter,
    lint,
    migrate_claim_block,
    preamble_extract,
    query,
    resolve,
)

SECRET = "outside-secret-value"

OUTSIDE_MEMORY = (
    "---\n"
    "name: outside\n"
    f"description: {SECRET}\n"
    "type: reference\n"
    "uid: mem-outside\n"
    "status: active\n"
    "decision_topic: the-topic\n"
    "---\n\n"
    f"{SECRET} body.\n"
)

INSIDE_MEMORY = (
    "---\n"
    "name: inside\n"
    "description: a memory that lives in the folder\n"
    "type: reference\n"
    "uid: mem-inside\n"
    "status: active\n"
    "decision_topic: the-topic\n"
    "---\n\n"
    "Inside body.\n"
)


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks are not available on this platform")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    store = tmp_path / "store"
    store.mkdir()
    (store / "inside.md").write_text(INSIDE_MEMORY, encoding="utf-8")
    (store / "inside2.md").write_text(
        INSIDE_MEMORY.replace("inside", "inside2"), encoding="utf-8"
    )
    return store


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    target = tmp_path / "victim.md"
    target.write_text(OUTSIDE_MEMORY, encoding="utf-8")
    return target


@pytest.fixture
def swapped_link(folder: Path, outside: Path, monkeypatch) -> Path:
    """`link.md` points outside, and every earlier check sees a plain file."""
    link = folder / "link.md"
    _symlink(link, outside)
    _checks_see_no_link(monkeypatch)
    return link


def _checks_see_no_link(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(os.path, "islink", lambda path: False)


def _printed(run) -> str:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = run()
    return f"{result!r}\n{out.getvalue()}\n{err.getvalue()}"


TOOLS = {
    "query": lambda f: [(h.rel, h.fm, h.body) for h in query.discover(f)],
    "index_gen": lambda f: index_gen.process(f, "print"),
    "recall": lambda f: json.dumps(recall.build_index(f)),
    "lint": lambda f: lint._iter_live_memories(f),
    "resolve": lambda f: resolve._walk_memory(f),
    "link_rewriter": lambda f: [m.frontmatter for m in link_rewriter.index_folder(f).memories],
    "audit_deep": lambda f: [(r.rel, r.fm) for r in audit_deep.discover_files(f)],
    "backfill": lambda f: frontmatter_backfill.cmd_run([f], apply=False, limit=0),
}


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tool_does_not_read_a_link_swapped_in_after_its_walk(folder, swapped_link, tool):
    produced = _printed(lambda: TOOLS[tool](folder))

    assert SECRET not in produced
    assert "inside" in produced


def test_audit_reports_the_swapped_link_as_unreadable(folder, swapped_link, capsys):
    _count, blob = audit.audit_target(
        folder, stale_days=90, fix=False, add_defaults=False, json_out=True
    )

    assert "link.md: cannot read file" in blob["violations"]
    assert SECRET not in capsys.readouterr().out


def test_backfill_plans_nothing_for_a_swapped_link(folder, swapped_link):
    assert frontmatter_backfill.plan_change(swapped_link, folder) is None


def test_index_gen_does_not_read_a_config_swapped_for_a_link(folder, tmp_path, monkeypatch):
    target = tmp_path / "config.yaml"
    target.write_text(f"title: T\npreamble: {SECRET}\n", encoding="utf-8")
    _symlink(folder / "_memforge.yaml", target)
    _checks_see_no_link(monkeypatch)

    produced = _printed(lambda: index_gen.process(folder, "print"))

    assert SECRET not in produced
    assert "# Memory Index" in produced


def test_preamble_extract_does_not_read_an_index_swapped_for_a_link(
    folder, tmp_path, monkeypatch
):
    target = tmp_path / "notes.md"
    target.write_text(f"# Title\n\n{SECRET}\n", encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)
    _checks_see_no_link(monkeypatch)

    produced = _printed(lambda: preamble_extract.cmd_run([folder], apply=True))

    assert SECRET not in produced
    assert not (folder / "_memforge.yaml").exists()


LEGACY_INDEX = (
    "# Index\n\n```yaml\n"
    "# memforge:competing-claims:begin\n"
    "- decision_topic: the-topic\n"
    "  status: competing\n"
    "    status: at-an-indent-the-fixer-warns-about\n"
    "# memforge:competing-claims:end\n"
    "```\n"
)


def test_migrate_claim_block_skips_a_symlinked_index_without_reading_it(
    folder, tmp_path, capsys
):
    target = tmp_path / "index-elsewhere.md"
    target.write_text(LEGACY_INDEX, encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)
    nested = folder / "topic"
    nested.mkdir()
    (nested / "MEMORY.md").write_text(LEGACY_INDEX, encoding="utf-8")

    rc = migrate_claim_block.main(["--memory-root", str(folder)])

    err = capsys.readouterr().err
    assert rc == 0
    assert target.read_text(encoding="utf-8") == LEGACY_INDEX
    assert f"skipping {folder / 'MEMORY.md'}" in err and "symlink" in err
    # The warning below quotes a line number of the file it read: one such
    # line for the real index, none for the linked one.
    assert err.count("unexpected indent") == 1
    assert "  state: competing\n" in (nested / "MEMORY.md").read_text(encoding="utf-8")


def test_migrate_file_does_not_read_an_index_swapped_for_a_link(
    folder, tmp_path, monkeypatch, capsys
):
    target = tmp_path / "index-elsewhere.md"
    target.write_text(LEGACY_INDEX, encoding="utf-8")
    _symlink(folder / "MEMORY.md", target)
    _checks_see_no_link(monkeypatch)

    count = migrate_claim_block.migrate_file(folder / "MEMORY.md", dry_run=False)

    err = capsys.readouterr().err
    assert count == 0
    assert "unexpected indent" not in err
    assert target.read_text(encoding="utf-8") == LEGACY_INDEX
