"""`memforge attest-agent` — issue a signed agent-session attestation.

Spec ref: §"Agent session attestation content scope (v0.5.1+)".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge import agent_session, crypto
from memforge.identity import IdentityError, load_operator_identity


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
        help="Operation to grant beyond default [write, resolve]. May be repeated.",
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
    fpr = identity.get("key_fingerprint")
    if not fpr:
        print("operator-identity missing key_fingerprint.", file=sys.stderr)
        return 1

    if args.agent_pubkey_fingerprint:
        agent_fpr = args.agent_pubkey_fingerprint
    else:
        # Generate an ephemeral key for this session.
        try:
            agent_fpr = crypto.gpg_gen_key_batch(
                name_real=f"memforge-agent-{args.adapter_prefix}",
                name_email=f"agent-{operator_uuid}@memforge.local",
                expire="1",  # 1-day GnuPG expiry; the attestation expires_at is authoritative.
            )
        except crypto.CryptoError as exc:
            print(f"agent key generation failed: {exc}", file=sys.stderr)
            return 1
    try:
        agent_pub_b64 = crypto.gpg_export_public_key(agent_fpr)
    except crypto.CryptoError as exc:
        print(f"could not export agent pubkey: {exc}", file=sys.stderr)
        return 1

    ops = ["write", "resolve"]
    if args.capability:
        for c in args.capability:
            if c not in ops:
                ops.append(c)

    try:
        record = agent_session.build_attestation(
            operator_uuid=operator_uuid,
            agent_pubkey_b64=agent_pub_b64,
            agent_pubkey_algo="gpg-ed25519",
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
