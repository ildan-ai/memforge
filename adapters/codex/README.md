# adapters/codex

OpenAI Codex CLI adapter for MemForge. Surfaces a memory folder via the standard `AGENTS.md` (openai/anthropic/google/cursor collaborative spec at https://agents.md/), and versions it via the portable `memory-watch` filesystem watcher.

This adapter also covers other AGENTS.md-aware agents that don't have their own dedicated MemForge adapter: **Cline**, **Continue**, **Windsurf**, and any future agent that adopts the convention. The wiring is identical because AGENTS.md is the contract.

## Setup

1. Pick a memory folder. `git init` if not already.
2. Seed `MEMORY.md` (or copy from an existing memory folder).
3. **Load on session start** — copy `AGENTS.md.template` to your repo root as `AGENTS.md`, edit the memory-folder path. The agent will pick up `AGENTS.md` automatically on session start (the AGENTS.md spec defines this as a "README for agents").
4. **Auto-commit on writes** — install and run the cross-platform watcher:
   ```bash
   python3 -m pip install watchdog        # one-time, all platforms
   ~/claude-projects/memforge/tools/memory-watch
   ```
   For daemon-style operation pick the template from `../cursor/`:
   - **macOS** — `daemon-launchd.plist` (LaunchAgent)
   - **Linux** — `daemon-systemd.service` (user systemd)
   - **Windows** — `daemon-windows-task.ps1` (PowerShell registers a Scheduled Task)
5. Restart your agent.

## What works

- **Auto-load**: AGENTS.md is the agent-neutral session-start surface. Codex CLI, Cline, Continue, Windsurf, and any AGENTS.md-aware agent will read it on every session.
- **Auto-commit**: `memory-watch` versions every settled write under the memory folder. Independent of which agent did the write (or a human editor, or another tool).
- **All MemForge tools** work unchanged.
- **Multi-folder**: AGENTS.md can reference multiple memory folders. The agent inlines the linked MEMORY.md content.

## What does NOT work (vs Claude Code adapter)

- **No read tracking**. None of these agents expose a per-file Read event. The `.last_used.json` ledger stays empty; `memory-audit` falls back to filesystem mtime.
- **No skills / slash commands**. Each agent has its own command surface; the `consolidate-memory` skill does not port. Run the tools from a terminal.
- **Auto-commit message granularity**. `memory-watch` writes a timestamp-based commit message; CC's hook captures the tool name + file path. For richer commit messages, use the agent's native git integration where available.
- **Inlining cost**. Agents that inline AGENTS.md content into every prompt pay the token cost. For large MEMORY.md files (>5KB), consider running `memory-index-gen --print --viewer-tier internal` and committing a smaller filtered index.

## Files in this adapter

- `AGENTS.md.template` — drop-in template that points at the memory folders. Copy to your repo root as `AGENTS.md` and edit paths.
- `daemon-launchd.plist` and `daemon-systemd.service` — for daemon-style memory-watch, see `../cursor/` (the same files work for any agent).

## Per-agent notes

### OpenAI Codex CLI

`AGENTS.md` is read automatically. No additional wiring needed beyond the template and `memory-watch`.

### Cline (VSCode extension)

Cline's "Custom Instructions" panel can point at `AGENTS.md`. Settings → Cline → Custom Instructions → reference `@AGENTS.md` in the global rules.

### Continue (VSCode / JetBrains)

Continue's `~/.continue/config.yaml` has a `rules:` section. Add an entry referencing your `AGENTS.md`, or paste the MemForge-loading instruction directly inline.

### Windsurf (Codeium Cascade)

Windsurf reads `.windsurfrules` at the repo root. Symlink: `ln -s AGENTS.md .windsurfrules`. Both files have the same role (session-start instructions).

## Honoring sensitivity

To exclude `restricted` or `privileged` memories from the agent context, generate a filtered MEMORY.md and link to it from AGENTS.md instead of the canonical one:

```bash
memory-index-gen --print --viewer-tier internal --path ~/.claude/global-memory \
  > ~/.claude/global-memory/MEMORY.public.md
```

Then in `AGENTS.md`, reference `MEMORY.public.md` instead of `MEMORY.md`. Re-generate when memory changes (or wire it as a pre-commit hook on the memory folder).
