"""Shared LLM-dispatcher safety classification for cloud-egress tools.

The local-only default in `memory-dedup` and `memory-lint` depends on
classifying a dispatcher command as local vs. cloud. The prior heuristic
matched a local-looking SUBSTRING anywhere in the command, so
`some-cli --model gemma2` or `curl https://host/path?x=ollama` slipped through
as "local" and could exfiltrate memory content to a cloud endpoint.

This module classifies by the EXECUTABLE (the first shell token, basename),
backed by an allowlist of known local model runners, and rejects any command
carrying a network indicator. It is FAIL-CLOSED: an unrecognized local wrapper
is treated as non-local, which only forces the operator to pass an explicit
`--allow-cloud` / `--allow-cloud-dispatcher` opt-in. It never leaks.

Operators with a custom local runner register its basename via the
`MEMFORGE_LOCAL_DISPATCHERS` env var (comma-separated) instead of weakening
the gate for everyone.
"""

from __future__ import annotations

import os
import re
import shlex

# Unambiguously-local model runners. Deliberately conservative: a tool whose
# locality is configuration-dependent (e.g. a router that may target a cloud
# tier) is NOT listed; the operator allowlists it explicitly if desired.
_LOCAL_EXECUTABLES = {
    "ollama",
    "llama",
    "llama.cpp",
    "llamafile",
    "llama-cli",
    "llama-server",
    "lms",
    "lm-studio",
    "localai",
    "vllm",
    "koboldcpp",
    "jan",
    "mlx_lm.generate",
}

# Command prefixes that wrap the real executable; skip them to find it.
_WRAPPERS = {"env", "nice", "nohup", "stdbuf", "timeout", "ionice", "taskset"}

# Any of these in the command means it can reach the network -> not local.
_NETWORK_RE = re.compile(
    r"https?://|\bcurl\b|\bwget\b|\bnc\b|\bncat\b|\bssh\b|\bscp\b|\bapi\.",
    re.IGNORECASE,
)


def _extra_local() -> set[str]:
    raw = os.environ.get("MEMFORGE_LOCAL_DISPATCHERS", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def is_local_dispatcher(cmd: str) -> bool:
    """True only when the command's executable is a known (or operator-
    registered) local model runner AND the command carries no network
    indicator. Fail-closed on anything unrecognized."""
    if not cmd or not cmd.strip():
        return False
    if _NETWORK_RE.search(cmd):
        return False
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    if not toks:
        return False
    i = 0
    exe = os.path.basename(toks[i]).lower()
    while exe in _WRAPPERS and i + 1 < len(toks):
        i += 1
        exe = os.path.basename(toks[i]).lower()
    return exe in (_LOCAL_EXECUTABLES | _extra_local())
