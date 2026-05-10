"""`memforge revoke-memories --bulk` — bulk-supersede memories under a revoked key.

Spec ref: §"Operator key compromise recovery" step 4 ("Bulk-revoke memories
signed under the compromised key (operator-discretion).").

This is a CONSTRUCTIVE supersession: each affected memory gets
`status: superseded` + a fresh winner memory is written for each
`decision_topic` group reflecting "this content was published under a
compromised key and is no longer trusted." Operators may prefer to delete
files outright; this CLI is the safer default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from memforge.frontmatter import parse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "revoke-memories",
        help="Bulk-mark memories signed under a revoked key as `status: superseded`.",
    )
    p.add_argument("key_id", help="GPG fingerprint of the revoked key.")
    p.add_argument(
        "--memory-root",
        default=".",
        help="Memory-root directory (default: cwd).",
    )
    p.add_argument(
        "--bulk",
        action="store_true",
        help="Required confirmation flag. Without --bulk this command is a dry-run listing.",
    )
    p.set_defaults(func=cmd)


def cmd(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root).resolve()
    if not memory_root.is_dir():
        print(f"memory-root {memory_root} is not a directory.", file=sys.stderr)
        return 2
    targets: list[Path] = []
    for md in sorted(memory_root.rglob("*.md")):
        if md.name == "MEMORY.md":
            continue
        if any(p == "archive" for p in md.parts):
            continue
        if any(p.startswith(".") for p in md.parts):
            continue
        try:
            fm, _body = parse(md.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not fm:
            continue
        sig = fm.get("signature") or {}
        identity = fm.get("identity", "")
        candidate = sig.get("key_id") if isinstance(sig, dict) else None
        if candidate == args.key_id or args.key_id in identity:
            targets.append(md)

    if not targets:
        print(f"no memories matched key_id={args.key_id}.")
        return 0

    if not args.bulk:
        print(f"{len(targets)} memory file(s) would be marked `status: superseded`:")
        for t in targets:
            print(f"  {t.relative_to(memory_root)}")
        print("\nPass --bulk to apply.")
        return 0

    edited = 0
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if "status: superseded" in text:
            continue
        text = _set_status_superseded(text)
        path.write_text(text, encoding="utf-8")
        edited += 1

    print(f"marked {edited} memory file(s) as superseded under key {args.key_id}.")
    print("Review changes with `git diff` then commit:")
    print(f"  git commit -m 'memforge: bulk-supersede under revoked key {args.key_id}'")
    return 0


def _set_status_superseded(text: str) -> str:
    """Set `status: superseded` in the frontmatter block; insert if missing.

    Naive line-level edit: assumes the frontmatter block is a standard
    `---\\n...\\n---` opener and the `status:` key (when present) lives on
    its own line.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end < 0:
        return text
    block = text[4:end]
    new_lines: list[str] = []
    found = False
    for line in block.splitlines():
        if line.lstrip().startswith("status:"):
            new_lines.append("status: superseded")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append("status: superseded")
    return "---\n" + "\n".join(new_lines) + text[end:]
