#!/bin/bash
# PostToolUse hook: update .last_used.json sidecar when CC reads a memory file.
#
# The ledger is a simple { filename: iso8601-timestamp } JSON map kept in each
# memory folder. It tracks explicit agent reads so memory-audit and related
# tools can distinguish "never touched since creation" from "read recently".
#
# CC's automatic session-start memory load does NOT fire a Read tool event,
# so this hook only captures reads the agent chose to perform. That's the
# intended coverage — rote session-loading is not meaningful use.
#
# Failures are swallowed so a hook blip never breaks the tool call.

set +e

payload=$(cat)
tool_name=$(printf '%s' "$payload" | jq -r '.tool_name // empty')
file_path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')

[[ "$tool_name" != "Read" ]] && exit 0
[[ -z "$file_path" ]] && exit 0

per_cwd_root="$HOME/.claude/projects/${USER}-claude-projects/memory"
global_root="$HOME/.claude/global-memory"

case "$file_path" in
  "$per_cwd_root"/*.md) folder="$per_cwd_root" ;;
  "$global_root"/*.md)  folder="$global_root" ;;
  *) exit 0 ;;
esac

filename=$(basename "$file_path")

# MEMORY.md is the index; skip it. Reads of the index at session start or by
# tooling aren't "use" signals in the semantic sense we're tracking.
[[ "$filename" == "MEMORY.md" ]] && exit 0

ledger="$folder/.last_used.json"
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

[[ ! -f "$ledger" ]] && printf '%s\n' '{}' > "$ledger"

tmp=$(mktemp) || exit 0
if jq --arg f "$filename" --arg t "$now" '.[$f] = $t' "$ledger" > "$tmp" 2>/dev/null; then
  mv "$tmp" "$ledger"
else
  rm -f "$tmp"
fi

exit 0
