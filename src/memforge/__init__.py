"""MemForge: portable, agent-neutral persistent memory.

Public API surface for tooling. Each tool under tools/ is a thin CLI wrapper
around these modules. Keep imports cheap; tools are invoked frequently.
"""

from memforge.frontmatter import parse, has_frontmatter, render
from memforge.paths import default_memory_paths, ARCHIVE_DIRNAME
from memforge.discovery import walk_memory_files, is_memory_file
from memforge.models import Memory, FolderIndex, Link

# Single-source the version from installed package metadata so __version__,
# the PyPI artifact, and `pip show` can never drift. The literal fallback is
# only used when running from an uninstalled source tree (e.g. PYTHONPATH=src
# in CI) and is kept in lockstep with pyproject at release time.
try:  # pragma: no cover - trivial metadata lookup
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("ildan-memforge")
    except PackageNotFoundError:
        __version__ = "0.9.0"
except ImportError:  # pragma: no cover - importlib.metadata always present on 3.10+
    __version__ = "0.9.0"

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
