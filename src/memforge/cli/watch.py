# memory-watch: cross-platform filesystem-watcher auto-commit for memory
# folders.
#
# Portable equivalent of the Claude Code PostToolUse auto-commit hook,
# for agents that don't have an event-hook surface (Cursor, Aider, Codex
# CLI, Cline, Continue, Windsurf, generic editor workflows).
#
# Cross-platform via the `watchdog` library: macOS (FSEvents), Linux
# (inotify), Windows (ReadDirectoryChangesW). It is a dependency of
# ildan-memforge, so installing the package installs it.
#
# Usage:
#   memory-watch                                # watches default folders
#   memory-watch --path /path/to/memory         # specific folder (repeatable)
#   memory-watch --quiet                        # suppress per-commit log
#   memory-watch --debounce-ms 2000             # coalesce within N ms
#
# Defaults: per-cwd memory + ~/.claude/global-memory/.
#
# Multiple events within --debounce-ms coalesce into one commit so a
# multi-file write doesn't produce a commit per file.
#
# Commit scope: memory files (`*.md` outside `.memforge/`, MEMORY.md
# included), not `git add -A`, so editor swap files and OS metadata are not
# committed. Keep a committed `.gitignore` in the memory folder for anything
# else you want excluded; the watcher will not stage it regardless.
#
# Nothing under `.memforge/` is committed here: the config, snoozes, audit
# waivers, recall synonyms and signer lists each change in a deliberate commit
# with its own `memforge:` prefix, not in an automatic one made because some
# process wrote a file. The recall index `.memforge/recall-index.json` is a
# local cache that memory-recall rebuilds from the files, so it is never
# committed (add it to the folder's `.gitignore`). A file outside that scope
# that you staged yourself stays staged and is not swept into the automatic
# commit.

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from memforge._programs import ProgramInFolder, find_program_outside, git_env
from memforge.discovery import access_error
from memforge._terminal import terminal_safe, visible

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    sys.stderr.write(
        "error: watchdog is not installed. It comes with ildan-memforge; "
        "reinstall the package in this Python environment.\n"
    )
    sys.exit(2)


# Listing pathspec. Names are then filtered by _in_scope and passed to git
# literally, so no exclude pattern has to reach into .memforge/ (git refuses
# a pathspec beyond a symbolic link, which would drop the whole commit).
CANDIDATES = ("*.md",)


def _literal(name: str) -> str:
    # A name read as plain text, never as a pattern. The `:(literal)` magic does
    # this per pathspec; GIT_LITERAL_PATHSPECS would do it too, but git hands
    # that variable to the user's hooks, where a pre-commit hook selecting
    # files with '*.md' then matches nothing and lets a broken memory in.
    return f":(literal){name}"


def _in_scope(name: str) -> bool:
    # casefold: on a case-insensitive file system `.MemForge/` is the same folder.
    return name.endswith(".md") and not name.casefold().startswith(".memforge/")


def is_git_repo(path: Path) -> bool:
    return os.path.exists(path / ".git")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scoped_names(listing: bytes) -> list[str]:
    # Bytes, decoded the way the file system encodes names: a name that is
    # not valid UTF-8 must not stop the commit for every other file.
    names = (os.fsdecode(n) for n in listing.split(b"\0") if n)
    return [n for n in dict.fromkeys(names) if _in_scope(n)]


class CommitDebouncer:
    """One per watched repo. Schedules a commit after debounce_ms of
    silence. Idempotent if no changes."""

    def __init__(self, repo: Path, debounce_ms: int, quiet: bool) -> None:
        self.repo = repo
        self.debounce_s = debounce_ms / 1000.0
        self.quiet = quiet
        self.last_event = 0.0
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def trigger(self) -> None:
        with self.lock:
            self.last_event = time.monotonic()
            if self.timer is not None:
                self.timer.cancel()
            self.timer = threading.Timer(self.debounce_s, self._commit)
            self.timer.daemon = True
            self.timer.start()

    def _commit(self) -> None:
        # Never os.chdir here: _commit runs on a per-repo Timer thread and
        # os.chdir is process-global, so with two watched folders one repo's
        # chdir can land between another repo's chdir and its git add/commit,
        # committing repo A's change into repo B's working directory. Bind every
        # git call to this repo with `-C <repo>` instead (mirrors resolve._git).
        repo = str(self.repo)
        # A watcher started from a git hook or a shell with GIT_DIR exported
        # would otherwise commit to that repository instead of this one.
        env = git_env()
        env.pop("GIT_LITERAL_PATHSPECS", None)
        try:
            git = find_program_outside("git", self.repo)
        except ProgramInFolder as exc:
            sys.stderr.write(f"[memory-watch] not committing in {visible(self.repo)}: {visible(exc)}\n")
            return
        if git is None:
            sys.stderr.write(f"[memory-watch] not committing in {visible(self.repo)}: git not found on PATH\n")
            return
        try:
            unstaged = subprocess.call([git, "-C", repo, "diff", "--quiet"], env=env)
            staged = subprocess.call([git, "-C", repo, "diff", "--cached", "--quiet"], env=env)
            untracked = subprocess.check_output(
                [git, "-C", repo, "ls-files", "--others", "--exclude-standard"],
                env=env, text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return
        if unstaged == 0 and staged == 0 and not untracked:
            return
        # Scope staging to memory content rather than `git add -A`, so editor
        # swap files, .DS_Store, other transient junk and the .memforge/ control
        # files do not ride along in the `memory: filesystem write` commit
        # (mirrors resolve.py's scoped commit). A committed .gitignore in the
        # memory folder remains the guard for anything else.
        try:
            changed = subprocess.check_output(
                [git, "-C", repo, "ls-files", "-z", "--modified", "--deleted", "--others",
                 "--exclude-standard", "--", *CANDIDATES],
                env=env,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return
        to_add = _scoped_names(changed)
        if to_add:
            subprocess.call([git, "-C", repo, "add", "-A", "--", *map(_literal, to_add)], env=env)
        try:
            staged_names = subprocess.check_output(
                [git, "-C", repo, "diff", "--cached", "--name-only", "-z", "--", *CANDIDATES],
                env=env,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return
        names = _scoped_names(staged_names)
        if not names:
            return
        msg = f"memory: filesystem write ({now_utc()})"
        # Commit exactly the names just listed, read literally, so a staged
        # file outside the watcher's scope does not ride along and a file name
        # cannot act as a pathspec pattern.
        rc = subprocess.run(
            [git, "-C", repo, "commit", "-q", "-m", msg, "--", *map(_literal, names)],
            env=env,
        ).returncode
        if rc == 0 and not self.quiet:
            print(f"[memory-watch] committed in {visible(self.repo)} at {now_utc()}", flush=True)


class MemoryHandler(FileSystemEventHandler):
    """Routes filesystem events to the right repo's debouncer.
    Filters out events under .git/."""

    def __init__(self, repo: Path, debouncer: CommitDebouncer) -> None:
        self.repo = repo
        self.debouncer = debouncer

    def _under_git(self, path: str) -> bool:
        try:
            rel = Path(path).resolve().relative_to(self.repo.resolve())
        except ValueError:
            return False
        return rel.parts and rel.parts[0] == ".git"

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        if self._under_git(event.src_path):
            return
        self.debouncer.trigger()


def default_paths() -> list[Path]:
    # Centralized in memforge.paths (env override -> grandfathered .claude layout
    # if present -> ~/.memforge). Preserve the .exists() filter so we only watch
    # folders that actually exist; one that cannot be looked at is kept, so it
    # is reported.
    from memforge.paths import default_memory_paths
    return [p for p in default_memory_paths() if os.path.exists(p) or access_error(p)]


@terminal_safe
def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-watch",
        description="Cross-platform memory-folder auto-commit watcher.",
    )
    p.add_argument("--path", action="append", default=[], help="Memory folder (repeatable)")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--debounce-ms", type=int, default=2000)
    args = p.parse_args()

    folders = [Path(p).resolve() for p in args.path] if args.path else default_paths()
    if not folders:
        sys.stderr.write("error: no folders specified and no defaults found\n")
        return 2

    valid: list[Path] = []
    for f in folders:
        err = access_error(f)
        if err is not None:
            sys.stderr.write(f"skip: {visible(f)} (cannot read it: {visible(err)})\n")
            continue
        if not os.path.exists(f):
            sys.stderr.write(f"skip: {visible(f)} (does not exist)\n")
            continue
        if not is_git_repo(f):
            sys.stderr.write(f"skip: {visible(f)} (not a git repo; run 'git init' first)\n")
            continue
        try:
            git_found = find_program_outside("git", f) is not None
        except ProgramInFolder as exc:
            sys.stderr.write(f"error: {visible(exc)}; refusing to run it\n")
            return 2
        if not git_found:
            sys.stderr.write("error: git not found on PATH\n")
            return 2
        valid.append(f)

    if not valid:
        sys.stderr.write("error: no valid memory folders to watch\n")
        return 2

    observers: list[Observer] = []
    for repo in valid:
        debouncer = CommitDebouncer(repo, args.debounce_ms, args.quiet)
        handler = MemoryHandler(repo, debouncer)
        observer = Observer()
        observer.schedule(handler, str(repo), recursive=True)
        observer.start()
        observers.append(observer)
        if not args.quiet:
            print(f"[memory-watch] watching {visible(repo)} (debounce {args.debounce_ms}ms)", flush=True)

    if not args.quiet:
        print(f"[memory-watch] {len(observers)} folder(s) watched. Ctrl-C to stop.", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if not args.quiet:
            print("\n[memory-watch] stopping...", flush=True)
    finally:
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
