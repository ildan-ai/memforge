"""Top-level `memforge` CLI dispatcher.

Wires the `memforge` subcommands under one argparse entry point. Each
subcommand module exposes `register(subparsers)`.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from memforge._terminal import terminal_safe
from memforge.cli import _removed, init_store, paths, sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memforge",
        # Do not hardcode a point version in the --help text; the package
        # version is available via `memforge --version`.
        description="MemForge reference CLI: memory-root setup and commit trust.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=_version_string(),
    )
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    init_store.register(sub)
    sync.register(sub)
    paths.register(sub)
    _removed.register(sub)

    return p


@terminal_safe
def main(argv=None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    # A removed command answers before parsing, so any trailing flags or
    # arguments still get the notice rather than a usage error.
    if args_list and args_list[0] in _removed.REMOVED_COMMANDS:
        return _removed.run(args_list[0])
    parser = build_parser()
    args = parser.parse_args(args_list)
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
    except Exception as exc:  # noqa: BLE001 - top-level partner-facing guard
        # A clean `memforge: <error>` and exit 1 for any exception a handler
        # did not catch, instead of a raw traceback. The full traceback is
        # available behind MEMFORGE_DEBUG.
        if os.environ.get("MEMFORGE_DEBUG"):
            traceback.print_exc()
        print(f"memforge: {exc}", file=sys.stderr)
        return 1


def _version_string() -> str:
    try:
        from importlib.metadata import version
        return f"memforge {version('ildan-memforge')}"
    except Exception:
        return "memforge (version unavailable)"


if __name__ == "__main__":
    sys.exit(main())
