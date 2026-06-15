"""Default memory-folder path resolution.

Single source of truth for where MemForge looks for memory folders. All CLIs
MUST call default_memory_paths() rather than re-deriving the layout, so the
package stays IDE-neutral and OS-neutral in one place.

Resolution order:
  1. MEMFORGE_MEMORY_PATH env var (os.pathsep-separated roots): explicit,
     portable, IDE/OS-neutral. Highest priority.
  2. Back-compat grandfather: the Claude Code adapter's historical layout
     (~/.claude/projects/<user>-claude-projects/memory + ~/.claude/global-memory),
     used ONLY when it already exists on disk, so existing installs keep working
     with zero config. This is the one adapter layout the core grandfathers.
  3. IDE-neutral default under the memforge home (~/.memforge/{memory,
     global-memory}): same root as ~/.memforge/operator-identity.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

ARCHIVE_DIRNAME = "archive"
MEMORY_PATH_ENV = "MEMFORGE_MEMORY_PATH"


def _current_user() -> str:
    # OS-neutral: USER on POSIX, USERNAME on Windows.
    return os.environ.get("USER") or os.environ.get("USERNAME") or ""


def default_memory_paths() -> list[Path]:
    """Return the default memory folders for this user, per-cwd before global.

    Tools that scan "all memory folders" should iterate in this order. See the
    module docstring for the resolution order (env override, then a grandfathered
    Claude Code layout if present, then the IDE-neutral ~/.memforge default).
    """
    env = os.environ.get(MEMORY_PATH_ENV)
    if env:
        roots = [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
        if roots:
            return roots

    home = Path.home()
    user = _current_user()
    cc_percwd = (
        home / ".claude" / "projects" / f"{user}-claude-projects" / "memory"
        if user
        else None
    )
    cc_global = home / ".claude" / "global-memory"
    if cc_global.exists() or (cc_percwd is not None and cc_percwd.exists()):
        out: list[Path] = []
        if cc_percwd is not None:
            out.append(cc_percwd)
        out.append(cc_global)
        return out

    mf = home / ".memforge"
    return [mf / "memory", mf / "global-memory"]
