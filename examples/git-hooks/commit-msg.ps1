#!/usr/bin/env pwsh
# MemForge commit-msg hook. PowerShell port for Windows + cross-platform
# PowerShell users. Enforces the `memforge:` prefix grammar for scoped
# Tier-2 paths per the spec. Reject = exit 1; staged changes are
# preserved so the operator can fix the message and try again.
#
# Install (Windows PowerShell or pwsh 7+ on any platform):
#   Copy-Item examples\git-hooks\commit-msg.ps1 .git\hooks\commit-msg.ps1
#   # Git's hooks dispatcher looks for `.git\hooks\commit-msg` (no
#   # extension). Drop this tiny wrapper at that path:
#
#     #!/usr/bin/env pwsh
#     # commit-msg wrapper for PowerShell hook
#     pwsh -NoLogo -NonInteractive -ExecutionPolicy Bypass `
#       -File (Join-Path $PSScriptRoot 'commit-msg.ps1') @args
#     exit $LASTEXITCODE
#
#   # Then: git config core.hooksPath .git\hooks (if not already set).

param([Parameter(Mandatory = $true)][string]$MsgFile)

$ErrorActionPreference = 'Stop'

$msg = Get-Content -LiteralPath $MsgFile -TotalCount 1 -ErrorAction SilentlyContinue
if (-not $msg) { $msg = '' }

$staged = (& git diff --cached --name-only) -split "`r?`n" | Where-Object { $_ }

function Test-StartsWith([string]$prefix) {
  return $msg.StartsWith($prefix)
}
function Test-TouchesExact([string]$path) {
  return $staged -contains $path
}
function Test-TouchesPattern([string]$regex) {
  return ($staged | Where-Object { $_ -match $regex }).Count -gt 0
}

function Stop-Commit([string]$problem, [string]$hint) {
  [Console]::Error.WriteLine('MemForge commit-msg hook: rejecting commit.')
  [Console]::Error.WriteLine("  $problem")
  if ($hint) { [Console]::Error.WriteLine("  Hint: $hint") }
  exit 1
}

# .memforge/operator-registry.yaml -> memforge: operator-registry OR memforge: rotate-key
if (Test-TouchesExact '.memforge/operator-registry.yaml') {
  if (-not (Test-StartsWith 'memforge: operator-registry') -and `
      -not (Test-StartsWith 'memforge: rotate-key')) {
    Stop-Commit `
      "operator-registry.yaml staged but commit prefix is not 'memforge: operator-registry' or 'memforge: rotate-key'." `
      "Run the corresponding memforge CLI command and use the message it prints."
  }
}

# .memforge/config.yaml -> memforge: config (Tier 2 BLOCKER per spec)
if (Test-TouchesExact '.memforge/config.yaml') {
  if (-not (Test-StartsWith 'memforge: config')) {
    Stop-Commit `
      ".memforge/config.yaml staged but commit prefix is not 'memforge: config'." `
      "Per spec: config edits MUST land in 'memforge: config' commits that touch ONLY the config file."
  }
}

# .memforge/snoozes/<topic>.yaml -> memforge: snooze <topic>
if (Test-TouchesPattern '^\.memforge/snoozes/.*\.yaml$') {
  if (-not (Test-StartsWith 'memforge: snooze ')) {
    Stop-Commit `
      "Snooze file staged but commit prefix is not 'memforge: snooze <topic>'." `
      "Snooze edits MUST land in 'memforge: snooze <topic>' commits."
  }
}

# Revocation events -> memforge: revoke OR memforge: revocation-snapshot
if (Test-TouchesPattern '^\.memforge/revocations?/') {
  if (-not (Test-StartsWith 'memforge: revoke') -and `
      -not (Test-StartsWith 'memforge: revocation-snapshot')) {
    Stop-Commit `
      "Revocation artifact staged but commit prefix is not 'memforge: revoke' or 'memforge: revocation-snapshot'." `
      "Run 'memforge revoke <key_id>' or 'memforge revocation-snapshot' and use the printed message."
  }
}

exit 0
