"""Sender-sequence file ops (v0.5.0+).

Spec ref: §"Sender-uid format (MUST)", §"Sender-sequence + signed
checkpoints (MUST)", integrity invariant 20.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from memforge import crypto
from memforge.identity import IdentityError, check_fs_mode, now_iso, write_secure_yaml
from memforge.registry import SENDER_SEQUENCE_SUBDIR, REGISTRY_DIRNAME


SENDER_UID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}:[0-9a-f]{64}$")
CHECKPOINT_INTERVAL_SEQUENCES = 100
CHECKPOINT_INTERVAL_HOURS = 24


def mint_sender_uid(operator_uuid: str) -> str:
    """Generate a `<operator-uuid>:<32-byte-hex>` sender_uid."""
    suffix = secrets.token_hex(32)
    candidate = f"{operator_uuid}:{suffix}"
    if not SENDER_UID_RE.match(candidate):
        raise IdentityError(f"minted sender-uid failed format check: {candidate}")
    return candidate


def validate_sender_uid(sender_uid: str) -> None:
    """Raise `IdentityError` if `sender_uid` is malformed per §"Sender-uid format"."""
    if not SENDER_UID_RE.match(sender_uid):
        raise IdentityError(
            f"sender-uid {sender_uid!r} does not match `<operator-uuid>:<32-byte-hex>`"
        )


def sender_sequence_path(memory_root: Path, sender_uid: str) -> Path:
    return memory_root / REGISTRY_DIRNAME / SENDER_SEQUENCE_SUBDIR / f"{sender_uid}.yaml"


def init_sender_sequence(memory_root: Path, *, sender_uid: str, operator_uuid: str) -> Path:
    """Create a new sender-sequence file. FS mode 0600 / parent 0700."""
    validate_sender_uid(sender_uid)
    data = {
        "sender_uid": sender_uid,
        "operator_uuid": operator_uuid,
        "created": now_iso(),
        "current_sequence": 0,
        "checkpoints": [],
    }
    path = sender_sequence_path(memory_root, sender_uid)
    write_secure_yaml(path, data)
    return path


def load_sender_sequence(memory_root: Path, sender_uid: str) -> dict:
    """Load a sender-sequence file after FS-mode + ownership verification.

    Raises `IdentityError` (fail-closed) per integrity invariant 20 if
    modes don't match or ownership doesn't match the effective uid.
    """
    path = sender_sequence_path(memory_root, sender_uid)
    check_fs_mode(path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise IdentityError(f"sender-sequence {path} must be a YAML mapping")
    return data


def increment_sequence(memory_root: Path, sender_uid: str) -> int:
    """Atomically increment the per-sender sequence. Returns the new sequence.

    Detects uint64 overflow (per invariant 20) and raises `IdentityError`.
    """
    data = load_sender_sequence(memory_root, sender_uid)
    current = int(data.get("current_sequence", 0))
    new_seq = current + 1
    if new_seq >= (1 << 64):
        raise IdentityError(
            f"sender-sequence overflow for {sender_uid!r}; rotate to a new sender-uid"
        )
    data["current_sequence"] = new_seq
    path = sender_sequence_path(memory_root, sender_uid)
    write_secure_yaml(path, data)
    return new_seq


def should_publish_checkpoint(data: dict) -> bool:
    """Return True if a fresh signed checkpoint is due per §"Sender-sequence".

    Trigger: every 100 sequences OR 24 hours (whichever first).
    """
    current = int(data.get("current_sequence", 0))
    checkpoints = data.get("checkpoints", []) or []
    if not checkpoints:
        return current >= 1
    last = checkpoints[-1]
    last_seq = int(last.get("sequence", 0))
    if current - last_seq >= CHECKPOINT_INTERVAL_SEQUENCES:
        return True
    try:
        last_ts = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return True
    elapsed = datetime.now(timezone.utc) - last_ts
    return elapsed >= timedelta(hours=CHECKPOINT_INTERVAL_HOURS)


def publish_checkpoint(memory_root: Path, sender_uid: str, *, signer_fingerprint: str) -> dict:
    """Append a signed checkpoint to the sender-sequence file. Returns the new entry."""
    data = load_sender_sequence(memory_root, sender_uid)
    current = int(data.get("current_sequence", 0))
    operator_uuid = data["operator_uuid"]
    timestamp = now_iso()
    envelope = crypto.canonical_envelope(
        {
            "sender_uid": sender_uid,
            "sequence": current,
            "timestamp": timestamp,
            "operator_uuid": operator_uuid,
        }
    )
    sig = crypto.gpg_sign_detached(envelope, fingerprint=signer_fingerprint)
    entry = {"sequence": current, "timestamp": timestamp, "signature": sig}
    data.setdefault("checkpoints", []).append(entry)
    path = sender_sequence_path(memory_root, sender_uid)
    write_secure_yaml(path, data)
    return entry
