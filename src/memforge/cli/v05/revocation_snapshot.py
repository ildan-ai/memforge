"""`memforge revocation-snapshot` — emit a signed snapshot commit.

Spec ref: §"Revocation snapshot mechanism".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import revocation
from memforge.identity import IdentityError, load_operator_identity


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "revocation-snapshot",
        help="Walk the revocation set + emit a signed `memforge: revocation-snapshot <hash>` commit.",
    )
    p.add_argument(
        "--repo-root",
        default=".",
        help="Git repo root (default: cwd).",
    )
    p.add_argument(
        "--output",
        default="-",
        help="Where to write the snapshot commit body (default: stdout).",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(f"operator-identity not loadable: {exc}", file=sys.stderr)
        return 1
    fpr = identity.get("key_fingerprint")
    if not fpr:
        print("operator-identity missing key_fingerprint.", file=sys.stderr)
        return 1
    repo = Path(args.repo_root).resolve()
    try:
        rev_set = revocation.walk_revocation_set(repo)
        commit_msg, body = revocation.build_revocation_snapshot_body(rev_set, signer_fingerprint=fpr)
    except revocation.RevocationError as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1

    if args.output == "-":
        sys.stdout.write(commit_msg)
        sys.stdout.write("\n")
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(commit_msg)
        print(
            f"snapshot ({len(body['revocations'])} revocations) written to {args.output}. "
            f"Apply with: git commit --allow-empty -F {args.output}",
            file=sys.stderr,
        )
    return 0
