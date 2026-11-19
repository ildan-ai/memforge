"""Stubs for the key-system commands removed in 1.0.

MemForge no longer carries keys, a registry, revocation or recovery. The old
command names stay so a script or a habit gets a clear notice instead of
"invalid choice". A stub reads no argument and touches no file, key or
keyring.
"""

from __future__ import annotations

import argparse
import sys

REMOVED_COMMANDS: tuple[str, ...] = (
    "init-operator",
    "operator-registry",
    "rotate-key",
    "revoke",
    "revocation-snapshot",
    "revoke-cache-refresh",
    "memories-by-key",
    "revoke-memories",
    "upgrade-v04-memories",
    "recovery-init",
    "recovery-backup-confirm",
    "attest-agent",
    "messaging-doctor",
)

# Same code argparse uses for a usage error; any non-zero value satisfies the
# removal contract.
EXIT_REMOVED = 2


def notice(name: str) -> str:
    return (
        f"memforge {name}: removed in 1.0.\n"
        "MemForge no longer manages keys, registries, revocation, recovery or "
        "agent attestations, so there is nothing to migrate. Commit trust now "
        "comes from your git signing setup and `memforge sync`.\n"
        "See docs/migrating-to-1.0.md in the repository and the [1.0.0] entry "
        "in CHANGELOG.md."
    )


def run(name: str) -> int:
    print(notice(name), file=sys.stderr)
    return EXIT_REMOVED


def register(subparsers: argparse._SubParsersAction) -> None:
    """List the stubs in `--help`. `main` answers them before argparse runs."""
    for name in REMOVED_COMMANDS:
        p = subparsers.add_parser(
            name, help="Removed in 1.0 (prints a notice, exits non-zero)."
        )
        p.set_defaults(func=lambda _args, _name=name: run(_name))
