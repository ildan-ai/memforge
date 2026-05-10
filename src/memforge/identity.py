"""Operator + agent identity primitives (MemForge v0.5.0+ / v0.5.1+).

Spec refs: §"Multi-identity primitives (v0.5.0+)", §"Operator identity +
cross-store references (v0.5.0+)", §"Agent session attestation content
scope (v0.5.1+)".

Provides:

- UUIDv7 generation (operator-UUIDs + revocation-UIDs).
- `~/.memforge/operator-identity.yaml` read/write with mandatory FS-mode +
  ownership checks per integrity invariant 21.
- Agent-session-id format regex + minter per v0.5.1 invariant 25.
"""

from __future__ import annotations

import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from memforge._security import (
    SecurityError,
    host_node_name,
    restrict_dir_to_owner,
    restrict_file_to_owner,
    verify_owner_restricted,
)


OPERATOR_IDENTITY_PATH = Path.home() / ".memforge" / "operator-identity.yaml"
RECOVERY_SECRET_PATH = Path.home() / ".memforge" / "recovery-secret.bin"
PER_USER_CONFIG_PATH = Path.home() / ".memforge" / "config.yaml"


AGENT_SESSION_ID_RE = re.compile(r"^[a-z0-9]+-\d{4}-\d{2}-\d{2}-[a-z0-9]{8,16}$")


class IdentityError(SecurityError):
    """Fail-closed identity-layer error. Spec §"Cross-cutting fail-closed posture".

    Inherits from `SecurityError` so callers can catch either name without
    branching on which abstraction layer raised.
    """


def generate_uuidv7() -> str:
    """Generate a UUIDv7 (timestamp-ordered) string.

    Layout (128 bits total): 48-bit unix-ms | 4-bit version=7 | 12-bit rand_a
    | 2-bit variant=0b10 | 62-bit rand_b.
    """
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(secrets.token_bytes(2), "big") & 0x0FFF
    rand_b = int.from_bytes(secrets.token_bytes(8), "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    return str(uuid.UUID(int=value))


def now_iso() -> str:
    """Current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_fs_mode(path: Path, *, file_mode: int = 0o600, parent_mode: int = 0o700) -> None:
    """Verify `path` is restricted to the current owner. Cross-platform.

    Delegates to `_security.verify_owner_restricted`, which uses POSIX
    mode bits on Unix and Windows ACLs (via icacls) on Windows. Spec
    integrity invariant 21 is platform-agnostic; this function is its
    normative implementation.

    Raises `IdentityError` (fail-closed) on any violation.
    """
    verify_owner_restricted(path, file_mode=file_mode, parent_mode=parent_mode)


def write_secure_yaml(path: Path, data: dict, *, file_mode: int = 0o600, parent_mode: int = 0o700) -> None:
    """Write YAML restricted to current owner, atomically. Cross-platform.

    Closes the TOCTOU window where the file would exist at default umask /
    inherited-ACL between create and permission enforcement: opens with
    O_CREAT|O_EXCL on a sibling tmp path, writes content, fsyncs, restricts
    permissions on the tmp file BEFORE atomic rename. Parent dir
    permissions are restricted before the create so a hostile parent
    cannot be substituted mid-write.

    POSIX: file_mode + parent_mode enforce as POSIX mode bits.
    Windows: file + parent ACLs locked down via icacls per _security.py.
    """
    restrict_dir_to_owner(path.parent, mode=parent_mode)
    tmp_path = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
    # POSIX: O_CREAT honors the mode argument (subject to umask). Windows:
    # the mode argument is ignored; restrict_file_to_owner applies ACL
    # restriction after open and before rename, so the eventual file is
    # correctly restricted on both platforms.
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        restrict_file_to_owner(tmp_path, mode=file_mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_secure_bytes(path: Path, data: bytes, *, file_mode: int = 0o600, parent_mode: int = 0o700) -> None:
    """Write bytes restricted to current owner, atomically. Cross-platform.

    Same TOCTOU-closing pattern as `write_secure_yaml`: O_CREAT|O_EXCL on a
    sibling tmp file, fsync, restrict permissions, atomic rename.
    """
    restrict_dir_to_owner(path.parent, mode=parent_mode)
    tmp_path = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        restrict_file_to_owner(tmp_path, mode=file_mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_operator_identity(path: Optional[Path] = None) -> dict:
    """Load `~/.memforge/operator-identity.yaml` after FS-mode verification.

    Raises `IdentityError` if file missing, wrong mode, wrong ownership, or
    YAML invalid. Successful return: dict with keys `operator_uuid`,
    `operator_name`, `created`, `machine_origin`, `key_fingerprint` (v0.5.1
    extension; advisory pointer to the current GPG key).
    """
    target = path or OPERATOR_IDENTITY_PATH
    check_fs_mode(target)
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise IdentityError(f"identity file {target} YAML parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise IdentityError(f"identity file {target} must be a YAML mapping")
    for required in ("operator_uuid", "created"):
        if required not in data:
            raise IdentityError(f"identity file {target} missing required field `{required}`")
    return data


def save_operator_identity(
    *,
    operator_uuid: str,
    operator_name: str,
    key_fingerprint: Optional[str] = None,
    path: Optional[Path] = None,
) -> Path:
    """Write a fresh `~/.memforge/operator-identity.yaml`.

    `key_fingerprint` is an advisory v0.5.1 pointer to the current GPG key;
    authoritative key material lives in the per-store operator-registry.
    """
    target = path or OPERATOR_IDENTITY_PATH
    data = {
        "operator_uuid": operator_uuid,
        "operator_name": operator_name,
        "created": now_iso(),
        "machine_origin": host_node_name(),
    }
    if key_fingerprint:
        data["key_fingerprint"] = key_fingerprint
    write_secure_yaml(target, data)
    return target


def mint_agent_session_id(adapter_prefix: str, *, now: Optional[datetime] = None) -> str:
    """Mint a v0.5.1-conformant agent-session-id.

    Format: `<adapter_prefix>-YYYY-MM-DD-<8-16 lowercase base32 chars>`.
    `adapter_prefix` is the operator-facing adapter name (`cc`, `cursor`,
    `aider`, etc.); lowercased + stripped of non-alphanumerics here.
    """
    safe_prefix = re.sub(r"[^a-z0-9]+", "", adapter_prefix.lower())
    if not safe_prefix:
        raise IdentityError("adapter_prefix must contain at least one alphanumeric character")
    date_str = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    alphabet = "abcdefghijklmnopqrstuvwxyz234567"
    suffix = "".join(secrets.choice(alphabet) for _ in range(12))
    candidate = f"{safe_prefix}-{date_str}-{suffix}"
    if not AGENT_SESSION_ID_RE.match(candidate):
        raise IdentityError(f"minted agent-session-id failed format check: {candidate}")
    return candidate


def validate_agent_session_id(agent_session_id: str) -> None:
    """Raise `IdentityError` if the agent-session-id violates the v0.5.1 regex."""
    if not AGENT_SESSION_ID_RE.match(agent_session_id):
        raise IdentityError(
            f"agent-session-id {agent_session_id!r} does not match the v0.5.1 format "
            f"regex ^[a-z0-9]+-\\d{{4}}-\\d{{2}}-\\d{{2}}-[a-z0-9]{{8,16}}$"
        )


def parse_identity(identity: str) -> dict:
    """Parse an `identity` frontmatter string into its components.

    Returns dict with keys `class` ("operator" or "agent"), `operator_uuid`,
    and (for agent identities) `agent_session_id`. Raises `IdentityError`
    on malformed input.
    """
    parts = identity.split(":")
    if len(parts) < 2:
        raise IdentityError(f"identity {identity!r} missing `<class>:<operator-uuid>` separator")
    cls = parts[0]
    if cls not in ("operator", "agent"):
        raise IdentityError(f"identity class {cls!r} must be one of: operator, agent")
    out: dict = {"class": cls, "operator_uuid": parts[1]}
    if cls == "agent":
        if len(parts) != 3:
            raise IdentityError(f"agent identity {identity!r} must be `agent:<uuid>:<session-id>`")
        validate_agent_session_id(parts[2])
        out["agent_session_id"] = parts[2]
    return out
