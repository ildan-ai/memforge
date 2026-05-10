"""`memforge messaging-doctor` — diagnostic sanity-checks for the v0.5+ adapter posture.

Spec ref: §"Cross-cutting fail-closed posture (v0.5.1+)".

Walks the fail-closed checklist + reports OK / WARN / FAIL on each item.
Designed for operator pre-flight before declaring a deployment ready.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

import yaml

from memforge import crypto, registry as registry_mod
from memforge.identity import (
    IdentityError,
    OPERATOR_IDENTITY_PATH,
    PER_USER_CONFIG_PATH,
    RECOVERY_SECRET_PATH,
    check_fs_mode,
    load_operator_identity,
)
from memforge.registry import REGISTRY_DIRNAME


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "messaging-doctor",
        help="Run the v0.5.1 fail-closed checklist + report posture (OK / WARN / FAIL).",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    failures = 0

    failures += _check(
        "gpg binary on PATH",
        lambda: bool(crypto.gpg_version()),
    )

    failures += _check(
        f"operator-identity readable + 0600 ({OPERATOR_IDENTITY_PATH})",
        lambda: bool(load_operator_identity()),
    )

    failures += _check(
        f"recovery-secret 0600 + 0700 parent ({RECOVERY_SECRET_PATH})",
        lambda: (check_fs_mode(RECOVERY_SECRET_PATH) or True),
    )

    failures += _check(
        "recovery.acknowledged_backup_procedure on file",
        lambda: _check_backup_ack(),
    )

    failures += _check(
        f"operator-registry signature verifies ({memory_root}/.memforge/operator-registry.yaml)",
        lambda: bool(registry_mod.load_registry(memory_root, verify_signature=True)),
    )

    # Sender-sequence directory + agent-sessions directory: presence + modes.
    for sub in ("sender-sequence", "agent-sessions", "seen-nonces", "receiver-state"):
        d = memory_root / REGISTRY_DIRNAME / sub
        if d.exists():
            failures += _check(
                f"{sub}/ exists + 0700",
                lambda d=d: _check_dir_mode(d, 0o700),
            )

    # Config sanity: identity.agent_session_max_lifetime_hours within range.
    cfg_path = memory_root / REGISTRY_DIRNAME / "config.yaml"
    if cfg_path.is_file():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        lifetime = (cfg.get("identity") or {}).get("agent_session_max_lifetime_hours")
        if lifetime is not None:
            failures += _check(
                "identity.agent_session_max_lifetime_hours in [0.25, 168]",
                lambda lifetime=lifetime: 0.25 <= lifetime <= 168,
            )

    print()
    if failures == 0:
        print("messaging-doctor: ALL CHECKS PASSED. v0.5.1 posture is healthy.")
        return 0
    print(f"messaging-doctor: {failures} FAILURE(S). Investigate before relying on v0.5+ writes.")
    return 1


def _check(label: str, fn) -> int:
    try:
        result = fn()
        if result:
            print(f"  OK    {label}")
            return 0
        print(f"  FAIL  {label} (predicate returned False)")
        return 1
    except Exception as exc:
        print(f"  FAIL  {label} -- {exc}")
        return 1


def _check_dir_mode(d: Path, expected: int) -> bool:
    return stat.S_IMODE(d.stat().st_mode) == expected


def _check_backup_ack() -> bool:
    if not PER_USER_CONFIG_PATH.is_file():
        return False
    with open(PER_USER_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return bool((cfg.get("recovery") or {}).get("acknowledged_backup_procedure"))
