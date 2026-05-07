# adapters/vscode-copilot

GitHub Copilot Chat (VS Code) adapter for MemForge. Surfaces a memory folder via Copilot's `.github/copilot-instructions.md` convention (and/or the `github.copilot.chat.codeGeneration.instructions` setting), and versions it via the cross-platform `memory-watch` filesystem watcher.

This adapter is for **GitHub Copilot Chat in VS Code** specifically. Other VS Code-resident agents (Cline, Continue, Codeium / Windsurf) are AGENTS.md-aware and use the `codex/` adapter instead.

## Setup

1. Pick a memory folder. `git init` if not already.
2. Seed `MEMORY.md` (or copy from an existing memory folder).
3. **Load on session start** — choose one of:

   **Option A: per-repo `.github/copilot-instructions.md` (recommended).** Copy `copilot-instructions.md.template` into `<repo>/.github/copilot-instructions.md`, edit the memory-folder paths. Copilot Chat reads this file automatically when enabled.

   Make sure VS Code Settings has `github.copilot.chat.codeGeneration.useInstructionFiles` set to `true` (default in recent versions).

   **Option B: workspace or user settings.** Add to `.vscode/settings.json` (workspace) or your user settings:
   ```json
   "github.copilot.chat.codeGeneration.instructions": [
     { "file": "${workspaceFolder}/.github/copilot-instructions.md" },
     { "file": "${userHome}/.claude/global-memory/MEMORY.md" }
   ]
   ```
   The `file` reference inlines the file content into every Copilot Chat request.

4. **Auto-commit on writes** — install and run the cross-platform watcher:
   ```bash
   python3 -m pip install watchdog        # one-time, all platforms
   ~/claude-projects/memforge/tools/memory-watch
   ```
   For daemon-style operation pick the template from `../cursor/`:
   - **macOS** — `daemon-launchd.plist`
   - **Linux** — `daemon-systemd.service`
   - **Windows** — `daemon-windows-task.ps1`
5. Restart VS Code (or reload the Copilot Chat session).

## What works

- **Auto-load**: `.github/copilot-instructions.md` is the per-repo Copilot custom-instruction surface. Copilot inlines its content into every chat request.
- **Multi-folder via settings.json**: `github.copilot.chat.codeGeneration.instructions` accepts multiple `file:` entries, so you can load both per-cwd and global memory.
- **Auto-commit**: `memory-watch` versions every settled write under the memory folder. Independent of which extension did the write.
- **All MemForge tools** work unchanged (operate on filesystem markdown).

## What does NOT work (vs Claude Code adapter)

- **No read tracking**. Copilot does not expose a per-file Read event; the `.last_used.json` ledger stays empty.
- **No skills / slash commands**. Copilot has its own slash commands (`/explain`, `/fix`, `/tests`, etc.) but no plugin system to register a memory-management workflow. Run the tools from the integrated terminal.
- **`.github/copilot-instructions.md` is per-repo, not global**. To get global memory loaded across all repos, use the user-settings `github.copilot.chat.codeGeneration.instructions` path (Option B above) which references the absolute path of the global memory file.
- **Inlining cost**. Copilot inlines the entire instructions file into every request. Large `MEMORY.md` files (>10KB) eat token budget and slow responses. For big folders, run `memory-index-gen --print --viewer-tier internal` periodically and reference the filtered file instead.

## Files in this adapter

- `copilot-instructions.md.template` — drop-in template for `.github/copilot-instructions.md`. Edit the memory-folder paths.
- `vscode-settings.snippet.json` — copy/paste-ready snippet for VS Code user or workspace `settings.json` to wire the user-level memory load.

## Operator-side prompts variants

Copilot Chat also supports `.github/prompts/*.prompt.md` (preview / Insiders) for reusable prompt templates. If you want a memory-aware prompt template (e.g., "summarize ongoing work using MEMORY.md"), see the VS Code docs for `github.copilot.chat.promptFiles`. Not yet templated in this adapter.

## Honoring sensitivity

To exclude `restricted` or `privileged` memories from Copilot context, pre-generate a filtered MEMORY.md and reference that one:

```bash
memory-index-gen --print --viewer-tier internal \
  --path ~/.claude/global-memory \
  > ~/.claude/global-memory/MEMORY.public.md
```

Then point `.github/copilot-instructions.md` (or settings.json) at `MEMORY.public.md`. Re-generate when memory changes (or wire it as a pre-commit hook on the memory folder).

## Why this is a separate adapter from `codex/`

GitHub Copilot Chat does NOT honor the AGENTS.md convention. It uses its own `.github/copilot-instructions.md` file plus VS Code settings as the loading surfaces. The content can be the same (memory-folder paths + instructions to read them) but the file location is different. If you're running multiple agents in the same repo, you can have both AGENTS.md (for AGENTS.md-aware agents) and `.github/copilot-instructions.md` (for Copilot) point at the same MEMORY.md.
