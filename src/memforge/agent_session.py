"""Agent session attestation (v0.5.1+).

Spec ref: §"Agent session attestation content scope (v0.5.1+)" and
integrity invariants 23-25.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

from memforge import crypto
from memforge._security import secure_read_text
from memforge.identity import (
    IdentityError,
    check_fs_mode,
    mint_agent_session_id,
    now_iso,
    validate_agent_session_id,
    write_secure_yaml,
)
from memforge.registry import AGENT_SESSIONS_SUBDIR, REGISTRY_DIRNAME, SEEN_NONCES_SUBDIR


DEFAULT_SESSION_LIFETIME_HOURS = 24
MIN_SESSION_LIFETIME_HOURS = 0.25  # 15 minutes
MAX_SESSION_LIFETIME_HOURS = 7 * 24  # 7 days
DEFAULT_ALLOWED_OPERATIONS = ("write", "resolve")
SUPPORTED_OPERATIONS = ("write", "resolve", "revoke", "registry-edit", "key-rotation", "fresh-start")


class AttestationError(Exception):
    """Fail-closed attestation-layer error per spec §"Cross-cutting fail-closed posture"."""


def attestation_path(memory_root: Path, agent_session_id: str) -> Path:
    validate_agent_session_id(agent_session_id)
    return memory_root / REGISTRY_DIRNAME / AGENT_SESSIONS_SUBDIR / f"{agent_session_id}.yaml"


def _canonical_for_signature(record: dict) -> bytes:
    payload = {k: v for k, v in record.items() if k != "operator_signature"}
    return crypto.canonical_envelope(payload)


def build_attestation(
    *,
    operator_uuid: str,
    agent_pubkey_b64: str,
    agent_pubkey_algo: str,
    capability_memory_roots: Iterable[Path],
    capability_allowed_operations: Iterable[str] = DEFAULT_ALLOWED_OPERATIONS,
    lifetime_hours: float = DEFAULT_SESSION_LIFETIME_HOURS,
    adapter_prefix: str = "cc",
    signer_fingerprint: Optional[str] = None,
) -> dict:
    """Build a signed agent-session attestation record.

    `signer_fingerprint` is the operator's current long-lived GPG key.
    """
    if not (MIN_SESSION_LIFETIME_HOURS <= lifetime_hours <= MAX_SESSION_LIFETIME_HOURS):
        raise AttestationError(
            f"lifetime_hours {lifetime_hours} outside [{MIN_SESSION_LIFETIME_HOURS}, {MAX_SESSION_LIFETIME_HOURS}]"
        )
    crypto.gpg_check_algo_accepted(agent_pubkey_algo)
    if not signer_fingerprint:
        raise AttestationError("signer_fingerprint required to sign attestation")
    issued = datetime.now(timezone.utc).replace(microsecond=0)
    expires = issued + timedelta(hours=lifetime_hours)
    ops = list(capability_allowed_operations)
    for op in ops:
        if op not in SUPPORTED_OPERATIONS:
            raise AttestationError(
                f"capability operation {op!r} not in supported set {SUPPORTED_OPERATIONS}"
            )
    record = {
        "agent_session_id": mint_agent_session_id(adapter_prefix, now=issued),
        "operator_uuid": operator_uuid,
        "agent_pubkey": agent_pubkey_b64,
        "agent_pubkey_algo": agent_pubkey_algo,
        "nonce": secrets.token_hex(32),
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "capability_scope": {
            "memory_roots": [str(Path(p).resolve()) for p in capability_memory_roots],
            "allowed_operations": ops,
        },
    }
    envelope = _canonical_for_signature(record)
    sig_b64 = crypto.gpg_sign_detached(envelope, fingerprint=signer_fingerprint)
    record["operator_signature"] = {
        "algo": "gpg-ed25519",
        "signing_time": now_iso(),
        "value": sig_b64,
    }
    return record


def save_attestation(memory_root: Path, record: dict) -> Path:
    """Persist an attestation record. FS mode 0600 / parent 0700.

    The CLI is responsible for committing the file into git (the attestation
    file is part of the v0.5.1 store state).
    """
    path = attestation_path(memory_root, record["agent_session_id"])
    write_secure_yaml(path, record)
    return path


def load_attestation(memory_root: Path, agent_session_id: str) -> dict:
    """Load + FS-mode-verify an attestation. Raises on tamper / mode / ownership issues.

    Uses secure_read_text for TOCTOU-safe read on POSIX (O_NOFOLLOW +
    fd-fstat verification); equivalent path-level check on Windows.
    """
    path = attestation_path(memory_root, agent_session_id)
    text = secure_read_text(path)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise AttestationError(f"attestation {path} must be a YAML mapping")
    for required in (
        "agent_session_id",
        "operator_uuid",
        "agent_pubkey",
        "agent_pubkey_algo",
        "nonce",
        "issued_at",
        "expires_at",
        "capability_scope",
        "operator_signature",
    ):
        if required not in data:
            raise AttestationError(f"attestation {path} missing required field `{required}`")
    return data


def verify_attestation(record: dict, *, signer_fingerprint: str) -> bool:
    """Verify the operator_signature on an attestation record.

    Returns True on a valid signature against the provided fingerprint.
    Does NOT check expiry or seen-nonce state; callers do that.
    """
    sig = record.get("operator_signature")
    if not isinstance(sig, dict) or "value" not in sig:
        return False
    crypto.gpg_check_algo_accepted(sig.get("algo", ""))
    envelope = _canonical_for_signature(record)
    return crypto.gpg_verify_detached(envelope, signature_b64=sig["value"], expected_fingerprint=signer_fingerprint)


def check_not_expired(record: dict, *, signing_time_iso: Optional[str] = None) -> None:
    """Raise `AttestationError` if `signing_time` is outside `[issued_at, expires_at]`.

    When `signing_time_iso` is None, uses current UTC time (used at
    attestation issuance to verify the freshly-built record).
    """
    when = signing_time_iso or now_iso()
    if when < record["issued_at"]:
        raise AttestationError(
            f"signing_time {when} predates attestation issued_at {record['issued_at']}"
        )
    if when > record["expires_at"]:
        raise AttestationError(
            f"signing_time {when} > attestation expires_at {record['expires_at']}; attestation expired"
        )


def check_scope(record: dict, *, write_path: Path, operation: str) -> None:
    """Raise `AttestationError` if `write_path` / `operation` violate capability_scope."""
    scope = record.get("capability_scope") or {}
    allowed_roots = [Path(p) for p in scope.get("memory_roots") or []]
    resolved = write_path.resolve()
    if not any(_is_within(resolved, root) for root in allowed_roots):
        raise AttestationError(
            f"write path {resolved} outside capability_scope.memory_roots {allowed_roots}"
        )
    ops = scope.get("allowed_operations") or list(DEFAULT_ALLOWED_OPERATIONS)
    if operation not in ops:
        raise AttestationError(
            f"operation {operation!r} not in capability_scope.allowed_operations {ops}"
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def seen_nonce_path(memory_root: Path, operator_uuid: str) -> Path:
    return memory_root / REGISTRY_DIRNAME / SEEN_NONCES_SUBDIR / f"{operator_uuid}.yaml"


def is_nonce_seen(memory_root: Path, operator_uuid: str, nonce: str) -> bool:
    path = seen_nonce_path(memory_root, operator_uuid)
    if not path.is_file():
        return False
    data = yaml.safe_load(secure_read_text(path)) or {}
    return nonce in (data.get("nonces") or {})


def record_seen_nonce(memory_root: Path, operator_uuid: str, nonce: str, *, expires_at: str) -> None:
    """Append a nonce to the per-operator seen-set + garbage-collect expired entries.

    Closes the unbounded-seen-set DoS surfaced by both v0.5.1 panels. Per
    spec §"Seen-nonce set bounding (SHOULD; operational risk)", receivers
    SHOULD bound the seen-nonce set; we GC entries whose `expires_at` has
    already passed (relative to current wall-clock + the default
    backdating clock-skew window of 10 minutes, to stay safe for in-flight
    writes whose signing_time is within the legitimate skew window).
    """
    path = seen_nonce_path(memory_root, operator_uuid)
    data = {}
    if path.is_file():
        data = yaml.safe_load(secure_read_text(path)) or {}
    nonces = data.setdefault("nonces", {})
    nonces[nonce] = {"expires_at": expires_at, "first_seen_at": now_iso()}
    # GC: drop entries whose expires_at + 10 min skew has already passed.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    expired = [n for n, meta in nonces.items() if meta.get("expires_at", "") < cutoff]
    for n in expired:
        del nonces[n]
    write_secure_yaml(path, data)
