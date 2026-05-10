"""Revocation event builder + revocation-set walker.

Spec ref: §"Key lifecycle + revocation (v0.5.0+)" and integrity invariant 22.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from memforge import crypto, registry as registry_mod
from memforge.identity import generate_uuidv7, now_iso


REVOKE_PREFIX = "memforge: revoke "
SNAPSHOT_PREFIX = "memforge: revocation-snapshot "
FRESH_START_PREFIX = "memforge: fresh-start "
KEY_COMPROMISE_PREFIX = "memforge: key-compromise "
REGISTRY_PREFIX = "memforge: operator-registry"


class RevocationError(Exception):
    """Fail-closed revocation-layer error."""


def build_revoke_body(
    *,
    key_id: str,
    reason: str,
    revoked_by_uuid: str,
    signer_fingerprint: str,
    revoked_at: Optional[str] = None,
) -> tuple[str, dict]:
    """Build a signed revocation commit body. Returns (commit_message, body_dict).

    The commit message starts with `memforge: revoke <key_id>` (invariant 22).
    The body is a YAML mapping with `revocation_signature` covering the
    canonical envelope of the rest of the body.
    """
    if len(reason) < 8:
        raise RevocationError(
            f"revocation reason must be >= 8 characters; got {len(reason)} chars"
        )
    body = {
        "key_id": key_id,
        "revoked_at": revoked_at or now_iso(),
        "reason": reason,
        "revoked_by": revoked_by_uuid,
        "revocation_uid": generate_uuidv7(),
    }
    envelope = crypto.canonical_envelope(body)
    body["revocation_signature"] = crypto.gpg_sign_detached(envelope, fingerprint=signer_fingerprint)
    commit_message = f"{REVOKE_PREFIX}{key_id}\n\n{yaml.safe_dump(body, sort_keys=False)}"
    return commit_message, body


def verify_revoke_body(body: dict, *, expected_signer_fingerprint: str) -> bool:
    """Verify a revoke commit body's signature against the expected signer fingerprint.

    Returns True if the signature is valid. False otherwise (no raise).
    """
    sig = body.get("revocation_signature")
    if not sig:
        return False
    payload = {k: v for k, v in body.items() if k != "revocation_signature"}
    envelope = crypto.canonical_envelope(payload)
    return crypto.gpg_verify_detached(envelope, signature_b64=sig, expected_fingerprint=expected_signer_fingerprint)


def parse_revoke_commit_body(commit_msg: str) -> Optional[dict]:
    """Extract the YAML body from a `memforge: revoke ...` commit message.

    Returns the parsed dict, or None if the message doesn't match the
    expected shape (caller treats as Tier 2 BLOCKER per integrity invariant
    22 if it appears to modify revocation state).
    """
    if not commit_msg.startswith(REVOKE_PREFIX):
        return None
    parts = commit_msg.split("\n", 2)
    if len(parts) < 3:
        return None
    try:
        body = yaml.safe_load(parts[2])
    except yaml.YAMLError:
        return None
    if not isinstance(body, dict):
        return None
    for required in ("key_id", "revoked_at", "reason", "revoked_by", "revocation_uid"):
        if required not in body:
            return None
    return body


def walk_revocation_set(repo_path: Path, *, since_commit: Optional[str] = None) -> dict[str, dict]:
    """Walk git history collecting `memforge: revoke ...` commit bodies.

    Returns a dict keyed by `key_id` whose values are the parsed bodies.
    When the same key_id appears multiple times, the earliest revocation
    (by `revoked_at`) wins (most permissive for the receiver: once revoked,
    always revoked).

    Verification of `revocation_signature` is the caller's responsibility
    (signature verification needs the operator-registry to resolve signer
    pubkeys, which is a registry-layer call).
    """
    rev_set: dict[str, dict] = {}
    args = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--format=%H%x00%B%x00END%x00",
    ]
    if since_commit:
        args.append(f"{since_commit}..HEAD")
    try:
        out = subprocess.check_output(args, text=True)
    except subprocess.CalledProcessError as exc:
        raise RevocationError(f"git log failed: {exc.output}") from exc
    # Parse the null-delimited commit-message blocks.
    for chunk in out.split("\x00END\x00\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("\x00", 1)
        if len(parts) < 2:
            continue
        commit_hash = parts[0].strip()
        message = parts[1]
        body = parse_revoke_commit_body(message)
        if body is None:
            continue
        body["_commit_hash"] = commit_hash
        existing = rev_set.get(body["key_id"])
        if existing is None or body["revoked_at"] < existing["revoked_at"]:
            rev_set[body["key_id"]] = body
    return rev_set


def is_key_revoked_at(rev_set: dict[str, dict], key_id: str, signing_time_iso: str) -> bool:
    """Return True if `key_id` is revoked as of `signing_time_iso`.

    Per §"Signing-time-aware verification" rule 3: the signing key was NOT
    revoked at `signature.signing_time`. So the function returns True only
    when `revoked_at <= signing_time`.
    """
    entry = rev_set.get(key_id)
    if entry is None:
        return False
    return entry["revoked_at"] <= signing_time_iso


def build_revocation_snapshot_body(rev_set: dict[str, dict], *, signer_fingerprint: str) -> tuple[str, dict]:
    """Build a `memforge: revocation-snapshot <hash>` commit body.

    Returns (commit_message, body_dict) where body is YAML-serializable.
    """
    payload = {
        "snapshot_time": now_iso(),
        "revocations": [
            {
                "key_id": v["key_id"],
                "revoked_at": v["revoked_at"],
                "revoked_by": v["revoked_by"],
                "revocation_uid": v["revocation_uid"],
            }
            for v in rev_set.values()
        ],
    }
    envelope = crypto.canonical_envelope(payload)
    snap_hash = crypto.sha256_hex(envelope)
    body = dict(payload)
    body["snapshot_hash"] = snap_hash
    body["snapshot_signature"] = crypto.gpg_sign_detached(envelope, fingerprint=signer_fingerprint)
    commit_message = f"{SNAPSHOT_PREFIX}{snap_hash}\n\n{yaml.safe_dump(body, sort_keys=False)}"
    return commit_message, body


def find_revocation_snapshot_commit(repo_path: Path) -> Optional[str]:
    """Return the most recent revocation-snapshot commit hash, or None.

    Per §"Revocation snapshot mechanism": adapter walks git history from
    the latest snapshot forward to bound O(N) cold-start cost.
    """
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_path),
                "log",
                "-n",
                "1",
                "--grep",
                "^memforge: revocation-snapshot",
                "--format=%H",
            ],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    return out or None
