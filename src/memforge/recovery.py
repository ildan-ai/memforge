"""Recovery-secret install + backup-acknowledgment.

Spec ref: §"Recovery-secret filesystem mode (normative)" and integrity
invariant 21.

Provides:
- `init_recovery_secret(operator_uuid, registry)` generates the 32-byte
  recovery secret + computes its SHA256 hash for anchoring in the signed
  operator-registry per §"Recovery-secret content integrity (MUST)".
- `verify_recovery_secret_integrity(registry, operator_uuid)` recomputes
  SHA256 of the on-disk secret and compares against the registry-anchored
  hash; fail-closed on mismatch.
- `record_backup_acknowledgment()` flips
  `recovery.acknowledged_backup_procedure: true` in the per-user config.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Optional

import yaml

from memforge.identity import (
    IdentityError,
    PER_USER_CONFIG_PATH,
    RECOVERY_SECRET_PATH,
    check_fs_mode,
    write_secure_bytes,
    write_secure_yaml,
)
from memforge import crypto


class RecoveryError(Exception):
    """Fail-closed recovery-layer error."""


def init_recovery_secret(*, force: bool = False) -> tuple[Path, str]:
    """Generate `~/.memforge/recovery-secret.bin` if absent. Returns (path, sha256_hex).

    Per §"Recovery-secret format": 32 random bytes from a CSPRNG.
    """
    if RECOVERY_SECRET_PATH.exists() and not force:
        # Verify modes + return existing hash; do NOT overwrite.
        check_fs_mode(RECOVERY_SECRET_PATH)
        return RECOVERY_SECRET_PATH, crypto.sha256_file(RECOVERY_SECRET_PATH)
    secret_bytes = secrets.token_bytes(32)
    write_secure_bytes(RECOVERY_SECRET_PATH, secret_bytes)
    return RECOVERY_SECRET_PATH, crypto.sha256_hex(secret_bytes)


def anchor_secret_hash_in_registry(registry: dict, *, operator_uuid: str, sha256_hex_str: str) -> dict:
    """Set `operators[<uuid>].recovery_secret_sha256 = <hex>` in the registry.

    Caller signs + saves the registry afterward.
    """
    for op in registry["operators"]:
        if op["operator_uuid"] == operator_uuid:
            op["recovery_secret_sha256"] = sha256_hex_str
            return registry
    raise RecoveryError(f"operator {operator_uuid!r} not found in registry")


def verify_recovery_secret_integrity(registry: dict, *, operator_uuid: str) -> None:
    """Verify the on-disk recovery-secret SHA256 matches the registry anchor.

    Raises `RecoveryError` (fail-closed) per §"Recovery-secret content
    integrity (MUST)" if the file is missing, modes are wrong, or the
    SHA256 differs from the registry-anchored value.
    """
    check_fs_mode(RECOVERY_SECRET_PATH)
    actual_sha = crypto.sha256_file(RECOVERY_SECRET_PATH)
    anchored: Optional[str] = None
    for op in registry["operators"]:
        if op["operator_uuid"] == operator_uuid:
            anchored = op.get("recovery_secret_sha256")
            break
    if anchored is None:
        raise RecoveryError(
            f"operator {operator_uuid!r} has no `recovery_secret_sha256` anchored in registry; "
            "run `memforge recovery-init` to bind."
        )
    if actual_sha != anchored:
        raise RecoveryError(
            f"recovery-secret SHA256 ({actual_sha}) does not match registry-anchored hash "
            f"({anchored}). Fail-closed: file may have been tampered with. Reinstall via "
            "`memforge recovery-init` OR investigate."
        )


def load_per_user_config() -> dict:
    """Load `~/.memforge/config.yaml`. Returns empty dict if missing."""
    if not PER_USER_CONFIG_PATH.is_file():
        return {}
    with open(PER_USER_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def record_backup_acknowledgment() -> Path:
    """Flip `recovery.acknowledged_backup_procedure: true` in per-user config.

    Per §"Recovery-secret backup acknowledgment (MUST)": adapters refuse
    to load v0.5+ memories until this flag is set.
    """
    config = load_per_user_config()
    config.setdefault("recovery", {})
    config["recovery"]["acknowledged_backup_procedure"] = True
    write_secure_yaml(PER_USER_CONFIG_PATH, config)
    return PER_USER_CONFIG_PATH


def check_backup_acknowledged() -> None:
    """Raise `RecoveryError` if backup acknowledgment is not on file."""
    config = load_per_user_config()
    if not (config.get("recovery") or {}).get("acknowledged_backup_procedure"):
        raise RecoveryError(
            "Recovery-secret backup procedure not acknowledged. "
            "Run `memforge recovery-backup-confirm` after backing up "
            f"{RECOVERY_SECRET_PATH} to offline media."
        )
