"""`memforge revoke <key_id>` — publish a signed revocation event.

Spec ref: §"Revocation events as git commits" and integrity invariant 22.
"""

from __future__ import annotations

import argparse
import sys

from memforge import revocation
from memforge.identity import IdentityError, load_operator_identity


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "revoke",
        help="Build a signed `memforge: revoke <key_id>` commit body.",
    )
    p.add_argument("key_id", help="Target key fingerprint to revoke.")
    p.add_argument(
        "--reason",
        required=True,
        help="Free-form reason (>= 8 characters) — surfaced to receivers + audit.",
    )
    p.add_argument(
        "--output",
        default="-",
        help="Where to write the commit message body (default: stdout). Use the output to `git commit -F <file>`.",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
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
    try:
        commit_msg, _body = revocation.build_revoke_body(
            key_id=args.key_id,
            reason=args.reason,
            revoked_by_uuid=operator_uuid,
            signer_fingerprint=fpr,
        )
    except revocation.RevocationError as exc:
        print(f"revoke build failed: {exc}", file=sys.stderr)
        return 1

    if args.output == "-":
        sys.stdout.write(commit_msg)
        sys.stdout.write("\n")
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(commit_msg)
        print(f"commit message written to {args.output}.", file=sys.stderr)
        print(f"Apply with: git commit --allow-empty -F {args.output}", file=sys.stderr)
    return 0
