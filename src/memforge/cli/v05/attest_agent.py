"""`memforge attest-agent` — issue a signed agent-session attestation.

Spec ref: §"Agent session attestation content scope (v0.5.1+)".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import agent_session, crypto
from memforge.identity import IdentityError, load_operator_identity


# Operations that grant elevated authority beyond the safe default
# [write, resolve]. Per SPEC.md §"Capability-scope defaults via reference CLI",
# adapters SHOULD warn loudly + require operator confirmation before issuing an
# attestation that grants any of these.
ELEVATED_OPERATIONS = frozenset({"revoke", "registry-edit", "key-rotation", "fresh-start"})


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "attest-agent",
        help="Issue a signed agent-session attestation (nonce + expires_at + capability_scope).",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root where the attestation is persisted (default: cwd).",
    )
    p.add_argument(
        "--adapter-prefix",
        default="cc",
        help="Adapter prefix for the agent-session-id (cc, cursor, aider, etc.).",
    )
    p.add_argument(
        "--agent-pubkey-fingerprint",
        help="Pre-existing GPG fingerprint for the agent's ephemeral key. "
        "If omitted, generates a fresh ephemeral Ed25519 keypair.",
    )
    p.add_argument(
        "--lifetime-hours",
        type=float,
        default=agent_session.DEFAULT_SESSION_LIFETIME_HOURS,
        help="Attestation lifetime in hours (default 24; floor 0.25; ceiling 168).",
    )
    p.add_argument(
        "--capability",
        action="append",
        default=None,
        help="Operation to grant beyond default [write, resolve]. May be repeated. "
        "Elevated operations (revoke, registry-edit, key-rotation, fresh-start) "
        "require interactive confirmation OR --yes-i-understand-elevated.",
    )
    p.add_argument(
        "--yes-i-understand-elevated",
        action="store_true",
        help="Non-interactive confirmation that this attestation may grant elevated "
        "operations (revoke / registry-edit / key-rotation / fresh-start). Required "
        "for those scopes when stdin is not a TTY.",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    # dispatch-01: exit-code convention -> 2 = usage / precondition / operator-
    # must-fix-input; 1 = operational / crypto / io failure. The identity-
    # missing + missing-key-fingerprint + elevated-not-confirmed guards are
    # precondition/usage-class and now return 2 (was 1), matching init_operator /
    # init_store / operator_registry so partner automation can rely on the
    # contract across commands.
    try:
        identity = load_operator_identity()
    except IdentityError as exc:
        print(f"operator-identity not loadable: {exc}", file=sys.stderr)
        return 2
    operator_uuid = identity["operator_uuid"]
    fpr = identity.get("key_fingerprint")
    if not fpr:
        print("operator-identity missing key_fingerprint.", file=sys.stderr)
        return 2

    # Build the requested capability set first so the elevated-scope gate can
    # run BEFORE any key generation / signing side effect.
    ops = ["write", "resolve"]
    if args.capability:
        for c in args.capability:
            if c not in ops:
                ops.append(c)

    elevated = [op for op in ops if op in ELEVATED_OPERATIONS]
    if elevated and not _confirm_elevated(elevated, assume_yes=args.yes_i_understand_elevated):
        # dispatch-01: precondition/usage-class (operator must re-run with
        # confirmation) -> exit 2, not 1.
        print("attest-agent aborted: elevated capabilities not confirmed.", file=sys.stderr)
        return 2

    if args.agent_pubkey_fingerprint:
        agent_fpr = args.agent_pubkey_fingerprint
        # Bring-your-own-key path: resolve the key's ACTUAL algo from the
        # keyring instead of assuming ed25519. Recording the wrong algo here
        # would make write-verification (which reads key+algo from the
        # attestation per SPEC.md §"Agent write verification") use the wrong
        # algorithm. Fail closed if the algo cannot be determined.
        agent_algo = _resolve_agent_algo(agent_fpr)
        if agent_algo is None:
            print(
                f"could not resolve algorithm for agent key {agent_fpr}. "
                "Import the key (`gpg --import`) so it is in the local keyring, "
                "or use --gen-key (omit --agent-pubkey-fingerprint).",
                file=sys.stderr,
            )
            return 1
    else:
        # Generate an ephemeral key for this session. gen-key always produces
        # Ed25519 (crypto.gpg_gen_key_batch), so the algo is known.
        try:
            agent_fpr = crypto.gpg_gen_key_batch(
                name_real=f"memforge-agent-{args.adapter_prefix}",
                name_email=f"agent-{operator_uuid}@memforge.local",
                expire="1",  # 1-day GnuPG expiry; the attestation expires_at is authoritative.
            )
        except crypto.CryptoError as exc:
            print(f"agent key generation failed: {exc}", file=sys.stderr)
            return 1
        agent_algo = "gpg-ed25519"
    try:
        agent_pub_b64 = crypto.gpg_export_public_key(agent_fpr)
    except crypto.CryptoError as exc:
        print(f"could not export agent pubkey: {exc}", file=sys.stderr)
        return 1

    try:
        record = agent_session.build_attestation(
            operator_uuid=operator_uuid,
            agent_pubkey_b64=agent_pub_b64,
            agent_pubkey_algo=agent_algo,
            capability_memory_roots=[memory_root],
            capability_allowed_operations=ops,
            lifetime_hours=args.lifetime_hours,
            adapter_prefix=args.adapter_prefix,
            signer_fingerprint=fpr,
        )
        path = agent_session.save_attestation(memory_root, record)
    except agent_session.AttestationError as exc:
        print(f"attest-agent failed: {exc}", file=sys.stderr)
        return 1

    print(f"agent-session-id: {record['agent_session_id']}")
    print(f"agent fingerprint: {agent_fpr}")
    print(f"attestation file: {path} (mode 0600)")
    print(f"issued_at:  {record['issued_at']}")
    print(f"expires_at: {record['expires_at']}")
    print(f"capabilities: {record['capability_scope']['allowed_operations']}")
    print()
    print("Commit the attestation:")
    print(f"  git add {path}")
    print(f"  git commit -m 'memforge: attest-agent {record['agent_session_id']}'")
    return 0


def _resolve_agent_algo(fingerprint: str) -> str | None:
    """Resolve the agent key's actual algo from the local keyring, or None.

    Fail-closed: returns None if the key is not present in the secret keyring,
    so the caller refuses to stamp a guessed algorithm onto the attestation.
    """
    try:
        keys = crypto.gpg_list_secret_keys()
    except crypto.CryptoError:
        return None
    norm = fingerprint.replace(" ", "").upper()
    for k in keys:
        if (k.get("fingerprint") or "").upper() == norm:
            algo = k.get("algo")
            if algo:
                return algo
    return None


def _confirm_elevated(elevated_ops: list[str], *, assume_yes: bool) -> bool:
    """Warn loudly + require operator confirmation for elevated capabilities.

    Per SPEC.md §"Capability-scope defaults via reference CLI": adapters SHOULD
    warn loudly + require operator confirmation before granting revoke /
    registry-edit / key-rotation / fresh-start. The warning ALWAYS prints to
    stderr; confirmation comes from `--yes-i-understand-elevated` (non-
    interactive) OR an interactive y/N prompt. When stdin is not a TTY and the
    flag was not passed, we fail closed (refuse) rather than silently granting.
    """
    print(
        "WARNING: this attestation grants ELEVATED capabilities: "
        + ", ".join(sorted(elevated_ops))
        + ".\n  An agent holding this attestation can act with operator-level "
        "authority for those operations\n  (revoke keys, edit the operator-registry, "
        "rotate keys, or fresh-start the chain).",
        file=sys.stderr,
    )
    if assume_yes:
        print("  Confirmed via --yes-i-understand-elevated.", file=sys.stderr)
        return True
    if not sys.stdin.isatty():
        print(
            "  Refusing to grant elevated capabilities non-interactively. "
            "Re-run with --yes-i-understand-elevated to confirm.",
            file=sys.stderr,
        )
        return False
    try:
        answer = input("  Grant these elevated capabilities? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")
