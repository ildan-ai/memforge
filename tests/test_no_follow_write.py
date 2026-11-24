"""The file helpers decide on the opened file, not on a check made before it.

A check followed by a separate open leaves a window in which the path can be
swapped for a link. These tests plant the link where an earlier check has
already passed (the pre-check is patched to see nothing) and assert the
outside file is not written.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from memforge import discovery, recall
from memforge.discovery import SymlinkWriteRefused, write_text_inside

OUTSIDE = "outside content\n"
TEXTS = [
    "plain\n",
    "no trailing newline",
    "two\nlines\n",
    "carriage\r\nreturns\rkept as written\n",
    "unicode: café ☃ \U0001f600\n",
    "",
]


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks are not available on this platform")


def _hardlink(link: Path, target: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hard links are not available on this platform")


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    """(memory folder, a file outside it)."""
    folder = tmp_path / "store"
    folder.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text(OUTSIDE, encoding="utf-8")
    return folder, outside


@pytest.fixture
def pre_check_sees_no_link(monkeypatch) -> None:
    """Stands in for a link swapped in after the path was checked."""
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(os.path, "islink", lambda path: False)


@pytest.fixture(params=["with O_NOFOLLOW", "without O_NOFOLLOW"])
def each_platform_path(request, monkeypatch) -> None:
    """Run once as built and once as on a platform with no O_NOFOLLOW, where
    only the comparison of the opened file with the path can refuse."""
    if request.param == "without O_NOFOLLOW":
        monkeypatch.setattr(discovery, "_O_NOFOLLOW", 0, raising=False)


@pytest.mark.parametrize("text", TEXTS)
def test_new_file_matches_path_write_text(tmp_path: Path, text: str):
    expected = tmp_path / "expected.md"
    expected.write_text(text, encoding="utf-8")

    write_text_inside(tmp_path, tmp_path / "new.md", text)

    assert (tmp_path / "new.md").read_bytes() == expected.read_bytes()


@pytest.mark.parametrize("text", TEXTS)
def test_existing_file_is_replaced_like_path_write_text(tmp_path: Path, text: str):
    target = tmp_path / "existing.md"
    target.write_text("a much longer previous content that must not survive\n" * 3,
                      encoding="utf-8")
    expected = tmp_path / "expected.md"
    expected.write_text(text, encoding="utf-8")

    write_text_inside(tmp_path, target, text)

    assert target.read_bytes() == expected.read_bytes()


def test_symlink_swapped_in_after_the_check_is_not_written_through(
    store, pre_check_sees_no_link, each_platform_path
):
    folder, outside = store
    _symlink(folder / "note.md", outside)

    with pytest.raises(SymlinkWriteRefused, match="symlink"):
        write_text_inside(folder, folder / "note.md", "new content\n")

    assert outside.read_text(encoding="utf-8") == OUTSIDE


def test_dangling_symlink_swapped_in_does_not_create_its_target(
    store, pre_check_sees_no_link, each_platform_path
):
    folder, outside = store
    target = outside.with_name("never-created.txt")
    _symlink(folder / "note.md", target)

    with pytest.raises(SymlinkWriteRefused, match="symlink"):
        write_text_inside(folder, folder / "note.md", "new content\n")

    # Windows opens a link by following it, so a link swapped in after the
    # check has its missing target created, empty, before the refusal.
    if os.name != "nt":
        assert not target.exists()


def test_hard_link_to_an_outside_file_is_refused_untruncated(store):
    folder, outside = store
    _hardlink(folder / "note.md", outside)

    with pytest.raises(SymlinkWriteRefused, match="hard link"):
        write_text_inside(folder, folder / "note.md", "new content\n")

    assert outside.read_text(encoding="utf-8") == OUTSIDE


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_named_pipe_at_the_path_is_refused_without_waiting_for_a_reader(store):
    # A plain write-only open of a pipe with no reader never returns.
    folder, _outside = store
    os.mkfifo(folder / "note.md")

    with pytest.raises(SymlinkWriteRefused, match="not a regular file"):
        write_text_inside(folder, folder / "note.md", "new content\n")


def test_large_write_to_an_existing_file_is_complete(tmp_path: Path):
    # The open is non-blocking so a pipe cannot hang it; a regular file must
    # still get every byte.
    target = tmp_path / "existing.md"
    target.write_text("previous\n", encoding="utf-8")
    text = "0123456789abcdef\n" * 200_000

    write_text_inside(tmp_path, target, text)

    assert target.read_text(encoding="utf-8") == text


def test_directory_at_the_path_is_refused(store):
    folder, _outside = store
    (folder / "note.md").mkdir()

    with pytest.raises(OSError):
        write_text_inside(folder, folder / "note.md", "new content\n")


def test_recall_index_is_still_written_and_replaced_atomically(store):
    folder, _outside = store
    (folder / "one.md").write_text(
        "---\nname: one\ndescription: first memory\ntype: reference\n---\n\nBody.\n",
        encoding="utf-8",
    )

    first = recall.write_index(folder, recall.build_index(folder))
    second = recall.write_index(folder, recall.build_index(folder))

    assert first == second and recall.load_index(folder) is not None
    assert not first.with_suffix(".json.tmp").exists()
