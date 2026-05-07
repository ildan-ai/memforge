"""Default memory-folder path resolution.

Single source of truth for where MemForge looks for memory folders.
Replaces the 12 hand-rolled copies across tools (see code-craftsman:CC-002).
"""

from __future__ import annotations

import os
from pathlib import Path

ARCHIVE_DIRNAME = "archive"


def default_memory_paths() -> list[Path]:
    """Return the default memory folders for this user.

    Order matters: per-cwd folder is preferred over global. Tools that scan
    "all memory folders" should iterate in this order.
    """
    user = os.environ.get("USER", "")
    return [
        Path.home() / ".claude" / "projects" / f"{user}-claude-projects" / "memory",
        Path.home() / ".claude" / "global-memory",
    ]
