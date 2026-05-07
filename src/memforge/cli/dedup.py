"""memory-dedup — flag near-duplicate entries in a memory folder via LLM.

Reports candidate pairs/groups for the user to review; never modifies files.

Defaults to --local-only mode (sends to a local model only) per the
2026-05-07 code-review-panel BLOCKER on uncontrolled cloud-LLM exfil of
catalog data. Disable with --allow-cloud-dispatcher only when the operator
has confirmed the catalog contains no sensitive content.

Per spec v0.3.0: descriptions are short, public-classification metadata.
Operators MUST NOT place PII, credentials, or other sensitive data in
description fields. memory-dedup warns on descriptions > 50 characters
(default) and refuses to ship descriptions at all in --redact-descriptions
mode.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from memforge.frontmatter import parse  # noqa: E402

DEFAULT_PATH = Path.home() / ".claude" / "global-memory"

# Dispatchers we know route to a local-only model. Anything else, in
# --local-only mode, is rejected with a clear error. Operators bring
# their own dispatcher; the patterns below recognize common local-model
# CLIs (ollama, llama.cpp, lm-studio) and a small allowlist of local-
# model aliases. To add your own local-dispatcher pattern, set the
# MEMORY_DEDUP_DISPATCHER environment variable or pass --dispatcher.
LOCAL_DISPATCHER_PATTERNS = [
    re.compile(r"\bollama\b"),
    re.compile(r"\bllama\.cpp\b"),
    re.compile(r"\blm-studio\b|\blms\b"),
    re.compile(r"--model\s+(gemma2|qwen2|deepseek-coder|llama3|phi3|mistral|local-)"),
]


def is_local_dispatcher(cmd: str) -> bool:
    """Heuristic: does the dispatcher command target a local model?

    Conservative: only patterns we recognize as local pass. Cloud-tier
    aliases explicitly do NOT pass; the operator must opt in via
    --allow-cloud-dispatcher.
    """
    return any(p.search(cmd) for p in LOCAL_DISPATCHER_PATTERNS)


def _which(name: str) -> Optional[str]:
    """Return the absolute path to `name` on $PATH, or None."""
    return shutil.which(name)


def default_dispatcher() -> str:
    """Pick a dispatcher by probing $PATH for known local-model CLIs."""
    for cli, args in (
        ("ollama", "run gemma2:2b"),
        ("llama.cpp", ""),
        ("lms", "chat"),
    ):
        path = _which(cli)
        if path:
            return f"{path} {args}".strip()
    # No local dispatcher on PATH. Return a placeholder that fails the
    # local-pattern check so the operator gets the helpful error message
    # instead of a confusing "command not found".
    return "no-local-dispatcher-found"


def collect_catalog(folder: Path, redact_descriptions: bool, warn_threshold: int) -> tuple[list[Path], list[str], list[str]]:
    """Walk top-level .md files, extract name/type/description.

    Returns (file_paths, catalog_lines, warnings). When redact_descriptions
    is True, descriptions are replaced with "[redacted]" before any prompt
    construction. Warnings list flags any description longer than
    warn_threshold characters so the operator sees them on stderr before
    the dispatch.
    """
    files: list[Path] = []
    lines: list[str] = []
    warnings: list[str] = []

    candidates = sorted(folder.glob("*.md"))
    idx = 0
    for f in candidates:
        if f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = parse(text)
        name = str(fm.get("name", ""))
        type_ = str(fm.get("type", ""))
        desc = str(fm.get("description", ""))

        if len(desc) > warn_threshold:
            warnings.append(f"  {f.name}: description is {len(desc)} chars (>{warn_threshold}); review for sensitive content")

        if redact_descriptions:
            desc_to_ship = "[redacted — pass --no-redact-descriptions to include]"
        else:
            desc_to_ship = desc

        idx += 1
        files.append(f)
        lines.append(f"{idx} | {f.name} | [{type_}] {name} — {desc_to_ship}")

    return files, lines, warnings


PROMPT_TEMPLATE = """You are auditing a personal knowledge base for near-duplicate entries.

Each entry has: id | filename | [type] name — description

CATALOG ({n} entries):
{catalog}

Task: identify groups of entries that cover the same ground and would be candidates for merging. Be CONSERVATIVE: flag only when two or more entries are genuinely redundant, not merely related. Different aspects of the same project are NOT duplicates. Related feedback rules are NOT duplicates unless they state the same rule.

Output ONLY a JSON array of objects, nothing else. Each object has:
- "ids": array of two or more catalog IDs that appear duplicative
- "reason": one-sentence explanation of the overlap

If no duplicates exist, output the empty array: []

Example output:
[{{"ids":[3,14],"reason":"Both entries describe the same meeting reschedule."}}]
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="memory-dedup",
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
SECURITY: This tool ships memory metadata to an LLM. By default it runs
in --local-only mode (only local models accepted) and --redact-descriptions
mode (only filename + name + type sent, not description body). Override
both flags only when the catalog has been confirmed free of sensitive
content. See SPEC.md §sensitivity-and-redaction.
""",
    )
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH,
                        help="Memory folder to scan (default: ~/.claude/global-memory)")
    parser.add_argument("--dispatcher", default=None,
                        help="Command that reads prompt on stdin, prints response on stdout")
    parser.add_argument("--allow-cloud-dispatcher", action="store_true",
                        help="OPT-IN: allow cloud-tier LLM dispatchers (default: refuse). "
                             "Only enable when catalog is confirmed sensitive-content-free.")
    parser.add_argument("--no-redact-descriptions", action="store_true",
                        help="OPT-IN: send description bodies to the LLM (default: redact). "
                             "Only enable when descriptions are confirmed PII/credential-free.")
    parser.add_argument("--description-warn-threshold", type=int, default=50,
                        help="Warn when descriptions exceed this length (default: 50 chars)")
    parser.add_argument("--json", action="store_true",
                        help="Print the raw JSON verdict from the LLM")
    args = parser.parse_args()

    target: Path = args.path.expanduser().resolve()
    if not target.is_dir():
        sys.stderr.write(f"error: path not a directory: {target}\n")
        return 2

    dispatcher = args.dispatcher or os.environ.get("MEMORY_DEDUP_DISPATCHER") or default_dispatcher()

    if not args.allow_cloud_dispatcher and not is_local_dispatcher(dispatcher):
        sys.stderr.write(
            "error: dispatcher does not match a known local-model pattern.\n"
            f"       dispatcher: {dispatcher}\n"
            "       This tool defaults to --local-only mode to prevent uncontrolled\n"
            "       data exfil to cloud LLMs. To proceed:\n"
            "         1. Use a local dispatcher (route --task memory-dedup, ollama, etc), OR\n"
            "         2. Pass --allow-cloud-dispatcher AFTER confirming the catalog\n"
            "            has no sensitive content (no credentials, no PII, no privileged data).\n"
        )
        return 2

    redact = not args.no_redact_descriptions

    files, catalog_lines, warnings = collect_catalog(
        target,
        redact_descriptions=redact,
        warn_threshold=args.description_warn_threshold,
    )
    if not files:
        print(f"No memory files found in {target}")
        return 0

    if warnings:
        sys.stderr.write("⚠ description-length warnings (review before dispatch):\n")
        for w in warnings:
            sys.stderr.write(w + "\n")
        sys.stderr.write("\n")

    sys.stderr.write(
        f"Scanning {len(files)} entries in {target}\n"
        f"  via: {dispatcher}\n"
        f"  local-only: {not args.allow_cloud_dispatcher}\n"
        f"  redact-descriptions: {redact}\n\n"
    )

    catalog = "\n".join(catalog_lines)
    prompt = PROMPT_TEMPLATE.format(n=len(files), catalog=catalog)

    try:
        proc = subprocess.run(
            dispatcher,
            shell=True,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("error: dispatcher timed out after 300s\n")
        return 2

    response = proc.stdout.strip()
    if not response:
        sys.stderr.write(
            f"error: dispatcher returned no output. Check '{dispatcher}' is callable.\n"
            f"       stderr: {proc.stderr.strip()[:200]}\n"
        )
        return 2

    json_text = re.sub(r"^```(?:json)?\s*$", "", response, flags=re.MULTILINE).strip()
    if json_text.endswith("```"):
        json_text = json_text[:-3].rstrip()

    try:
        groups = json.loads(json_text)
        if not isinstance(groups, list):
            raise ValueError("response is not a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"warning: response was not valid JSON ({e}). Raw response:\n----\n{response}\n----\n")
        return 2

    if args.json:
        print(json.dumps(groups, indent=2))
        return 0

    if not groups:
        print(f"No near-duplicates flagged across {len(files)} entries.")
        return 0

    print(f"Candidate duplicate groups ({len(groups)}):\n")
    for g in groups:
        ids = g.get("ids", [])
        reason = g.get("reason", "")
        names: list[str] = []
        for i in ids:
            try:
                idx = int(i) - 1
                if 0 <= idx < len(files):
                    names.append(files[idx].name)
            except (TypeError, ValueError):
                continue
        if not names:
            continue
        print(f"- {', '.join(str(i) for i in ids)}")
        print(f"    files: {', '.join(names)}")
        print(f"    reason: {reason}")
        print()

    print("Review each group and decide: merge, keep both, or drop one.")
    print("memory-dedup never edits memory files; it only surfaces candidates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
