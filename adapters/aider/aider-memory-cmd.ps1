# aider-memory-cmd.ps1: PowerShell wrapper for aider + MemForge.
#
# Dot-source from your PowerShell profile so it loads in every session:
#   notepad $PROFILE
#   . "<path to your memforge clone>\adapters\aider\aider-memory-cmd.ps1"
#
# Then invoke `aider-mem <args>` instead of `aider <args>`. Reads the
# MEMORY.md of each default memory folder at session start. The folders come
# from `memforge paths`, so this wrapper finds the same store as every
# memforge tool (MEMFORGE_MEMORY_PATH, else the Claude Code layout, else
# ~/.memforge). Needs `memforge` on PATH.

function aider-mem {
    if (-not (Get-Command memforge -ErrorAction SilentlyContinue)) {
        Write-Error "aider-mem: memforge is not on PATH, so the memory folders are unknown. Install memforge or add it to PATH; set MEMFORGE_MEMORY_PATH to choose the folders it reports."
        return
    }
    # memforge writes the folder names as UTF-8 bytes on Windows; decode them
    # as UTF-8 whatever the console code page is.
    $consoleEncoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [Text.Encoding]::UTF8
        $folders = @(& memforge paths)
    } finally {
        [Console]::OutputEncoding = $consoleEncoding
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "aider-mem: memforge paths failed; not starting aider."
        return
    }
    $readArgs = @()

    foreach ($folder in $folders) {
        if (-not $folder) { continue }
        $index = Join-Path $folder 'MEMORY.md'
        if (Test-Path -LiteralPath $index -PathType Leaf) { $readArgs += @('--read', $index) }
    }

    if ($readArgs.Count -eq 0) {
        Write-Warning "no memory folders found at default paths; running aider without memory context"
    }

    & aider @readArgs @args
}
