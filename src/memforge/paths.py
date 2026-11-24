"""Default memory-folder path resolution.

Single source of truth for where MemForge looks for memory folders. All CLIs
MUST call default_memory_paths() rather than re-deriving the layout, so the
package stays IDE-neutral and OS-neutral in one place.

Resolution order:
  1. MEMFORGE_MEMORY_PATH env var (os.pathsep-separated roots): explicit,
     portable, IDE/OS-neutral. Highest priority.
  2. Back-compat grandfather: the Claude Code adapter's layout
     (~/.claude/projects/<project folder>/memory + ~/.claude/global-memory),
     used ONLY when it already exists on disk, so existing installs keep working
     with zero config. <project folder> is the git root of the working folder
     (the nearest folder up the tree holding a .git directory, or the .git file
     a linked worktree has), else the working folder itself, named the way
     Claude Code names it (see claude_code_project_dirname). Claude Code keys
     auto-memory by the git root; whether it maps a linked worktree to the
     main checkout instead is unconfirmed, and MEMFORGE_MEMORY_PATH covers
     that case. This is the one adapter layout the core grandfathers.
  3. IDE-neutral default under the memforge home (~/.memforge/{memory,
     global-memory}).
"""

from __future__ import annotations

import os
from pathlib import Path

ARCHIVE_DIRNAME = "archive"
MEMORY_PATH_ENV = "MEMFORGE_MEMORY_PATH"


# Claude Code cuts a longer folder name and adds a hash so long paths stay distinct.
_CLAUDE_CODE_DIRNAME_MAX = 200
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def claude_code_project_dirname(project: str) -> str:
    """Return the name Claude Code gives a project's folder under ~/.claude/projects/.

    Every UTF-16 code unit that is not an ASCII letter or digit becomes "-". A
    name over 200 characters is cut to 200 and suffixed with "-" and the base-36
    absolute value of the 32-bit string hash (h = 31*h + unit) of the project path.
    """
    # surrogatepass: a POSIX name that is not valid UTF-8 arrives with
    # surrogate escapes, one code unit per such byte, which becomes "-".
    raw = project.encode("utf-16-le", "surrogatepass")
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    name = "".join(chr(u) if u < 128 and chr(u).isalnum() else "-" for u in units)
    if len(name) <= _CLAUDE_CODE_DIRNAME_MAX:
        return name
    return f"{name[:_CLAUDE_CODE_DIRNAME_MAX]}-{_base36(abs(_string_hash(units)))}"


def _string_hash(units: list[int]) -> int:
    h = 0
    for u in units:
        h = (h * 31 + u) & 0xFFFFFFFF
    return h - (1 << 32) if h >= 1 << 31 else h


def _base36(n: int) -> str:
    digits = ""
    while True:
        n, r = divmod(n, 36)
        digits = _BASE36[r] + digits
        if n == 0:
            return digits


def _project_folder(cwd: Path) -> Path:
    """Return the nearest folder at or above cwd that holds a .git entry, else cwd.

    A filesystem walk, not a git subprocess, because every CLI start resolves it.
    """
    for folder in (cwd, *cwd.parents):
        if os.path.exists(folder / ".git"):
            return folder
    return cwd


def default_memory_paths(start: Path | None = None) -> list[Path]:
    """Return the default memory folders for this user, per-cwd before global.

    The per-cwd folder belongs to `start` (an absolute folder) when given, else to
    the working folder, so a tool run for another repo reads that repo's store.

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
    try:
        project = claude_code_project_dirname(str(_project_folder(start or Path.cwd())))
        cc_percwd: Path | None = home / ".claude" / "projects" / project / "memory"
    except OSError:
        # The working folder was removed under us; there is no project to name.
        cc_percwd = None
    # Imported here: discovery imports this module.
    from memforge.discovery import access_error

    def present(p: Path) -> bool:
        # A folder that cannot be looked at is there: taking it for absent
        # would switch the tools to another store without a word.
        return os.path.exists(p) or access_error(p) is not None

    cc_global = home / ".claude" / "global-memory"
    if present(cc_global) or (cc_percwd is not None and present(cc_percwd)):
        out: list[Path] = []
        if cc_percwd is not None:
            out.append(cc_percwd)
        out.append(cc_global)
        return out

    mf = home / ".memforge"
    return [mf / "memory", mf / "global-memory"]
