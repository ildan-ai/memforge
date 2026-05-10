"""`memforge operator-registry {add|verify|remove|fresh-start}`.

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
        "operator-registry",
        help="Manage the operator-registry (add / verify / remove / fresh-start).",
    )
    p.add_argument(
        "action",
        choices=["add", "verify", "remove", "fresh-start"],
        help="Operation to perform.",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.add_argument("--operator-uuid", help="Target operator-UUID (for add / remove / fresh-start).")
    p.add_argument("--operator-name", help="Operator name (for add).")
    p.add_argument("--pubkey-fingerprint", help="GPG fingerprint to add (for add / fresh-start).")
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()

    if args.action == "verify":
        try:
            reg = registry_mod.load_registry(memory_root, verify_signature=True)
        except registry_mod.RegistryError as exc:
            print(f"verify FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"operator-registry signature OK ({len(reg['operators'])} operator(s) listed).")
        for op in reg["operators"]:
            active_keys = [k for k in op.get("public_keys", []) if k.get("status", "active") == "active"]
            print(
                f"  - {op['operator_uuid']} {op.get('operator_name','')} "
                f"status={op.get('status','active')} active_keys={len(active_keys)}"
            )
        return 0

    # Mutations require the operator-identity on this machine.
    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(f"operator-identity not loadable: {exc}. Run `memforge init-operator` first.", file=sys.stderr)
        return 1
    signer_uuid = identity["operator_uuid"]
    signer_fpr = identity.get("key_fingerprint")
    if not signer_fpr:
        print("operator-identity missing key_fingerprint. Re-run init-operator.", file=sys.stderr)
        return 1

    try:
        reg = registry_mod.load_registry(memory_root, verify_signature=True)
    except registry_mod.RegistryError as exc:
        print(f"load failed: {exc}", file=sys.stderr)
        return 1

    if args.action == "add":
        if not (args.operator_uuid and args.operator_name and args.pubkey_fingerprint):
            print(
                "add requires --operator-uuid, --operator-name, --pubkey-fingerprint",
                file=sys.stderr,
            )
            return 2
        try:
            pub_b64 = crypto.gpg_export_public_key(args.pubkey_fingerprint)
            algo = _resolve_algo(args.pubkey_fingerprint)
            reg = registry_mod.add_operator(
                reg,
                operator_uuid=args.operator_uuid,
                operator_name=args.operator_name,
                key_id=args.pubkey_fingerprint,
                algo=algo,
                public_material_b64=pub_b64,
            )
        except (crypto.CryptoError, registry_mod.RegistryError) as exc:
            print(f"add failed: {exc}", file=sys.stderr)
            return 1

    elif args.action == "remove":
        if not args.operator_uuid:
            print("remove requires --operator-uuid", file=sys.stderr)
            return 2
        try:
            reg = registry_mod.remove_operator(reg, operator_uuid=args.operator_uuid)
        except registry_mod.RegistryError as exc:
            print(f"remove failed: {exc}", file=sys.stderr)
            return 1

    elif args.action == "fresh-start":
        if not (args.operator_uuid and args.pubkey_fingerprint):
            print("fresh-start requires --operator-uuid + --pubkey-fingerprint", file=sys.stderr)
            return 2
        try:
            pub_b64 = crypto.gpg_export_public_key(args.pubkey_fingerprint)
            algo = _resolve_algo(args.pubkey_fingerprint)
            reg = registry_mod.fresh_start(
                reg,
                operator_uuid=args.operator_uuid,
                new_key_id=args.pubkey_fingerprint,
                new_algo=algo,
                new_public_material_b64=pub_b64,
            )
        except (crypto.CryptoError, registry_mod.RegistryError) as exc:
            print(f"fresh-start failed: {exc}", file=sys.stderr)
            return 1

    path = registry_mod.sign_and_save(reg, memory_root, signer_uuid=signer_uuid, signer_fingerprint=signer_fpr)
    print(f"operator-registry updated + signed at {path}.")
    suffix = {
        "add": "operator-registry add",
        "remove": "operator-registry remove",
        "fresh-start": f"fresh-start {args.operator_uuid}",
    }[args.action]
    print(f"Commit with: git commit -m 'memforge: {suffix}'")
    return 0


def _resolve_algo(fingerprint: str) -> str:
    for k in crypto.gpg_list_secret_keys():
        if k["fingerprint"] == fingerprint:
            return k["algo"]
    # If not in secret keys, check public key list shape (we don't have a
    # separate helper; fall through to default).
    return "gpg-ed25519"
