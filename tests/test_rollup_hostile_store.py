"""memory-rollup on a store whose committed content is hostile: a source file
that is a link, and history records that name files other than the ones
create moved. Each case runs with and without memory-link-rewriter found."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from memforge.cli import rollup

needs_links = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")

SIGNERS = "alice namespaces=\"git\" ssh-ed25519 AAAAgoodkey\n"


def _memory(folder: Path, name: str) -> Path:
    path = folder / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: fixture {name}\ntype: project\n"
        f"uid: mem-{name}\nstatus: active\n---\n\nBody for {name}.\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(params=["no-rewriter", "rewriter"])
def rewriter(request, tmp_path_factory, monkeypatch) -> None:
    """No link rewriter found, or the package's own one."""
    found = None
    if request.param == "rewriter":
        found = tmp_path_factory.mktemp("bin") / "memory-link-rewriter"
        if os.name == "nt":
            # Windows starts no #! script; a batch file runs the same module.
            found = found.with_suffix(".cmd")
            found.write_text(
                f'@"{sys.executable}" -c "import sys; '
                'from memforge.cli.link_rewriter import main; sys.exit(main())" %*\r\n',
                encoding="utf-8",
            )
        else:
            found.write_text(
                f"#!{sys.executable}\nimport sys\n"
                "from memforge.cli.link_rewriter import main\nsys.exit(main())\n",
                encoding="utf-8",
            )
            found.chmod(0o755)
    monkeypatch.setattr(rollup, "find_link_rewriter", lambda: found)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A memory folder with a.md, b.md and a signer list in .memforge."""
    folder = tmp_path / "store"
    folder.mkdir()
    _memory(folder, "a")
    _memory(folder, "b")
    (folder / ".memforge").mkdir()
    (folder / ".memforge" / "allowed_signers").write_text(SIGNERS, encoding="utf-8")
    return folder


def _record(folder: Path, slug: str, readme: str, moved: list[dict[str, str]]) -> Path:
    history = folder / rollup.HISTORY_DIRNAME
    history.mkdir(exist_ok=True)
    path = history / f"20990101T000000Z-{slug}.json"
    path.write_text(
        json.dumps({"operation": "create", "slug": slug, "readme": readme, "moved": moved}),
        encoding="utf-8",
    )
    return path


# ---------- create: a source that is a link ----------


@needs_links
def test_create_refuses_a_source_linked_outside_the_store(folder, tmp_path, rewriter, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("SECRET-CONTENT\n", encoding="utf-8")
    os.symlink(outside / "secret.md", folder / "x.md")

    rc = rollup.cmd_create(folder, "ops", [Path("a.md"), Path("x.md")], None, None, False)

    assert rc == 2
    assert (outside / "secret.md").read_text(encoding="utf-8") == "SECRET-CONTENT\n"
    assert not (folder / "ops").exists()
    assert (folder / "a.md").exists()
    assert "symlink" in capsys.readouterr().err


@needs_links
def test_create_refuses_a_source_linked_to_a_store_file(folder, rewriter):
    os.symlink(".memforge/allowed_signers", folder / "x.md")

    rc = rollup.cmd_create(folder, "ops", [Path("a.md"), Path("x.md")], None, None, False)

    assert rc == 2
    assert (folder / ".memforge" / "allowed_signers").read_text(encoding="utf-8") == SIGNERS
    assert not (folder / "ops").exists()


def test_create_refuses_a_source_that_is_not_a_file(folder, rewriter):
    (folder / "x.md").mkdir()

    rc = rollup.cmd_create(folder, "ops", [Path("a.md"), Path("x.md")], None, None, False)

    assert rc == 2
    assert os.path.isdir(folder / "x.md")
    assert not (folder / "ops").exists()


def test_create_and_undo_round_trip(folder, rewriter):
    assert rollup.cmd_create(folder, "ops", [Path("a.md"), Path("b.md")], None, None, False) == 0
    assert (folder / "ops" / "a.md").read_text(encoding="utf-8").endswith("Body for a.\n")
    assert not (folder / "a.md").exists()

    assert rollup.cmd_undo(folder, "ops", False) == 0
    assert (folder / "a.md").read_text(encoding="utf-8").endswith("Body for a.\n")
    assert (folder / "b.md").exists()
    assert not (folder / "ops").exists()


# ---------- undo: every path a history record names ----------


def test_undo_does_not_delete_a_store_file_named_as_the_readme(folder, rewriter, capsys):
    (folder / "ops").mkdir()
    _record(folder, "ops", str(folder / ".memforge" / "allowed_signers"), [])

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert (folder / ".memforge" / "allowed_signers").read_text(encoding="utf-8") == SIGNERS
    assert "outside the memory folder" in capsys.readouterr().err


def test_undo_does_not_overwrite_a_store_file_named_as_a_source(folder, rewriter):
    (folder / "notes.md").write_text("mallory ssh-ed25519 AAAAevilkey\n", encoding="utf-8")
    (folder / "ops").mkdir()
    _record(folder, "ops", str(folder / "ops" / "README.md"),
            [{"from": str(folder / ".memforge" / "allowed_signers"),
              "to": str(folder / "notes.md")}])

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert (folder / ".memforge" / "allowed_signers").read_text(encoding="utf-8") == SIGNERS
    assert (folder / "notes.md").exists()


def test_undo_does_not_overwrite_an_existing_top_level_file(folder, rewriter):
    (folder / "ops").mkdir()
    _memory(folder / "ops", "c")
    _record(folder, "ops", str(folder / "ops" / "README.md"),
            [{"from": str(folder / "a.md"), "to": str(folder / "ops" / "c.md")}])

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert (folder / "a.md").read_text(encoding="utf-8").endswith("Body for a.\n")
    assert (folder / "ops" / "c.md").exists()


def _snapshot(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
        for p in folder.rglob("*") if p.is_file() and rollup.HISTORY_DIRNAME not in p.parts
    }


@pytest.fixture
def rollup_folder(folder: Path) -> Path:
    """`folder` with a rollup folder ops/ holding c.md and ops/sub/ holding s.md."""
    (folder / "ops" / "sub").mkdir(parents=True)
    _memory(folder / "ops", "c")
    _memory(folder / "ops" / "sub", "s")
    return folder


@pytest.mark.parametrize("readme", ["ops/../a.md", "a.md", "ops/sub/s.md", "ops/c.md"])
def test_undo_takes_the_readme_only_where_create_writes_it(rollup_folder, rewriter, readme):
    folder = rollup_folder
    _record(folder, "ops", f"{folder}/{readme}", [])
    before = _snapshot(folder)

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert _snapshot(folder) == before


@pytest.mark.parametrize("source, moved", [
    ("x.md", "ops/../b.md"),
    ("x.md", "b.md"),
    ("x.md", "ops/sub/s.md"),
    (".memforge/x.md", "ops/c.md"),
    ("ops/../x.md", "ops/c.md"),
])
def test_undo_takes_a_move_only_from_the_slug_folder_to_the_top(
    rollup_folder, rewriter, source, moved
):
    folder = rollup_folder
    _record(folder, "ops", str(folder / "ops" / "README.md"),
            [{"from": f"{folder}/{source}", "to": f"{folder}/{moved}"}])
    before = _snapshot(folder)

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert _snapshot(folder) == before


def test_undo_refuses_a_malformed_record(folder, rewriter):
    (folder / "ops").mkdir()
    _record(folder, "ops", str(folder / "ops" / "README.md"), [{"from": 5}])

    assert rollup.cmd_undo(folder, "ops", False) == 2


@needs_links
def test_undo_refuses_a_rollup_folder_that_is_a_link(folder, rewriter):
    os.symlink(".memforge", folder / "ops")
    _record(folder, "ops", str(folder / "ops" / "README.md"),
            [{"from": str(folder / "stolen"), "to": str(folder / "ops" / "allowed_signers")}])

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert (folder / ".memforge" / "allowed_signers").read_text(encoding="utf-8") == SIGNERS
    assert not (folder / "stolen").exists()


# ---------- the history folder itself ----------

SECRET_SLUG = "outside-the-store"


def _history_elsewhere(folder: Path, tmp_path: Path) -> None:
    """The history folder is a link to a folder outside the store holding a record."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / f"20990101T000000Z-{SECRET_SLUG}.json").write_text(
        json.dumps({"operation": "create", "slug": SECRET_SLUG, "moved": []}), encoding="utf-8"
    )
    os.symlink(elsewhere, folder / rollup.HISTORY_DIRNAME)


@needs_links
def test_list_refuses_a_history_folder_that_is_a_link(folder, tmp_path, capsys):
    _history_elsewhere(folder, tmp_path)

    assert rollup.cmd_list(folder) == 2

    out = capsys.readouterr()
    assert SECRET_SLUG not in out.out
    assert "symlink or not a folder" in out.err


@needs_links
def test_undo_refuses_a_history_folder_that_is_a_link(folder, tmp_path, rewriter, capsys):
    _history_elsewhere(folder, tmp_path)

    assert rollup.cmd_undo(folder, SECRET_SLUG, False) == 2

    assert "symlink or not a folder" in capsys.readouterr().err


@pytest.mark.parametrize("op", ["list", "undo", "create"])
def test_a_history_folder_that_is_a_file_is_refused(folder, rewriter, capsys, op):
    (folder / rollup.HISTORY_DIRNAME).write_text("not a folder\n", encoding="utf-8")

    if op == "list":
        rc = rollup.cmd_list(folder)
    elif op == "undo":
        rc = rollup.cmd_undo(folder, "ops", False)
    else:
        rc = rollup.cmd_create(folder, "ops", [Path("a.md")], None, None, False)

    assert rc == 2
    assert "symlink or not a folder" in capsys.readouterr().err
    assert (folder / "a.md").exists()
    assert not (folder / "ops").exists()


needs_modes = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="needs POSIX permission bits that the user cannot bypass",
)


@needs_modes
@pytest.mark.parametrize("op", ["list", "undo"])
@pytest.mark.parametrize("locked, mode", [("history", 0o000), ("store", 0o000), ("store", 0o444)])
def test_an_unreadable_history_folder_is_reported_not_empty(
    folder, rewriter, capsys, op, locked, mode
):
    _record(folder, "ops", str(folder / "ops" / "README.md"), [])
    target = folder / rollup.HISTORY_DIRNAME if locked == "history" else folder
    target.chmod(mode)
    try:
        rc = rollup.cmd_list(folder) if op == "list" else rollup.cmd_undo(folder, "ops", True)
    finally:
        target.chmod(0o755)

    assert rc == 2
    assert "cannot read rollup history" in capsys.readouterr().err


@needs_modes
@pytest.mark.parametrize("locked, mode", [("parent", 0o000), ("store", 0o444)])
def test_create_reports_an_unreadable_folder_not_a_missing_one(
    folder, rewriter, capsys, locked, mode
):
    target = folder.parent if locked == "parent" else folder
    target.chmod(mode)
    try:
        rc = rollup.cmd_create(folder, "ops", [Path("a.md")], None, None, False)
    finally:
        target.chmod(0o755)

    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot read" in err
    assert "not found" not in err
    assert (folder / "a.md").exists()


# ---------- history written before create kept file names as given ----------


def _earlier_create(folder: Path, slug: str, names: list[str]) -> None:
    """Lay out a rollup and its record as create wrote them when it recorded
    each source by its resolved path and the rest by the folder as given."""
    (folder / slug).mkdir()
    moved = []
    for name in names:
        moved.append({"from": str((folder / name).resolve()), "to": str(folder / slug / name)})
        os.rename(folder / name, folder / slug / name)
    (folder / slug / "README.md").write_text("# rollup\n", encoding="utf-8")
    history = folder / rollup.HISTORY_DIRNAME
    history.mkdir()
    (history / f"20260101T000000Z-{slug}.json").write_text(json.dumps({
        "schema": "memforge-rollup-history/v1", "operation": "create", "slug": slug,
        "timestamp": "20260101T000000Z", "folder": str(folder), "moved": moved,
        "readme": str(folder / slug / "README.md"),
    }, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(params=["relative", "through-a-link"])
def spelled_folder(request, folder: Path, tmp_path: Path, monkeypatch) -> Path:
    """``folder`` spelled as a relative path, or through a linked parent."""
    if request.param == "relative":
        monkeypatch.chdir(tmp_path)
        return Path(folder.name)
    if os.name == "nt":
        pytest.skip("needs POSIX symlinks")
    os.symlink(tmp_path, tmp_path.parent / f"{tmp_path.name}-via")
    return tmp_path.parent / f"{tmp_path.name}-via" / folder.name


def test_undo_reverses_history_from_the_earlier_create(folder, spelled_folder, rewriter):
    _earlier_create(spelled_folder, "ops", ["a.md", "b.md"])

    assert rollup.cmd_undo(spelled_folder, "ops", False) == 0

    assert (folder / "a.md").read_text(encoding="utf-8").endswith("Body for a.\n")
    assert (folder / "b.md").exists()
    assert not (folder / "ops").exists()


def test_undo_from_the_command_line_reverses_history_from_the_earlier_create(
    folder, spelled_folder, rewriter, monkeypatch
):
    _earlier_create(spelled_folder, "ops", ["a.md"])
    monkeypatch.setenv("MEMFORGE_MEMORY_PATH", str(spelled_folder))
    monkeypatch.setattr(sys, "argv", ["memory-rollup", "undo", "--slug", "ops"])

    assert rollup.main() == 0

    assert (folder / "a.md").read_text(encoding="utf-8").endswith("Body for a.\n")
    assert not (folder / "ops").exists()


def test_undo_still_refuses_a_spelled_folder_record_naming_another_folder(
    folder, spelled_folder, rewriter
):
    (folder / "other").mkdir()
    _earlier_create(spelled_folder, "ops", ["a.md"])
    record = next((folder / rollup.HISTORY_DIRNAME).iterdir())
    data = json.loads(record.read_text(encoding="utf-8"))
    data["moved"][0]["from"] = str((folder / "other" / "a.md").resolve())
    record.write_text(json.dumps(data), encoding="utf-8")

    assert rollup.cmd_undo(spelled_folder, "ops", False) == 2

    assert (folder / "ops" / "a.md").exists()
    assert not (folder / "other" / "a.md").exists()


# ---------- a history record that is not what create writes ----------

BAD_RECORDS = {
    "not-json": "{",
    "deeply-nested": "[" * 100_000,
    "a-list": "[]",
    "a-number": "5",
    "null": "null",
    "readme-a-number": '{"operation": "create", "readme": 5, "moved": []}',
    "moved-a-number": '{"operation": "create", "moved": 5}',
    "move-a-number": '{"operation": "create", "moved": [5]}',
    "move-paths-not-text": '{"operation": "create", "moved": [{"from": 5, "to": []}]}',
    "operation-a-list": '{"operation": [], "moved": []}',
}


def _bad_record(folder: Path, text: str) -> Path:
    history = folder / rollup.HISTORY_DIRNAME
    history.mkdir(exist_ok=True)
    path = history / "20990101T000000Z-ops.json"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("text", BAD_RECORDS.values(), ids=BAD_RECORDS.keys())
def test_undo_refuses_a_malformed_record_by_name(folder, rewriter, capsys, text):
    (folder / "ops").mkdir()
    _memory(folder / "ops", "c")
    record = _bad_record(folder, text)
    before = _snapshot(folder)

    assert rollup.cmd_undo(folder, "ops", False) == 2

    assert record.name in capsys.readouterr().err
    assert _snapshot(folder) == before
    assert record.read_text(encoding="utf-8") == text


# list shows a record's operation, timestamp, slug and file count; a record
# whose other fields are malformed lists as it is, and undo refuses it.
LIST_BAD_RECORDS = {
    **{k: v for k, v in BAD_RECORDS.items()
       if k not in ("readme-a-number", "move-a-number", "move-paths-not-text")},
    "operation-a-number": '{"operation": 5, "slug": "ops", "moved": []}',
    "slug-an-object": '{"operation": "create", "slug": {}, "moved": []}',
    "timestamp-a-list": '{"operation": "create", "timestamp": [], "moved": []}',
}


@pytest.mark.parametrize("text", LIST_BAD_RECORDS.values(), ids=LIST_BAD_RECORDS.keys())
def test_list_reports_a_malformed_record_and_lists_the_rest(folder, capsys, text):
    record = _bad_record(folder, text)
    _record(folder, "good", str(folder / "good" / "README.md"), [])

    assert rollup.cmd_list(folder) == 0

    out = capsys.readouterr().out
    assert f"[malformed] {record.name}" in out or f"[unreadable] {record.name}" in out
    assert "slug=good" in out
