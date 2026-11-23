#!/bin/bash
# PostToolUse hook: update .last_used.json sidecar when CC reads a memory file.
#
# The ledger is a simple { filename: iso8601-timestamp } JSON map kept in each
# memory folder. It tracks explicit agent reads so memory-audit and related
# tools can distinguish "never touched since creation" from "read recently".
#
# CC's automatic session-start memory load does NOT fire a Read tool event,
# so this hook only captures reads the agent chose to perform. That's the
# intended coverage: rote session-loading is not meaningful use.
#
# Failures are swallowed so a hook blip never breaks the tool call.

set +e

# Claude Code names each project folder after the project's path, so any
# ~/.claude/projects/<project>/memory/ folder is a per-project memory root.
projects_root="$HOME/.claude/projects"
global_root="$HOME/.claude/global-memory"

# Every program below is found the same way as in memory-auto-commit.sh: only
# through absolute PATH entries, and never inside a memory store. The hook can
# run from inside a store, where an empty, "." or relative PATH entry would
# find a program anyone who can commit there has placed; an absolute entry
# into a store would too.
abs_path=""
IFS=: read -r -a path_entries <<<"$PATH"
for entry in "${path_entries[@]}"; do
  [[ "$entry" == /* ]] && abs_path="${abs_path:+$abs_path:}$entry"
done
PATH="$abs_path"

# True when the folder $1 (a real path) is a memory store root or inside one.
# Compared by file identity, so another spelling of the same folder matches.
in_store() {
  local dir=$1
  while :; do
    [[ "$dir" -ef "$projects_root" || "$dir" -ef "$global_root" ]] && return 0
    [[ "$dir" == / || -z "$dir" ]] && return 1
    dir=${dir%/*}
    [[ -n "$dir" ]] || dir=/
  done
}

# Print the path of program $1, or fail when it is not found or its real
# path, after every link, lies inside a store.
program() {
  local bin real dir target hops=0
  bin=$(command -v "$1" 2>/dev/null) || return 1
  [[ "$bin" == /* ]] || return 1
  real=$bin
  while [[ -L "$real" ]]; do
    (( hops++ < 40 )) || return 1
    target=$("$readlink_bin" "$real") || return 1
    [[ "$target" == /* ]] || target="${real%/*}/$target"
    dir=$(cd -P "${target%/*}" 2>/dev/null && pwd -P) || return 1
    in_store "$dir" && return 1
    real="$dir/${target##*/}"
  done
  dir=$(cd -P "${real%/*}" 2>/dev/null && pwd -P) || return 1
  in_store "$dir" && return 1
  printf '%s\n' "$bin"
}

# program() follows links with readlink, so readlink itself cannot be found
# that way. The first readlink on PATH that is a regular file, not a link, in
# a folder outside the stores is used; a link there could point at one
# planted in a store. A Homebrew coreutils readlink is such a link, and the
# system one after it on PATH is then used. None: the hook does nothing.
readlink_bin=""
for entry in "${path_entries[@]}"; do
  [[ "$entry" == /* && -f "$entry/readlink" && -x "$entry/readlink" && ! -L "$entry/readlink" ]] \
    || continue
  readlink_dir=$(cd -P "$entry" 2>/dev/null && pwd -P) || continue
  in_store "$readlink_dir" && continue
  readlink_bin="$readlink_dir/readlink"
  break
done
[[ -n "$readlink_bin" ]] || exit 0

cat_bin=$(program cat) && jq_bin=$(program jq) && basename_bin=$(program basename) \
  && date_bin=$(program date) && mktemp_bin=$(program mktemp) && mv_bin=$(program mv) \
  && rm_bin=$(program rm) || exit 0

payload=$("$cat_bin")
tool_name=$(printf '%s' "$payload" | "$jq_bin" -r '.tool_name // empty')
file_path=$(printf '%s' "$payload" | "$jq_bin" -r '.tool_input.file_path // empty')

[[ "$tool_name" != "Read" ]] && exit 0
[[ -z "$file_path" ]] && exit 0

# A path with a "." or ".." segment can name a folder outside the one it
# appears to be in, so it is never acted on.
case "/$file_path/" in
  */./*|*/../*) exit 0 ;;
esac

case "$file_path" in
  "$projects_root"/*/memory/*.md)
    rest="${file_path#"$projects_root"/}"
    project="${rest%%/*}"
    # "projects//memory/" names no project; "projects/memory" is not a store.
    [[ -n "$project" ]] || exit 0
    [[ "${rest#"$project"/}" == memory/* ]] || exit 0
    folder="$projects_root/$project/memory"
    ;;
  "$global_root"/*.md)  folder="$global_root" ;;
  *) exit 0 ;;
esac

filename=$("$basename_bin" "$file_path")

# MEMORY.md is the index; skip it. Reads of the index at session start or by
# tooling aren't "use" signals in the semantic sense we're tracking.
[[ "$filename" == "MEMORY.md" ]] && exit 0

ledger="$folder/.last_used.json"
now=$("$date_bin" -u +%Y-%m-%dT%H:%M:%SZ)

# The ledger is read and replaced only when it is a regular file, never
# through a link: a committed link would have the hook copy whatever file it
# names into the store, or create one outside it. A missing ledger is created
# with noclobber, which fails rather than follow a link that appears first.
[[ -L "$ledger" ]] && exit 0
[[ -e "$ledger" && ! -f "$ledger" ]] && exit 0
if [[ ! -e "$ledger" ]]; then
  ( set -o noclobber; printf '%s\n' '{}' > "$ledger" ) 2>/dev/null || exit 0
fi

tmp=$("$mktemp_bin") || exit 0
# The single-quoted text is a jq program; jq expands $f and $t, not the shell.
# shellcheck disable=SC2016
if "$jq_bin" --arg f "$filename" --arg t "$now" '.[$f] = $t' "$ledger" > "$tmp" 2>/dev/null; then
  "$mv_bin" "$tmp" "$ledger"
else
  "$rm_bin" -f "$tmp"
fi

exit 0
