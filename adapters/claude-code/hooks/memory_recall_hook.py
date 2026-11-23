#!/usr/bin/env python3
# MemForge recall hook for Claude Code (UserPromptSubmit).
#
# Injects the descriptions of memories whose triggers match the user's prompt so
# the agent recalls the right memory at the right moment, instead of bulk-loading
# the whole MEMORY.md index every session.
#
# It shells out (via subprocess, NOT a shell) to the INSTALLED `memory-recall`
# reader (pip install ildan-memforge), which reads a precompiled recall index.
# Keep the index fresh with `memory-index-gen --with-recall-index` (or the
# auto-commit hook's `memory-recall --rebuild` refresh).
#
# Register in ~/.claude/settings.json under "UserPromptSubmit", with the
# absolute path of the Python interpreter of the environment memforge is
# installed in (a bare `python3` is looked up by the shell that runs the hook):
#   {
#     "hooks": [
#       { "type": "command",
#         "command": "/ABSOLUTE/PATH/TO/ENV/bin/python /ABSOLUTE/PATH/adapters/claude-code/hooks/memory_recall_hook.py",
#         "timeout": 10 }
#     ]
#   }
#
# Reads the UserPromptSubmit hook JSON payload on stdin; writes matched-memory
# context to stdout (which Claude Code injects as context for the turn).
#
# Fail-open-empty: any failure writes nothing to stdout and exits 0; when no
# `memory-recall` may be started, one stderr line says recall is off and why. Recall never blocks a
# prompt (spec v0.6.0 §"Recall operation", post-condition 7). A subprocess
# timeout bounds latency. This is a Python hook (not bash) so it is portable to
# Windows and avoids shell-parsing fragility.

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MAX_PROMPT_BYTES = 65536  # cap the prompt handed to the reader (DoS guard)
RECALL_TIMEOUT_SECONDS = 3


NOT_FOUND = "no memory-recall on an absolute PATH entry"


class RecallOff(Exception):
    """No `memory-recall` the hook may start; the message says why."""


def _recall_program() -> str:
    """The absolute path of the `memory-recall` to start; RecallOff if none.

    The hook runs in the folder Claude Code was opened in, where anyone who can
    commit can place a program of that name, and a bare name is looked up
    there: through a `.` or relative PATH entry, and on Windows with no PATH
    entry at all. The `memory-recall` in the scripts folder of the Python
    environment running this hook comes first, then PATH searched the way
    every memforge tool searches it. Either is refused inside a memory store;
    one from PATH also inside the working folder, the other memory folders of
    `_also_refused`, and the repository that holds any of them.
    """
    try:
        from memforge._programs import ProgramInFolder, find_program_outside, refuse_inside
        from memforge.paths import default_memory_paths
    except ImportError:
        return _recall_program_without_memforge()
    stores = [path for path in default_memory_paths() if os.path.isdir(path)]
    scripts = os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")
    try:
        own = shutil.which(os.path.join(scripts, "memory-recall"))
        if own is not None:
            own = os.path.abspath(own)
            refuse_inside("memory-recall", own, *stores)
            return own
        # The working folder is refused too; each folder with the repository that holds it.
        found = find_program_outside(
            "memory-recall", *stores, *map(Path, _also_refused()), Path(os.getcwd()))
    except ProgramInFolder as exc:
        raise RecallOff(str(exc)) from None
    if found is None:
        raise RecallOff(NOT_FOUND)
    return found


def _also_refused() -> list[str]:
    """The memory folders a program found on PATH may not lie in besides the
    stores this run would use: the `MEMFORGE_MEMORY_PATH` roots as written, the
    Claude Code project and global folders and the memforge home. A store
    layout the user is not on today is still where another tool or a later
    run writes memory, so a program planted there is refused all the same.
    """
    home = os.path.expanduser("~")
    roots = [p for p in os.environ.get("MEMFORGE_MEMORY_PATH", "").split(os.pathsep) if p.strip()]
    return roots + [os.path.join(home, ".claude", "projects"), os.path.join(home, ".claude", "global-memory"),
                    os.path.join(home, ".memforge")]


def _inside(path: str, folder: str) -> bool:
    """True when the real path of `path` lies in `folder`, compared by identity."""
    try:
        target = os.stat(folder)
    except OSError:
        return False
    parent = os.path.dirname(os.path.realpath(path))
    while True:
        try:
            if os.path.samestat(os.stat(parent), target):
                return True
        except OSError:
            pass
        up = os.path.dirname(parent)
        if up == parent:
            return False
        parent = up


# The three functions below repeat, in standard-library code, how memforge names
# the memory stores (memforge.paths.default_memory_paths) and their
# repositories (memforge._programs.repository_top), so that without memforge
# the hook refuses exactly the folders the memforge lookup refuses.

_CLAUDE_CODE_DIRNAME_MAX = 200
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _claude_code_project_dirname(project: str) -> str:
    """memforge.paths.claude_code_project_dirname: the name Claude Code gives a
    project's folder under ~/.claude/projects/."""
    raw = project.encode("utf-16-le", "surrogatepass")
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    name = "".join(chr(u) if u < 128 and chr(u).isalnum() else "-" for u in units)
    if len(name) <= _CLAUDE_CODE_DIRNAME_MAX:
        return name
    h = 0
    for u in units:
        h = (h * 31 + u) & 0xFFFFFFFF
    n = abs(h - (1 << 32) if h >= 1 << 31 else h)
    digits = ""
    while True:
        n, r = divmod(n, 36)
        digits = _BASE36[r] + digits
        if n == 0:
            break
    return f"{name[:_CLAUDE_CODE_DIRNAME_MAX]}-{digits}"


def _memory_stores() -> list[str]:
    """memforge.paths.default_memory_paths() for the working folder."""
    env = os.environ.get("MEMFORGE_MEMORY_PATH")
    if env:
        roots = [os.path.expanduser(p) for p in env.split(os.pathsep) if p.strip()]
        if roots:
            return roots
    home = os.path.expanduser("~")
    try:
        project = cwd = os.getcwd()
    except OSError:
        percwd = None
    else:
        folder = cwd
        while True:
            if os.path.exists(os.path.join(folder, ".git")):
                project = folder
                break
            up = os.path.dirname(folder)
            if up == folder:
                break
            folder = up
        percwd = os.path.join(home, ".claude", "projects", _claude_code_project_dirname(project), "memory")
    cc_global = os.path.join(home, ".claude", "global-memory")
    if os.path.exists(cc_global) or (percwd is not None and os.path.exists(percwd)):
        return ([percwd] if percwd is not None else []) + [cc_global]
    return [os.path.join(home, ".memforge", "memory"), os.path.join(home, ".memforge", "global-memory")]


def _repository_top(folder: str) -> str | None:
    """memforge._programs.repository_top: the nearest folder at or above the
    real path of `folder` that holds a `.git` entry, found without git."""
    real = os.path.realpath(folder)
    while True:
        if os.path.lexists(os.path.join(real, ".git")):
            return real
        up = os.path.dirname(real)
        if up == real:
            return None
        real = up


def _recall_program_without_memforge() -> str:
    """The PATH lookup, with the same folder checks, when this interpreter
    cannot import memforge, as when memforge is installed with pipx and the
    hook runs under another Python.

    Absolute PATH entries only. Refused inside each memory store memforge
    would use, the other memory folders of `_also_refused`, the working
    folder, and the git repository that holds any of them, in the order the
    memforge lookup checks them.
    """
    folders = []
    stores = [p for p in _memory_stores() if os.path.isdir(p)]
    for folder in stores + _also_refused() + [os.getcwd()]:
        folders.append(folder)
        top = _repository_top(folder)
        if top is not None:
            folders.append(top)
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not os.path.isabs(entry):
            continue
        found = shutil.which(os.path.join(entry, "memory-recall"))
        if found is None:
            continue
        found = os.path.abspath(found)
        for folder in folders:
            if _inside(found, folder):
                raise RecallOff(f"memory-recall resolves to {found}, inside {folder}")
        return found
    raise RecallOff(NOT_FOUND)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return 0
        prompt = prompt[:MAX_PROMPT_BYTES]
        try:
            program = _recall_program()
        except RecallOff as exc:
            # One line, so an operator sees recall is off instead of assuming
            # nothing matched; the prompt still goes through.
            sys.stderr.write(f"memory_recall_hook: recall is off: {exc}\n")
            return 0
        result = subprocess.run(
            [program, "--stdin"],
            input=prompt,
            encoding="utf-8",
            capture_output=True,
            timeout=RECALL_TIMEOUT_SECONDS,
            check=False,
        )
        if result.stdout:
            sys.stdout.write(result.stdout)
    except FileNotFoundError:
        pass  # memforge / memory-recall not installed -> nothing to do
    except Exception:
        pass  # fail-open-empty: never block or fail the prompt
    return 0


if __name__ == "__main__":
    sys.exit(main())
