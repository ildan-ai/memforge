"""`memforge memories-by-key <key_id>` — list memories signed by a key.

Spec ref: §"Reader-side revocation walk" (the inverse direction: given a
revoked key, find every memory written under it for follow-up action).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge.frontmatter import parse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "memories-by-key",
        help="Walk a memory folder + list every memory file whose `identity` resolves to <key_id>.",
    )
    p.add_argument("key_id", help="GPG fingerprint of the signing key to filter on.")
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.add_argument(
        "--operator-uuid",
        help="Restrict to memories signed under this operator-UUID (matches the identity prefix).",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    if not memory_root.is_dir():
        print(f"memory-root {memory_root} is not a directory.", file=sys.stderr)
        return 2

    matches: list[tuple[Path, str]] = []
    for md in sorted(memory_root.rglob("*.md")):
        if md.name == "MEMORY.md":
            continue
        if any(part == "archive" for part in md.parts):
            continue
        if any(part.startswith(".") for part in md.parts):
            continue
        try:
            fm, _body = parse(md.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not fm or "identity" not in fm:
            continue
        identity = fm.get("identity", "")
        signature = fm.get("signature") or {}
        if args.operator_uuid and args.operator_uuid not in identity:
            continue
        # `signature.value` is the detached sig bytes; the `key_id` we filter
        # on is the GPG fingerprint anchored in the operator-registry. Since
        # the frontmatter doesn't carry the fingerprint directly, we look
        # for it in the optional `key_id` field (operator may add it as a
        # convenience for `memories-by-key` queries).
        candidate_key = signature.get("key_id") if isinstance(signature, dict) else None
        if candidate_key == args.key_id or args.key_id in identity:
            matches.append((md, identity))

    if not matches:
        print(f"no memories matched key_id={args.key_id} under {memory_root}.")
        return 0
    print(f"{len(matches)} memory file(s) signed under key {args.key_id}:")
    for path, identity in matches:
        print(f"  {path.relative_to(memory_root)}  ({identity})")
    return 0
