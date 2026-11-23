"""A folder swapped for a symlink after a tool has checked it is not followed.

The file helpers open the memory folder once and walk down to the file one
folder at a time, each refusing a link, so a folder replaced by a link to an
outside folder between the check and the open cannot redirect a read or a
write. Here the swap is done by the first open the helper makes into the
folder, which is the latest moment an attacker could act.

memory-audit reads its index, rollup READMEs and the files it rewrites the
same way, so a symlinked or swapped one is reported and never read through.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from memforge import discovery, recall
from memforge.cli import (
    audit,
    audit_deep,
    frontmatter_backfill,
    index_gen,
    link_rewriter,
    lint,
    migrate_claim_block,
    query,
    resolve,
)
from memforge.discovery import (
    LinkReadRefused,
    SymlinkWriteRefused,
    read_bytes_no_follow,
    read_text_no_follow,
    write_text_inside,
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

needs_dir_fd = pytest.mark.skipif(
    os.open not in os.supports_dir_fd,
    reason="opening relative to a folder descriptor is not available here",
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
    return folder


@pytest.fixture
def outside_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "README.md").write_text(OUTSIDE_MEMORY, encoding="utf-8")
    return folder


@pytest.fixture
def swap_on_first_open(store: Path, outside_dir: Path, tmp_path: Path, monkeypatch):
    """Replace ``topic/`` with a link to the outside folder at the first open
    by path of the memory folder or anything under ``topic/``."""
    sub = store / "topic"
    watched = {os.fspath(store), os.fspath(store.resolve())}
    prefixes = (os.fspath(sub) + os.sep, os.fspath(sub.resolve()) + os.sep)
    real_open = os.open
    swapped = []

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        name = os.fspath(path)
        if not swapped and dir_fd is None and (name in watched or name.startswith(prefixes)):
            swapped.append(name)
            sub.rename(tmp_path / "moved-away")
            _symlink(sub, outside_dir)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapping_open)
    return swapped


# ---------- the helpers ----------


@needs_dir_fd
def test_write_is_refused_when_a_folder_is_swapped_before_the_open(
    store, outside_dir, swap_on_first_open
):
    with pytest.raises(SymlinkWriteRefused):
        write_text_inside(store, store / "topic" / "README.md", "new content\n")

    assert swap_on_first_open, "the swap never ran"
    assert (outside_dir / "README.md").read_text(encoding="utf-8") == OUTSIDE_MEMORY


@needs_dir_fd
def test_new_file_is_not_created_through_a_swapped_folder(
    store, outside_dir, swap_on_first_open
):
    with pytest.raises(SymlinkWriteRefused):
        write_text_inside(store, store / "topic" / "new.md", "new content\n")

    assert swap_on_first_open, "the swap never ran"
    assert not (outside_dir / "new.md").exists()


@needs_dir_fd
def test_read_bytes_is_refused_when_a_folder_is_swapped_before_the_open(
    store, swap_on_first_open
):
    with pytest.raises(LinkReadRefused):
        read_bytes_no_follow(store / "topic" / "README.md", root=store)

    assert swap_on_first_open, "the swap never ran"


@needs_dir_fd
def test_read_text_is_refused_when_a_folder_is_swapped_before_the_open(
    store, swap_on_first_open
):
    with pytest.raises(LinkReadRefused):
        read_text_no_follow(store / "topic" / "README.md", root=store)

    assert swap_on_first_open, "the swap never ran"


def test_read_with_root_refuses_a_folder_that_already_leaves_it(store, outside_dir):
    _symlink(store / "linked", outside_dir)

    with pytest.raises(LinkReadRefused, match="leaves"):
        read_text_no_follow(store / "linked" / "README.md", root=store)


@pytest.fixture(params=["by folder descriptor", "by path"])
def each_open_path(request, monkeypatch) -> None:
    """Run once as built and once as on a platform with no ``dir_fd``."""
    if request.param == "by path":
        monkeypatch.setattr(discovery, "_OPEN_BY_FOLDER", False, raising=False)


def test_nested_file_round_trips_with_a_root(store, each_open_path):
    target = store / "topic" / "deeper" / "note.md"
    target.parent.mkdir()

    write_text_inside(store, target, "two\nlines\n")

    assert target.read_text(encoding="utf-8") == "two\nlines\n"
    assert read_text_no_follow(target, root=store) == "two\nlines\n"
    assert read_bytes_no_follow(target, limit=3, root=store) == b"two"


def test_symlinked_file_under_a_root_is_still_refused(store, outside_dir, each_open_path):
    _symlink(store / "topic" / "link.md", outside_dir / "README.md")

    with pytest.raises(LinkReadRefused, match="symlink"):
        read_text_no_follow(store / "topic" / "link.md", root=store)
    with pytest.raises(SymlinkWriteRefused, match="symlink"):
        write_text_inside(store, store / "topic" / "link.md", "new content\n")
    assert (outside_dir / "README.md").read_text(encoding="utf-8") == OUTSIDE_MEMORY


# ---------- the tools that walk a folder ----------


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
}


@needs_dir_fd
@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tool_does_not_read_through_a_folder_swapped_before_the_open(
    store, swap_on_first_open, tool
):
    produced = _printed(lambda: TOOLS[tool](store))

    assert SECRET not in produced


def _swap_before(module, name: str, store: Path, outside_dir: Path, tmp_path, monkeypatch):
    """Swap ``topic/`` for a link to the outside folder as ``module.name`` is
    first called, after the tool's walk and reads have checked the folder."""
    real = getattr(module, name)
    swapped = []

    def swap_then_call(*args, **kwargs):
        if not swapped:
            swapped.append(name)
            (store / "topic").rename(tmp_path / "moved-away")
            _symlink(store / "topic", outside_dir)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, swap_then_call)
    return swapped


def test_resolve_does_not_write_through_a_swapped_folder(
    store, outside_dir, tmp_path, monkeypatch
):
    swapped = _swap_before(resolve, "render", store, outside_dir, tmp_path, monkeypatch)

    with contextlib.suppress(OSError):
        _printed(
            lambda: resolve.main(
                ["the-topic", "--memory-root", str(store), "--winner-uid", "mem-inside"]
            )
        )

    assert swapped, "the swap never ran"
    assert (outside_dir / "README.md").read_text(encoding="utf-8") == OUTSIDE_MEMORY


def test_backfill_does_not_write_through_a_swapped_folder(
    store, outside_dir, tmp_path, monkeypatch
):
    swapped = _swap_before(
        frontmatter_backfill, "apply_change", store, outside_dir, tmp_path, monkeypatch
    )

    _printed(lambda: frontmatter_backfill.cmd_run([store], apply=True, limit=0))

    assert swapped, "the swap never ran"
    assert (outside_dir / "README.md").read_text(encoding="utf-8") == OUTSIDE_MEMORY


def test_migrate_claim_block_does_not_write_through_a_swapped_folder(
    store, outside_dir, tmp_path, monkeypatch
):
    legacy = (
        "# Index\n\n```yaml\n"
        "# memforge:competing-claims:begin\n"
        "- decision_topic: the-topic\n"
        "  status: competing\n"
        "# memforge:competing-claims:end\n"
        "```\n"
    )
    (store / "topic" / "MEMORY.md").write_text(legacy, encoding="utf-8")
    (outside_dir / "MEMORY.md").write_text(legacy, encoding="utf-8")
    swapped = _swap_before(
        migrate_claim_block, "migrate_text", store, outside_dir, tmp_path, monkeypatch
    )

    _printed(lambda: migrate_claim_block.main(["--memory-root", str(store)]))

    assert swapped, "the swap never ran"
    assert (outside_dir / "MEMORY.md").read_text(encoding="utf-8") == legacy


# ---------- memory-audit reads ----------


def _run_audit(folder: Path, **flags) -> tuple[dict, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _count, blob = audit.audit_target(
            folder,
            stale_days=90,
            fix=flags.get("fix", False),
            add_defaults=flags.get("add_defaults", False),
            json_out=True,
        )
    return blob, out.getvalue()


def test_audit_reports_a_symlinked_index_as_unreadable(store, tmp_path):
    elsewhere = tmp_path / "index-elsewhere.md"
    elsewhere.write_text(f"# Index\n\n- [x]({SECRET}.md): hook\n", encoding="utf-8")
    _symlink(store / "MEMORY.md", elsewhere)

    blob, out = _run_audit(store)

    assert any(v.startswith("MEMORY.md: cannot read file") for v in blob["violations"])
    assert SECRET not in json.dumps(blob) + out


def test_audit_reports_a_symlinked_rollup_readme_as_unreadable(store, tmp_path):
    elsewhere = tmp_path / "readme-elsewhere.md"
    elsewhere.write_text(f"# Topic\n\n- [x]({SECRET}.md): hook\n", encoding="utf-8")
    (store / "topic" / "README.md").unlink()
    (store / "topic" / "note.md").write_text(INSIDE_MEMORY, encoding="utf-8")
    _symlink(store / "topic" / "README.md", elsewhere)

    blob, out = _run_audit(store)

    assert any("Rollup README unreadable: topic/README.md" in v for v in blob["violations"])
    assert SECRET not in json.dumps(blob) + out


def test_add_defaults_does_not_reread_a_file_swapped_for_a_link(
    store, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "no-fence.md"
    elsewhere.write_text(f"{SECRET}\n", encoding="utf-8")
    note = store / "inside.md"

    def swap_then_agree(_prompt=""):
        note.unlink()
        _symlink(note, elsewhere)
        return "y"

    monkeypatch.setattr("builtins.input", swap_then_agree)

    _blob, out = _run_audit(store, add_defaults=True)

    assert "SKIPPED inside.md: cannot read file" in out
    assert "closing fence" not in out
    assert elsewhere.read_text(encoding="utf-8") == f"{SECRET}\n"


def test_fix_does_not_reread_an_index_swapped_for_a_link(store, tmp_path, monkeypatch):
    index = store / "MEMORY.md"
    index.write_text(
        "# Index\n\n"
        "- [inside](inside.md): hook\n"
        "- [topic](topic/README.md): hook\n"
        "- [gone](gone.md): hook\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "index-elsewhere.md"
    elsewhere.write_text(f"# {SECRET}\n", encoding="utf-8")
    answers = iter(["n", "y"])

    def swap_then_answer(_prompt=""):
        if not index.is_symlink():
            index.unlink()
            _symlink(index, elsewhere)
        return next(answers)

    monkeypatch.setattr("builtins.input", swap_then_answer)

    _blob, out = _run_audit(store, add_defaults=True, fix=True)

    assert "--fix: cannot read" in out
    assert "not found (skipped)" not in out
    assert elsewhere.read_text(encoding="utf-8") == f"# {SECRET}\n"
