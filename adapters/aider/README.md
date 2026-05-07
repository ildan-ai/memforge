# adapters/aider

Aider adapter for MemForge. Surfaces a memory folder via Aider's `--read` flag (or `.aider.conf.yml`), and relies on Aider's native auto-commit cycle for versioning.

## Setup

1. Pick a memory folder. `git init` if not already.
2. Seed `MEMORY.md` (or copy from an existing memory folder).
3. **Load on session start** — choose one:

   **Option A: command-line flag.** Add to your aider invocation:
   ```bash
   aider --read ~/.claude/global-memory/MEMORY.md \
         --read ~/.claude/projects/<workspace>/memory/MEMORY.md \
         <your-source-files>
   ```

   **Option B: `.aider.conf.yml` (recommended for daily use).** Copy `aider.conf.yml.example` from this folder into `~/.aider.conf.yml` (global) or your project root, edit the paths, then start aider normally.

4. **Auto-commit on writes** — aider's native git integration auto-commits source-file edits per turn, but it does NOT auto-commit changes to `--read` files (those are read-only context). For memory files that aider edits via the `/add` command, aider DOES auto-commit. For external edits to memory files (your editor, tools, other agents), install and run the cross-platform watcher:
   ```bash
   python3 -m pip install watchdog        # one-time, all platforms
   ~/claude-projects/memforge/tools/memory-watch
   ```
   For daemon-style operation pick the template from `../cursor/`:
   - **macOS** — `daemon-launchd.plist`
   - **Linux** — `daemon-systemd.service`
   - **Windows** — `daemon-windows-task.ps1` (PowerShell)

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

- `aider.conf.yml.example` — Aider config template with `read:` entries pointing at typical memory folders. Copy to `~/.aider.conf.yml` (macOS / Linux) or `%USERPROFILE%\.aider.conf.yml` (Windows) and edit.
- `aider-memory-cmd.sh` — bash/zsh convenience function that wraps `aider` with the right `--read` flags for memforge folders. Source from `~/.bashrc` or `~/.zshrc` and use `aider-mem` instead of `aider`.
- `aider-memory-cmd.ps1` — PowerShell equivalent for Windows. Dot-source from your PowerShell profile (`. $PROFILE` after editing).

## Aider's commit-msg hook

Aider auto-commits with messages like `aider: <description>`. If you want memory-folder commits to follow a consistent prefix (e.g., `memory: edit`), wire a git `commit-msg` hook in the memory folder that prefixes any aider commit with `memory:`. This is optional; aider's default messages are reasonable.

## Honoring sensitivity

To exclude `restricted` or `privileged` memories from the aider context, pre-generate a filtered MEMORY.md:

```bash
memory-index-gen --print --viewer-tier internal --path ~/your-memory-folder > /tmp/aider-memory.md
aider --read /tmp/aider-memory.md ...
```

Re-run when memory changes. (Could be wired as a pre-aider shell function.)
