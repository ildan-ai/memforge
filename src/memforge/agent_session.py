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
from memforge.sender_sequence import exclusive_file_lock


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
        # Record the signer key's ACTUAL algorithm rather than a hardcoded
        # literal: verify_attestation re-checks sig.algo against the accepted
        # set, so the stored algo must describe the key that actually signed.
        "algo": _resolve_signer_algo(signer_fingerprint),
        "signing_time": now_iso(),
        "value": sig_b64,
    }
    return record


def _resolve_signer_algo(fingerprint: str) -> str:
    """Resolve the signing key's algorithm from the local keyring.

    agent-algo-fallback-01: FAIL CLOSED when the key cannot be resolved to a
    concrete algo rather than stamping a hardcoded `gpg-ed25519`. The stored
    `operator_signature.algo` is re-checked by verify_attestation against the
    accepted-algo gate, so a default-stamped label would describe an assumed
    algorithm, not the key that actually signed (e.g. on a partner deploy with a
    non-default keyring layout the resolve can miss and the label would become
    decorative). We resolve from the secret keyring (the signing key) and from
    the public keyring as a fallback, and raise AttestationError if neither can
    classify the key's algo, so the stored value is always derived from the key
    material.
    """
    norm = fingerprint.replace(" ", "").upper()
    try:
        for k in crypto.gpg_list_secret_keys():
            if (k.get("fingerprint") or "").upper() == norm:
                algo = k.get("algo")
                if algo:
                    return algo
    except crypto.CryptoError:
        pass
    # Fallback: resolve from the public keyring material (covers isolated
    # GNUPGHOME / subkey layouts the secret-key list may not surface cleanly).
    try:
        pub_algo = crypto.gpg_resolve_public_algo(fingerprint)
    except crypto.CryptoError:
        pub_algo = None
    if pub_algo:
        return pub_algo
    raise AttestationError(
        f"could not resolve the signing algorithm of key {fingerprint} from the gpg "
        "keyring; refusing to stamp an assumed algo on the attestation "
        "(agent-algo-fallback-01 fail-closed). Ensure the signing key is present."
    )


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


def _parse_iso_aware(value: str, *, field: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepting a trailing `Z`) to an aware datetime.

    registry-03: `check_not_expired` must not do raw lexicographic string
    comparison. A caller-supplied `+00:00`-form signing_time sorts below an
    equal-instant `Z`-form issued_at/expires_at ('+' (0x2B) < 'Z' (0x5A)),
    silently misjudging expiry. Parse both operands and fail closed
    (`AttestationError`) on an unparseable input rather than string-comparing.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise AttestationError(
            f"unparseable ISO-8601 timestamp for {field}: {value!r}. Fail-closed."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def check_not_expired(record: dict, *, signing_time_iso: Optional[str] = None) -> None:
    """Raise `AttestationError` if `signing_time` is outside `[issued_at, expires_at]`.

    When `signing_time_iso` is None, uses current UTC time (used at
    attestation issuance to verify the freshly-built record).

    registry-03: comparisons run on parsed aware datetimes (timezone-form
    insensitive), not raw string compares; fails closed on unparseable input.
    """
    when = _parse_iso_aware(signing_time_iso or now_iso(), field="signing_time")
    issued = _parse_iso_aware(record["issued_at"], field="issued_at")
    expires = _parse_iso_aware(record["expires_at"], field="expires_at")
    if when < issued:
        raise AttestationError(
            f"signing_time {when.isoformat()} predates attestation issued_at {record['issued_at']}"
        )
    if when > expires:
        raise AttestationError(
            f"signing_time {when.isoformat()} > attestation expires_at {record['expires_at']}; attestation expired"
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
    """Read-only seen-nonce check (NOT atomic with recording).

    nonce-replay-01: this read happens OUTSIDE the lock that guards
    record_seen_nonce, so a check-then-record across the lock gap leaves a
    concurrent-replay TOCTOU window (two verifications of the same captured
    attestation both observe 'not seen' before either records). Use
    `claim_nonce` for the replay defense; it performs the check AND the record
    atomically under one exclusive lock. This helper remains only for read-only
    inspection / diagnostics where atomicity is not required.
    """
    path = seen_nonce_path(memory_root, operator_uuid)
    if not path.is_file():
        return False
    data = yaml.safe_load(secure_read_text(path)) or {}
    return nonce in (data.get("nonces") or {})


def claim_nonce(memory_root: Path, operator_uuid: str, nonce: str, *, expires_at: str) -> bool:
    """Atomically check-and-record a nonce. Returns True if newly claimed.

    nonce-replay-01: the replay defense MUST be atomic. This reads the
    seen-nonce set, records the nonce, and (on a fresh nonce) writes the updated
    set ALL while holding the same exclusive advisory lock, so two concurrent
    verifications of the same captured attestation cannot both observe
    'not seen'. Returns False (already seen -> reject the write as a replay) when
    the nonce is already present; returns True (newly recorded -> admit) when it
    was not. Callers branch on this single result instead of calling
    `is_nonce_seen` then `record_seen_nonce` across a lock gap.
    """
    path = seen_nonce_path(memory_root, operator_uuid)
    with exclusive_file_lock(path):
        data = {}
        if path.is_file():
            data = yaml.safe_load(secure_read_text(path)) or {}
        nonces = data.setdefault("nonces", {})
        if nonce in nonces:
            return False
        nonces[nonce] = {"expires_at": expires_at, "first_seen_at": now_iso()}
        # GC expired entries on the same write (mirrors record_seen_nonce).
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        expired = [n for n, meta in nonces.items() if meta.get("expires_at", "") < cutoff]
        for n in expired:
            del nonces[n]
        write_secure_yaml(path, data)
        return True


def record_seen_nonce(memory_root: Path, operator_uuid: str, nonce: str, *, expires_at: str) -> None:
    """Append a nonce to the per-operator seen-set + garbage-collect expired entries.

    Closes the unbounded-seen-set DoS surfaced by both v0.5.1 panels. Per
    spec §"Seen-nonce set bounding (SHOULD; operational risk)", receivers
    SHOULD bound the seen-nonce set; we GC entries whose `expires_at` has
    already passed (relative to current wall-clock + the default
    backdating clock-skew window of 10 minutes, to stay safe for in-flight
    writes whose signing_time is within the legitimate skew window).

    agent-session-01: the load -> mutate -> write cycle is serialized with the
    same exclusive advisory lock as the sender-sequence increment, so two
    concurrent attestation verifications for the same operator_uuid cannot both
    read the prior nonce set and have the second write clobber the first (which
    would drop a just-recorded nonce and re-open a replay window). is_nonce_seen
    is the replay defense, so a lost-update there is a real concurrency gap.
    """
    path = seen_nonce_path(memory_root, operator_uuid)
    with exclusive_file_lock(path):
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
