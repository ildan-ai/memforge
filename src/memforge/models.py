"""Domain models for memory files and link traversal.

Promoted from memory-link-rewriter to a shared module per code-review-panel
recommendation CC-008/CC-R001 + extensibility-reviewer:1. Tools that
previously passed bare Path objects should adopt these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Memory:
    """A single memory file located on disk.

    `path` is the absolute resolved path. `relpath` is relative to `root`.
    Frontmatter scalar fields are surfaced as named attributes; the full
    frontmatter dict is in `frontmatter`.
    """
    path: Path
    relpath: Path
    root: Path
    uid: str | None = None
    name: str | None = None
    tier: str | None = None
    has_frontmatter: bool = False
    frontmatter: dict = field(default_factory=dict)


@dataclass
class FolderIndex:
    """Indexed view of a memory folder.

    `memories` preserves walk order. `by_uid` and `by_relpath` enable O(1)
    lookup. `duplicate_uids` collects UID collisions for audit reporting.
    """
    root: Path
    memories: list[Memory] = field(default_factory=list)
    by_uid: dict[str, Memory] = field(default_factory=dict)
    by_relpath: dict[str, Memory] = field(default_factory=dict)
    duplicate_uids: list[tuple[str, list[Memory]]] = field(default_factory=list)


@dataclass
class Link:
    """A markdown link extracted from a memory body.

    `is_mem_uri` True when the target matches `mem:<uid>` (the canonical
    cross-folder link form). `span` is the (start, end) offsets in the
    source text for surgical rewriting.
    """
    text: str
    target: str
    is_mem_uri: bool
    uid: str | None
    span: tuple[int, int]
