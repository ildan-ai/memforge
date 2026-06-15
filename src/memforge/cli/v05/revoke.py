"""`memforge revoke <key_id>` — publish a signed revocation event.

Spec ref: §"Revocation events as git commits" and integrity invariant 22.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

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
    p.add_argument(
        "--commit",
        action="store_true",
        help="Perform the GPG-signed empty revocation commit directly in --repo-root, "
        "so the commit body + author-date are produced atomically. The commit is "
        "signed (git commit -S); an unsigned revocation commit is not attributable.",
    )
    p.add_argument(
        "--repo-root",
        default=".",
        help="Git repo root for --commit (default: cwd).",
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

    if args.commit:
        repo = Path(args.repo_root).resolve()
        try:
            _signed_empty_commit(repo, commit_msg, signer_fingerprint=fpr)
        except _RevokeCommitError as exc:
            print(f"revoke commit failed: {exc}", file=sys.stderr)
            return 1
        # revoke-01: success confirmation goes to STDOUT for consistency with
        # every other v0.5 success summary; stderr is reserved for warnings /
        # errors so a partner scripting `revoke --commit` and capturing stdout
        # sees the confirmation on success.
        print(f"signed revocation commit landed in {repo} (key {fpr}).")
        return 0

    if args.output == "-":
        sys.stdout.write(commit_msg)
        sys.stdout.write("\n")
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(commit_msg)
        print(f"commit message written to {args.output}.", file=sys.stderr)
        # NOTE: an unsigned revocation commit is not attributable. Either pass
        # --commit (which GPG-signs for you) or ensure commit signing is
        # configured (`git config commit.gpgsign true`) before committing.
        print(
            f"Apply with: git commit -S --allow-empty -F {args.output}  "
            "(GPG-signed; unsigned revocation commits are not attributable)",
            file=sys.stderr,
        )
    return 0


class _RevokeCommitError(Exception):
    """Failure performing the signed revocation commit."""


def _signed_empty_commit(repo: Path, commit_msg: str, *, signer_fingerprint: str) -> None:
    """Create a GPG-signed empty commit carrying `commit_msg` in `repo`.

    Uses `git commit -S<fpr> --allow-empty -F -` so the body + author-date are
    produced atomically and the commit is attributable to the operator key.
    Raises `_RevokeCommitError` on any git failure.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            f"-S{signer_fingerprint}",
            "--allow-empty",
            "-F",
            "-",
        ],
        input=commit_msg,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise _RevokeCommitError(
            f"git commit exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
