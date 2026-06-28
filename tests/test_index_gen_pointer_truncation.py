"""Tests for index-gen pointer-hook truncation to the byte cap (spec v0.6.3+).

The pointer hook (description excerpt) in a MEMORY.md bullet is truncated so the
whole line stays within POINTER_LINE_BYTE_CAP. The full description remains
authoritative in frontmatter + the recall index, so truncation is lossless for
recall and only shortens the human-browsable hook.
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.index_gen import (
    POINTER_LINE_BYTE_CAP,
    _bullet,
    _truncate_hook,
    MemoryFile,
)


def _mf(name: str, rel_path: str, description: str) -> MemoryFile:
    return MemoryFile(path=Path(rel_path), rel_path=rel_path, name=name, description=description)


# ---------- _truncate_hook ----------


def test_truncate_hook_under_budget_unchanged():
    assert _truncate_hook("short desc", 180) == "short desc"


def test_truncate_hook_over_budget_appends_ellipsis():
    out = _truncate_hook("x" * 300, 50)
    assert out.endswith("...")
    assert len(out.encode("utf-8")) <= 50


def test_truncate_hook_splits_on_utf8_boundary():
    # 4-byte emoji; truncating mid-codepoint must not raise and must stay valid utf-8.
    out = _truncate_hook("a" * 40 + "\U0001f600" * 10, 45)
    out.encode("utf-8").decode("utf-8")  # must not raise
    assert out.endswith("...")
    assert len(out.encode("utf-8")) <= 45


# ---------- _bullet ----------


def test_bullet_no_description():
    b = _bullet(_mf("Name", "name.md", ""))
    assert b == "- [Name](name.md)"


def test_bullet_short_description_not_truncated():
    desc = "a concise hook"
    b = _bullet(_mf("Name", "name.md", desc))
    assert b == f"- [Name](name.md): {desc}"
    assert "..." not in b


def test_bullet_long_description_fits_cap_and_marks_truncation():
    desc = "This is a deliberately long description. " * 8  # ~328 bytes
    b = _bullet(_mf("A Memory", "sub/a-memory.md", desc))
    assert len(b.encode("utf-8")) <= POINTER_LINE_BYTE_CAP
    assert b.endswith("...")
    assert b.startswith("- [A Memory](sub/a-memory.md): ")


def test_bullet_full_description_preserved_on_object():
    # Truncation must not mutate the MemoryFile's description (frontmatter stays whole).
    desc = "z" * 400
    mf = _mf("N", "n.md", desc)
    _bullet(mf)
    assert mf.description == desc


def test_bullet_prefix_leaves_no_room_omits_hook():
    # Pathologically long name/path: no room for a hook, so the hook is omitted
    # entirely (no over-cap description appended). The bare title/path line may
    # still exceed the cap, but that overrun is attributable to the title/path.
    long_name = "N" * 200
    b = _bullet(_mf(long_name, "n.md", "some description text"))
    assert b == f"- [{long_name}](n.md)"
    assert "some description text" not in b


def test_bullet_prefix_near_cap_omits_hook_within_reason():
    # A title/path that lands a few bytes under the cap leaves <=3 bytes for a hook:
    # the hook is dropped rather than producing a 1-2 char "...".
    name = "N" * 165  # prefix "- [N*165](n.md): " is ~185 bytes > cap
    b = _bullet(_mf(name, "n.md", "desc"))
    assert b == f"- [{name}](n.md)"
