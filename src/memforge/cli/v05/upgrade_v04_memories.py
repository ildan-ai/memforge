"""`memforge upgrade-v04-memories` — bulk add identity+signature to v0.4 memories.

Spec ref: §"Mixed v0.4 / v0.5 deployment posture" and integrity invariant 16.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from memforge import crypto
from memforge.frontmatter import parse, has_frontmatter
from memforge.identity import IdentityError, load_operator_identity, now_iso


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "upgrade-v04-memories",
        help="Add v0.5 `identity` + `signature` frontmatter to v0.4-shaped memories under a memory-root.",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Required confirmation flag. Without --apply this is a dry-run listing.",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    if not memory_root.is_dir():
        print(f"memory-root {memory_root} is not a directory.", file=sys.stderr)
        return 2

    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(f"operator-identity not loadable: {exc}", file=sys.stderr)
        return 1
    operator_uuid = identity["operator_uuid"]
    fpr = identity.get("key_fingerprint")
    if not fpr:
        print("operator-identity missing key_fingerprint.", file=sys.stderr)
        return 1

    targets: list[Path] = []
    for md in sorted(memory_root.rglob("*.md")):
        if md.name == "MEMORY.md":
            continue
        if any(p == "archive" for p in md.parts):
            continue
        if any(p.startswith(".") for p in md.parts):
            continue
        text = md.read_text(encoding="utf-8")
        if not has_frontmatter(text):
            continue
        fm, _body = parse(text)
        fm = fm or {}
        if "identity" in fm and "signature" in fm:
            continue  # already v0.5+
        targets.append(md)

    if not targets:
        print("no v0.4-shaped memories found.")
        return 0
    if not args.apply:
        print(f"{len(targets)} memory file(s) would be upgraded:")
        for t in targets:
            print(f"  {t.relative_to(memory_root)}")
        print("\nPass --apply to upgrade in-place.")
        return 0

    edited = 0
    for path in targets:
        text = path.read_text(encoding="utf-8")
        # Find the frontmatter end and the body, then sign over a canonical
        # envelope of (body, identity, sender_uid="", sequence_number=0,
        # signing_time). For upgrade we use placeholder sender_uid/seq=0
        # since v0.4 memories predate the sender-sequence machinery; new
        # writes use real sender state per §"Substrate-independent envelope
        # contract".
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---", 4)
        if end < 0:
            continue
        block = text[4 : end]
        body = text[end + 4 :].lstrip("\n")
        identity_str = f"operator:{operator_uuid}"
        signing_time = now_iso()
        envelope = crypto.canonical_envelope(
            {
                "memory_body": body,
                "identity": identity_str,
                "sender_uid": "",
                "sequence_number": 0,
                "signing_time": signing_time,
            }
        )
        try:
            sig_b64 = crypto.gpg_sign_detached(envelope, fingerprint=fpr)
        except crypto.CryptoError as exc:
            print(f"sign failed on {path}: {exc}", file=sys.stderr)
            continue
        new_block_lines = block.rstrip("\n").splitlines()
        new_block_lines.append(f"identity: {identity_str}")
        new_block_lines.append("signature:")
        new_block_lines.append("  algo: gpg-ed25519")
        new_block_lines.append(f"  signing_time: {signing_time}")
        new_block_lines.append(f"  value: {sig_b64}")
        new_block_lines.append(f"  key_id: {fpr}")
        new_text = "---\n" + "\n".join(new_block_lines) + "\n---\n\n" + body
        path.write_text(new_text, encoding="utf-8")
        edited += 1

    print(f"upgraded {edited} memory file(s) in-place under {memory_root}.")
    print("Review with `git diff` then commit (single-shot is fine; not a resolve commit).")
    return 0
