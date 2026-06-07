#!/usr/bin/env python3
# MemForge recall hook for Claude Code (UserPromptSubmit).
#
# Injects the descriptions of memories whose triggers match the user's prompt so
# the agent recalls the right memory at the right moment, instead of bulk-loading
# the whole MEMORY.md index every session.
#
# It shells out (via subprocess, NOT a shell) to the INSTALLED `memory-recall`
# reader (pip install ildan-memforge), which reads a precompiled recall index.
# Keep the index fresh with `memory-index-gen --with-recall-index` (or the
# auto-commit hook's `memory-recall --rebuild` refresh).
#
# Register in ~/.claude/settings.json under "UserPromptSubmit":
#   {
#     "hooks": [
#       { "type": "command",
#         "command": "python3 /ABSOLUTE/PATH/adapters/claude-code/hooks/memory_recall_hook.py",
#         "timeout": 10 }
#     ]
#   }
#
# Reads the UserPromptSubmit hook JSON payload on stdin; writes matched-memory
# context to stdout (which Claude Code injects as context for the turn).
#
# Fail-open-empty: any failure writes nothing and exits 0. Recall never blocks a
# prompt (spec v0.6.0 §"Recall operation", post-condition 7). A subprocess
# timeout bounds latency. This is a Python hook (not bash) so it is portable to
# Windows and avoids shell-parsing fragility.

import json
import subprocess
import sys

MAX_PROMPT_BYTES = 65536  # cap the prompt handed to the reader (DoS guard)
RECALL_TIMEOUT_SECONDS = 3


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return 0
        prompt = prompt[:MAX_PROMPT_BYTES]
        result = subprocess.run(
            ["memory-recall", "--stdin"],
            input=prompt,
            encoding="utf-8",
            capture_output=True,
            timeout=RECALL_TIMEOUT_SECONDS,
            check=False,
        )
        if result.stdout:
            sys.stdout.write(result.stdout)
    except FileNotFoundError:
        pass  # memforge / memory-recall not installed -> nothing to do
    except Exception:
        pass  # fail-open-empty: never block or fail the prompt
    return 0


if __name__ == "__main__":
    sys.exit(main())
