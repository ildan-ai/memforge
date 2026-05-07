# Register memforge memory-watch as a Windows Scheduled Task that
# starts at user login and stays running.
#
# Usage (PowerShell, regular user — no admin needed for user tasks):
#   1. Edit $MemoryWatchPath below to point at your memory-watch script.
#   2. Run this script once:
#        powershell -ExecutionPolicy Bypass -File daemon-windows-task.ps1
#   3. Verify:
#        Get-ScheduledTask -TaskName "MemForge-MemoryWatch"
#   4. Stop / unregister:
#        Stop-ScheduledTask -TaskName "MemForge-MemoryWatch"
#        Unregister-ScheduledTask -TaskName "MemForge-MemoryWatch" -Confirm:$false
#
# Prereqs:
#   - Python 3 on PATH.
#   - watchdog installed (python -m pip install watchdog).
#   - git on PATH.
#   - Memory folder is git-init'd.

$TaskName = "MemForge-MemoryWatch"

# Edit this to your memory-watch location.
# Typical path on Windows when memforge is cloned to your user profile:
$MemoryWatchPath = "$env:USERPROFILE\claude-projects\memforge\tools\memory-watch"

# Resolve the python executable; py launcher is preferred on Windows.
$PythonExe = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Error "Python not found on PATH. Install Python 3 first."
    exit 1
}

if (-not (Test-Path $MemoryWatchPath)) {
    Write-Error "memory-watch not found at $MemoryWatchPath. Edit this script."
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$MemoryWatchPath`" --quiet"

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
