"""Every tool reads and writes store files without following a link.

A tool that walks a memory folder checks what it finds, then reads it. A
folder swapped for a link to an outside folder after the walk, or a link
planted where a tool reads its own state under ``.memforge/``, must not put
outside content into the tool's output or let the tool write outside the
folder. Each test here plants or swaps the link at the latest point the tool
leaves open and checks the outside content never shows.
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
    _concurrency_audit,
    agents_md_gen,
    audit_deep,
    cluster_suggest,
    dedup,
    detect,
    dlp_scan,
    index_gen,
    link_rewriter,
    promote,
    rollup,
    validate,
)
from memforge import paths

SECRET = "outside-secret-value"

INSIDE_MEMORY = (
    "---\n"
    "name: inside\n"
    "description: a memory that lives in the folder\n"
    "type: reference\n"
    "uid: mem-inside\n"
    "status: active\n"
    "---\n\n"
    "Inside body.\n"
)

# Every field a tool prints carries the secret, so any read of this file
# shows in the tool's output.
OUTSIDE_MEMORY = (
    "---\n"
    "name: outside\n"
    f"description: {SECRET}\n"
    "type: reference\n"
    f"uid: {SECRET}\n"
    f"status: {SECRET}\n"
    f"tier: {SECRET}\n"
    f"tags: [topic:{SECRET}]\n"
    "---\n\n"
    f"See [x](../store/inside.md#{SECRET}), [y](mem:{SECRET}) and mem:{SECRET}.\n"
)


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks are not available on this platform")


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A memory folder with one top-level memory and a rollup subfolder."""
    folder = tmp_path / "store"
    (folder / "topic").mkdir(parents=True)
    (folder / "inside.md").write_text(INSIDE_MEMORY, encoding="utf-8")
    (folder / "topic" / "README.md").write_text(
        INSIDE_MEMORY.replace("inside", "inside2"), encoding="utf-8"
    )
    # memory-validate checks the fields of a memory file, not of a README.
    (folder / "topic" / "note.md").write_text(
        INSIDE_MEMORY.replace("inside", "inside3"), encoding="utf-8"
    )
    return folder


@pytest.fixture
def outside_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "README.md").write_text(OUTSIDE_MEMORY, encoding="utf-8")
    (folder / "note.md").write_text(OUTSIDE_MEMORY, encoding="utf-8")
    return folder


@pytest.fixture
def swap_after(store: Path, outside_dir: Path, tmp_path: Path, monkeypatch):
    """Return a patcher: ``swap_after(module, name)`` makes ``module.name``
    run as before, then replace ``topic/`` with a link to the outside folder
    before its result reaches the caller. Patching the walk or the index a
    tool builds puts the swap after every check the tool makes."""
    swapped: list[str] = []

    def patch(module, name: str) -> list[str]:
        real = getattr(module, name)

        def call_then_swap(*args, **kwargs):
            result = real(*args, **kwargs)
            if hasattr(result, "__next__"):
                result = iter(list(result))
            if not swapped:
                swapped.append(name)
                (store / "topic").rename(tmp_path / "moved-away")
                _symlink(store / "topic", outside_dir)
            return result

        monkeypatch.setattr(module, name, call_then_swap)
        return swapped

    return patch


def _printed(run) -> str:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = run()
    return f"{result!r}\n{out.getvalue()}\n{err.getvalue()}"


# ---------- a folder swapped after the walk ----------


WALKED_TOOLS = {
    "concurrency collect_state": (
        _concurrency_audit, "walk_md_files", lambda f: _concurrency_audit.collect_state(f)
    ),
    "concurrency tier1": (
        _concurrency_audit, "walk_md_files", lambda f: _concurrency_audit.tier1_findings(f, {}, {})
    ),
    "detect rule catalog": (
        detect, "walk_md_files", lambda f: detect._build_rule_catalog([f])
    ),
    "promote inbound links": (
        promote, "walk_md_files", lambda f: promote._scan_inbound_path_links(f, f / "inside.md")
    ),
    "validate": (
        validate, "walk_md_files", lambda f: validate.main(["--path", str(f), "--json"])
    ),
    "audit_deep mem links": (
        audit_deep, "discover_files", lambda f: audit_deep.audit(f, {}, 90)
    ),
    "link_rewriter check": (
        link_rewriter, "index_folder",
        lambda f: link_rewriter.cmd_check(link_rewriter.index_folder(f)),
    ),
    "link_rewriter upgrade": (
        link_rewriter, "index_folder",
        lambda f: link_rewriter.cmd_upgrade(link_rewriter.index_folder(f), dry_run=True),
    ),
}


@pytest.mark.parametrize("tool", sorted(WALKED_TOOLS))
def test_tool_does_not_read_through_a_folder_swapped_after_its_walk(store, swap_after, tool):
    module, walk, run = WALKED_TOOLS[tool]
    swapped = swap_after(module, walk)

    produced = _printed(lambda: run(store))

    assert swapped, "the swap never ran"
    assert SECRET not in produced


@pytest.mark.parametrize("batch", [False, True], ids=["rename", "rename-batch"])
def test_rename_reads_everything_before_it_moves_anything(
    store, outside_dir, swap_after, batch
):
    swapped = swap_after(link_rewriter, "index_folder")
    idx = link_rewriter.index_folder(store)
    src, dst = store / "inside.md", store / "renamed.md"

    # rename-batch already rolls its moves back when a write is refused, so
    # its read is shown by the plan it prints in a dry run.
    if batch:
        produced = _printed(lambda: link_rewriter.cmd_rename_batch(idx, [(src, dst)], dry_run=True))
    else:
        produced = _printed(lambda: link_rewriter.cmd_rename(idx, src, dst, dry_run=False))

    assert swapped, "the swap never ran"
    assert produced.startswith("2\n")
    assert "references to rewrite" not in produced
    assert src.exists() and not dst.exists(), "the rename was applied after a refused read"
    assert (outside_dir / "README.md").read_text(encoding="utf-8") == OUTSIDE_MEMORY


def test_dlp_memory_folder_scan_does_not_read_through_a_swapped_folder(
    store, outside_dir, tmp_path, monkeypatch
):
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    (outside_dir / "README.md").write_text(f"aws_access_key_id = {key}\n", encoding="utf-8")
    monkeypatch.setattr(paths, "default_memory_paths", lambda: [store])
    sub = store / "topic"
    prefix = os.fspath(sub) + os.sep
    real_open = os.open
    swapped = []

    def swap_at_first_open_under_topic(path, flags, mode=0o777, *, dir_fd=None):
        name = os.fspath(path)
        if not swapped and dir_fd is None and (name == os.fspath(store) or name.startswith(prefix)):
            swapped.append(name)
            sub.rename(tmp_path / "moved-away")
            _symlink(sub, outside_dir)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_at_first_open_under_topic)
    monkeypatch.setattr("sys.argv", ["memory-dlp-scan", "--memory-folders", "--no-detect-secrets"])

    produced = _printed(dlp_scan.main)

    assert swapped, "the swap never ran"
    assert "aws" not in produced.lower()
    assert "unreadable_file" in produced


# ---------- a top-level file that is a link ----------


@pytest.fixture
def linked_top_level(store: Path, outside_dir: Path) -> Path:
    _symlink(store / "linked.md", outside_dir / "README.md")
    return store


TOP_LEVEL_TOOLS = {
    "dedup": lambda f: dedup.collect_catalog(f, redact_descriptions=False, warn_threshold=0),
    "cluster_suggest": lambda f: [
        (r.path.name, r.fm, sorted(r.topic_tags)) for r in cluster_suggest.discover_top_level(f)
    ],
    "agents_md_gen": lambda f: [
        (e.name, e.description, e.body) for e in agents_md_gen.load_memory_folder(f)
    ],
}


@pytest.mark.parametrize("tool", sorted(TOP_LEVEL_TOOLS))
def test_tool_does_not_read_a_top_level_file_that_is_a_link(linked_top_level, tool):
    produced = _printed(lambda: TOP_LEVEL_TOOLS[tool](linked_top_level))

    assert SECRET not in produced


# ---------- promote ----------


def _promote_folders(store: Path, tmp_path: Path) -> Path:
    target = tmp_path / "global"
    target.mkdir()
    (target / "MEMORY.md").write_text("# Memory Index\n\n", encoding="utf-8")
    return target


def test_promote_does_not_rewrite_a_source_index_that_is_a_link(store, tmp_path):
    target = _promote_folders(store, tmp_path)
    elsewhere = tmp_path / "index-elsewhere.md"
    index = "# Index\n\n- [inside](inside.md): hook\n"
    elsewhere.write_text(index, encoding="utf-8")
    _symlink(store / "MEMORY.md", elsewhere)

    _printed(lambda: promote.main([
        "inside.md", "--source", str(store), "--target", str(target), "--yes", "--no-commit",
    ]))

    assert elsewhere.read_text(encoding="utf-8") == index
    assert (store / "inside.md").exists()


def test_promote_does_not_copy_through_a_target_swapped_after_the_check(
    store, tmp_path, monkeypatch
):
    target = _promote_folders(store, tmp_path)
    (store / "MEMORY.md").write_text("# Index\n\n- [inside](inside.md): hook\n", encoding="utf-8")
    outside_file = tmp_path / "created-outside.md"
    real = promote._find_pointer_line
    swapped = []

    def swap_then_find(*args, **kwargs):
        if not swapped:
            swapped.append(True)
            _symlink(target / "inside.md", outside_file)
        return real(*args, **kwargs)

    monkeypatch.setattr(promote, "_find_pointer_line", swap_then_find)

    _printed(lambda: promote.main([
        "inside.md", "--source", str(store), "--target", str(target), "--yes", "--no-commit",
    ]))

    assert swapped, "the swap never ran"
    assert not outside_file.exists()
    assert (store / "inside.md").exists()


def test_promote_keeps_the_bytes_mode_and_times_of_the_file(store, tmp_path):
    target = _promote_folders(store, tmp_path)
    (store / "MEMORY.md").write_text("# Index\n\n- [inside](inside.md): hook\n", encoding="utf-8")
    source_file = store / "inside.md"
    source_file.write_bytes(INSIDE_MEMORY.replace("\n", "\r\n").encode("utf-8"))
    os.chmod(source_file, 0o640)
    os.utime(source_file, ns=(1_000_000_000_000_000_000, 1_100_000_000_000_000_000))
    before = os.stat(source_file)
    data = source_file.read_bytes()

    rc = promote.main([
        "inside.md", "--source", str(store), "--target", str(target), "--yes", "--no-commit",
    ])

    after = os.stat(target / "inside.md")
    assert rc == 0
    assert (target / "inside.md").read_bytes() == data
    assert after.st_mtime_ns == before.st_mtime_ns
    assert (after.st_mode & 0o777) == (before.st_mode & 0o777)


# ---------- rollup history ----------


def test_rollup_list_does_not_read_a_history_record_that_is_a_link(store, tmp_path):
    record = tmp_path / "record-elsewhere.json"
    record.write_text(json.dumps({"operation": "create", "slug": SECRET}), encoding="utf-8")
    (store / rollup.HISTORY_DIRNAME).mkdir()
    _symlink(store / rollup.HISTORY_DIRNAME / "20260101T000000Z-the-slug.json", record)

    produced = _printed(lambda: rollup.cmd_list(store))

    assert SECRET not in produced
    assert "[unreadable]" in produced


def test_rollup_create_does_not_write_history_through_a_linked_folder(
    store, outside_dir, monkeypatch
):
    monkeypatch.setattr(rollup, "find_link_rewriter", lambda: None)
    _symlink(store / rollup.HISTORY_DIRNAME, outside_dir)

    with contextlib.suppress(OSError):
        _printed(lambda: rollup.cmd_create(
            store, "the-slug", [Path("inside.md")], None, None, dry_run=False
        ))

    assert sorted(p.name for p in outside_dir.iterdir()) == ["README.md", "note.md"]


# ---------- tool state under .memforge/ ----------


@pytest.fixture
def linked_state_folder(store: Path, outside_dir: Path) -> Path:
    """``.memforge/`` in the store is a link to the outside folder."""
    _symlink(store / ".memforge", outside_dir)
    return outside_dir


def test_recall_synonyms_are_not_read_through_a_link(store, linked_state_folder):
    (linked_state_folder / "recall-synonyms.yaml").write_text(
        f"map:\n  {SECRET}: [x]\n", encoding="utf-8"
    )

    assert SECRET not in recall.load_synonyms(store)


def test_recall_index_is_not_read_through_a_link(store, linked_state_folder):
    payload = {
        "version": recall.INDEX_VERSION,
        "entries": {SECRET: {"name": SECRET, "desc": SECRET}},
        "tokens": {},
        "phrases": {},
    }
    (linked_state_folder / "recall-index.json").write_text(json.dumps(payload), encoding="utf-8")

    assert recall.load_index(store) is None


def test_concurrency_waivers_are_not_read_through_a_link(store, linked_state_folder):
    (linked_state_folder / "audit-waivers.yaml").write_text(
        "waived_commits:\n  - abcdef1234567\n", encoding="utf-8"
    )

    assert _concurrency_audit._load_waivers(store).commits == frozenset()


def test_index_gen_snooze_is_not_read_through_a_link(store, linked_state_folder):
    (linked_state_folder / "snoozes").mkdir()
    (linked_state_folder / "snoozes" / "the-topic.yaml").write_text(
        "snoozed_until: '2999-01-01'\n", encoding="utf-8"
    )

    assert index_gen._read_snooze(store, "the-topic") is None


def test_index_gen_check_does_not_compare_against_a_linked_index(store, tmp_path):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        index_gen.process(store, "print")
    expected = out.getvalue()
    elsewhere = tmp_path / "index-elsewhere.md"
    elsewhere.write_text(expected, encoding="utf-8")
    _symlink(store / "MEMORY.md", elsewhere)

    produced = _printed(lambda: index_gen.process(store, "check"))

    assert not produced.startswith("0\n")
    assert "symlink" in produced
