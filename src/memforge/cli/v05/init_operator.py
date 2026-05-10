"""`memforge init-operator` — generate operator-UUID + register a GPG key.

Spec ref: §"Operator-identity file (per-machine)" and §"Trust-bootstrap
procedure for multi-operator deployments".
"""

from __future__ import annotations

import argparse
import sys

from memforge import crypto
from memforge.identity import (
    IdentityError,
    OPERATOR_IDENTITY_PATH,
    generate_uuidv7,
    save_operator_identity,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "init-operator",
        help="Generate operator-UUID + register a GPG signing key as the operator identity.",
    )
    p.add_argument("--name", required=True, help="Operator name (advisory; informational).")
    p.add_argument(
        "--gpg-fingerprint",
        help="Existing GPG fingerprint to register (40-char primary fingerprint). "
        "If omitted, --gen-key must be passed to create a fresh Ed25519 keypair.",
    )
    p.add_argument(
        "--gen-key",
        action="store_true",
        help="Generate a new Ed25519 keypair non-interactively (no passphrase). "
        "Operators in production should set a passphrase post-generation via `gpg --edit-key`.",
    )
    p.add_argument(
        "--email",
        default=None,
        help="Email for the gen-key uid (defaults to <operator-uuid>@memforge.local).",
    )
    p.add_argument("--force", action="store_true", help="Overwrite existing operator-identity file.")
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    if OPERATOR_IDENTITY_PATH.exists() and not args.force:
        print(
            f"operator-identity already exists at {OPERATOR_IDENTITY_PATH}. "
            "Pass --force to overwrite (you will lose the existing operator-UUID).",
            file=sys.stderr,
        )
        return 2

    operator_uuid = generate_uuidv7()

    if args.gen_key:
        email = args.email or f"{operator_uuid}@memforge.local"
        try:
            fingerprint = crypto.gpg_gen_key_batch(name_real=args.name, name_email=email)
        except crypto.CryptoError as exc:
            print(f"key generation failed: {exc}", file=sys.stderr)
            return 1
    elif args.gpg_fingerprint:
        fingerprint = args.gpg_fingerprint.replace(" ", "").upper()
        # Sanity check that the key is present in the local keyring.
        matched = [k for k in crypto.gpg_list_secret_keys() if k["fingerprint"].endswith(fingerprint[-16:])]
        if not matched:
            print(
                f"fingerprint {fingerprint} not found in local gpg secret keyring. "
                "Import it first (`gpg --import`) or use --gen-key.",
                file=sys.stderr,
            )
            return 1
    else:
        print(
            "either --gpg-fingerprint or --gen-key required. Run `memforge init-operator --help`.",
            file=sys.stderr,
        )
        return 2

    try:
        path = save_operator_identity(
            operator_uuid=operator_uuid,
            operator_name=args.name,
            key_fingerprint=fingerprint,
        )
    except IdentityError as exc:
        print(f"failed to save operator-identity: {exc}", file=sys.stderr)
        return 1

    print(f"operator-UUID: {operator_uuid}")
    print(f"GPG fingerprint: {fingerprint}")
    print(f"identity file:   {path}  (mode 0600)")
    print()
    print("Next steps:")
    print("  1. Run `memforge recovery-init` to install the recovery secret.")
    print("  2. From your memory-root, run `memforge init-store` to bootstrap an operator-registry.")
    return 0
