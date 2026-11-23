"""`memforge paths`: print the default memory folders for scripts.

Adapters that cannot import the package (shell and PowerShell wrappers) call
this so they get the same folders as every memforge tool, instead of carrying
their own copy of the naming rule in memforge.paths.
"""

from __future__ import annotations

import argparse
import os
import sys

from memforge.paths import default_memory_paths


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "paths",
        help="Print the default memory folders, one per line.",
        description=(
            "Print the default memory folders, one per line, in the order memforge "
            "tools search them, and nothing else on stdout. Every folder is printed "
            "whether or not it exists; a caller tests each one itself. Exit 0 on "
            "success, 1 if a folder name holds a newline and cannot be printed one "
            "per line. Changes nothing."
        ),
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    # Bytes, not text, so a folder name the locale cannot encode still prints exactly.
    lines = [os.fsencode(folder) for folder in default_memory_paths()]
    if any(b"\n" in line for line in lines):
        print("memforge paths: a memory folder name holds a newline; set MEMFORGE_MEMORY_PATH "
              "to folders without one.", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(b"".join(line + b"\n" for line in lines))
    sys.stdout.buffer.flush()
    return 0
