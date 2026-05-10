"""`memforge recovery-init` — install the recovery-secret + anchor SHA256 in registry.

Spec ref: §"Recovery-secret filesystem mode (normative)", §"Recovery-secret
content integrity (MUST)".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import recovery, registry as registry_mod
from memforge.identity import IdentityError, load_operator_identity


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "recovery-init",
        help="Generate ~/.memforge/recovery-secret.bin + anchor its SHA256 in the signed operator-registry.",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root whose operator-registry should anchor the SHA256 (default: cwd).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Regenerate the recovery-secret even if one exists (destructive — old secret can no longer decrypt key-compromise events).",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(f"operator-identity not loadable: {exc}. Run `memforge init-operator` first.", file=sys.stderr)
        return 1
    operator_uuid = identity["operator_uuid"]
    fpr = identity.get("key_fingerprint")
    if not fpr:
        print("operator-identity missing key_fingerprint.", file=sys.stderr)
        return 1

    memory_root = Path(args.memory_root).resolve()
    try:
        reg = registry_mod.load_registry(memory_root, verify_signature=True)
    except registry_mod.RegistryError as exc:
        print(f"registry load failed: {exc}. Run `memforge init-store` first.", file=sys.stderr)
        return 1

    try:
        path, sha = recovery.init_recovery_secret(force=args.force)
        reg = recovery.anchor_secret_hash_in_registry(reg, operator_uuid=operator_uuid, sha256_hex_str=sha)
        registry_mod.sign_and_save(reg, memory_root, signer_uuid=operator_uuid, signer_fingerprint=fpr)
    except recovery.RecoveryError as exc:
        print(f"recovery-init failed: {exc}", file=sys.stderr)
        return 1

    print(f"recovery-secret installed at {path} (mode 0600).")
    print(f"SHA256 anchored in operator-registry: {sha}")
    print()
    print("CRITICAL: back up the secret to OFFLINE physical media (USB key in a safe / printed QR / etc.).")
    print("After backing it up, run `memforge recovery-backup-confirm` to acknowledge and unlock v0.5+ writes.")
    print()
    print("Commit the registry change:")
    print("  git commit -m 'memforge: operator-registry recovery-anchor'")
    return 0
