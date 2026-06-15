"""Operator-registry read/write/sign.

Spec ref: §"Operator identity + cross-store references (v0.5.0+)" subsection
"Operator-registry file (per-memory-root)" and integrity invariant 19.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from memforge import crypto
from memforge.identity import now_iso


DEFAULT_ROTATION_COOLDOWN_HOURS = 24
MIN_ROTATION_COOLDOWN_HOURS = 1


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


def config_path(memory_root: Path) -> Path:
    return memory_root / REGISTRY_DIRNAME / "config.yaml"


def read_rotation_cooldown_hours(memory_root: Optional[Path]) -> float:
    """Resolve `identity.rotation_cooldown_hours` from `.memforge/config.yaml`.

    Returns the configured value (floor-enforced at the call site via
    `_compute_cooldown_expiry`) or `DEFAULT_ROTATION_COOLDOWN_HOURS` when no
    config / key is present. A misconfigured (non-numeric) value falls back to
    the default rather than crashing the rotation, but a value below the floor
    surfaces as a `RegistryError` at expiry computation so an operator who
    intentionally configures a too-short window is told why.
    """
    if memory_root is None:
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)
    cfg = config_path(memory_root)
    if not cfg.is_file():
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)
    if not isinstance(data, dict):
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)
    identity_cfg = data.get("identity") or {}
    if not isinstance(identity_cfg, dict):
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)
    raw = identity_cfg.get("rotation_cooldown_hours")
    if raw is None:
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_ROTATION_COOLDOWN_HOURS)


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
        # regsign-01: fail closed if the SIGNER OPERATOR's top-level status is
        # not active. _resolve_signing_key rejects a superseded KEY, but a
        # registry where the operator is marked superseded while a key entry
        # still carries `status: active` (hand-edited / partial state) would
        # otherwise let a superseded operator's signature load the registry.
        # get_active_key already gates on operator status; the load/verify path
        # must too.
        if signer_entry.get("status", "active") != "active":
            raise RegistryError(
                f"operator-registry signed by operator {signing_uuid!r} whose status is "
                f"{signer_entry.get('status')!r} (not active); refusing to honor a "
                "non-active operator's signature."
            )
        signer_key = _resolve_signing_key(signer_entry, sig, signing_uuid)
        signer_fpr = signer_key.get("key_id")
        # Trust root is the registered key material (when present), NOT the
        # ambient local keyring. gpg_verify_detached imports the material into
        # an ephemeral keyring and verifies against ONLY that key, and asserts
        # the material resolves to the registered fingerprint. Older registries
        # that predate `public_material` fall back to keyring + exact-pin.
        public_material = signer_key.get("public_material")
        envelope = _canonical_for_signature(data)
        if not crypto.gpg_verify_detached(
            envelope,
            signature_b64=sig["value"],
            expected_fingerprint=signer_fpr,
            registered_public_material_b64=public_material,
        ):
            raise RegistryError(
                "operator-registry signature did not verify. Fail-closed: refusing to load v0.5+ memories. "
                "Investigate the registry OR rotate to a known-good state."
            )
    return data


def _resolve_signing_key(signer_entry: dict, sig: dict, signing_uuid: str) -> dict:
    """Resolve the public_key entry that the registry signature should verify against.

    Preferred path (v0.5.1+): the signature block records `signing_key_id`, the
    fingerprint of the key that actually signed. We match it against the
    signing operator's listed `public_keys` and require the key be listed and
    not superseded. This is correct during a rotation cool-down where the
    operator has two active keys: the old "first active" heuristic could pin
    the wrong key and brick the store.

    Back-compat path: a registry written before `signing_key_id` existed has no
    recorded key id. We fall back to the signer's first ACTIVE key. This keeps
    existing stores loadable; the one-time fix is to re-sign with any current
    CLI (sign_and_save now always records `signing_key_id`).
    """
    keys = signer_entry.get("public_keys", [])
    recorded_key_id = sig.get("signing_key_id")
    if recorded_key_id:
        for k in keys:
            if k.get("key_id") == recorded_key_id:
                if k.get("status", "active") == "superseded":
                    raise RegistryError(
                        f"operator-registry signing key {recorded_key_id!r} is superseded "
                        f"for operator {signing_uuid!r}; refusing to honor a superseded-key signature."
                    )
                return k
        raise RegistryError(
            f"operator-registry signature records signing_key_id {recorded_key_id!r} "
            f"which is not listed under signer {signing_uuid!r}. Reject the signature."
        )
    # Back-compat: no recorded key id. Pin the first active key.
    for k in keys:
        if k.get("status", "active") == "active":
            return k
    raise RegistryError(
        f"operator-registry signer {signing_uuid!r} has no active public key listed"
    )


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
    memory_root: Optional[Path] = None,
    cooldown_hours: Optional[float] = None,
) -> dict:
    """Append a rotated key per §"Cross-signed rotation chain".

    Old key remains `status: active` during the cool-down; the new key is
    appended with `chain_index = N+1`. The CLI is responsible for enforcing
    the cool-down on writers.

    Cool-down duration: `cooldown_hours` (explicit) wins; else
    `identity.rotation_cooldown_hours` from `<memory_root>/.memforge/config.yaml`
    (when `memory_root` is given); else `DEFAULT_ROTATION_COOLDOWN_HOURS`. The
    configured DURATION is stored alongside the expiry so receivers can
    re-derive the window from the rotation commit's author-date if they choose.
    """
    crypto.gpg_check_algo_accepted(new_algo)
    hours = _resolve_cooldown_hours(cooldown_hours, memory_root)
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
                "rotation_cooldown_hours": hours,
                "rotation_cooldown_expires_at": _compute_cooldown_expiry(hours=hours),
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
    memory_root: Optional[Path] = None,
    cooldown_hours: Optional[float] = None,
) -> dict:
    """Publish a fresh-start operator entry (no cross-signature; breaks the chain).

    Per §"Cross-signed rotation chain": fresh-start commits use prefix
    `memforge: fresh-start <operator-uuid>` AND are still subject to the
    cool-down (compromised-key fresh-start is a privilege-escalation vector).
    Cool-down duration resolution matches `add_rotated_key`.
    """
    crypto.gpg_check_algo_accepted(new_algo)
    hours = _resolve_cooldown_hours(cooldown_hours, memory_root)
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
                "rotation_cooldown_hours": hours,
                "rotation_cooldown_expires_at": _compute_cooldown_expiry(hours=hours),
                "status": "active",
            }
        )
        return registry
    raise RegistryError(f"operator {operator_uuid!r} not found in registry")


def _find_key_entry(registry: dict, operator_uuid: str, key_id: str) -> Optional[dict]:
    """Return the public_key entry for (operator_uuid, key_id), or None."""
    for op in registry["operators"]:
        if op.get("operator_uuid") != operator_uuid:
            continue
        for k in op.get("public_keys", []):
            if k.get("key_id") == key_id:
                return k
    return None


def sign_and_save(registry: dict, memory_root: Path, *, signer_uuid: str, signer_fingerprint: str) -> Path:
    """Sign + persist the registry. Returns the path written.

    The CLI is responsible for committing the change with prefix
    `memforge: operator-registry` (single-file scope) per integrity
    invariant 19.

    The signature block records `signing_key_id` (the fingerprint of the key
    that actually produced `value`) so the loader can resolve the exact
    verification key rather than guessing the signer's "first active" key.
    During a rotation cool-down an operator has TWO active keys; without the
    recorded key id the loader could pin the wrong one and brick the store.
    """
    # Resolve the algo from the key that ACTUALLY signed (matched by
    # signer_fingerprint), not the signer's first-active key. Fail closed if
    # the signing key is not listed under the signing operator: a signature
    # whose key cannot be described by the registry is not loadable later.
    #
    # registry-01 (BLOCKER): resolve + validate the signer key entry BEFORE
    # producing the signature or writing anything. If the signer's key entry is
    # 'superseded' (e.g. remove_operator just superseded this operator's keys),
    # the loader's _resolve_signing_key will refuse to honor the resulting
    # signature on the very next load_registry, permanently bricking the store
    # (it cannot be re-signed because load fails closed first). We mirror the
    # loader's superseded-key check here so we never WRITE a registry signed by
    # a superseded key. Same guard for a superseded SIGNER OPERATOR.
    signer_key = _find_key_entry(registry, signer_uuid, signer_fingerprint)
    if signer_key is None:
        raise RegistryError(
            f"signing key {signer_fingerprint!r} is not listed under operator "
            f"{signer_uuid!r} in the registry; cannot record a coherent signature block. "
            "Add the key (operator-registry add / rotate-key) before signing."
        )
    if signer_key.get("status", "active") == "superseded":
        raise RegistryError(
            f"refusing to sign the operator-registry with superseded key "
            f"{signer_fingerprint!r} (operator {signer_uuid!r}): the loader rejects "
            "superseded-key signatures, so writing this would brick the store "
            "(it could never be loaded or re-signed). Sign with an active key, or "
            "do not supersede the signing key in the same operation."
        )
    signer_op = next(
        (op for op in registry.get("operators", []) if op.get("operator_uuid") == signer_uuid),
        None,
    )
    if signer_op is not None and signer_op.get("status", "active") != "active":
        raise RegistryError(
            f"refusing to sign the operator-registry as superseded operator "
            f"{signer_uuid!r}: a superseded operator's signature is not honored on load. "
            "Sign with an active operator identity."
        )
    payload = _canonical_for_signature(registry)
    sig_b64 = crypto.gpg_sign_detached(payload, fingerprint=signer_fingerprint)
    signer_algo = signer_key.get("algo")
    if not signer_algo:
        raise RegistryError(
            f"signing key {signer_fingerprint!r} has no `algo` recorded; refusing to "
            "stamp an algo field that does not describe the signing key."
        )
    registry["registry_signature"] = {
        "algo": signer_algo,
        "signing_uuid": signer_uuid,
        "signing_key_id": signer_fingerprint,
        "signing_time": now_iso(),
        "value": sig_b64,
    }
    path = registry_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(registry, f, sort_keys=False, default_flow_style=False)
    return path


def _resolve_cooldown_hours(cooldown_hours: Optional[float], memory_root: Optional[Path]) -> float:
    """Resolve the effective cool-down duration in hours.

    Precedence: explicit `cooldown_hours` > config (`identity.rotation_cooldown_hours`
    via `memory_root`) > `DEFAULT_ROTATION_COOLDOWN_HOURS`.
    """
    if cooldown_hours is not None:
        return float(cooldown_hours)
    return read_rotation_cooldown_hours(memory_root)


def _compute_cooldown_expiry(*, hours: float = DEFAULT_ROTATION_COOLDOWN_HOURS) -> str:
    """Default 24-hour cool-down expiry. Floor 1h enforced at the registry layer."""
    if hours < MIN_ROTATION_COOLDOWN_HOURS:
        raise RegistryError(
            f"rotation cool-down ({hours}h) below floor {MIN_ROTATION_COOLDOWN_HOURS}h. "
            "Cool-down is a security-critical window for detecting unauthorized rotations."
        )
    return (
        (datetime.now(timezone.utc) + timedelta(hours=hours))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_iso_or_fail(value: str, *, field: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepting a trailing `Z`) to an aware datetime.

    registry-03: security-decision comparators must NOT do raw lexicographic
    string compares -- a caller-supplied `+00:00`-form (or differently-padded)
    timestamp sorts incorrectly against a `Z`-form one ('+' (0x2B) < 'Z' (0x5A)
    and < digits), silently defeating expiry / cool-down / revocation checks
    with no parse error. We parse both operands to aware datetimes and fail
    closed (`RegistryError`) on an unparseable input rather than string-comparing.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise RegistryError(
            f"unparseable ISO-8601 timestamp for {field}: {value!r}. Fail-closed: "
            "refusing to make a security decision on an unparseable timestamp."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def key_is_in_cooldown(
    registry: dict,
    key_id: str,
    *,
    at_time: Optional[str] = None,
    commit_author_date: Optional[str] = None,
) -> bool:
    """Return True if `key_id` is currently in its rotation cool-down window.

    Spec ref: §"Mandatory cool-down period". A key that was rotated in via
    `rotate-key` carries `rotation_cooldown_expires_at` in its registry
    entry. Until that timestamp passes, the key MUST NOT be honored for
    write signatures (registry-layer enforcement; closes the v0.5.2
    threat-model MAJOR that previously left enforcement at the CLI layer).

    `at_time` defaults to current UTC; callers verifying historical
    signatures should pass `signature.signing_time` to honor signing-time-
    aware verification.

    registry-02 / rotate-02: SPEC.md §"Mandatory cool-down period" anchors the
    window to "the rotation commit's git author-date + rotation_cooldown_hours",
    but `rotation_cooldown_expires_at` is frozen at registry-BUILD time
    (datetime.now() when add_rotated_key ran), which only equals the commit
    author-date when the same process builds and commits with ~zero delay. A
    registry built / staged / committed later (or imported / replayed) gets a
    window that ends too early. When `commit_author_date` is supplied, we
    RE-DERIVE the expiry from `commit_author_date + rotation_cooldown_hours`
    (the durable field is stored), matching the spec anchor, instead of trusting
    the build-time-frozen `rotation_cooldown_expires_at`.

    registry-03: comparisons are done on parsed aware datetimes, not raw string
    lexicographic compares, and fail closed on an unparseable timestamp.
    """
    when_dt = _parse_iso_or_fail(at_time or now_iso(), field="at_time")
    for op in registry.get("operators", []):
        for k in op.get("public_keys", []):
            if k.get("key_id") != key_id:
                continue
            cooldown_until = k.get("rotation_cooldown_expires_at")
            if commit_author_date is not None:
                # Re-derive from the authoritative commit author-date + stored
                # duration (registry-02 spec anchor). Fall back to the stored
                # expiry only if the duration was not recorded.
                hours = k.get("rotation_cooldown_hours")
                if hours is not None:
                    try:
                        author_dt = _parse_iso_or_fail(
                            commit_author_date, field="commit_author_date"
                        )
                    except RegistryError:
                        raise
                    expiry_dt = author_dt + timedelta(hours=float(hours))
                    return when_dt < expiry_dt
            if not cooldown_until:
                return False
            cooldown_dt = _parse_iso_or_fail(
                cooldown_until, field="rotation_cooldown_expires_at"
            )
            return when_dt < cooldown_dt
    return False


def verify_signing_key_acceptable(
    registry: dict,
    key_id: str,
    *,
    signing_time: Optional[str] = None,
) -> None:
    """Verify that `key_id` is acceptable as a signing key at `signing_time`.

    Combines registry-membership + active-status + cool-down checks. Revocation
    check is delegated to the caller (since the revocation set lives in git
    history, not the registry).

    Raises `RegistryError` (fail-closed) on:
      - `key_id` not in the registry under any operator.
      - the resolved operator entry is not `status: active`.
      - the resolved KEY entry is not `status: active` (cooldown-active-01).
      - `key_id` is in cool-down at `signing_time`.

    cooldown-active-01: this is the natural seam an adapter reaches for before
    honoring a write signature, so membership-only acceptance was an
    extensibility trap: a key superseded by rotation / remove_operator /
    fresh_start passed as 'acceptable to sign with' as long as it was not in
    cool-down. We now assert active status on BOTH the operator and the specific
    key entry (mirroring get_active_key), fail-closed. The registry carries no
    per-key superseded-at timestamp, so active-status is enforced outright rather
    than signing-time-relative; a write signed by a now-retired key is rejected
    here (the caller must not honor it).

    Callers (typically adapters verifying a write's signature) MUST also
    consult the revocation set per §"Signing-time-aware verification".
    """
    signer_operator = find_operator_by_key_id(registry, key_id)
    if signer_operator is None:
        raise RegistryError(
            f"signing key {key_id!r} not present in operator-registry. Reject the signature."
        )
    if signer_operator.get("status", "active") != "active":
        raise RegistryError(
            f"signing key {key_id!r} belongs to operator "
            f"{signer_operator.get('operator_uuid')!r} whose status is "
            f"{signer_operator.get('status')!r} (not active); refusing to accept a "
            "non-active operator's signing key. Reject the signature."
        )
    key_entry = _find_key_entry(registry, signer_operator.get("operator_uuid"), key_id)
    if key_entry is None or key_entry.get("status", "active") != "active":
        status = key_entry.get("status") if key_entry else "missing"
        raise RegistryError(
            f"signing key {key_id!r} is not active (status {status!r}); a superseded / "
            "retired key is not acceptable as a signing key (cooldown-active-01). "
            "Reject the signature."
        )
    if key_is_in_cooldown(registry, key_id, at_time=signing_time):
        raise RegistryError(
            f"signing key {key_id!r} is in rotation cool-down at {signing_time or 'now'}. "
            "Receivers MUST reject writes signed by this key until the cool-down expires "
            "(spec §\"Mandatory cool-down period\"). If you control this key and the rotation "
            "was unauthorized, run `memforge: key-compromise` rather than waiting out the window."
        )


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
