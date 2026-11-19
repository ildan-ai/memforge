# MemForge quickstart

Single-person setup: from a fresh `pip install` to a working memory store, a clean audit and recall. About 5 minutes.

Sharing the store with other people or agents? Add commit trust once the basics work. See [Team setup](./team-bootstrap.md). Coming from an older version? See [Migrating to 1.0](./migrating-to-1.0.md).

## Supported platforms

macOS, Linux and native Windows.

## Prerequisites

- Python >= 3.10
- git
- For shared stores in `ssh` mode: OpenSSH `ssh-keygen` on your `PATH`. The `gpg` mode needs GnuPG and the `keyless` mode needs `gitsign`. Both modes are refused until their proofs pass.

## Step 1: install

```bash
pip install ildan-memforge
memforge --version
# prints the installed package version
```

On macOS / Homebrew Python or any system with `EXTERNALLY-MANAGED` Python, prefer:

```bash
brew install pipx
pipx install ildan-memforge
```

On Windows (PowerShell):

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
pipx install ildan-memforge
memforge --version
```

## Step 2: create a store

```bash
cd /path/to/your/project-memory-root   # for a shared store, this must be the top level of its git repository
git init            # if it is not a git repository yet
memforge init-store
```

This creates the `.memforge/` folder. To prepare a signer list for a shared store at the same time, pass a mode:

```bash
memforge init-store --mode ssh
```

For `ssh` and `gpg` it puts your own configured git signing key in the list when it can find one. Otherwise it writes a comment-only file you fill in before the first sync. If any signer list file already exists it changes nothing and exits 2. Skip `--mode` if the store is only yours.

Add a memory file (see the README for the shape), then check it:

```bash
memory-audit --strict
```

## Step 3: share it (optional)

A store that several people or agents write to is protected by signed commits and a signer list, checked by `memforge sync`. Setup for two people, a company with single sign-on, and keyless signing is in [Team setup](./team-bootstrap.md). The short version for `ssh` mode:

```bash
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
git config commit.gpgsign true
# put every signer's public key in .memforge/allowed_signers, commit it signed, push
memforge sync --adopt-local              # the creator, first time
memforge sync --trust-head <commit>      # everyone else, first time, after cloning
memforge sync                            # every time after
```

Only `memforge sync` checks anything. Plain `git pull` is not checked.

## Commit hygiene and the `memforge:` prefixes

You will see "now run `git add` + `git commit -m 'memforge: ...'`" instructions throughout the reference CLI. The reasons are deliberate and worth knowing up front.

**MemForge does not install git hooks.** The reference CLI writes files to disk + tells you what to commit. Pre-commit hooks, post-commit hooks, server-side hooks: those are your responsibility. The spec keeps it that way so MemForge works on every git host (GitHub, GitLab, Gitea, self-hosted, even bare-bones working trees) without injecting machinery you did not ask for.

**The `memforge:` commit-message prefix is parsed.** Audit and resolve walk the commit log and key behavior off the prefix:

- `memforge: resolve <decision_topic>` - the only commit that may transition memories to `status: superseded` for that topic; cross-topic mutations in the same commit are BLOCKER.
- `memforge: snooze <decision_topic>` / `memforge: config` / `memforge: alias <topic>` - each scoped to its own file. Wrong-prefix-or-out-of-scope commits are BLOCKER.

If you commit resolve, snooze, config or alias changes under a different prefix, MemForge's audit will not parse them and downstream behavior breaks (memories never supersede, etc.). Always use the prefix the CLI suggests.

**Automation: Claude Code users get this for free.** If you drive MemForge through Claude Code's auto-memory system, the PostToolUse `memory-auto-commit.sh` hook commits every Write/Edit inside a memory folder automatically. You still need to honor the `memforge:`-prefix convention for the operations above (resolves, snoozes, config edits, alias edits); those run through the `memforge` CLI which prints the right commit message for you. Exclude the signer list files (`.memforge/allowed_signers`, `.memforge/allowed_gpg_fingerprints`, `.memforge/allowed_identities`) from any auto-commit hook: a change to the list must land in a deliberate commit signed by an already listed signer.

**Automation: non-Claude-Code users.** Wire your own. Two reasonable patterns with drop-in example scripts in the repo at [`examples/`](../examples/). Every example ships in bash (macOS / Linux / Git Bash on Windows / WSL) and PowerShell (native Windows + cross-platform pwsh 7+); install whichever matches your shell.

### Pattern 1: commit-msg hook for prefix enforcement (recommended starting point)

A commit-msg hook is the lowest-friction way to catch the most common mistake: editing a Tier 2 file without using the right `memforge:` prefix. The hook rejects the commit with a diagnostic so you fix the message before it lands.

bash drop-in at [`examples/git-hooks/commit-msg`](../examples/git-hooks/commit-msg). PowerShell drop-in at [`examples/git-hooks/commit-msg.ps1`](../examples/git-hooks/commit-msg.ps1) (Windows install instructions in [`examples/README.md`](../examples/README.md)). Install (bash):

```bash
cp examples/git-hooks/commit-msg .git/hooks/commit-msg
chmod +x .git/hooks/commit-msg
```

Source of the bash version (also at the path above):

```bash
#!/usr/bin/env bash
# MemForge commit-msg hook. Enforces the memforge: prefix grammar for
# scoped Tier-2 paths per spec. Reject = exit 1; staged changes are
# preserved so the operator can fix the message and try again.
set -e

msg_file="$1"
msg=$(head -n1 "$msg_file" 2>/dev/null || true)
staged=$(git diff --cached --name-only)

touches_exact()   { echo "$staged" | grep -q "^$1$"; }
touches_pattern() { echo "$staged" | grep -qE "$1"; }
starts_with()     { [[ "$msg" =~ ^"$1" ]]; }

fail() {
  echo "MemForge commit-msg hook: rejecting commit." >&2
  echo "  $1" >&2
  [ -n "${2:-}" ] && echo "  Hint: $2" >&2
  exit 1
}

# .memforge/config.yaml -> memforge: config (Tier 2 BLOCKER per spec)
if touches_exact ".memforge/config.yaml"; then
  if ! starts_with "memforge: config"; then
    fail ".memforge/config.yaml staged but commit prefix is not 'memforge: config'." \
         "Per spec: config edits MUST land in 'memforge: config' commits that touch ONLY the config file."
  fi
fi

# .memforge/snoozes/<topic>.yaml -> memforge: snooze <topic>
if touches_pattern "^\.memforge/snoozes/.*\.yaml$"; then
  if ! starts_with "memforge: snooze "; then
    fail "Snooze file staged but commit prefix is not 'memforge: snooze <topic>'." \
         "Snooze edits MUST land in 'memforge: snooze <topic>' commits."
  fi
fi

exit 0
```

Extend the hook for `memforge: resolve <topic>` and `memforge: alias <topic>` as your team starts using those operations.

### Pattern 2: auto-commit watcher for plain markdown memories

Use a file-system watcher to auto-commit memory `.md` writes (the common case). The watcher explicitly skips Tier 2 scoped paths so it does not race the `memforge` CLI.

bash drop-in at [`examples/scripts/memforge-auto-commit-watcher.sh`](../examples/scripts/memforge-auto-commit-watcher.sh) (requires fswatch on macOS or inotifywait on Linux). PowerShell drop-in at [`examples/scripts/memforge-auto-commit-watcher.ps1`](../examples/scripts/memforge-auto-commit-watcher.ps1) (uses `System.IO.FileSystemWatcher`; no external dependencies on Windows). Source of the bash version (also at the path above):

```bash
#!/usr/bin/env bash
# MemForge auto-commit watcher. macOS: requires fswatch (brew install fswatch).
# Linux: requires inotify-tools (apt install inotify-tools). Skips Tier 2
# scoped paths so the memforge CLI's own commits are not raced.
set -e
ROOT="${1:-$PWD}"
cd "$ROOT"

scoped_path() {
  case "$1" in
    .memforge/config.yaml) return 0 ;;
    .memforge/snoozes/*) return 0 ;;
    *) return 1 ;;
  esac
}

handle() {
  local path="$1"
  [ -f "$path" ] || return 0
  local rel
  rel=$(python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$path" "$ROOT" 2>/dev/null || echo "$path")
  scoped_path "$rel" && return 0
  case "$rel" in *.md) ;; *) return 0 ;; esac
  git add -- "$rel" 2>/dev/null || return 0
  git diff --cached --quiet -- "$rel" && return 0
  git commit -m "memory: auto-commit $rel" >/dev/null 2>&1 || true
}

if command -v fswatch >/dev/null 2>&1; then
  fswatch -0 -r "$ROOT" | while IFS= read -r -d '' path; do handle "$path"; done
elif command -v inotifywait >/dev/null 2>&1; then
  inotifywait -m -r -e close_write --format '%w%f' "$ROOT" | while read -r path; do handle "$path"; done
else
  echo "Install fswatch (macOS) or inotify-tools (Linux), or use the Claude Code PostToolUse hook." >&2
  exit 1
fi
```

The Claude Code PostToolUse `memory-auto-commit.sh` hook (operator-side; not shipped in this repo) implements roughly the same shape, scoped to Claude Code's tool-call lifecycle rather than fs-level events. Fork either as a starting point.

**Bottom line.** Commits are the audit trail. Hygiene is yours; MemForge works cleanly as long as the commits land with the right prefixes. For a shared store, signed commits are also the trust control (see [Team setup](./team-bootstrap.md)); do not auto-commit changes to the signer list files, because they must land in a deliberate signed commit. The commit-msg hook above is enough to catch the bulk of the common mistakes; the watcher pattern is the easy step beyond.

### Pattern 3: write-boundary validation gate (`memory-validate`)

`memory-validate` (v0.7.0) is the syntax-aware pre-write gate. It HARD-fails any memory file whose frontmatter does not parse as a YAML mapping, the most common break being an unquoted colon in a value (`description: this breaks: here`), so the malformed file is rejected at the write boundary instead of surfacing later as a `memory-audit` failure.

Validate a file (or a batch) directly:

```bash
memory-validate ~/.memforge/global-memory/some-memory.md       # one file
memory-validate --path ~/.memforge/global-memory               # walk a folder
memory-validate --path ~/.memforge/global-memory --strict      # SOFT findings fail too
memory-validate --json some-memory.md                        # machine-readable
```

Exit is nonzero on a HARD frontmatter-parse failure always; SOFT findings (pointer/line caps, missing v0.4 fields, bad `tier`/`status`) only fail under `--strict`. So the gate idiom is just `memory-validate <file> || reject`.

Wire it as a git pre-commit hook so a malformed write never enters history (`.git/hooks/pre-commit` in the memory repo):

```bash
#!/usr/bin/env bash
staged=$(git diff --cached --name-only --diff-filter=ACM -- '*.md')
[ -z "$staged" ] && exit 0
echo "$staged" | xargs memory-validate || {
  echo "memory-validate: blocked commit; fix the frontmatter above and re-stage" >&2
  exit 1
}
```

This works for **any** IDE (every adapter versions via git). Claude Code can gate one step earlier, before the bytes hit disk, with a `PreToolUse` shim. Both wirings, plus per-IDE specifics (Cursor, VS Code Copilot, Aider, Codex, Cline, Continue, Windsurf), are in [`adapter-implementation-guide.md`](./adapter-implementation-guide.md) §"Write-boundary gate".

## Recall (v0.6.0): query-triggered memory injection

As a memory set grows, loading the whole `MEMORY.md` into every session gets expensive and the one-line pointers are too terse to reliably surface the right memory. Recall is an alternative: on a query (for example the user's prompt), surface only the descriptions of the memories whose triggers match.

Build the recall index (a derived artifact at `.memforge/recall-index.json`):

```bash
memory-index-gen --with-recall-index --path ~/.memforge/global-memory
```

Query it:

```bash
memory-recall "how do I resolve a competing claim" --path ~/.memforge/global-memory
```

Recall surfaces `description` text only (never bodies), honors `sensitivity` / `access`, and is fail-open-empty (it never blocks the caller). Mark a memory `always: true` to surface it on every query; mark it `do_not_inject: true` to exclude it from recall (for example, content already loaded by another path). Explicit `triggers: [...]` refine matching; without them, triggers are derived from the name, tags, and description.

For Claude Code, register `adapters/claude-code/hooks/memory_recall_hook.py` under `UserPromptSubmit` to inject matches automatically, and let the auto-commit hook keep the index fresh via `memory-recall --rebuild`. See `adapters/claude-code/README.md`.

### Check recall-readiness (`memory-lint`)

Recall only works if descriptions are findable. `memory-lint` scores how each memory would fare against the recall trigger model and flags token-cost issues. It is read-only and local-only by default:

```bash
memory-lint --path ~/.memforge/global-memory
```

A memory scores low when its description is contentless ("Notes"), adds no distinctive term the name does not already carry, or shares only common terms with the rest of the corpus. Lint also flags over-budget always-sets, oversized descriptions/bodies, and (with `--injected-file CLAUDE.md`) memories that may already live in an always-loaded file. Pass `--json` for machine output, `--strict` to exit nonzero on weak memories in CI.

Model-assisted suggestions (sharper description + triggers) are opt-in and off by default. To enable, point `--dispatcher` at a local model; cloud models require the explicit `--allow-cloud`, and shipping the memory body (not just metadata) requires the stronger `--allow-cloud-body`. A deterministic secret scan blocks dispatch fail-closed. Suggestions are advisory; lint never edits files.

## What you have at the end

- A memory store with a clean `memory-audit`.
- Optionally, a signer list and a verified first sync for a shared store.
- Query-triggered recall and a recall-readiness lint.

For multi-person setup, see [`team-bootstrap.md`](./team-bootstrap.md).

## Troubleshooting

- **`memforge sync` says `no_pin`**: this is a first sync. Pass `--trust-head <commit>` or `--adopt-local`. See [Team setup](./team-bootstrap.md#troubleshooting).
- **A removed command prints a removal notice**: 1.0 removed the old key-system commands. See [Migrating to 1.0](./migrating-to-1.0.md).
