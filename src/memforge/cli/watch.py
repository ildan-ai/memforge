# memory-watch — cross-platform filesystem-watcher auto-commit for memory
# folders.
#
# Portable equivalent of the Claude Code PostToolUse auto-commit hook,
# for agents that don't have an event-hook surface (Cursor, Aider, Codex
# CLI, Cline, Continue, Windsurf, generic editor workflows).
#
# Cross-platform via the `watchdog` library: macOS (FSEvents), Linux
# (inotify), Windows (ReadDirectoryChangesW). Single dependency.
#
# Install:
#   python3 -m pip install watchdog
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
# Commit scope: staging is scoped to memory content (`*.md` + `.memforge/`),
# not `git add -A`, so editor swap files and OS metadata are not committed.
# Keep a committed `.gitignore` in the memory folder for anything else you
# want excluded; the watcher will not stage it regardless.

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    sys.stderr.write(
        "error: watchdog not installed. Run:\n"
        "    python3 -m pip install watchdog\n"
    )
    sys.exit(2)


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        try:
            unstaged = subprocess.call(["git", "-C", repo, "diff", "--quiet"])
            staged = subprocess.call(["git", "-C", repo, "diff", "--cached", "--quiet"])
            untracked = subprocess.check_output(
                ["git", "-C", repo, "ls-files", "--others", "--exclude-standard"],
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return
        if unstaged == 0 and staged == 0 and not untracked:
            return
        # Scope staging to memory content (*.md + the .memforge/ tool dir) rather
        # than `git add -A`, so editor swap files, .DS_Store, and other transient
        # junk landing during the debounce window do not ride along in the
        # `memory: filesystem write` commit (mirrors resolve.py's scoped commit;
        # watch-01). A committed .gitignore in the memory folder remains the
        # belt-and-suspenders guard for anything under these pathspecs.
        subprocess.call(
            ["git", "-C", repo, "add", "-A", "--", "*.md", ".memforge"]
        )
        msg = f"memory: filesystem write ({now_utc()})"
        rc = subprocess.call(["git", "-C", repo, "commit", "-q", "-m", msg])
        if rc == 0 and not self.quiet:
            print(f"[memory-watch] committed in {self.repo} at {now_utc()}", flush=True)


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
    # folders that actually exist.
    from memforge.paths import default_memory_paths
    return [p for p in default_memory_paths() if p.exists()]


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
        if not f.exists():
            sys.stderr.write(f"skip: {f} (does not exist)\n")
            continue
        if not is_git_repo(f):
            sys.stderr.write(f"skip: {f} (not a git repo; run 'git init' first)\n")
            continue
        if not shutil.which("git"):
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
            print(f"[memory-watch] watching {repo} (debounce {args.debounce_ms}ms)", flush=True)

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
