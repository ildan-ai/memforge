"""MemForge: portable, agent-neutral persistent memory.

Public API surface for tooling. Each tool under tools/ is a thin CLI wrapper
around these modules. Keep imports cheap; tools are invoked frequently.
"""

from memforge.frontmatter import parse, has_frontmatter, render
from memforge.paths import default_memory_paths, ARCHIVE_DIRNAME
from memforge.discovery import walk_memory_files, is_memory_file
from memforge.models import Memory, FolderIndex, Link

__version__ = "0.3.1"

__all__ = [
    "parse",
    "has_frontmatter",
    "render",
    "default_memory_paths",
    "ARCHIVE_DIRNAME",
    "walk_memory_files",
    "is_memory_file",
    "Memory",
    "FolderIndex",
    "Link",
    "__version__",
]
