#!/usr/bin/env pwsh
# MemForge auto-commit watcher. PowerShell port for Windows (uses
# System.IO.FileSystemWatcher). Watches a memory-root for memory `.md`
# writes and auto-commits each one as it lands. Skips everything under
# `.memforge/` (config, snoozes, waivers, signer lists): those change only
# in deliberate commits with their own `memforge:` prefixes.
#
# Requirements:
#   - PowerShell 5.1 (Windows) or pwsh 7+ (any platform)
#   - git on PATH
#
# Usage (Windows PowerShell):
#   .\memforge-auto-commit-watcher.ps1 -Root C:\path\to\memory-root
#
# Run as a background job:
#   Start-Job -Name MemForgeWatcher -ScriptBlock {
#       & C:\path\to\memforge-auto-commit-watcher.ps1 -Root C:\path\to\memory-root
#   }
#
# Stop with: Stop-Job MemForgeWatcher

param(
  [string]$Root = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path -LiteralPath $Root).Path
Set-Location -LiteralPath $Root

# git must act on this folder. These are the variables
# `git rev-parse --local-env-vars` lists: a watcher started with any of them
# set would commit to another repository, index or work tree instead.
foreach ($name in 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS',
                  'GIT_CONFIG_COUNT', 'GIT_OBJECT_DIRECTORY', 'GIT_DIR', 'GIT_WORK_TREE',
                  'GIT_IMPLICIT_WORK_TREE', 'GIT_GRAFT_FILE', 'GIT_INDEX_FILE',
                  'GIT_NO_REPLACE_OBJECTS', 'GIT_REPLACE_REF_BASE', 'GIT_PREFIX',
                  'GIT_SHALLOW_FILE', 'GIT_COMMON_DIR', 'GIT_LITERAL_PATHSPECS') {
  Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}

# File names come from whoever can write into the folder. Each is read as a
# plain name with the `:(literal)` pathspec magic below, never as a pattern.
# GIT_LITERAL_PATHSPECS is removed above: git hands it to your hooks, where a
# pre-commit hook selecting files with '*.md' then matches nothing.

function Test-ScopedPath([string]$rel) {
  # Case-insensitive: on Windows `.MemForge\` is the same folder.
  return $rel.StartsWith('.memforge/', [System.StringComparison]::OrdinalIgnoreCase)
}

function Invoke-AutoCommit([string]$fullPath, [string]$rootPath) {
  if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { return }
  $rel = $fullPath.Substring($rootPath.Length).TrimStart('\', '/').Replace('\', '/')
  if (Test-ScopedPath $rel) { return }
  if (-not ($rel -match '\.md$')) { return }
  & git add -- ":(literal)$rel" 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { return }
  & git diff --cached --quiet -- ":(literal)$rel"
  if ($LASTEXITCODE -eq 0) { return }  # nothing staged
  # Commit this file only. Anything else already staged stays staged.
  & git commit -m "memory: auto-commit $rel" -- ":(literal)$rel" 2>$null | Out-Null
}

$fsw = New-Object System.IO.FileSystemWatcher
$fsw.Path = $Root
$fsw.IncludeSubdirectories = $true
$fsw.NotifyFilter = [System.IO.NotifyFilters]::LastWrite `
                  -bor [System.IO.NotifyFilters]::FileName `
                  -bor [System.IO.NotifyFilters]::Size

Write-Host "MemForge watcher: watching $Root for memory .md writes. Ctrl-C to stop."

while ($true) {
  # 5-second timeout so the loop is interruptible by Ctrl-C.
  $changeTypes = [System.IO.WatcherChangeTypes]::Changed `
              -bor [System.IO.WatcherChangeTypes]::Created
  $result = $fsw.WaitForChanged($changeTypes, 5000)
  if (-not $result.TimedOut) {
    $fullPath = Join-Path $Root $result.Name
    Invoke-AutoCommit $fullPath $Root
  }
}
