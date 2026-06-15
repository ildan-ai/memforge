"""Sender-sequence file ops (v0.5.0+).

Spec ref: §"Sender-uid format (MUST)", §"Sender-sequence + signed
checkpoints (MUST)", integrity invariant 20.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import yaml

from memforge import crypto
from memforge._security import secure_read_text
from memforge.identity import IdentityError, check_fs_mode, now_iso, write_secure_yaml
from memforge.registry import SENDER_SEQUENCE_SUBDIR, REGISTRY_DIRNAME

try:
    import fcntl  # POSIX advisory file locking
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def exclusive_file_lock(target_path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock across a read-modify-write on `target_path`.

    seq-01 / agent-session-01: the per-sender sequence increment and the
    seen-nonce record are read-modify-write cycles spanning a load + a separate
    atomic write. The atomic write closes the WRITE race, but not the RMW race:
    two concurrent same-uid writers can both read state N and both persist N+1,
    minting duplicate sequence numbers (defeating the per-sender monotonic
    anti-replay floor in SPEC.md:804) or clobbering a just-recorded nonce
    (re-opening a replay window). We serialize the whole cycle on a `.lock`
    sibling file via POSIX `flock` (LOCK_EX). On a platform without `fcntl`
    (Windows) the lock is a best-effort no-op and the caller relies on the
    single-writer-per-sender_uid assumption documented on the public helpers.
    """
    if fcntl is None:
        # No advisory locking available (Windows). Single-writer-per-sender_uid
        # is the documented assumption; proceed without a lock.
        yield
        return
    lock_path = target_path.with_suffix(target_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


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
    """Load a sender-sequence file with TOCTOU-safe read + ownership verification.

    Raises `IdentityError` (fail-closed) per integrity invariant 20 if
    the file does not exist, modes don't match, ownership doesn't match
    the effective uid, or the path is a symlink (POSIX O_NOFOLLOW refusal).
    """
    path = sender_sequence_path(memory_root, sender_uid)
    text = secure_read_text(path)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise IdentityError(f"sender-sequence {path} must be a YAML mapping")
    return data


def increment_sequence(memory_root: Path, sender_uid: str) -> int:
    """Increment the per-sender sequence under an exclusive lock. Returns the new value.

    seq-01: the load -> +1 -> write cycle is serialized with an exclusive
    advisory lock (`exclusive_file_lock`) on a `.lock` sibling so two concurrent
    same-uid writers cannot both read sequence N and both persist N+1 (which
    would mint duplicate sequence numbers and defeat the per-sender monotonic
    anti-replay floor in SPEC.md:804). `write_secure_yaml` already makes the
    WRITE atomic (O_CREAT|O_EXCL tmp + rename); the lock closes the
    read-modify-write race that spans the load and the write. On a platform
    without POSIX advisory locking the lock is a no-op and the documented
    single-writer-per-sender_uid assumption applies.

    Detects uint64 overflow (per invariant 20) and raises `IdentityError`.
    """
    path = sender_sequence_path(memory_root, sender_uid)
    with exclusive_file_lock(path):
        data = load_sender_sequence(memory_root, sender_uid)
        current = int(data.get("current_sequence", 0))
        new_seq = current + 1
        if new_seq >= (1 << 64):
            raise IdentityError(
                f"sender-sequence overflow for {sender_uid!r}; rotate to a new sender-uid"
            )
        data["current_sequence"] = new_seq
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
    # sender-seq-02: distinguish "no parseable prior timestamp" (tamper /
    # corruption) from "time elapsed". The old code returned True on
    # ValueError/KeyError, silently forcing an extra checkpoint and MASKING
    # tampering of a signed-store-state file (SPEC.md:789-804, FS 0600). Fail
    # closed (raise IdentityError) on a malformed checkpoint timestamp, matching
    # the module's otherwise fail-closed posture, instead of treating corruption
    # as "time to checkpoint".
    raw_ts = last.get("timestamp")
    if raw_ts is None:
        raise IdentityError(
            "last checkpoint entry has no `timestamp`; sender-sequence checkpoint "
            "metadata is corrupt or tampered. Fail-closed (sender-seq-02)."
        )
    try:
        last_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise IdentityError(
            f"last checkpoint `timestamp` {raw_ts!r} is not parseable ISO-8601; "
            "sender-sequence checkpoint metadata is corrupt or tampered. "
            "Fail-closed (sender-seq-02)."
        ) from exc
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
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
