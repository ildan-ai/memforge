"""`memforge recovery-backup-confirm` — acknowledge offline backup of the recovery-secret.

Spec ref: §"Recovery-secret backup acknowledgment (MUST)".
"""

from __future__ import annotations

import argparse
import sys

from memforge import recovery


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "recovery-backup-confirm",
        help="Acknowledge that ~/.memforge/recovery-secret.bin has been backed up to offline media.",
    )
    p.add_argument(
        "--i-have-backed-up-the-secret",
        action="store_true",
        required=True,
        help="Required affirmation flag. The flag name is the acknowledgment.",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    try:
        path = recovery.record_backup_acknowledgment()
    except Exception as exc:
        print(f"failed to record acknowledgment: {exc}", file=sys.stderr)
        return 1
    print(f"recovery.acknowledged_backup_procedure: true set in {path}.")
    print("v0.5+ writes are now unlocked on this machine.")
    return 0
