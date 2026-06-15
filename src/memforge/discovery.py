"""Memory-file discovery: walk a memory folder skipping archive/ + MEMORY.md.

Single replacement for the 5+ ad-hoc discovery loops scattered across tools
(see code-craftsman:CC-006).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from memforge.paths import ARCHIVE_DIRNAME


def is_memory_file(path: Path, root: Optional[Path] = None) -> bool:
    """True iff path is a candidate memory file.

    Memory files are .md files that are NOT MEMORY.md (the index) and are
    not under an archive/ subtree of the memory root.

    The archive check is evaluated relative to ``root`` when supplied, so it
    matches the pruning done by :func:`walk_memory_files` (which prunes
    archive/ only below the root). Without ``root``, the path is treated as
    already-relative-to-root and any ``archive`` component rejects it.

    Passing ``root`` is REQUIRED for consistency when the memory root itself
    lives under a directory named ``archive`` (for example a partner storing
    memory under ``data-archive/memory/``). In that case the absolute-path
    check would reject every memory file while ``walk_memory_files`` yields
    them, causing silent data loss for a consumer that filters
    ``walk_memory_files`` output through ``is_memory_file`` (closes
    discovery-01).
    """
    if not path.name.endswith(".md"):
        return False
    if path.name == "MEMORY.md":
        return False
    if root is not None:
        try:
            rel_parts = path.resolve().relative_to(Path(root).resolve()).parts
        except ValueError:
            # path is not under root; fall back to the whole-path check.
            rel_parts = path.parts
        if ARCHIVE_DIRNAME in rel_parts:
            return False
        return True
    if ARCHIVE_DIRNAME in path.parts:
        return False
    return True


def walk_memory_files(root: Path) -> Iterable[Path]:
    """Yield all memory files under root.

    Prunes archive/ subtrees during the walk for efficiency. Skips MEMORY.md
    indexes (those are emitted by memory-index-gen, not authored).
    """
    root = Path(root)
    if not root.is_dir():
        return

    for dirpath, dirnames, filenames in os.walk(root):
        if ARCHIVE_DIRNAME in dirnames:
            dirnames.remove(ARCHIVE_DIRNAME)

        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            if fn == "MEMORY.md":
                continue
            yield Path(dirpath) / fn
