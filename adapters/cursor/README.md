# adapters/cursor

Cursor adapter for MemForge. Surfaces a memory folder via Cursor's `.cursor/rules/` system, and versions it via the portable `memory-watch` filesystem watcher.

## Setup

1. Pick a memory folder (typically `~/.claude/projects/<workspace>/memory/` or `~/.cursor/memory/`).
2. `git init` inside the memory folder so changes can be versioned.
3. Seed `MEMORY.md` (or copy from an existing memory folder).
4. **Load on session start** — copy `rules/memory-load.mdc` into `~/.cursor/rules/` (global) or `<repo>/.cursor/rules/` (project-scoped). Edit the path inside the rule to match your memory folder.
5. **Auto-commit on writes** — install the cross-platform watcher:
   ```bash
   python3 -m pip install watchdog        # one-time
   ~/claude-projects/memforge/tools/memory-watch
   ```
   This runs in the foreground until you Ctrl-C. To run as a daemon at login, pick the template that matches your OS:
   - **macOS** — `daemon-launchd.plist` (LaunchAgent)
   - **Linux** — `daemon-systemd.service` (user-level systemd)
   - **Windows** — `daemon-windows-task.ps1` (PowerShell-driven Scheduled Task)
6. Restart Cursor. The rule will load `MEMORY.md` content into every chat as session-start context.

### Windows-specific notes

- Cursor on Windows reads `.cursor/rules/*.mdc` from the same paths as macOS / Linux. Copy `rules/memory-load.mdc` into `%USERPROFILE%\.cursor\rules\` (global) or `<repo>\.cursor\rules\` (project).
- Edit `rules/memory-load.mdc` to use Windows-style paths if your memory folders live under `%USERPROFILE%`. The `@`-syntax in the rule accepts absolute paths in either form.
- `memory-watch` requires Python 3 + the `watchdog` package + git on `PATH`. Install Python 3 from python.org or the Microsoft Store; `py -m pip install watchdog` for the watcher backend.

## What works

- **Auto-load**: rules in `.cursor/rules/` are surfaced to the model on every chat. Pointing one at `MEMORY.md` gives MemForge a session-start surface equivalent to Claude Code's `autoMemoryDirectory`.
- **Auto-commit**: `memory-watch` runs `git add -A && git commit` on every settled filesystem write inside the memory folder. Same effect as the Claude Code `PostToolUse` hook, just driven by filesystem events instead of tool events.
- **All MemForge tools**: `memory-audit-deep`, `memory-rollup`, `memory-query`, `memory-cluster-suggest`, `memory-dlp-scan`, `memory-audit-log`, etc. all work unchanged. They operate on the filesystem, not on the agent.
- **Sensitivity filtering**: Cursor rules support `alwaysApply: false` + glob scoping. You can split sensitive memories into a separate folder and load only the public-tier ones via a more restrictive rule.

## What does NOT work (vs Claude Code adapter)

- **No read tracking**. Cursor does not expose a per-file Read event the way Claude Code's `PostToolUse` matcher does. The `.last_used.json` ledger that drives `memory-audit`'s staleness check stays empty. Audit falls back to filesystem mtime, which is less accurate (loaded != actually consulted).
- **No skills / slash commands**. Cursor has its own command surface; the Claude Code `consolidate-memory` skill does not port. You can replicate the workflow manually by running the tools from a terminal.
- **No `Edit` vs `Write` distinction in commit messages**. The `memory-watch` commit message is just a timestamp; CC's hook gets the tool name and file path from the event payload.

## Files in this adapter

- `rules/memory-load.mdc` — Cursor rule that loads `MEMORY.md` content as session-start context. Copy to `~/.cursor/rules/` or `<repo>/.cursor/rules/`.
- `daemon-launchd.plist` — macOS LaunchAgent template for running `memory-watch` as a background daemon. Copy to `~/Library/LaunchAgents/com.memforge.memory-watch.plist`, edit the path, then `launchctl load ~/Library/LaunchAgents/com.memforge.memory-watch.plist`.
- `daemon-systemd.service` — Linux systemd user-service template. Copy to `~/.config/systemd/user/memforge-watch.service`, then `systemctl --user enable --now memforge-watch.service`.
- `daemon-windows-task.ps1` — Windows PowerShell script that registers a Scheduled Task running `memory-watch` at user login. Run once: `powershell -ExecutionPolicy Bypass -File daemon-windows-task.ps1`. Edit the `$MemoryWatchPath` variable inside before running.

## Multi-memory-folder workflow

Cursor's rules system loads all matching rules. To load both per-cwd and global memory, copy `memory-load.mdc` twice with different paths. Order is alphabetical; rename to control precedence.

## Honoring sensitivity

If you want Cursor to ignore `restricted` or `privileged` memories, run `memory-index-gen --viewer-tier internal --print` and pipe the output to a Cursor rule body. Re-generate when memory changes.

### v0.4 enforcement

Pair the filtered rule body with the v0.4 enforcement gates so mislabeled memories cannot leak into Cursor:

```bash
memory-audit --export-tier=internal --strict --path ~/your-memory-folder
memory-dlp-scan --paths ~/your-memory-folder/*.md --strict
```

The audit's export-tier gate fails BLOCKER on declared-tier > export-tier; the DLP cross-check fails BLOCKER on body content whose implied tier exceeds the declared label. Privileged-tier enforcement is a hard floor. See `docs/adapter-implementation-guide.md` §"Secure-mode sensitivity enforcement (v0.4.0+)".
