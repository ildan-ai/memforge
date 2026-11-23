#!/bin/bash
# aider-memory-cmd.sh: convenience shell function for aider + MemForge.
#
# Source from ~/.bashrc or ~/.zshrc:
#   source "<path to your memforge clone>/adapters/aider/aider-memory-cmd.sh"
#
# Then invoke `aider-mem <args>` instead of `aider <args>`. Reads the
# MEMORY.md of each default memory folder at session start. The folders come
# from `memforge paths`, so this wrapper finds the same store as every
# memforge tool (MEMFORGE_MEMORY_PATH, else the Claude Code layout, else
# ~/.memforge). Needs `memforge` on PATH.

aider-mem() {
  if ! command -v memforge >/dev/null 2>&1; then
    echo "aider-mem: memforge is not on PATH, so the memory folders are unknown. Install memforge or add it to PATH; set MEMFORGE_MEMORY_PATH to choose the folders it reports." >&2
    return 1
  fi
  local folders folder
  folders=$(memforge paths) || {
    echo "aider-mem: memforge paths failed; not starting aider." >&2
    return 1
  }
  local read_args=()

  while IFS= read -r folder; do
    [[ -n "$folder" && -f "$folder/MEMORY.md" ]] && read_args+=("--read" "$folder/MEMORY.md")
  done <<< "$folders"

  if [[ ${#read_args[@]} -eq 0 ]]; then
    echo "warning: no memory folders found at default paths; running aider without memory context" >&2
  fi

  aider "${read_args[@]}" "$@"
}
