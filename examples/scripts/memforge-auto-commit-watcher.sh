#!/usr/bin/env bash
# MemForge auto-commit watcher. Watches a memory-root for memory `.md`
# writes and auto-commits each one as it lands. Explicitly skips Tier 2
# scoped paths so it does not race the `memforge` CLI which lands its
# own scoped commits with the right prefixes.
#
# Requirements:
#   - macOS: brew install fswatch
#   - Linux: apt install inotify-tools (or distro equivalent)
#   - python3 (used for portable relative-path computation)
#
# Usage:
#   ./memforge-auto-commit-watcher.sh [<memory-root>]
#
# Run from a memory-root (or pass the path as $1), in the background:
#   ./memforge-auto-commit-watcher.sh ~/.claude/global-memory &
#
# Stop with `kill <PID>` or fg + Ctrl-C.

set -e

ROOT="${1:-$PWD}"
cd "$ROOT"

scoped_path() {
  case "$1" in
    .memforge/operator-registry.yaml|.memforge/config.yaml) return 0 ;;
    .memforge/snoozes/*|.memforge/revocations/*|.memforge/sender-sequence/*|.memforge/agent-sessions/*) return 0 ;;
    *) return 1 ;;
  esac
}

handle() {
  local path="$1"
  [ -f "$path" ] || return 0
  local rel
  rel=$(python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$path" "$ROOT" 2>/dev/null || echo "$path")
  scoped_path "$rel" && return 0
  case "$rel" in
    *.md) ;;
    *) return 0 ;;
  esac
  git add -- "$rel" 2>/dev/null || return 0
  if git diff --cached --quiet -- "$rel"; then
    return 0
  fi
  git commit -m "memory: auto-commit $rel" >/dev/null 2>&1 || true
}

if command -v fswatch >/dev/null 2>&1; then
  fswatch -0 -r "$ROOT" | while IFS= read -r -d '' path; do handle "$path"; done
elif command -v inotifywait >/dev/null 2>&1; then
  inotifywait -m -r -e close_write --format '%w%f' "$ROOT" | while read -r path; do handle "$path"; done
else
  echo "Neither fswatch (macOS) nor inotifywait (Linux) is installed." >&2
  echo "Install one, or use the Claude Code PostToolUse memory-auto-commit.sh hook." >&2
  exit 1
fi
