"""Operator-registry read/write/sign.

Spec ref: §"Operator identity + cross-store references (v0.5.0+)" subsection
"Operator-registry file (per-memory-root)" and integrity invariant 19.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from memforge import crypto
from memforge.identity import now_iso


REGISTRY_DIRNAME = ".memforge"
REGISTRY_FILENAME = "operator-registry.yaml"
AGENT_SESSIONS_SUBDIR = "agent-sessions"
SENDER_SEQUENCE_SUBDIR = "sender-sequence"
RECEIVER_STATE_SUBDIR = "receiver-state"
SEEN_NONCES_SUBDIR = "seen-nonces"
REVOCATION_CACHE = "revocation-cache.yaml"


class RegistryError(Exception):
    """Fail-closed registry-layer error."""


def registry_path(memory_root: Path) -> Path:
    return memory_root / REGISTRY_DIRNAME / REGISTRY_FILENAME


def _canonical_for_signature(registry: dict) -> bytes:
    """Build the canonical payload that the registry_signature covers.

    Excludes the `registry_signature` field itself; includes everything else
    deterministically.
    """
    payload = {k: v for k, v in registry.items() if k != "registry_signature"}
    return crypto.canonical_envelope(payload)


def load_registry(memory_root: Path, *, verify_signature: bool = True) -> dict:
    """Load + optionally verify the operator-registry.

    Verification failure → `RegistryError` per integrity invariant 19
    (adapter HALTS / fail-closed).
    """
    path = registry_path(memory_root)
    if not path.is_file():
        raise RegistryError(
            f"operator-registry missing at {path}. Run `memforge init-store` from the memory-root."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise RegistryError(f"operator-registry YAML parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("operator-registry must be a YAML mapping")
    if not isinstance(data.get("operators"), list):
        raise RegistryError("operator-registry must have a `operators` list")
    if verify_signature:
        sig = data.get("registry_signature")
        if not isinstance(sig, dict) or "value" not in sig:
            raise RegistryError("operator-registry missing valid `registry_signature` block")
        crypto.gpg_check_algo_accepted(sig.get("algo", ""))
        signing_uuid = sig.get("signing_uuid")
        signer_entry = None
        for op in data["operators"]:
            if op.get("operator_uuid") == signing_uuid:
                signer_entry = op
                break
        if signer_entry is None:
            raise RegistryError(
                f"operator-registry signed by operator {signing_uuid!r} who is not listed in `operators`"
            )
        signer_fpr = None
        for k in signer_entry.get("public_keys", []):
            if k.get("status", "active") == "active":
                signer_fpr = k.get("key_id")
                break
        if not signer_fpr:
            raise RegistryError(
                f"operator-registry signer {signing_uuid!r} has no active public key listed"
            )
        envelope = _canonical_for_signature(data)
        if not crypto.gpg_verify_detached(envelope, signature_b64=sig["value"], expected_fingerprint=signer_fpr):
            raise RegistryError(
                "operator-registry signature did not verify. Fail-closed: refusing to load v0.5+ memories. "
                "Investigate the registry OR rotate to a known-good state."
            )
    return data


def init_registry(*, operator_uuid: str, operator_name: str, key_id: str, algo: str, public_material_b64: str) -> dict:
    """Build an unsigned operator-registry skeleton with one operator entry."""
    crypto.gpg_check_algo_accepted(algo)
    return {
        "spec_version": "0.5.1",
        "operators": [
            {
                "operator_uuid": operator_uuid,
                "operator_name": operator_name,
                "status": "active",
                "public_keys": [
                    {
                        "key_id": key_id,
                        "algo": algo,
                        "public_material": public_material_b64,
                        "chain_index": 0,
                        "introduced_at": now_iso(),
                        "introduced_by_commit": "",  # set at commit time by CLI
                        "status": "active",
                    }
                ],
            }
        ],
    }


def add_operator(
    registry: dict,
    *,
    operator_uuid: str,
    operator_name: str,
    key_id: str,
    algo: str,
    public_material_b64: str,
) -> dict:
    """Append a new operator entry. Raises if `operator_uuid` already present."""
    crypto.gpg_check_algo_accepted(algo)
    for op in registry["operators"]:
        if op["operator_uuid"] == operator_uuid:
            raise RegistryError(
                f"operator {operator_uuid!r} already present in registry. "
                "Use `memforge operator-registry remove` first or `rotate-key` to update keys."
            )
    registry["operators"].append(
        {
            "operator_uuid": operator_uuid,
            "operator_name": operator_name,
            "status": "active",
            "public_keys": [
                {
                    "key_id": key_id,
                    "algo": algo,
                    "public_material": public_material_b64,
                    "chain_index": 0,
                    "introduced_at": now_iso(),
                    "introduced_by_commit": "",
                    "status": "active",
                }
            ],
        }
    )
    return registry


def remove_operator(registry: dict, *, operator_uuid: str) -> dict:
    """Mark an operator entry `status: superseded`. Does NOT delete history."""
    for op in registry["operators"]:
        if op["operator_uuid"] == operator_uuid:
            op["status"] = "superseded"
            for k in op.get("public_keys", []):
                k["status"] = "superseded"
            return registry
    raise RegistryError(f"operator {operator_uuid!r} not found in registry")


def add_rotated_key(
    registry: dict,
    *,
    operator_uuid: str,
    new_key_id: str,
    new_algo: str,
    new_public_material_b64: str,
    cross_signature_by_old: str,
    cross_signature_by_new: str,
) -> dict:
    """Append a rotated key per §"Cross-signed rotation chain".

    Old key remains `status: active` during the cool-down (24h default); the
    new key is appended with `chain_index = N+1`. The CLI is responsible for
    enforcing the cool-down on writers.
    """
    crypto.gpg_check_algo_accepted(new_algo)
    for op in registry["operators"]:
        if op["operator_uuid"] != operator_uuid:
            continue
        existing = op.get("public_keys", [])
        next_index = max((k.get("chain_index", 0) for k in existing), default=-1) + 1
        existing.append(
            {
                "key_id": new_key_id,
                "algo": new_algo,
                "public_material": new_public_material_b64,
                "chain_index": next_index,
                "introduced_at": now_iso(),
                "introduced_by_commit": "",
                "cross_signature_by_old": cross_signature_by_old,
                "cross_signature_by_new": cross_signature_by_new,
                "rotated_at": now_iso(),
                "rotation_cooldown_expires_at": _compute_cooldown_expiry(),
                "status": "active",
            }
        )
        op["public_keys"] = existing
        return registry
    raise RegistryError(f"operator {operator_uuid!r} not found in registry")


def fresh_start(
    registry: dict,
    *,
    operator_uuid: str,
    new_key_id: str,
    new_algo: str,
    new_public_material_b64: str,
) -> dict:
    """Publish a fresh-start operator entry (no cross-signature; breaks the chain).

    Per §"Cross-signed rotation chain": fresh-start commits use prefix
    `memforge: fresh-start <operator-uuid>` AND are still subject to the
    cool-down (compromised-key fresh-start is a privilege-escalation vector).
    """
    crypto.gpg_check_algo_accepted(new_algo)
    for op in registry["operators"]:
        if op["operator_uuid"] != operator_uuid:
            continue
        # Supersede the old keys.
        for k in op.get("public_keys", []):
            k["status"] = "superseded"
        op["public_keys"].append(
            {
                "key_id": new_key_id,
                "algo": new_algo,
                "public_material": new_public_material_b64,
                "chain_index": 0,  # fresh-start resets the chain
                "introduced_at": now_iso(),
                "introduced_by_commit": "",
                "fresh_start": True,
                "rotation_cooldown_expires_at": _compute_cooldown_expiry(),
                "status": "active",
            }
        )
        return registry
    raise RegistryError(f"operator {operator_uuid!r} not found in registry")


def sign_and_save(registry: dict, memory_root: Path, *, signer_uuid: str, signer_fingerprint: str) -> Path:
    """Sign + persist the registry. Returns the path written.

    The CLI is responsible for committing the change with prefix
    `memforge: operator-registry` (single-file scope) per integrity
    invariant 19.
    """
    payload = _canonical_for_signature(registry)
    sig_b64 = crypto.gpg_sign_detached(payload, fingerprint=signer_fingerprint)
    # Resolve signer's declared algo from the registry.
    signer_algo = "gpg-ed25519"
    for op in registry["operators"]:
        if op["operator_uuid"] == signer_uuid:
            for k in op.get("public_keys", []):
                if k.get("status", "active") == "active":
                    signer_algo = k.get("algo", signer_algo)
                    break
            break
    registry["registry_signature"] = {
        "algo": signer_algo,
        "signing_uuid": signer_uuid,
        "signing_time": now_iso(),
        "value": sig_b64,
    }
    path = registry_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(registry, f, sort_keys=False, default_flow_style=False)
    return path


def _compute_cooldown_expiry() -> str:
    """Default 24-hour cool-down expiry. Operator config MAY narrow but not below 1h."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_active_key(registry: dict, operator_uuid: str) -> Optional[dict]:
    """Return the active public_key entry for `operator_uuid`, or None."""
    for op in registry["operators"]:
        if op["operator_uuid"] != operator_uuid:
            continue
        if op.get("status", "active") != "active":
            return None
        for k in op.get("public_keys", []):
            if k.get("status", "active") == "active":
                return k
    return None


def find_operator_by_key_id(registry: dict, key_id: str) -> Optional[dict]:
    """Find the operator entry whose public_keys contain `key_id` (active or superseded)."""
    for op in registry["operators"]:
        for k in op.get("public_keys", []):
            if k.get("key_id") == key_id:
                return op
    return None
