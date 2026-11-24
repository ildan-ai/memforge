# adapters/aider

Aider adapter for MemForge. Surfaces a memory folder via Aider's `--read` flag (or `.aider.conf.yml`), and relies on Aider's native auto-commit cycle for versioning.

## Setup

1. Pick a memory folder. `git init` if not already.
2. Seed `MEMORY.md` (or copy from an existing memory folder).
3. **Load on session start**: choose one:

   **Option A: command-line flag.** Pass `--read` for the `MEMORY.md` of each folder `memforge paths` prints. On a fresh 1.0 install those are:
   ```bash
   aider --read ~/.memforge/memory/MEMORY.md \
         --read ~/.memforge/global-memory/MEMORY.md \
         <your-source-files>
   ```
   If you already have a Claude Code store, `memforge paths` prints `~/.claude/projects/<project>/memory` and `~/.claude/global-memory` instead; use those. The `aider-mem` wrapper below does this lookup for you.

   **Option B: `.aider.conf.yml` (recommended for daily use).** Copy `aider.conf.yml.example` from this folder into `~/.aider.conf.yml` (global) or your project root, edit the paths, then start aider normally.

4. **Auto-commit on writes**: aider's native git integration auto-commits source-file edits per turn, but it does NOT auto-commit changes to `--read` files (those are read-only context). For memory files that aider edits via the `/add` command, aider DOES auto-commit. For external edits to memory files (your editor, tools, other agents), install and run the cross-platform watcher:
   ```bash
   memory-watch --path <memory folder>
   ```
   `memory-watch` is installed with `ildan-memforge`, `watchdog` included.
   For daemon-style operation pick the template from `../cursor/`:
   - **macOS**: `daemon-launchd.plist`
   - **Linux**: `daemon-systemd.service`
   - **Windows**: `daemon-windows-task.ps1` (PowerShell)

## What works

- **Auto-load**: aider's `--read` inlines the file's content as session-start context. Re-read on every turn (no staleness).
- **Inline editing**: if you `/add` a memory file in aider, aider can edit it like any source file. Aider's auto-commit cycle then versions the change.
- **All MemForge tools**: `memory-audit-deep`, `memory-rollup`, `memory-query`, `memory-cluster-suggest`, `memory-dlp-scan`, `memory-audit-log`, `memory-frontmatter-backfill`, `memory-preamble-extract` all work unchanged.
- **Multi-folder**: pass `--read` multiple times for per-cwd + global memory.

## What does NOT work (vs Claude Code adapter)

- **No read tracking**. Aider does not expose a per-file Read event; the `.last_used.json` ledger stays empty. `memory-audit` falls back to filesystem mtime for staleness.
- **No skills / slash commands** that map to `consolidate-memory`. Aider has its own slash commands (`/add`, `/drop`, `/run`, etc.) but no plugin system to register a memory-management workflow. Run the tools from a terminal instead.
- **`--read` content is part of context, not the live file**. If your tool (memory-rollup, memory-audit-deep, etc.) modifies MEMORY.md, you have to restart aider or re-`/read` to see the new content. Aider does NOT hot-reload `--read` files mid-session.
- **Auto-commit only fires for files aider edits**. External writes (memory-rollup, manual edits, other agents) need `memory-watch` running, OR an explicit `git commit` from the operator.

## Files in this adapter

- `aider.conf.yml.example`: Aider config template with `read:` entries pointing at typical memory folders. Copy to `~/.aider.conf.yml` (macOS / Linux) or `%USERPROFILE%\.aider.conf.yml` (Windows) and edit.
- `aider-memory-cmd.sh`: bash/zsh convenience function that wraps `aider` with the right `--read` flags for memforge folders. Source from `~/.bashrc` or `~/.zshrc` and use `aider-mem` instead of `aider`. It asks `memforge paths` for the default memory folders and passes `--read` for each folder's `MEMORY.md` that exists, so it reads the same store as every memforge tool: `MEMFORGE_MEMORY_PATH` if set, else the Claude Code store of the git root (the nearest folder up the tree with a `.git` entry, or the current folder outside a repository), else `~/.memforge`. In a linked worktree that is the worktree's folder; whether Claude Code does the same is unconfirmed, so set `MEMFORGE_MEMORY_PATH` if the store is not found. It needs `memforge` on `PATH`; without it, it says so and does not start aider.
- `aider-memory-cmd.ps1`: PowerShell equivalent for Windows, with the same behavior. Dot-source from your PowerShell profile (`. $PROFILE` after editing).

## Write-boundary gate (spec v0.7.0)

Aider has no pre-write hook, so the gate is the **git pre-commit hook** (Tier B): `memory-validate` over the staged `*.md` files. It fires on Aider's native auto-commit for files edited via `/add`, and on the `memory-watch` commit for external edits, so both write paths are covered by one hook. The copy-paste hook is in [`../../docs/adapter-implementation-guide.md`](../../docs/adapter-implementation-guide.md) §"Write-boundary gate".

## Aider's commit-msg hook

Aider auto-commits with messages like `aider: <description>`. If you want memory-folder commits to follow a consistent prefix (e.g., `memory: edit`), wire a git `commit-msg` hook in the memory folder that prefixes any aider commit with `memory:`. This is optional; aider's default messages are reasonable.

## Honoring sensitivity

`memory-index-gen --viewer-tier` filters by the `access` label only, not by `sensitivity`. A memory with `sensitivity: privileged` and no `access` line is still printed. So run the gates first and start aider only if they pass:

```bash
memory-audit --export-tier=internal --strict --path ~/your-memory-folder &&
memory-dlp-scan --paths ~/your-memory-folder/*.md --strict &&
out=$(mktemp) &&
memory-index-gen --print --viewer-tier internal --path ~/your-memory-folder > "$out" &&
aider --read "$out" ...
```

The filtered index goes to a file `mktemp` creates (mode 600, a name nobody else can predict), not to a fixed name in `/tmp`, which other accounts can read or pre-create. Remove it when the session ends: `rm -f "$out"`.

Re-run when memory changes. (Could be wired as a pre-aider shell function.)

### v0.4 enforcement

The audit's export-tier gate fails BLOCKER on declared-tier > export-tier; the DLP cross-check fails BLOCKER on body content whose implied tier exceeds the declared label. Privileged-tier enforcement is a hard floor. See `docs/adapter-implementation-guide.md` §"Secure-mode sensitivity enforcement (v0.4.0+)".
