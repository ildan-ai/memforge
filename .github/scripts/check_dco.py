#!/usr/bin/env python3
"""Fail a pull request when any of its commits lacks a matching DCO sign-off.

A commit passes when its message carries a `Signed-off-by:` trailer whose email
matches the commit author's email (case-insensitive). Merge commits are skipped.

Usage: check_dco.py <base-sha> <head-sha>
Exit 0 when every commit in base..head is signed off, 1 otherwise, 2 on usage error.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

SIGNOFF = re.compile(r"^Signed-off-by: .+ <([^<>\s]+)>\s*$", re.MULTILINE)
FIELD = "\x1f"
RECORD = "\x1e"


@dataclass(frozen=True)
class Commit:
    sha: str
    author_email: str
    message: str


def unsigned(commits: list[Commit]) -> list[Commit]:
    return [
        c for c in commits
        if c.author_email.lower() not in {e.lower() for e in SIGNOFF.findall(c.message)}
    ]


def read_commits(base: str, head: str) -> list[Commit]:
    out = subprocess.run(
        ["git", "log", "--no-merges", f"--format=%H{FIELD}%ae{FIELD}%B{RECORD}", f"{base}..{head}"],
        check=True, capture_output=True, text=True,
    ).stdout
    commits = []
    for record in out.split(RECORD):
        if record.strip():
            sha, email, message = record.strip("\n").split(FIELD, 2)
            commits.append(Commit(sha, email, message))
    return commits


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_dco.py <base-sha> <head-sha>", file=sys.stderr)
        return 2
    missing = unsigned(read_commits(*argv))
    for c in missing:
        print(f"{c.sha[:12]}: no 'Signed-off-by:' matching author <{c.author_email}>")
    if missing:
        print("Sign off each commit with `git commit -s` (see CONTRIBUTING.md).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
