"""Top-level `memforge` CLI dispatcher (v0.5.1+).

Wires every v0.5.1 reference subcommand under a single argparse-based
entry point. Each subcommand module exposes `register(subparsers)`.
"""

from __future__ import annotations

import argparse
import sys

from memforge.cli.v05 import (
    attest_agent,
    init_operator,
    init_store,
    memories_by_key,
    messaging_doctor,
    operator_registry,
    recovery_backup_confirm,
    recovery_init,
    revocation_snapshot,
    revoke,
    revoke_cache_refresh,
    revoke_memories,
    rotate_key,
    upgrade_v04_memories,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memforge",
        description="MemForge v0.5.1 reference CLI for operator + agent identity, "
        "operator-registry, key rotation + revocation, agent session attestation, "
        "and messaging-adapter diagnostics.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=_version_string(),
    )
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    init_operator.register(sub)
    init_store.register(sub)
    operator_registry.register(sub)
    rotate_key.register(sub)
    revoke.register(sub)
    revocation_snapshot.register(sub)
    memories_by_key.register(sub)
    revoke_memories.register(sub)
    upgrade_v04_memories.register(sub)
    revoke_cache_refresh.register(sub)
    messaging_doctor.register(sub)
    recovery_init.register(sub)
    recovery_backup_confirm.register(sub)
    attest_agent.register(sub)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help(sys.stderr)
        return 1
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return 1
    try:
        return int(func(args) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def _version_string() -> str:
    try:
        from importlib.metadata import version
        return f"memforge {version('ildan-memforge')}"
    except Exception:
        return "memforge (version unavailable)"


if __name__ == "__main__":
    sys.exit(main())
