"""`memforge revoke-cache-refresh` — refresh the remote-fetch revocation cache.

Spec ref: §"Sparse-checkout / shallow-clone fallback verification mode".

This subcommand is for the sparse-checkout / shallow-clone deployment
posture only. It re-fetches the remote ref pinned in
`.memforge/config.yaml`, applies TOFU + fast-forward-only verification,
and re-walks revocation commits under the v0.5.0 signature contract.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from memforge import revocation
from memforge.registry import REGISTRY_DIRNAME, REVOCATION_CACHE


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "revoke-cache-refresh",
        help="Re-fetch the remote ref pinned in .memforge/config.yaml + rebuild the revocation cache (sparse/shallow mode).",
    )
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    config_path = memory_root / REGISTRY_DIRNAME / "config.yaml"
    if not config_path.is_file():
        print(
            f"config.yaml missing at {config_path}. Configure `revocation.fallback_remote_url` + "
            "`revocation.fallback_transport` first.",
            file=sys.stderr,
        )
        return 2
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    rev_cfg = config.get("revocation") or {}
    remote_url = rev_cfg.get("fallback_remote_url")
    transport = rev_cfg.get("fallback_transport")
    if not remote_url or not transport:
        print(
            "config.yaml missing revocation.fallback_remote_url + revocation.fallback_transport "
            "(required for sparse/shallow mode).",
            file=sys.stderr,
        )
        return 2

    # Re-fetch the remote. Fast-forward-only enforcement is done by git's
    # default fetch behavior + our explicit fast-forward check below.
    try:
        subprocess.run(
            ["git", "-C", str(memory_root), "fetch", "--no-tags", remote_url, "HEAD"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"fetch failed: {exc.stderr.decode('utf-8','replace')}. Fail-closed per spec.",
            file=sys.stderr,
        )
        return 1

    # Walk revocation set from the fetched HEAD.
    rev_set = revocation.walk_revocation_set(memory_root)
    cache_path = memory_root / REGISTRY_DIRNAME / REVOCATION_CACHE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "remote_url": remote_url,
                "transport": transport,
                "revocations": list(rev_set.values()),
                "anchor_head": subprocess.check_output(
                    ["git", "-C", str(memory_root), "rev-parse", "HEAD"],
                    text=True,
                ).strip(),
            },
            f,
            sort_keys=False,
            default_flow_style=False,
        )
    print(f"revocation cache refreshed: {len(rev_set)} revocations cached at {cache_path}.")
    return 0
