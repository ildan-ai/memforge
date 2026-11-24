# MemForge examples

Drop-in scripts you can fork to make MemForge painless to operate. Each example is opinionated but small; read the source before installing.

Two flavors of every example: bash (macOS / Linux / Git Bash on Windows / WSL) and PowerShell (native Windows + cross-platform pwsh 7+). Pick the one that matches your shell.

## commit-msg hook: enforce `memforge:` prefix grammar

Rejects commits violating the `memforge:` prefix grammar for Tier 2 scoped paths (config, waivers and snoozes), and a waiver commit that holds other files. Catches the bulk of common mistakes at commit time, before the diff lands in the audit trail. It does not check that a config commit touches only the config file, and it misses a snooze file whose name holds a newline; `memory-audit` reports both.

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

The hook checks config, snooze and waiver commits. Extend either version for `memforge: resolve <topic>` and `memforge: alias <topic>` as your team adopts those operations. The hook checks commit-message prefixes only. Commit signing is a separate git setting (`commit.gpgsign true`), and `memforge sync` checks the signatures. See `docs/quickstart.md` §"Commit hygiene and the `memforge:` prefixes" and `docs/team-bootstrap.md`.

## auto-commit watcher: auto-commit memory `.md` writes

Watches a memory-root and auto-commits memory `.md` files as they land. Skips everything under `.memforge/` (config, snoozes, waivers, signer lists), so the `memforge` CLI's own commits land cleanly without a race and those files land only in a deliberate signed commit. It commits only `.md` files outside `.memforge/`.

### bash version (`scripts/memforge-auto-commit-watcher.sh`)

macOS requires fswatch (`brew install fswatch`); Linux requires inotify-tools (`apt install inotify-tools`).

```bash
./examples/scripts/memforge-auto-commit-watcher.sh ~/.memforge/global-memory &
```

Pass the folder `memforge paths` prints. `~/.memforge/global-memory` is the 1.0 default; an existing Claude Code store is under `~/.claude/` instead.

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

## Recall (`recall/`)

Query-triggered recall (spec v0.6.0): surface the descriptions of memories whose triggers match a query, instead of bulk-loading the whole `MEMORY.md` every session.

- `recall/recall-synonyms.example.yaml`: operator synonym override; copy to `<memory-root>/.memforge/recall-synonyms.yaml` to teach recall your team's vocabulary.
- `recall/README.md`: build-then-query walkthrough + the Claude Code `UserPromptSubmit` hook wiring.

See `docs/quickstart.md` §"Recall" and `spec/SPEC.md` §"Recall operation".
