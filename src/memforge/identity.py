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
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml


OPERATOR_IDENTITY_PATH = Path.home() / ".memforge" / "operator-identity.yaml"
RECOVERY_SECRET_PATH = Path.home() / ".memforge" / "recovery-secret.bin"
PER_USER_CONFIG_PATH = Path.home() / ".memforge" / "config.yaml"


AGENT_SESSION_ID_RE = re.compile(r"^[a-z0-9]+-\d{4}-\d{2}-\d{2}-[a-z0-9]{8,16}$")


class IdentityError(Exception):
    """Fail-closed identity-layer error. Spec §"Cross-cutting fail-closed posture"."""


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
    """Verify file at `path` has expected mode + parent dir mode + uid match.

    Raises `IdentityError` (fail-closed) on any violation per integrity
    invariant 21 + §"Cross-cutting fail-closed posture" items 3, 4, 6, 7.
    """
    if not path.exists():
        raise IdentityError(f"identity file missing: {path}")
    st = path.stat()
    actual_mode = stat.S_IMODE(st.st_mode)
    if actual_mode != file_mode:
        raise IdentityError(
            f"identity file {path} mode is {oct(actual_mode)}, expected {oct(file_mode)}. "
            f"Run `chmod {oct(file_mode)[2:]} {path}`."
        )
    if st.st_uid != os.geteuid():
        raise IdentityError(
            f"identity file {path} uid={st.st_uid} != effective uid={os.geteuid()}. "
            "Fail-closed; investigate ownership."
        )
    parent = path.parent
    parent_st = parent.stat()
    actual_parent_mode = stat.S_IMODE(parent_st.st_mode)
    if actual_parent_mode != parent_mode:
        raise IdentityError(
            f"parent dir {parent} mode is {oct(actual_parent_mode)}, expected {oct(parent_mode)}. "
            f"Run `chmod {oct(parent_mode)[2:]} {parent}`."
        )


def write_secure_yaml(path: Path, data: dict, *, file_mode: int = 0o600, parent_mode: int = 0o700) -> None:
    """Write YAML with 0600 file mode + 0700 parent. Used for identity files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, parent_mode)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    os.chmod(path, file_mode)


def write_secure_bytes(path: Path, data: bytes, *, file_mode: int = 0o600, parent_mode: int = 0o700) -> None:
    """Write bytes with 0600 file mode + 0700 parent. Used for recovery-secret."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, parent_mode)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, file_mode)


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
        "machine_origin": os.uname().nodename if hasattr(os, "uname") else "unknown",
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
