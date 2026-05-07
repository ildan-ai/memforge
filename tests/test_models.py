"""Tests for memforge.models dataclass defaults."""

from __future__ import annotations

from pathlib import Path

from memforge.models import FolderIndex, Link, Memory


def test_memory_defaults_optional_fields():
    m = Memory(
        path=Path("/tmp/a.md"),
        relpath=Path("a.md"),
        root=Path("/tmp"),
    )
    assert m.uid is None
    assert m.name is None
    assert m.tier is None
    assert m.has_frontmatter is False
    assert m.frontmatter == {}


def test_memory_does_not_share_frontmatter_dict_between_instances():
    """default_factory(dict) gives each Memory its own dict; bug magnet
    if regressed to a class-level mutable default."""
    a = Memory(path=Path("a"), relpath=Path("a"), root=Path("."))
    b = Memory(path=Path("b"), relpath=Path("b"), root=Path("."))
    a.frontmatter["touched"] = True
    assert "touched" not in b.frontmatter


def test_folder_index_defaults_are_empty_collections():
    fi = FolderIndex(root=Path("/tmp"))
    assert fi.memories == []
    assert fi.by_uid == {}
    assert fi.by_relpath == {}
    assert fi.duplicate_uids == []


def test_folder_index_collections_are_per_instance():
    a = FolderIndex(root=Path("/a"))
    b = FolderIndex(root=Path("/b"))
    a.memories.append(Memory(path=Path("x"), relpath=Path("x"), root=Path("/a")))
    a.by_uid["u1"] = a.memories[0]
    a.duplicate_uids.append(("u1", []))
    assert b.memories == []
    assert b.by_uid == {}
    assert b.duplicate_uids == []


def test_link_dataclass_required_fields():
    link = Link(
        text="see X",
        target="mem:abc123",
        is_mem_uri=True,
        uid="abc123",
        span=(10, 25),
    )
    assert link.text == "see X"
    assert link.target == "mem:abc123"
    assert link.is_mem_uri is True
    assert link.uid == "abc123"
    assert link.span == (10, 25)


def test_link_supports_path_target_with_no_uid():
    link = Link(
        text="legacy",
        target="../other.md",
        is_mem_uri=False,
        uid=None,
        span=(0, 5),
    )
    assert link.is_mem_uri is False
    assert link.uid is None
