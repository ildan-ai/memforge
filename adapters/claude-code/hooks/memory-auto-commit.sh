#!/bin/bash
# PostToolUse hook: auto-commit changes inside memory folders.
# Runs after every Write/Edit on a path under a Claude Code project memory
# folder or the global-memory folder. Failures are swallowed so the tool never
# appears to fail because of versioning.

set +e

# Claude Code names each project folder after the project's path, so any
# ~/.claude/projects/<project>/memory/ folder is a per-project memory root.
projects_root="$HOME/.claude/projects"
global_root="$HOME/.claude/global-memory"

# Every program below is found the same way as in memory-read-tracker.sh:
# only through absolute PATH entries, and never inside a memory store. The
# hook runs from inside a store, where an empty, "." or relative PATH entry
# would find a program anyone who can commit there has placed; an absolute
# entry into a store would too.
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
# that way. The first readlink on PATH in a folder outside the stores is used
# when it is a regular file, or a link in /usr/bin or /bin: a link elsewhere
# could point at one planted in a store, and only root can change those two
# folders. A Homebrew coreutils readlink is a link elsewhere, so the system
# one after it on PATH is used. uutils coreutils and busybox install readlink
# as a link in /usr/bin or /bin; it is run by that link, since a multi-call
# binary picks its command by the name it is started as, and refused when its
# target lies in a store. None: the hook does nothing.
readlink_bin=""
for entry in "${path_entries[@]}"; do
  [[ "$entry" == /* && -f "$entry/readlink" && -x "$entry/readlink" ]] || continue
  readlink_dir=$(cd -P "$entry" 2>/dev/null && pwd -P) || continue
  in_store "$readlink_dir" && continue
  if [[ -L "$entry/readlink" ]]; then
    [[ "$readlink_dir" == /usr/bin || "$readlink_dir" == /bin ]] || continue
    target=$("$readlink_dir/readlink" -f "$readlink_dir/readlink") || continue
    [[ "$target" == /* ]] || continue
    target_dir=$(cd -P "${target%/*}" 2>/dev/null && pwd -P) || continue
    in_store "$target_dir" && continue
  fi
  readlink_bin="$readlink_dir/readlink"
  break
done
[[ -n "$readlink_bin" ]] || exit 0

cat_bin=$(program cat) && jq_bin=$(program jq) && git_bin=$(program git) \
  && tr_bin=$(program tr) || exit 0

payload=$("$cat_bin")
file_path=$(printf '%s' "$payload" | "$jq_bin" -r '.tool_input.file_path // empty')
tool_name=$(printf '%s' "$payload" | "$jq_bin" -r '.tool_name // "unknown"')

[[ -z "$file_path" ]] && exit 0

# A path with a "." or ".." segment can name a folder outside the one it
# appears to be in, so it is never acted on.
case "/$file_path/" in
  */./*|*/../*) exit 0 ;;
esac

case "$file_path" in
  "$projects_root"/*/memory/*)
    rest="${file_path#"$projects_root"/}"
    project="${rest%%/*}"
    # "projects//memory/" names no project; "projects/memory" is not a store.
    [[ -n "$project" ]] || exit 0
    [[ "${rest#"$project"/}" == memory/* ]] || exit 0
    repo_root="$projects_root/$project/memory"
    ;;
  "$global_root"/*)  repo_root="$global_root" ;;
  *) exit 0 ;;
esac

[[ ! -d "$repo_root/.git" ]] && exit 0

cd "$repo_root" || exit 0

# git must act on this store. These are the variables
# `git rev-parse --local-env-vars` lists: a hook started with any of them set
# would read or commit to another repository, index or work tree instead.
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS \
  GIT_CONFIG_COUNT GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE \
  GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS \
  GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE GIT_COMMON_DIR
# Names are read literally below with the `:(literal)` pathspec magic. This
# variable would do the same, but git hands it to the user's hooks, where a
# pre-commit hook selecting files with '*.md' then matches nothing.
unset GIT_LITERAL_PATHSPECS

# Auto-normalize frontmatter to v0.4 shape if backfill CLI is available.
# Closes the gap when an agent writes v0.3-shaped frontmatter (e.g., the
# Claude Code harness auto-memory instruction emits 3 fields). Idempotent;
# silently skipped if memory-frontmatter-backfill is not on PATH.
backfill_bin=$(program memory-frontmatter-backfill)
if [[ -n "$backfill_bin" ]]; then
  "$backfill_bin" --apply --path "$repo_root" >/dev/null 2>&1 || true
fi

# Refresh the recall index (spec v0.6.0) so query-triggered recall stays in sync
# with the memory just written. Recall-index-only refresh (does not regenerate
# MEMORY.md); idempotent; silently skipped if memory-recall is not on PATH.
recall_bin=$(program memory-recall)
if [[ -n "$recall_bin" ]]; then
  "$recall_bin" --rebuild --path "$repo_root" >/dev/null 2>&1 || true
fi

if "$git_bin" diff --quiet && "$git_bin" diff --cached --quiet \
  && [[ -z "$("$git_bin" ls-files --others --exclude-standard)" ]]; then
  exit 0
fi

rel_path="${file_path#"$repo_root"/}"

# The names come from the tool call. A newline would add lines, and so
# trailers, to a commit made under the store owner's signature, so the
# message carries a one-line form with every control character replaced.
one_line() { printf '%s' "$1" | LC_ALL=C "$tr_bin" '[:cntrl:]' '?'; }
msg="memory: $(one_line "$tool_name") $(one_line "$rel_path")"

# Scope: the written file and the memory files the helpers above rewrite
# (MEMORY.md included). Nothing under .memforge/: a signer list change must
# land in a deliberate commit by a listed signer, and config, snoozes and
# waivers only in their own `memforge:` commits, not because a tool call wrote
# a file. The recall index is a local cache, rebuilt from the memory files, and
# is never committed. Anything else, a file staged by hand included, stays
# where it is.
excluded=(':(exclude,icase).memforge')
included=(":(literal)$rel_path" '*.md')

# One add per path: git refuses the whole add when any one path matches nothing.
for spec in "${included[@]}"; do
  "$git_bin" add -A -- "$spec" "${excluded[@]}" >/dev/null 2>&1
done

names=()
while IFS= read -r -d '' name; do
  names+=("$name")
done < <("$git_bin" diff --cached --name-only -z -- "${included[@]}" "${excluded[@]}" 2>/dev/null)
[[ ${#names[@]} -eq 0 ]] && exit 0

# Commit exactly the names just listed, read literally, so a staged file
# outside the scope stays staged and a file name cannot act as a pattern.
literal_names=()
for name in "${names[@]}"; do
  literal_names+=(":(literal)$name")
done
"$git_bin" commit -q -m "$msg" -- "${literal_names[@]}" >/dev/null 2>&1 || true
exit 0
