"""The read helpers refuse a symlink on the opened file itself, so a link
swapped in after an earlier check is still not read through."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from memforge import discovery
from memforge.discovery import LinkReadRefused, read_bytes_no_follow, read_text_no_follow

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


@pytest.fixture(params=["with O_NOFOLLOW", "without O_NOFOLLOW"])
def each_platform_path(request, monkeypatch) -> None:
    """Run once as built and once as on a platform with no O_NOFOLLOW, where
    only the comparison of the opened file with the path can refuse."""
    if request.param == "without O_NOFOLLOW":
        monkeypatch.setattr(discovery, "_O_NOFOLLOW", 0)


@pytest.mark.parametrize("text", TEXTS)
def test_regular_file_reads_like_path_read(tmp_path: Path, text: str):
    target = tmp_path / "note.md"
    target.write_bytes(text.encode("utf-8"))

    assert read_bytes_no_follow(target) == target.read_bytes()
    assert read_text_no_follow(target) == target.read_text(encoding="utf-8")


def test_read_limit_returns_only_the_head(tmp_path: Path):
    target = tmp_path / "note.md"
    target.write_bytes(b"0123456789")

    assert read_bytes_no_follow(target, limit=4) == b"0123"


def test_symlink_is_not_read_through(store, each_platform_path):
    folder, outside = store
    _symlink(folder / "note.md", outside)

    with pytest.raises(LinkReadRefused, match="symlink"):
        read_bytes_no_follow(folder / "note.md")
    with pytest.raises(LinkReadRefused, match="symlink"):
        read_text_no_follow(folder / "note.md")


def test_hard_link_is_readable(store):
    """A hard link is the file itself; only a write through one is refused."""
    folder, outside = store
    _hardlink(folder / "note.md", outside)

    assert read_text_no_follow(folder / "note.md") == OUTSIDE


def test_directory_is_refused_as_not_a_regular_file(store):
    folder, _outside = store
    (folder / "note.md").mkdir()

    with pytest.raises(OSError):
        read_bytes_no_follow(folder / "note.md")


def test_missing_file_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_bytes_no_follow(tmp_path / "absent.md")


def test_invalid_utf8_raises_unicode_error_like_read_text(tmp_path: Path):
    target = tmp_path / "note.md"
    target.write_bytes(b"\xff\xfe broken")

    with pytest.raises(UnicodeDecodeError):
        read_text_no_follow(target)
