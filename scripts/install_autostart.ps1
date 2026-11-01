# Registers QuantDesk to start automatically when you log in to Windows, so the agent trades
# every market day without you having to remember to launch it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1          # install
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove  # uninstall
#
# Also recommended: Settings > System > Power > never sleep while plugged in (at least 08:00-16:00 IST).
param([switch]$Remove)

$TaskName = "QuantDesk Agent"
$Root = Split-Path -Parent $PSScriptRoot
$Qd = Join-Path $Root ".venv\Scripts\qd.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

if (-not (Test-Path $Qd)) { throw "Not found: $Qd. Create the venv and run: pip install -e .[garch]" }

$action = New-ScheduledTaskAction -Execute $Qd -Argument "start --no-open" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "QuantDesk autonomous trading agent + web app (http://localhost:8000)" -Force | Out-Null
Write-Host "Installed '$TaskName'. It starts at every logon; start it now with: Start-ScheduledTask -TaskName '$TaskName'"
