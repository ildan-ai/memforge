# aider-memory-cmd.ps1 — PowerShell wrapper for aider + MemForge.
#
# Dot-source from your PowerShell profile so it loads in every session:
#   notepad $PROFILE
#   . "$env:USERPROFILE\claude-projects\memforge\adapters\aider\aider-memory-cmd.ps1"
#
# Then invoke `aider-mem <args>` instead of `aider <args>`. Reads global
# + per-cwd memory at session start.

function aider-mem {
    $memGlobal  = "$env:USERPROFILE\.claude\global-memory\MEMORY.md"
    $memPerCwd  = "$env:USERPROFILE\.claude\projects\$env:USERNAME-claude-projects\memory\MEMORY.md"
    $readArgs = @()

    if (Test-Path $memGlobal)  { $readArgs += @('--read', $memGlobal) }
    if (Test-Path $memPerCwd)  { $readArgs += @('--read', $memPerCwd) }

    if ($readArgs.Count -eq 0) {
        Write-Warning "no memory folders found at default paths; running aider without memory context"
    }

    & aider @readArgs @args
}
