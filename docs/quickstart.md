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

Add a memory file (see the README for the shape), generate the index, then check the store:

```bash
memory-index-gen --path .
memory-audit --strict --path .
```

`--path` names the store to check. Without it, both commands use the default folders (the resolution order is under "60-second install" in the README), not the folder you are in.

After a `memforge-resolve`, run this routine again: the resolve commit leaves `MEMORY.md` alone, so its competing-claims block is stale until `memory-index-gen` runs. The superseded memory keeps its pointer in `MEMORY.md`, as every top-level memory outside `archive/` does.

## Step 3: share it (optional)

A store that several people or agents write to is protected by signed commits and a signer list, checked by `memforge sync`. Setup for two people, a company with single sign-on, and keyless signing is in [Team setup](./team-bootstrap.md). The short version for `ssh` mode, run inside the store's repository (after `git init`, or after `git clone` for everyone but the creator):

```bash
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
git config commit.gpgsign true
# add one line per signer to .memforge/allowed_signers:
#   <name> namespaces="git" <public key>
# then commit it signed and push with an upstream: git push -u origin <branch>
memforge sync --adopt-local              # the creator, first time
memforge sync --trust-head <commit>      # everyone else, first time, after cloning
memforge sync                            # every time after
```

Do not paste only the `.pub` file's content (`ssh-ed25519 AAAA... you@host`). Sync accepts a list with such a line, but `ssh-keygen` cannot match it to a signer, so every commit that signer makes is refused with `ssh_not_verified`. Put a name and `namespaces="git"` in front of the key, as above.

Push with `-u` (or set the upstream with `git branch --set-upstream-to`). Without an upstream, `memforge sync` exits 1 with `no_upstream`.

Only `memforge sync` checks anything. Plain `git pull` is not checked.

## Commit hygiene and the `memforge:` prefixes

You will see "now run `git add` + `git commit -m 'memforge: ...'`" instructions throughout the reference CLI. The reasons are deliberate and worth knowing up front.

**MemForge does not install git hooks.** The reference CLI writes files to disk + tells you what to commit. Pre-commit hooks, post-commit hooks, server-side hooks: those are your responsibility. The spec keeps it that way so MemForge works on every git host (GitHub, GitLab, Gitea, self-hosted, even bare-bones working trees) without injecting machinery you did not ask for.

**The `memforge:` commit-message prefix is parsed.** The spec gives each operation its own prefix and scope:

- `memforge: resolve <decision_topic>` - the only commit that may transition memories to `status: superseded` for that topic.
- `memforge: snooze <decision_topic>` / `memforge: config` / `memforge: alias <topic>` / `memforge: waiver` - each scoped to its own file.

What `memory-audit` checks of this, over the last 30 days of commits: a resolve commit may touch only its topic's members and snooze file; a change to `status: superseded`, a `superseded_by` write, a `decision_topic` change or the deletion of a topic member outside that topic's resolve commit is a BLOCKER; so is a `.memforge/config.yaml` change outside a config-only `memforge: config` commit, and a `.memforge/audit-waivers.yaml` change outside a waiver-only `memforge: waiver` commit. A snooze file change outside a single-file `memforge: snooze <topic>` commit, and a `topic_aliases` change outside resolve or a single-file `memforge: alias <topic>` commit, are MAJORs. Waivers are read only as committed at HEAD. The commit-msg hook below catches a wrong prefix before the commit lands.

Always use the prefix the CLI suggests.

**Automation: Claude Code users get this for free.** If you drive MemForge through Claude Code's auto-memory system, the PostToolUse `memory-auto-commit.sh` hook commits every Write/Edit inside a memory folder automatically. You still need to honor the `memforge:`-prefix convention for the operations above (resolves, snoozes, config edits, alias edits); those run through the `memforge` CLI which prints the right commit message for you. The shipped hook, `memory-watch` and the example watchers never commit the signer list files (`.memforge/allowed_signers`, `.memforge/allowed_gpg_fingerprints`, `.memforge/allowed_identities`). Keep that rule in any hook you write: a change to the list must land in a deliberate commit signed by an already listed signer.

**Automation: non-Claude-Code users.** Wire your own. Two reasonable patterns with drop-in example scripts in the repo at [`examples/`](../examples/). Every example ships in bash (macOS / Linux / Git Bash on Windows / WSL) and PowerShell (native Windows + cross-platform pwsh 7+); install whichever matches your shell.

### Pattern 1: commit-msg hook for prefix enforcement (recommended starting point)

A commit-msg hook is the lowest-friction way to catch the most common mistake: editing a Tier 2 file without using the right `memforge:` prefix. The hook rejects the commit with a diagnostic so you fix the message before it lands.

bash drop-in at [`examples/git-hooks/commit-msg`](../examples/git-hooks/commit-msg). PowerShell drop-in at [`examples/git-hooks/commit-msg.ps1`](../examples/git-hooks/commit-msg.ps1) (Windows install instructions in [`examples/README.md`](../examples/README.md)). Install (bash):

```bash
cp examples/git-hooks/commit-msg .git/hooks/commit-msg
chmod +x .git/hooks/commit-msg
```

Read the hook in that file before you install it; this page does not keep a copy, so what you read is what runs. For each commit it reads the first line of the message and the staged file names, and rejects the commit (exit 1, staged changes kept) when:

- `.memforge/config.yaml` is staged and the message does not start `memforge: config`;
- `.memforge/audit-waivers.yaml` is staged and the message is neither `memforge: waiver` nor starts with `memforge: waiver` and a space, or other files are staged with it;
- a `.memforge/snoozes/<topic>.yaml` file is staged and the message does not start with `memforge: snooze` and a space.

The hook checks prefixes, and that a waiver commit holds only the waiver file. It does not check that a config commit touches only the config file, and it misses a snooze file whose name holds a newline; `memory-audit` reports both.

Extend the hook for `memforge: resolve <topic>` and `memforge: alias <topic>` as your team starts using those operations.

### Pattern 2: auto-commit watcher for plain markdown memories

Use a file-system watcher to auto-commit memory `.md` writes (the common case). The watcher skips everything under `.memforge/` (config, snoozes, waivers, signer lists), so those change only in deliberate commits and it does not race the `memforge` CLI.

bash drop-in at [`examples/scripts/memforge-auto-commit-watcher.sh`](../examples/scripts/memforge-auto-commit-watcher.sh) (requires fswatch on macOS or inotifywait on Linux). PowerShell drop-in at [`examples/scripts/memforge-auto-commit-watcher.ps1`](../examples/scripts/memforge-auto-commit-watcher.ps1) (uses `System.IO.FileSystemWatcher`; no external dependencies on Windows). Run the shipped file, not a copy: it carries the safety lines a copy tends to lose. The bash version:

- unsets `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and the other variables `git rev-parse --local-env-vars` lists, so a watcher started from a git hook or a shell with them set still commits to the watched folder and not to another repository;
- names each file to git with the `:(literal)` pathspec magic, so a file name is never read as a git pattern, and removes `GIT_LITERAL_PATHSPECS` from the environment of the commit, so your pre-commit hooks see the files;
- skips any path under `.memforge/` in any letter case and any file that does not end in `.md`;
- commits each changed file alone (`git commit -- <file>`), so anything else you staged stays staged.

Start it from the memory folder, or pass the folder: `examples/scripts/memforge-auto-commit-watcher.sh <memory-root> &`.

The Claude Code PostToolUse hook at [`adapters/claude-code/hooks/memory-auto-commit.sh`](../adapters/claude-code/hooks/memory-auto-commit.sh) implements roughly the same shape, scoped to Claude Code's tool-call lifecycle rather than fs-level events. Fork either as a starting point.

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
unset GIT_LITERAL_PATHSPECS
staged=$(git diff --cached --name-only --diff-filter=ACMT -- '*.md')
[ -z "$staged" ] && exit 0
git diff --cached --name-only -z --diff-filter=ACMT -- '*.md' | xargs -0 memory-validate -- || {
  echo "memory-validate: blocked commit; fix the frontmatter above and re-stage" >&2
  exit 1
}
```

Keep the `unset` line: `memory-watch`, the example watchers and the Claude Code hook do not set `GIT_LITERAL_PATHSPECS` (each names files with `:(literal)` instead), but your own shell or tooling may set it. It makes git read `'*.md'` as a literal file name that matches nothing, so without the `unset` the hook sees no staged files and lets a malformed memory in.

This works for **any** IDE (every adapter versions via git). Claude Code can gate one step earlier, before the bytes hit disk, with a `PreToolUse` shim. Both wirings, plus per-IDE specifics (Cursor, VS Code Copilot, Aider, Codex, Cline, Continue, Windsurf), are in [`adapter-implementation-guide.md`](./adapter-implementation-guide.md) §"Write-boundary gate".

## Recall (v0.6.0): query-triggered memory injection

As a memory set grows, loading the whole `MEMORY.md` into every session gets expensive and the one-line pointers are too terse to reliably surface the right memory. Recall is an alternative: on a query (for example the user's prompt), surface only the descriptions of the memories whose triggers match.

Build the recall index (a derived local cache at `.memforge/recall-index.json`; no tool commits it):

```bash
memory-index-gen --with-recall-index --path ~/.memforge/global-memory
```

Query it:

```bash
memory-recall "how do I resolve a competing claim" --path ~/.memforge/global-memory
```

`memory-recall` compares a fingerprint of the memory files (name, size, modification time) with the one stored in the index before every query and rebuilds the index when they differ or when `.memforge/recall-synonyms.yaml` changed, or when a memory file was modified within 2 seconds of the time the index was built (the index records it), so a stale index, one that came in with a `git pull` or one edited by hand is never served. If your repository tracks the index from an earlier version, run `git rm --cached .memforge/recall-index.json`, commit that, and add `.memforge/recall-index.json` to the folder's `.gitignore`.

Recall surfaces `description` text only (never bodies), filters on `sensitivity` only when you pass `--sensitivity-max`, and is fail-open-empty (it never blocks the caller). With no `--sensitivity-max` there is no sensitivity ceiling: a `restricted` or `privileged` memory's description can be returned. On a per-prompt path, pass a ceiling, for example `memory-recall "<query>" --sensitivity-max internal --path ~/.memforge/global-memory`. Mark a memory `always: true` to surface it on every query; mark it `do_not_inject: true` to exclude it from recall (for example, content already loaded by another path). Explicit `triggers: [...]` refine matching; without them, triggers are derived from the name, tags, and description.

For Claude Code, register `adapters/claude-code/hooks/memory_recall_hook.py` under `UserPromptSubmit`, run by the absolute path of the Python interpreter in the environment you installed `ildan-memforge` into, to inject matches automatically, and, if you like, let the auto-commit hook refresh the index ahead of the next prompt via `memory-recall --rebuild`. See `adapters/claude-code/README.md`.

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
- **`memforge sync` says `no_upstream`**: the branch has no upstream. Push with `git push -u origin <branch>`, or run `git branch --set-upstream-to origin/<branch>`, then sync again.
- **A removed command prints a removal notice**: 1.0 removed the old key-system commands. See [Migrating to 1.0](./migrating-to-1.0.md).
