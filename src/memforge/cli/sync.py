"""`memforge sync`: verify the shared branch by commit trust, then fast-forward.

Spec ref: "Commit trust".
"""

from __future__ import annotations

import argparse
from pathlib import Path

from memforge import sync as sync_mod


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "sync",
        help="Fetch the shared branch, verify every commit against the committed signer list, then fast-forward.",
    )
    p.add_argument("--memory-root", default=".",
                   help="Memory-root directory (default: cwd). It must be the top level of its git repository, "
                        "because one verifiedHead is kept per repository and cannot serve two stores.")
    p.add_argument("--check", action="store_true",
                   help="Fetch and verify only; change no local branch, work tree or verifiedHead.")
    p.add_argument("--trust-head", metavar="COMMIT",
                   help="First sync: the full commit id you vouch for, up to and including it.")
    p.add_argument("--adopt-local", action="store_true",
                   help="First sync: pin local HEAD. A statement, not a check.")
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    return sync_mod.run_sync(
        Path(args.memory_root).resolve(),
        trust_head=args.trust_head,
        adopt_local=args.adopt_local,
        check=args.check,
    )
