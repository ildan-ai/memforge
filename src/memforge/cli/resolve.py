"""memforge-resolve - the canonical reconciliation operation for v0.4+.

Implements the spec contract from SPEC.md §"Multi-agent concurrency: competing
claims" / "The resolve operation". Walks memory folders, groups by
decision_topic, prompts operator to pick a winner (or accepts --winner-uid for
non-interactive use), applies the mutations, deletes the snooze if present,
and writes one atomic commit with the `memforge: resolve <topic>` prefix.

Post-conditions enforced by this tool (audit re-validates on HEAD):

  1. Exactly one member has status: active.
  2. Every other member has status: superseded AND superseded_by: [<winner-UID>].
  3. The winner's replaces lists exactly the superseded UIDs.
  4. ever_multi_member: true is set on the winner (monotonic anchor flag).
  5. Snooze file at .memforge/snoozes/<topic>.yaml is deleted if present.
  6. The commit touches only memory files in the topic + at most that snooze.
  7. The commit message starts with `memforge: resolve <topic>`.

Invocation:
    memforge-resolve <topic>                              # interactive
    memforge-resolve <topic> --winner-uid <uid>           # non-interactive
    memforge-resolve <topic> --memory-root <path> [...]   # explicit folder(s)
    memforge-resolve <topic> --dry-run                    # preview only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from memforge.frontmatter import parse, render
from memforge.cli._concurrency_audit import (
    _is_valid_slug,
    LIVE_STATUSES,
    REPLACES_CARDINALITY_CAP,
)

# LIVE_STATUSES is imported from the concurrency-audit module so the live/exit
# status partition has a single source of truth. The prior local
# EXIT_STATUSES constant here was dead (never referenced) and duplicated that
# module's definition; removed (closes resolve-02).


def _default_paths() -> list[Path]:
    # Centralized in memforge.paths (env override -> grandfathered .claude layout
    # if present -> ~/.memforge). resolve scans whatever roots it returns; no
    # .exists() filter here because _find_group already skips non-dir roots.
    from memforge.paths import default_memory_paths
    return default_memory_paths()


def _walk_memory(root: Path) -> list[tuple[Path, dict[str, Any], str]]:
    """Yield (path, frontmatter, body) for every .md file under root,
    excluding MEMORY.md and archive/."""
    out = []
    for path in sorted(root.rglob("*.md")):
        if path.name == "MEMORY.md":
            continue
        if "archive" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = parse(text)
        if not fm:
            continue
        out.append((path, fm, body))
    return out


def _find_group(
    roots: list[Path], topic: str
) -> tuple[Path, list[tuple[Path, dict[str, Any], str]], list[tuple[Path, dict[str, Any], str]]]:
    """Find all live AND prior-superseded members of `topic` across roots.

    Returns (containing_root, live_members, superseded_members). The superseded
    members are returned so a re-resolve can carry their UIDs into the new
    winner's `replaces` and re-point their `superseded_by` to the new winner,
    preserving SPEC post-condition 3 ("replaces: no fewer"). Errors if live
    members span multiple roots (cross-root resolves are out of scope).
    """
    by_root: dict[Path, list] = {}
    superseded_by_root: dict[Path, list] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path, fm, body in _walk_memory(root):
            if fm.get("decision_topic") != topic:
                continue
            status = fm.get("status")
            if status in LIVE_STATUSES:
                by_root.setdefault(root, []).append((path, fm, body))
            elif status == "superseded":
                superseded_by_root.setdefault(root, []).append((path, fm, body))
    if not by_root:
        return (None, [], [])
    if len(by_root) > 1:
        roots_with = ", ".join(str(r) for r in by_root)
        print(
            f"error: live members of decision_topic '{topic}' span multiple memory roots:",
            file=sys.stderr,
        )
        print(f"  {roots_with}", file=sys.stderr)
        print(
            "       cross-root resolution is not supported in v0.4. "
            "Move members to a single root before resolving.",
            file=sys.stderr,
        )
        sys.exit(2)
    root = next(iter(by_root))
    members = by_root[root]
    # Only carry prior-superseded members that live in the SAME root as the live
    # winner; superseded members in another root are out of scope for this
    # single-root resolve and are left untouched.
    superseded = superseded_by_root.get(root, [])
    return (root, members, superseded)


def _present_members(members: list[tuple[Path, dict[str, Any], str]]) -> None:
    print()
    print(f"Live members ({len(members)}):")
    for i, (path, fm, body) in enumerate(members, 1):
        first_line = next(
            (line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")),
            "(no body)",
        )
        if len(first_line) > 100:
            first_line = first_line[:97] + "..."
        print(
            f"  [{i}] uid={fm.get('uid', '<missing>')}"
            f" owner={fm.get('owner', '<missing>')}"
            f" status={fm.get('status', '<missing>')}"
            f" updated={fm.get('updated', '<missing>')}"
        )
        print(f"      {first_line}")
        print(f"      file: {path}")
    print()


def _choose_winner_interactive(members: list[tuple[Path, dict[str, Any], str]]) -> Optional[int]:
    _present_members(members)
    while True:
        try:
            raw = input(
                f"Pick winner [1-{len(members)}], or 'q' to abort: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\naborted.", file=sys.stderr)
            return None
        if raw == "q":
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(members):
                return n - 1
        print("invalid; try again.")


def _choose_winner_uid(members: list[tuple[Path, dict[str, Any], str]], winner_uid: str) -> Optional[int]:
    for i, (_, fm, _) in enumerate(members):
        if fm.get("uid") == winner_uid:
            return i
    return None


def _apply_mutations(
    members: list[tuple[Path, dict[str, Any], str]],
    winner_idx: int,
    dry_run: bool,
    prior_superseded: Optional[list[tuple[Path, dict[str, Any], str]]] = None,
) -> tuple[Path, list[Path]]:
    """Apply the v0.4 resolve post-conditions to disk. Returns (winner_path, touched_paths).

    `prior_superseded` is the set of members of the same decision_topic that
    were already status:superseded from an earlier resolve. Their UIDs are
    carried into the new winner's `replaces` and their `superseded_by` is
    re-pointed to the new winner, so a re-resolve never drops a historical
    member from the winner's replaces (SPEC post-condition 3, "no fewer") and
    never leaves a superseded member pointing at a now-superseded anchor (which
    the concurrency-audit symmetry check would flag).
    """
    prior_superseded = prior_superseded or []
    winner_path, winner_fm, winner_body = members[winner_idx]
    losers = [m for i, m in enumerate(members) if i != winner_idx]
    winner_uid = winner_fm.get("uid")
    if not winner_uid:
        print(f"error: winner memory at {winner_path} has no uid in frontmatter", file=sys.stderr)
        sys.exit(2)
    loser_uids = [fm.get("uid") for _, fm, _ in losers]
    if any(not u for u in loser_uids):
        print("error: one or more losers lack uid in frontmatter", file=sys.stderr)
        sys.exit(2)
    prior_uids = [fm.get("uid") for _, fm, _ in prior_superseded]
    if any(not u for u in prior_uids):
        print("error: one or more prior-superseded members lack uid in frontmatter", file=sys.stderr)
        sys.exit(2)

    # If the winner was itself the previous active anchor, it may already carry
    # the prior-superseded UIDs in its `replaces`; merge rather than clobber.
    # Order: winner's existing replaces (sans winner+current losers), then the
    # newly-superseded current losers, then any prior-superseded UID not yet
    # listed. Deduplicate while preserving first-seen order for byte-stability.
    existing_replaces = winner_fm.get("replaces") or []
    if not isinstance(existing_replaces, list):
        existing_replaces = []
    replaces: list[str] = []
    for uid in [*existing_replaces, *loser_uids, *prior_uids]:
        if not uid or uid == winner_uid:
            continue
        if uid not in replaces:
            replaces.append(uid)

    # SPEC §369 caps `replaces` cardinality at REPLACES_CARDINALITY_CAP (20), and
    # the concurrency audit raises a Tier-1 MAJOR above it. A topic that
    # accumulates 20+ members across repeated re-resolves would produce a winner
    # that resolve itself fails its own audit on. Warn the operator when the
    # merged list crosses the cap so the condition is visible rather than only
    # surfacing later at audit time (closes resolve-03).
    if len(replaces) > REPLACES_CARDINALITY_CAP:
        print(
            f"warning: winner '{winner_uid}' replaces {len(replaces)} members, "
            f"exceeding the spec cardinality cap of {REPLACES_CARDINALITY_CAP}; "
            "memory-audit will flag this as a MAJOR. Consider archiving the "
            "oldest superseded members for this decision_topic.",
            file=sys.stderr,
        )

    # Winner: replaces lists every current loser + every prior-superseded member,
    # set ever_multi_member: true, status -> active.
    new_winner_fm = dict(winner_fm)
    new_winner_fm["replaces"] = replaces
    new_winner_fm["ever_multi_member"] = True
    if new_winner_fm.get("status") != "active":
        new_winner_fm["status"] = "active"
    new_winner_text = render(new_winner_fm, winner_body)

    writes: list[tuple[Path, str]] = []
    # Current losers: status -> superseded, superseded_by -> [winner_uid].
    for path, fm, body in losers:
        new_fm = dict(fm)
        new_fm["status"] = "superseded"
        new_fm["superseded_by"] = [winner_uid]
        new_fm["ever_multi_member"] = True
        writes.append((path, render(new_fm, body)))
    # Prior-superseded members: re-point superseded_by at the new winner so the
    # audit symmetry check (each superseded member points to the sole active
    # winner, and the winner's replaces lists it) holds after a re-resolve.
    for path, fm, body in prior_superseded:
        new_fm = dict(fm)
        new_fm["status"] = "superseded"
        new_fm["superseded_by"] = [winner_uid]
        new_fm["ever_multi_member"] = True
        writes.append((path, render(new_fm, body)))

    if dry_run:
        print("dry-run: would write the following:")
        print(f"  WINNER {winner_path}: status=active, replaces={replaces}, ever_multi_member=true")
        for path, fm, _ in losers:
            print(f"  LOSER  {path}: status=superseded, superseded_by=[{winner_uid}]")
        for path, fm, _ in prior_superseded:
            print(f"  REPOINT {path}: superseded_by re-pointed to [{winner_uid}]")
        return (winner_path, [p for p, _ in writes])

    winner_path.write_text(new_winner_text, encoding="utf-8")
    for path, text in writes:
        path.write_text(text, encoding="utf-8")

    return (winner_path, [p for p, _ in writes])


def _delete_snooze(root: Path, topic: str, dry_run: bool) -> Optional[Path]:
    snoozes_dir = (root / ".memforge" / "snoozes").resolve()
    snooze = root / ".memforge" / "snoozes" / f"{topic}.yaml"
    # Defense in depth: refuse any path that escapes the snoozes directory even
    # if a malformed topic slipped past the slug check in main().
    resolved = snooze.resolve()
    if resolved.parent != snoozes_dir:
        print(
            f"error: refusing to delete snooze outside {snoozes_dir}: {resolved}",
            file=sys.stderr,
        )
        sys.exit(2)
    if not snooze.is_file():
        return None
    if dry_run:
        print(f"dry-run: would delete snooze at {snooze}")
        return snooze
    snooze.unlink()
    return snooze


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_toplevel(start: Path) -> Optional[Path]:
    """Return the toplevel of the git repo containing `start`, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _commit_resolution(memory_root: Path, topic: str, paths: list[Path], dry_run: bool) -> None:
    if dry_run:
        print(f"dry-run: would commit with message 'memforge: resolve {topic}'")
        return
    repo_root = _git_toplevel(memory_root)
    if repo_root is None:
        print(
            f"warn: {memory_root} is not inside a git repository; mutations applied but not committed.",
            file=sys.stderr,
        )
        return
    relpaths = []
    for p in paths:
        if p is None:
            continue
        try:
            relpaths.append(str(p.resolve().relative_to(repo_root.resolve())))
        except ValueError:
            # A touched path escaped the repo (e.g. a traversal snooze that the
            # containment checks should already have refused). Abort cleanly
            # rather than crash after the member writes already hit disk.
            print(
                f"error: resolve touched a path outside the git repo and cannot "
                f"be committed atomically: {p}",
                file=sys.stderr,
            )
            sys.exit(2)
    _git(repo_root, "add", "--", *relpaths)
    diff = _git(repo_root, "diff", "--cached", "--quiet", "--", *relpaths, check=False)
    if diff.returncode == 0:
        print(f"warn: no changes staged in {repo_root}; nothing to commit.", file=sys.stderr)
        return
    msg = f"memforge: resolve {topic}"
    # Commit with an explicit pathspec (--only semantics via trailing `-- <paths>`)
    # so an unrelated pre-staged change cannot ride along in the scope-locked
    # resolve commit (SPEC.md post-condition 6).
    _git(repo_root, "commit", "-o", "-m", msg, "--", *relpaths)
    print(f"committed: {msg} (in {repo_root})")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memforge-resolve",
        description="Resolve a competing-claim group on a decision_topic.",
    )
    parser.add_argument("topic", help="The decision_topic slug to resolve.")
    parser.add_argument(
        "--memory-root",
        action="append",
        help="Memory folder to search (repeatable). Defaults to per-cwd memory + global-memory.",
    )
    parser.add_argument(
        "--winner-uid",
        help="Non-interactive: pick the winner by UID instead of prompting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing or committing.",
    )
    args = parser.parse_args(argv)

    # Validate the topic with the canonical slug pipeline BEFORE any filesystem
    # or git operation. decision_topic values are attacker-influenceable memory
    # content and become filenames under .memforge/snoozes/; a traversal slug
    # (e.g. ../../secret/victim) must never reach a path interpolation.
    if not _is_valid_slug(args.topic):
        print(
            f"error: topic '{args.topic}' fails the slug pipeline "
            f"(must be lowercase ASCII, hyphen-separated, <=64 bytes, "
            f"regex `^[a-z0-9]+(-[a-z0-9]+)*$`, not a reserved name).",
            file=sys.stderr,
        )
        return 2

    if args.memory_root:
        roots = [Path(p).expanduser().resolve() for p in args.memory_root]
    else:
        roots = _default_paths()

    root, members, prior_superseded = _find_group(roots, args.topic)
    if len(members) == 0:
        print(f"no live members of decision_topic '{args.topic}' found.")
        return 0
    if len(members) == 1:
        print(
            f"only one live member of decision_topic '{args.topic}'; "
            f"nothing to resolve. uid={members[0][1].get('uid')}"
        )
        return 0

    if args.winner_uid:
        winner_idx = _choose_winner_uid(members, args.winner_uid)
        if winner_idx is None:
            print(
                f"error: --winner-uid '{args.winner_uid}' does not match any live member of '{args.topic}'.",
                file=sys.stderr,
            )
            uids = ", ".join(fm.get("uid", "?") for _, fm, _ in members)
            print(f"       live UIDs: {uids}", file=sys.stderr)
            return 2
    else:
        winner_idx = _choose_winner_interactive(members)
        if winner_idx is None:
            return 1

    winner_path, loser_paths = _apply_mutations(
        members, winner_idx, args.dry_run, prior_superseded
    )
    snooze_path = _delete_snooze(root, args.topic, args.dry_run)

    touched = [winner_path, *loser_paths]
    if snooze_path is not None:
        touched.append(snooze_path)
    _commit_resolution(root, args.topic, touched, args.dry_run)

    print(f"resolved '{args.topic}': winner={members[winner_idx][1].get('uid')}, "
          f"superseded={[fm.get('uid') for i, (_, fm, _) in enumerate(members) if i != winner_idx]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
