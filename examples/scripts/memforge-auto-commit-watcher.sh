#!/usr/bin/env bash
# MemForge auto-commit watcher. Watches a memory-root for memory `.md`
# writes and auto-commits each one as it lands. Skips everything under
# `.memforge/` (config, snoozes, waivers, signer lists): those change only
# in deliberate commits with their own `memforge:` prefixes.
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
#   ./memforge-auto-commit-watcher.sh ~/.memforge/global-memory &
# or any folder `memforge paths` prints.
#
# Stop with `kill <PID>` or fg + Ctrl-C.

set -e

ROOT="${1:-$PWD}"
cd "$ROOT"

# git must act on this folder. These are the variables
# `git rev-parse --local-env-vars` lists: a watcher started with any of them
# set would commit to another repository, index or work tree instead.
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS \
  GIT_CONFIG_COUNT GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE \
  GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS \
  GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE GIT_COMMON_DIR

# File names come from whoever can write into the folder. Each is read as a
# plain name with the `:(literal)` pathspec magic below, never as a pattern.
# GIT_LITERAL_PATHSPECS would do the same, but git hands it to your hooks,
# where a pre-commit hook selecting files with '*.md' then matches nothing.
unset GIT_LITERAL_PATHSPECS

scoped_path() {
  case "$1" in
    # Any letter case: on a case-insensitive file system it is the same folder.
    .[Mm][Ee][Mm][Ff][Oo][Rr][Gg][Ee]/*) return 0 ;;
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
  git add -- ":(literal)$rel" 2>/dev/null || return 0
  if git diff --cached --quiet -- ":(literal)$rel"; then
    return 0
  fi
  # Commit this file only. Anything else already staged stays staged.
  git commit -m "memory: auto-commit $rel" -- ":(literal)$rel" >/dev/null 2>&1 || true
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
