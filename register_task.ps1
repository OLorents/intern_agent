# Registers a Windows Scheduled Task that runs the intern-watch agent.
# Run ONCE from PowerShell (no admin needed for a per-user task):
#   .\register_task.ps1                       # daily at 08:30
#   .\register_task.ps1 -Times "08:00","20:00"  # twice a day
# Re-running updates the task (-Force).

param(
  [string[]]$Times = @("08:30"),          # one or more daily run times (24h, local)
  [string]$TaskName = "InternWatchAgent"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$agent     = Join-Path $scriptDir "agent.py"

# Resolve a Python launcher on PATH
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python not found on PATH." }

$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$agent`"" -WorkingDirectory $scriptDir
$triggers = foreach ($t in $Times) { New-ScheduledTaskTrigger -Daily -At $t }
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Description "Notifies on new ML/AI internship postings" -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at: $($Times -join ', ')."
Write-Host "Run it now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Remove it:   Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
