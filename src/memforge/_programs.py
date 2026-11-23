"""Find the programs memforge starts without trusting the folder it works on.

The tools run from inside memory stores, where anyone who can commit can place
an executable named `git` or `detect-secrets`. Each program is looked up here
and started by the absolute path this returns, never by a bare name.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


class ProgramInFolder(Exception):
    """A program resolves inside a folder the tool works on."""

    def __init__(self, name: str, path: str, folder: Path) -> None:
        super().__init__(f"{name} resolves to {path}, inside {folder}")
        self.name = name
        self.path = path
        self.folder = folder


def find_program(name: str) -> str | None:
    """The absolute path of `name` from the absolute PATH entries only.

    A `.`, empty or relative PATH entry, and the current folder that Windows
    searches first, would each find a program committed to the store the tool
    runs in; a name with a folder part makes `shutil.which` search that folder
    alone.
    """
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if os.path.isabs(entry):
            found = shutil.which(os.path.join(entry, name))
            if found is not None:
                return os.path.abspath(found)
    return None


def repository_top(folder: Path) -> Path | None:
    """The nearest folder at or above the real path of `folder` that holds a
    `.git` entry, else None.

    A filesystem walk, not a git subprocess: the git that would answer is the
    program being checked.
    """
    real = Path(os.path.realpath(folder))
    for candidate in (real, *real.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def _is_inside(parents: "list[Path] | tuple[Path, ...]", folder: Path) -> bool:
    """True when `folder` is one of `parents`, compared by identity."""
    try:
        target = os.stat(folder)
    except OSError:
        return False
    for parent in parents:
        try:
            if os.path.samestat(os.stat(parent), target):
                return True
        except OSError:
            continue
    return False


def refuse_inside(name: str, path: str, *folders: Path) -> None:
    """Raise ProgramInFolder when the real path of `path` lies inside one of
    `folders`, or inside the repository that holds one: anyone who can commit
    there could have replaced it, and git acts on that whole repository.

    The repository check passes over a program in the scripts folder of the
    Python environment memforge runs from (a project's own virtual
    environment, inside the project repository): whoever can write there can
    already change memforge's own code, so refusing it adds no trust. A
    program inside one of `folders` themselves is refused wherever it lies.

    Folders are compared by identity, not by spelling, so a case variant or a
    linked path to the same folder is caught too.
    """
    parents = Path(os.path.realpath(path)).parents
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    own_environment = _is_inside(parents, scripts)
    for folder in folders:
        if _is_inside(parents, folder):
            raise ProgramInFolder(name, path, folder)
        top = repository_top(folder)
        if top is not None and not own_environment and _is_inside(parents, top):
            raise ProgramInFolder(name, path, top)


# The variables `git rev-parse --local-env-vars` lists: each one points git at
# a repository, index, object store or config other than the one in `-C`. A
# tool started from inside a git hook inherits several of them, and git would
# then read or write that other repository while the tool reports on this one.
GIT_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


def git_env() -> dict[str, str]:
    """The caller's environment without the repository-local git variables,
    for a git command that must act on the repository it names with `-C`."""
    return {k: v for k, v in os.environ.items() if k not in GIT_LOCAL_ENV_VARS}


def find_program_outside(name: str, *folders: Path) -> str | None:
    """`find_program`, refused when the program lies inside one of `folders`."""
    path = find_program(name)
    if path is not None:
        refuse_inside(name, path, *folders)
    return path
