"""`memforge init-store` — initialize an operator-registry in a memory-root.

Spec ref: §"Operator-registry file (per-memory-root)".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import crypto, registry as registry_mod
from memforge.identity import IdentityError, load_operator_identity


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "init-store",
        help="Bootstrap the .memforge/ folder in a memory-root + create a signed operator-registry.",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd). The .memforge/ subfolder is created here.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing operator-registry. Destructive; review before use.",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    if not memory_root.is_dir():
        print(f"memory-root {memory_root} is not a directory.", file=sys.stderr)
        return 2

    registry_file = registry_mod.registry_path(memory_root)
    if registry_file.exists() and not args.force:
        print(
            f"operator-registry already exists at {registry_file}. "
            "Pass --force to overwrite (destructive).",
            file=sys.stderr,
        )
        return 2

    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(
            f"operator-identity not loadable: {exc}\n"
            "Run `memforge init-operator` first.",
            file=sys.stderr,
        )
        return 1

    operator_uuid = identity["operator_uuid"]
    operator_name = identity.get("operator_name", "")
    fingerprint = identity.get("key_fingerprint")
    if not fingerprint:
        print(
            "operator-identity file has no `key_fingerprint` field. "
            "Re-run `memforge init-operator --gpg-fingerprint <fpr>` to bind a signing key.",
            file=sys.stderr,
        )
        return 1

    try:
        pubkey_b64 = crypto.gpg_export_public_key(fingerprint)
        # Determine algo from the local keyring.
        algo = "gpg-ed25519"
        for k in crypto.gpg_list_secret_keys():
            if k["fingerprint"] == fingerprint:
                algo = k["algo"]
                break
        crypto.gpg_check_algo_accepted(algo)
        reg = registry_mod.init_registry(
            operator_uuid=operator_uuid,
            operator_name=operator_name,
            key_id=fingerprint,
            algo=algo,
            public_material_b64=pubkey_b64,
        )
        path = registry_mod.sign_and_save(reg, memory_root, signer_uuid=operator_uuid, signer_fingerprint=fingerprint)
    except (crypto.CryptoError, registry_mod.RegistryError) as exc:
        print(f"init-store failed: {exc}", file=sys.stderr)
        return 1

    print(f"operator-registry created at {path}")
    print(f"signed by operator {operator_uuid} via key {fingerprint}.")
    print()
    print("Next step:")
    print(f"  cd {memory_root} && git add .memforge/operator-registry.yaml")
    print('  git commit -m "memforge: operator-registry init"')
    return 0
