"""Memory-file discovery: walk a memory folder skipping archive/ + MEMORY.md.

Single replacement for the 5+ ad-hoc discovery loops scattered across tools
(see code-craftsman:CC-006).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from memforge.paths import ARCHIVE_DIRNAME


def is_memory_file(path: Path) -> bool:
    """True iff path is a candidate memory file.

    Memory files are .md files that are NOT MEMORY.md (the index) and are
    not under archive/ subtrees.
    """
    if not path.name.endswith(".md"):
        return False
    if path.name == "MEMORY.md":
        return False
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
