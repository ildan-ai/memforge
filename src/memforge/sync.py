"""`memforge sync`: fetch, verify the first-parent range, fast-forward, record.

Only this command checks signatures. It never merges or rebases, has no
override flag, and keeps one piece of local state, `memforge.verifiedHead`,
written only after a successful fast-forward.
"""

from __future__ import annotations

import os
import re
import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from memforge._sync_git import (
    Fail, Isolation, Refused, Store, SyncError, resolve_tools, tool_line,
)
from memforge._sync_verify import (
    MODE_LINES, RELEASED_MODES, CommitInfo, Verifier, parse_commit,
)

STATE_KEY = "memforge.verifiedHead"
_FULL_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _write_verified_head(store: Store, head: str) -> None:
    """The only writer of the state key. git replaces the config file by
    lock-and-rename, so the write is atomic."""
    result = store.git("config", "--local", STATE_KEY, head)
    if result.rc != 0:
        raise SyncError("state_write_failed", result.err.strip())


def _read_verified_head(store: Store) -> str | None:
    result = store.git("config", "--local", "--get", STATE_KEY)
    if result.rc == 1:
        return None
    if result.rc != 0:
        raise SyncError("state_invalid", result.err.strip())
    value = result.out.decode("utf-8", "replace").strip()
    if not _FULL_ID.fullmatch(value):
        raise SyncError("state_invalid", f"{STATE_KEY} is not a commit id")
    return value


def _pin(store: Store, trust_head: str | None, adopt_local: bool) -> str:
    if adopt_local:
        print("--adopt-local is a statement, not a check: it pins local HEAD without verifying it.")
        return store.must("rev-parse", "--verify", "HEAD^{commit}")
    value = (trust_head or "").lower()
    if not _FULL_ID.fullmatch(value):
        raise SyncError("pin_invalid", "--trust-head needs the full commit id")
    return value


def _first_parent_range(iso: Isolation, start: str, head: str, shallow: bool) -> list[CommitInfo]:
    """Commits after `start` up to `head` on the first-parent chain, oldest first."""
    chain: list[CommitInfo] = []
    current = head
    while current != start:
        result = iso.git("cat-file", "commit", current)
        if result.rc != 0:
            if shallow:
                raise Refused("shallow_range", f"commit {current} is not present in this shallow clone")
            raise SyncError("object_missing", f"commit {current} is not present")
        info = parse_commit(current, result.out)
        chain.append(info)
        if info.parent is None:
            raise Refused("history_rewrite", f"verified start {start} is not on the first-parent history of {head}")
        current = info.parent
    chain.reverse()
    return chain


def _upstream(store: Store) -> tuple[str, str]:
    """The remote and the branch ref the local upstream setting names."""
    branch = store.git("symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.rc != 0:
        raise SyncError("no_branch", "HEAD is detached; check out the shared branch")
    name = branch.out.decode().strip()
    remote = store.git("config", "--get", f"branch.{name}.remote")
    merge = store.git("config", "--get", f"branch.{name}.merge")
    if remote.rc != 0 or merge.rc != 0:
        raise SyncError("no_upstream", f"branch {name} has no upstream setting")
    return remote.out.decode().strip(), merge.out.decode().strip()


def _verify_range(iso: Isolation, tools, released: frozenset[str],
                  commits: list[CommitInfo]) -> None:
    verifier = Verifier(iso, tools, released)
    try:
        for commit in commits:
            try:
                verifier.verify(commit)
            except Fail as exc:
                raise Refused(exc.reason, exc.detail, commit.id) from None
    finally:
        for mode in verifier.modes_seen:
            print(f"mode {mode}: {MODE_LINES[mode]}")


def _sync(root: Path, trust_head: str | None, adopt_local: bool, check: bool,
          released: frozenset[str]) -> None:
    tools = resolve_tools()
    for tool in tools.values():
        print(tool_line(tool))
    if tools["git"].path is None:
        raise SyncError("git_missing", "git was not found")
    store = Store(tools["git"].path, root)
    top = store.git("rev-parse", "--show-toplevel")
    if top.rc != 0:
        raise SyncError("not_a_repository", f"{root} is not inside a git work tree")
    toplevel = top.out.decode("utf-8", "replace").strip()
    if os.path.realpath(root) != os.path.realpath(toplevel):
        raise SyncError("root_not_toplevel",
                        f"{root} is not the top level of its repository ({toplevel}); one verifiedHead serves one store")

    # 1. Start.
    if trust_head and adopt_local:
        raise SyncError("bad_arguments", "give only one of --trust-head and --adopt-local")
    verified = _read_verified_head(store)
    if verified is None:
        if not (trust_head or adopt_local):
            raise SyncError("no_pin", "first sync needs --trust-head <commit> or --adopt-local")
        start = _pin(store, trust_head, adopt_local)
    else:
        if trust_head or adopt_local:
            raise SyncError("pin_refused", "this store already has a verified head; pin flags are refused")
        start = verified
    local = store.must("rev-parse", "--verify", "HEAD^{commit}")
    if check:
        print(f"HEAD equals verifiedHead: {'yes' if local == verified else 'no'}"
              + ("" if verified else " (verifiedHead is unset)"))

    # 2. Fetch the branch the local upstream setting names.
    remote, branch_ref = _upstream(store)
    fetch = store.git("fetch", "--no-recurse-submodules", "--no-write-fetch-head", remote, branch_ref)
    if fetch.rc != 0:
        raise SyncError("fetch_failed", fetch.err.strip())
    fetched = store.must("rev-parse", "--verify", "@{upstream}^{commit}")

    objects = store.must("rev-parse", "--path-format=absolute", "--git-common-dir") + "/objects"
    shallow = store.must("rev-parse", "--is-shallow-repository") == "true"
    shallow_file = store.must("rev-parse", "--path-format=absolute", "--git-path", "shallow") if shallow else None
    with Isolation(tools["git"].path, objects, shallow_file) as iso:
        # 3. Target.
        if iso.git("merge-base", "--is-ancestor", local, fetched).rc == 0:
            head = fetched
        elif iso.git("merge-base", "--is-ancestor", fetched, local).rc == 0:
            head = local
        else:
            raise SyncError("diverged", f"local {local} and fetched {fetched} have diverged; sync never merges")

        # 4. Shape.
        commits = _first_parent_range(iso, start, head, shallow)

        # 5. Verify.
        _verify_range(iso, tools, released, commits)
    print(f"verified {len(commits)} commit(s) from {start} to {head}")

    if check:
        print("check only: no local branch, work tree or verifiedHead was changed")
        return

    # 6. Fast-forward.
    if head != local:
        merge = store.git("merge", "--ff-only", head)
        if merge.rc != 0:
            raise SyncError("ff_failed", merge.err.strip() or merge.out.decode("utf-8", "replace").strip())

    # 7. Record.
    _write_verified_head(store, head)

    # 8. Done.
    print(f"verifiedHead is now {head}")


@contextmanager
def _terminate_as_exit() -> Iterator[None]:
    """Turn SIGTERM into SystemExit so the throwaway directory is removed."""
    def handler(signum, frame):  # noqa: ARG001
        raise SystemExit(143)
    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_sync(root: Path, *, trust_head: str | None = None, adopt_local: bool = False,
             check: bool = False, released: frozenset[str] = RELEASED_MODES) -> int:
    try:
        with _terminate_as_exit():
            _sync(root, trust_head, adopt_local, check, released)
    except Refused as exc:
        where = f" commit {exc.commit}:" if exc.commit else ""
        print(f"memforge sync: refused ({exc.reason}):{where} {exc.detail}".rstrip(), file=sys.stderr)
        return 2
    except SyncError as exc:
        print(f"memforge sync: {exc.reason}: {exc.detail}".rstrip(": "), file=sys.stderr)
        return 1
    return 0
