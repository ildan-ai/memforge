"""`memforge rotate-key` — cross-signed key rotation with 24h cool-down.

Spec ref: §"Cross-signed rotation chain" and §"Mandatory cool-down period".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import crypto, registry as registry_mod
from memforge.identity import IdentityError, load_operator_identity, save_operator_identity


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "rotate-key",
        help="Rotate the current operator key (generate new keypair + cross-sign).",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.add_argument(
        "--email",
        default=None,
        help="Email for the new key's uid (defaults to <operator-uuid>@memforge.local).",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(f"operator-identity not loadable: {exc}", file=sys.stderr)
        return 1
    operator_uuid = identity["operator_uuid"]
    operator_name = identity.get("operator_name", "")
    old_fpr = identity.get("key_fingerprint")
    if not old_fpr:
        print("operator-identity missing key_fingerprint.", file=sys.stderr)
        return 1

    try:
        reg = registry_mod.load_registry(memory_root, verify_signature=True)
    except registry_mod.RegistryError as exc:
        print(f"registry load failed: {exc}", file=sys.stderr)
        return 1

    email = args.email or f"{operator_uuid}@memforge.local"
    try:
        new_fpr = crypto.gpg_gen_key_batch(name_real=operator_name, name_email=email)
        new_pub = crypto.gpg_export_public_key(new_fpr)
        new_algo = "gpg-ed25519"
    except crypto.CryptoError as exc:
        print(f"new key generation failed: {exc}", file=sys.stderr)
        return 1

    # Cross-signatures: old key signs the new pubkey envelope, and new key
    # signs the same envelope. Both attest to the rotation event.
    envelope = crypto.canonical_envelope(
        {
            "operator_uuid": operator_uuid,
            "new_key_id": new_fpr,
            "new_algo": new_algo,
            "new_public_material": new_pub,
            "rotation_from": old_fpr,
        }
    )
    try:
        sig_by_old = crypto.gpg_sign_detached(envelope, fingerprint=old_fpr)
        sig_by_new = crypto.gpg_sign_detached(envelope, fingerprint=new_fpr)
    except crypto.CryptoError as exc:
        print(f"cross-signing failed: {exc}", file=sys.stderr)
        return 1

    try:
        reg = registry_mod.add_rotated_key(
            reg,
            operator_uuid=operator_uuid,
            new_key_id=new_fpr,
            new_algo=new_algo,
            new_public_material_b64=new_pub,
            cross_signature_by_old=sig_by_old,
            cross_signature_by_new=sig_by_new,
        )
        # Sign + save the registry with the OLD key — the cool-down window
        # requires the new key's signatures to be rejected until 24h passes.
        path = registry_mod.sign_and_save(
            reg, memory_root, signer_uuid=operator_uuid, signer_fingerprint=old_fpr
        )
    except (crypto.CryptoError, registry_mod.RegistryError) as exc:
        print(f"rotation commit failed: {exc}", file=sys.stderr)
        return 1

    # Update local operator-identity to point at the new key (active writer
    # switches now; receivers still verify against the old key during cool-down).
    save_operator_identity(
        operator_uuid=operator_uuid,
        operator_name=operator_name,
        key_fingerprint=new_fpr,
    )

    print(f"key rotated: old={old_fpr} new={new_fpr}")
    print(f"registry signed (by old key) at {path}.")
    print(
        "24-hour cool-down window active. Receivers will reject writes signed by the new key "
        "until the cool-down expires."
    )
    print("Commit with: git commit -m 'memforge: operator-registry rotate'")
    return 0
