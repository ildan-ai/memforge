#!/usr/bin/env pwsh
# MemForge commit-msg hook. PowerShell port for Windows + cross-platform
# PowerShell users. Enforces the `memforge:` prefix grammar for scoped
# Tier-2 paths per the spec. Reject = exit 1; staged changes are
# preserved so the operator can fix the message and try again.
# Signed commits and the signer list are separate from this hook: set
# `commit.gpgsign true` in git and let `memforge sync` check signatures.
#
# What it does not check: that a config commit touches only the config
# file, and a snooze file whose name holds a newline. `memory-audit`
# reports both.
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

$staged = (& git -c core.quotePath=false diff --cached --name-only) -split "`r?`n" | Where-Object { $_ }

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

# .memforge/config.yaml -> memforge: config (Tier 2 BLOCKER per spec).
# Only the prefix is checked here; memory-audit checks the config-only rule.
if (Test-TouchesExact '.memforge/config.yaml') {
  if (-not (Test-StartsWith 'memforge: config')) {
    Stop-Commit `
      ".memforge/config.yaml staged but commit prefix is not 'memforge: config'." `
      "Per spec: config edits MUST land in 'memforge: config' commits that touch ONLY the config file (memory-audit checks that part)."
  }
}

# .memforge/audit-waivers.yaml -> memforge: waiver, alone (Tier 2 BLOCKER per spec)
if (Test-TouchesExact '.memforge/audit-waivers.yaml') {
  if (-not ($msg -ceq 'memforge: waiver' -or (Test-StartsWith 'memforge: waiver '))) {
    Stop-Commit `
      ".memforge/audit-waivers.yaml staged but commit prefix is not 'memforge: waiver'." `
      "Per spec: waiver edits MUST land in 'memforge: waiver' commits that touch ONLY the waiver file."
  }
  if (@($staged).Count -ne 1) {
    Stop-Commit `
      ".memforge/audit-waivers.yaml staged together with other files." `
      "Per spec: waiver edits MUST land in 'memforge: waiver' commits that touch ONLY the waiver file."
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

exit 0
