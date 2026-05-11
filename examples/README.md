# MemForge examples

Drop-in scripts you can fork to make MemForge painless to operate. Each example is opinionated but small; read the source before installing.

Two flavors of every example: bash (macOS / Linux / Git Bash on Windows / WSL) and PowerShell (native Windows + cross-platform pwsh 7+). Pick the one that matches your shell.

## commit-msg hook: enforce `memforge:` prefix grammar

Rejects commits violating the `memforge:` prefix grammar for Tier 2 scoped paths (operator-registry, config, snoozes, revocations). Catches the bulk of common mistakes at commit time, before the diff lands in the audit trail.

### bash version (`git-hooks/commit-msg`)

```bash
cp examples/git-hooks/commit-msg .git/hooks/commit-msg
chmod +x .git/hooks/commit-msg
```

### PowerShell version (`git-hooks/commit-msg.ps1`)

Windows git looks for `.git\hooks\commit-msg` (no extension); the PowerShell hook needs a thin wrapper so git can invoke it. Install:

```powershell
Copy-Item examples\git-hooks\commit-msg.ps1 .git\hooks\commit-msg.ps1
@"
#!/usr/bin/env pwsh
pwsh -NoLogo -NonInteractive -ExecutionPolicy Bypass ``
  -File (Join-Path `$PSScriptRoot 'commit-msg.ps1') @args
exit `$LASTEXITCODE
"@ | Set-Content -LiteralPath .git\hooks\commit-msg -Encoding utf8
```

Extend either version for `memforge: resolve <topic>` and `memforge: alias <topic>` as your team adopts those operations. See `docs/quickstart.md` §"Commit hygiene + signed `memforge:` prefixes" for the underlying contract.

## auto-commit watcher: auto-commit memory `.md` writes

Watches a memory-root and auto-commits memory `.md` files as they land. Skips Tier 2 scoped paths so the `memforge` CLI's own commits land cleanly without a race.

### bash version (`scripts/memforge-auto-commit-watcher.sh`)

macOS requires fswatch (`brew install fswatch`); Linux requires inotify-tools (`apt install inotify-tools`).

```bash
./examples/scripts/memforge-auto-commit-watcher.sh ~/.claude/global-memory &
```

Stop with `kill <PID>` or `fg` + Ctrl-C.

### PowerShell version (`scripts/memforge-auto-commit-watcher.ps1`)

Uses `System.IO.FileSystemWatcher`; no external dependencies on Windows.

```powershell
Start-Job -Name MemForgeWatcher -ScriptBlock {
  & C:\path\to\examples\scripts\memforge-auto-commit-watcher.ps1 -Root C:\path\to\memory-root
}
# Stop with: Stop-Job MemForgeWatcher
```

If you run Claude Code, its built-in PostToolUse `memory-auto-commit.sh` hook gives you the same shape scoped to Claude Code's tool-call lifecycle (rather than fs-level events); fork either of the above as a starting point.

## WebSocket scaffold (`websocket/`)

Starter material for operators standing up the v0.5 WebSocket messaging substrate. Includes:

- `websocket/README.md` — explains scope (this is scaffold, not a deployable relay; spec is the source of truth for the relay-side contract).
- `websocket/config.example.yaml` — reference shape for the `.memforge/config.yaml` `messaging:` block when adapter is `websocket`.
- `websocket/probe.py` — minimal Python probe that verifies a relay is reachable, accepts your operator's bearer token, and round-trips a placeholder envelope.

See `docs/team-bootstrap.md` §"Pick your transport: git-only or WebSocket?" for the operator-decision framing before standing any of this up.
