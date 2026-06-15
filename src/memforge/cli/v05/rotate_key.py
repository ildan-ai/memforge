"""`memforge rotate-key` — cross-signed key rotation with 24h cool-down.

Spec ref: §"Cross-signed rotation chain" and §"Mandatory cool-down period".
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
from pathlib import Path

from memforge import crypto, registry as registry_mod
from memforge.identity import (
    IdentityError,
    load_operator_identity,
    save_operator_identity,
    write_secure_yaml,
)


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
    p.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip the automatic git commit of the registry change. NOT recommended: "
        "the cool-down expiry is anchored to the rotation commit's author-date, so "
        "committing later than the rotation shortens (or expires) the detection window.",
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

    # rotate-01 (BLOCKER): the rotated key's gen-key uid MUST be unique. The old
    # default `<operator-uuid>@memforge.local` is the SAME uid init-operator
    # --gen-key used, so `gpg --quick-gen-key` REFUSES the duplicate and the
    # default `memforge rotate-key` (no --email) raised CryptoError for every
    # operator who bootstrapped via the primary onboarding path. We derive a
    # unique-by-default uid: `<operator-uuid>+rot<chain-index>-<nonce>@...`. The
    # chain index makes successive rotations human-distinguishable; the short
    # nonce guarantees uniqueness even if a `+rotN` uid was somehow already used.
    if args.email:
        email = args.email
    else:
        next_index = _next_chain_index(reg, operator_uuid)
        nonce = secrets.token_hex(3)
        email = f"{operator_uuid}+rot{next_index}-{nonce}@memforge.local"
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
            memory_root=memory_root,
        )
        # Sign + save the registry with the OLD key — the cool-down window
        # requires the new key's signatures to be rejected until the cool-down
        # passes.
        path = registry_mod.sign_and_save(
            reg, memory_root, signer_uuid=operator_uuid, signer_fingerprint=old_fpr
        )
    except (crypto.CryptoError, registry_mod.RegistryError) as exc:
        print(f"rotation commit failed: {exc}", file=sys.stderr)
        return 1

    # Update local operator-identity to point at the new key (active writer
    # switches now; receivers still verify against the old key during cool-down).
    id_path = save_operator_identity(
        operator_uuid=operator_uuid,
        operator_name=operator_name,
        key_fingerprint=new_fpr,
    )
    # rotate-06: save_operator_identity stamps a fresh `created` /
    # `machine_origin` on every write, which would erase the first-install
    # provenance the operator-UUID is meant to carry across rotations
    # (SPEC.md §"operator-identity": operator-UUID survives key rotation +
    # machine replacement). Restore the originals that we loaded before the
    # rotation, writing back through the same owner-restricted atomic path.
    _preserve_install_provenance(id_path, identity)

    # rotate-03: the cool-down expiry is anchored (frozen) at registry-build
    # time. The spec anchors it to the rotation COMMIT's git author-date, so we
    # commit immediately by default; build-time and commit author-date then
    # coincide (sub-second), keeping the persisted expiry faithful to the
    # commit. --no-commit opts out (and warns) for operators who must stage the
    # commit separately.
    committed = False
    if not args.no_commit:
        try:
            _git_commit_registry(memory_root, path)
            committed = True
        except _GitCommitError as exc:
            print(
                f"WARNING: registry written but auto-commit failed: {exc}\n"
                "  The cool-down expiry is anchored to the rotation commit author-date; "
                "commit NOW to keep the detection window intact:\n"
                "    git add .memforge/operator-registry.yaml && "
                "git commit -m 'memforge: operator-registry rotate'",
                file=sys.stderr,
            )

    print(f"key rotated: old={old_fpr} new={new_fpr}")
    print(f"registry signed (by old key) at {path}.")
    print(
        "24-hour cool-down window active. Receivers will reject writes signed by the new key "
        "until the cool-down expires."
    )
    if committed:
        print("rotation committed (memforge: operator-registry rotate); cool-down anchored to commit author-date.")
    else:
        print("Commit NOW with: git commit -m 'memforge: operator-registry rotate'")
    return 0


def _next_chain_index(reg: dict, operator_uuid: str) -> int:
    """Return the chain_index the next rotated key will receive for this operator.

    Mirrors registry.add_rotated_key's `max(chain_index) + 1` so the gen-key uid
    suffix lines up with the chain index the key is about to be stamped with.
    Returns 1 when the operator has only the initial chain-0 key.

    rotate-chainidx-01: the absent-operator branch returns 0 (NOT 1) to match
    add_rotated_key's `max(..., default=-1) + 1` semantics for an operator with
    no keys, so the uid suffix and the stamped chain_index can never disagree
    (the docstring's 'mirrors' claim holds even in the degenerate absent-operator
    path).
    """
    for op in reg.get("operators", []):
        if op.get("operator_uuid") != operator_uuid:
            continue
        keys = op.get("public_keys", []) or []
        return max((k.get("chain_index", 0) for k in keys), default=-1) + 1
    return 0


def _preserve_install_provenance(id_path: Path, prior_identity: dict) -> None:
    """Restore `created` / `machine_origin` from the pre-rotation identity.

    Reloads the just-written identity file and overwrites only the two
    provenance fields with the values carried before the rotation, then
    re-writes through the owner-restricted atomic path. A missing prior value
    (legacy identity file) is left as the freshly-stamped value.
    """
    prior_created = prior_identity.get("created")
    prior_origin = prior_identity.get("machine_origin")
    if prior_created is None and prior_origin is None:
        return
    try:
        current = load_operator_identity(id_path)
    except IdentityError:
        return
    if prior_created is not None:
        current["created"] = prior_created
    if prior_origin is not None:
        current["machine_origin"] = prior_origin
    write_secure_yaml(id_path, current)


class _GitCommitError(Exception):
    """rotate-key auto-commit failure (non-fatal; operator commits manually)."""


def _git_commit_registry(memory_root: Path, registry_file: Path) -> None:
    """Stage + commit the rotated operator-registry from `memory_root`.

    Commits only the single registry file (single-file scope per integrity
    invariant 19). Raises `_GitCommitError` if the directory is not a git repo
    or the git invocation fails, so the caller can warn rather than crash.
    """
    try:
        rel = registry_file.relative_to(memory_root)
    except ValueError:
        rel = registry_file
    for cmd in (
        ["git", "-C", str(memory_root), "add", str(rel)],
        ["git", "-C", str(memory_root), "commit", "-m", "memforge: operator-registry rotate"],
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise _GitCommitError(
                f"`{' '.join(cmd)}` exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
