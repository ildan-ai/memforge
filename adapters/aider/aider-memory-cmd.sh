#!/bin/bash
# aider-memory-cmd.sh — convenience shell function for aider + MemForge.
#
# Source from ~/.bashrc or ~/.zshrc:
#   source ~/claude-projects/memforge/adapters/aider/aider-memory-cmd.sh
#
# Then invoke `aider-mem <args>` instead of `aider <args>`. Reads global
# + per-cwd memory at session start. Edit the paths below to match your
# layout.

aider-mem() {
  local mem_global="$HOME/.claude/global-memory/MEMORY.md"
  local mem_per_cwd="$HOME/.claude/projects/${USER}-claude-projects/memory/MEMORY.md"
  local read_args=()

  [[ -f "$mem_global" ]] && read_args+=("--read" "$mem_global")
  [[ -f "$mem_per_cwd" ]] && read_args+=("--read" "$mem_per_cwd")

  if [[ ${#read_args[@]} -eq 0 ]]; then
    echo "warning: no memory folders found at default paths; running aider without memory context" >&2
  fi

  aider "${read_args[@]}" "$@"
}
