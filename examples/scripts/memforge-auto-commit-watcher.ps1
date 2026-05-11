#!/usr/bin/env pwsh
# MemForge auto-commit watcher. PowerShell port for Windows (uses
# System.IO.FileSystemWatcher). Watches a memory-root for memory `.md`
# writes and auto-commits each one as it lands. Explicitly skips Tier 2
# scoped paths so it does not race the `memforge` CLI which lands its
# own scoped commits with the right prefixes.
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

function Test-ScopedPath([string]$rel) {
  if ($rel -eq '.memforge/operator-registry.yaml') { return $true }
  if ($rel -eq '.memforge/config.yaml') { return $true }
  $scopedPrefixes = @(
    '.memforge/snoozes/',
    '.memforge/revocations/',
    '.memforge/sender-sequence/',
    '.memforge/agent-sessions/'
  )
  foreach ($prefix in $scopedPrefixes) {
    if ($rel.StartsWith($prefix)) { return $true }
  }
  return $false
}

function Invoke-AutoCommit([string]$fullPath, [string]$rootPath) {
  if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { return }
  $rel = $fullPath.Substring($rootPath.Length).TrimStart('\', '/').Replace('\', '/')
  if (Test-ScopedPath $rel) { return }
  if (-not ($rel -match '\.md$')) { return }
  & git add -- $rel 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { return }
  & git diff --cached --quiet -- $rel
  if ($LASTEXITCODE -eq 0) { return }  # nothing staged
  & git commit -m "memory: auto-commit $rel" 2>$null | Out-Null
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
