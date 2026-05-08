#!/bin/bash
# PostToolUse hook: auto-commit changes inside memory folders.
# Runs after every Write/Edit on a path under the per-cwd memory folder
# or the global-memory folder. Failures are swallowed so the tool never
# appears to fail because of versioning.

set +e

payload=$(cat)
file_path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')
tool_name=$(printf '%s' "$payload" | jq -r '.tool_name // "unknown"')

[[ -z "$file_path" ]] && exit 0

per_cwd_root="$HOME/.claude/projects/${USER}-claude-projects/memory"
global_root="$HOME/.claude/global-memory"

case "$file_path" in
  "$per_cwd_root"/*) repo_root="$per_cwd_root" ;;
  "$global_root"/*)  repo_root="$global_root" ;;
  *) exit 0 ;;
esac

[[ ! -d "$repo_root/.git" ]] && exit 0

cd "$repo_root" || exit 0

# Auto-normalize frontmatter to v0.4 shape if backfill CLI is available.
# Closes the gap when an agent writes v0.3-shaped frontmatter (e.g., the
# Claude Code harness auto-memory instruction emits 3 fields). Idempotent;
# silently skipped if memory-frontmatter-backfill is not on PATH.
backfill_bin=$(command -v memory-frontmatter-backfill 2>/dev/null)
if [[ -n "$backfill_bin" ]]; then
  "$backfill_bin" --apply --path "$repo_root" >/dev/null 2>&1 || true
fi

if git diff --quiet && git diff --cached --quiet && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
  exit 0
fi

rel_path="${file_path#$repo_root/}"
msg="memory: $tool_name $rel_path"

git add -A >/dev/null 2>&1
git commit -q -m "$msg" >/dev/null 2>&1 || true
exit 0
