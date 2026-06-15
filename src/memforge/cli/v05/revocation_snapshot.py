"""`memforge revocation-snapshot` — emit a signed snapshot commit.

Spec ref: §"Revocation snapshot mechanism".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import registry as registry_mod, revocation
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
        "--memory-root",
        default=None,
        help="Memory-root holding .memforge/operator-registry.yaml (default: --repo-root).",
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
    memory_root = Path(args.memory_root).resolve() if args.memory_root else repo

    # snapshot-verify-02: build the snapshot from the VERIFIED walk, not the raw
    # walk. The raw walk performs neither signature verification nor the
    # revoked_at clock-skew guard, so a forged / backdated revoke body in history
    # would be compressed into a snapshot carrying the operator's OWN valid
    # signature -- laundering it. Load + verify the operator-registry first and
    # use walk_revocation_set_verified so ONLY signature-valid, in-skew
    # revocations are ever signed into a snapshot (mirrors revoke-cache-refresh).
    try:
        registry = registry_mod.load_registry(memory_root, verify_signature=True)
    except registry_mod.RegistryError as exc:
        print(
            f"operator-registry load failed (needed to verify revocations before "
            f"snapshotting): {exc}",
            file=sys.stderr,
        )
        return 1

    # Snapshot generation walks the FULL revocation set (a new snapshot must
    # contain the complete current revocation state, so it must NOT start from a
    # prior snapshot -- which walk_revocation_set_verified already guarantees,
    # since the snapshot floor is disabled), but it still honors the operator-
    # configured caps in .memforge/config.yaml so a large legitimate history can
    # be snapshotted with a raised cap instead of dead-locking.
    try:
        rev_set = revocation.walk_revocation_set_verified(
            repo, registry, memory_root=memory_root
        )
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
