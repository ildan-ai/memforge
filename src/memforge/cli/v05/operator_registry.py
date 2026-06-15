"""`memforge operator-registry {add|verify|remove|fresh-start}`.

Spec ref: §"Operator-registry file (per-memory-root)".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from memforge import crypto, registry as registry_mod
from memforge.identity import IdentityError, load_operator_identity


# opreg-01: same 40-char hex primary-fingerprint contract init-operator enforces
# (init_operator._FULL_FPR_RE). A short id / typo / wrong-case value must never
# be exported + persisted as the registry `key_id`, which later signature
# lookups match on EXACTLY (registry._find_key_entry).
_FULL_FPR_RE = re.compile(r"^[0-9A-F]{40}$")


class _FingerprintError(Exception):
    """Operator-supplied fingerprint failed the canonical-fingerprint validation."""


def _validate_and_resolve_fingerprint(supplied: str) -> str:
    """Validate a 40-char hex fingerprint + resolve the keyring's CANONICAL value.

    Mirrors the init-operator initop-04 discipline for `operator-registry add`
    and `fresh-start`: require a full 40-char hex fingerprint (reject short ids
    with the same actionable message init-operator gives), then resolve the
    keyring's canonical primary fingerprint so the persisted `key_id` is the
    authoritative value (correct case, primary not subkey), not the user-typed
    string. Raises `_FingerprintError` (with an operator-facing message) on a
    bad shape or a key absent from the keyring.
    """
    normalized = supplied.replace(" ", "").upper()
    if not _FULL_FPR_RE.match(normalized):
        raise _FingerprintError(
            f"fingerprint {supplied!r} is not a 40-character hex primary fingerprint. "
            "Pass the full fingerprint (`gpg --fingerprint <id>`), not a short key id."
        )
    canonical = crypto.gpg_resolve_public_fingerprint(normalized)
    if canonical is None:
        raise _FingerprintError(
            f"fingerprint {normalized} not found in the local gpg keyring (could not "
            "resolve a canonical 40-char fingerprint). Import the public key first "
            "(`gpg --import`)."
        )
    return canonical


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
            canonical_fpr = _validate_and_resolve_fingerprint(args.pubkey_fingerprint)
        except _FingerprintError as exc:
            print(f"add failed: {exc}", file=sys.stderr)
            return 2
        try:
            pub_b64 = crypto.gpg_export_public_key(canonical_fpr)
            algo = _resolve_algo(canonical_fpr)
            reg = registry_mod.add_operator(
                reg,
                operator_uuid=args.operator_uuid,
                operator_name=args.operator_name,
                key_id=canonical_fpr,
                algo=algo,
                public_material_b64=pub_b64,
            )
        except (crypto.CryptoError, registry_mod.RegistryError, _FingerprintError) as exc:
            print(f"add failed: {exc}", file=sys.stderr)
            return 1

    elif args.action == "remove":
        if not args.operator_uuid:
            print("remove requires --operator-uuid", file=sys.stderr)
            return 2
        # registry-01 (BLOCKER): refuse to supersede the operator whose key is
        # about to sign the resulting registry. remove_operator sets the target
        # operator AND its keys to 'superseded'; sign_and_save then signs with
        # this machine's identity key (signer_uuid/signer_fpr). If the target IS
        # the signer, the freshly-superseded key signs the registry and the very
        # next load fails closed forever (bricked store). Self-removal must go
        # through a fresh-start or an alternate active signer, not a self-
        # supersede-then-self-sign.
        if args.operator_uuid == signer_uuid:
            print(
                "remove refused: you cannot remove the operator whose key signs this "
                "registry (you would supersede the signing key and brick the store on "
                "next load). Have another active operator sign the removal, or use "
                "`fresh-start` to break the chain.",
                file=sys.stderr,
            )
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
            canonical_fpr = _validate_and_resolve_fingerprint(args.pubkey_fingerprint)
        except _FingerprintError as exc:
            print(f"fresh-start failed: {exc}", file=sys.stderr)
            return 2
        try:
            pub_b64 = crypto.gpg_export_public_key(canonical_fpr)
            algo = _resolve_algo(canonical_fpr)
            reg = registry_mod.fresh_start(
                reg,
                operator_uuid=args.operator_uuid,
                new_key_id=canonical_fpr,
                new_algo=algo,
                new_public_material_b64=pub_b64,
            )
        except (crypto.CryptoError, registry_mod.RegistryError, _FingerprintError) as exc:
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
    """Resolve the algo label of `fingerprint` from the key material itself.

    opreg-algo-01: `add` / `fresh-start` register ANOTHER operator's PUBLIC key
    (imported via `gpg --import`), which is NOT in the local SECRET keyring. The
    old secret-keyring-only loop therefore always fell through to a hardcoded
    `gpg-ed25519`, persisting the WRONG algo for a non-Ed25519 (e.g. RSA-4096)
    operator key AND letting a sub-floor RSA key slip past add_operator's
    gpg_check_algo_accepted size gate (which trusts the recorded label). We
    resolve from the public+secret keyring and FAIL CLOSED if the algo cannot be
    classified rather than default-stamping ed25519.
    """
    # Prefer the locally-generated secret key's label when present (exact match).
    for k in crypto.gpg_list_secret_keys():
        if k["fingerprint"] == fingerprint:
            return k["algo"]
    # Imported public key (the add / fresh-start trust-bootstrap path): resolve
    # the real algo from the public keyring. Fail closed on an unclassifiable key.
    algo = crypto.gpg_resolve_public_algo(fingerprint)
    if algo is None:
        raise _FingerprintError(
            f"could not classify the signature algorithm of key {fingerprint} from the "
            "gpg keyring (refusing to default-stamp gpg-ed25519 for a key whose algo is "
            "unknown). Ensure the public key is imported (`gpg --import`)."
        )
    return algo
