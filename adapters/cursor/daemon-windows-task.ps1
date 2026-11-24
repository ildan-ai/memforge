# Register memforge memory-watch as a Windows Scheduled Task that
# starts at user login and stays running.
#
# Usage (PowerShell, regular user; no admin needed for user tasks):
#   1. Install the package (pip install ildan-memforge). It installs the
#      memory-watch command, watchdog included. Edit $MemoryFolder below to
#      name your memory folder.
#   2. Run this script once:
#        powershell -ExecutionPolicy Bypass -File daemon-windows-task.ps1
#   3. Verify:
#        Get-ScheduledTask -TaskName "MemForge-MemoryWatch"
#   4. Stop / unregister:
#        Stop-ScheduledTask -TaskName "MemForge-MemoryWatch"
#        Unregister-ScheduledTask -TaskName "MemForge-MemoryWatch" -Confirm:$false
#
# Prereqs:
#   - ildan-memforge installed, with its Scripts folder on PATH. No separate
#     watchdog install is needed.
#   - git on PATH.
#   - Memory folder is git-init'd.

$TaskName = "MemForge-MemoryWatch"

# Edit this to the absolute path of your memory folder.
$MemoryFolder = "<absolute path to your memory folder>"

# The memory-watch command that pip installed with ildan-memforge.
$MemoryWatchPath = (Get-Command memory-watch -ErrorAction SilentlyContinue).Source
if (-not $MemoryWatchPath) {
    Write-Error "memory-watch not found on PATH. Install ildan-memforge into an environment whose Scripts folder is on PATH."
    exit 1
}

if (-not (Test-Path $MemoryFolder)) {
    Write-Error "Memory folder not found at $MemoryFolder. Edit this script."
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute $MemoryWatchPath `
    -Argument "--path `"$MemoryFolder`" --quiet"

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# Replace any existing task by the same name.
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "MemForge filesystem watcher: auto-commits memory-folder writes to git."

Write-Host "Registered scheduled task '$TaskName' to start at login."
Write-Host "Start now: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Logs: %USERPROFILE%\AppData\Local\MemForge\memory-watch.log (configure stdout redirect if you want a log file)"
