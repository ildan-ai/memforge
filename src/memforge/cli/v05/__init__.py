"""v0.5.1 reference CLI subcommands.

Each module in this package exposes `register(subparsers)` to attach its
argparse subparser to the top-level `memforge` dispatcher.
"""

__all__ = [
    "init_operator",
    "init_store",
    "operator_registry",
    "rotate_key",
    "revoke",
    "revocation_snapshot",
    "memories_by_key",
    "revoke_memories",
    "upgrade_v04_memories",
    "revoke_cache_refresh",
    "messaging_doctor",
    "recovery_init",
    "recovery_backup_confirm",
    "attest_agent",
]
