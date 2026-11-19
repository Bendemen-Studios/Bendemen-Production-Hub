$ErrorActionPreference = "Stop"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AgentDir
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python 3 is not installed. Install Python 3.12 from https://www.python.org/downloads/windows/ and enable Add python.exe to PATH."
}
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created agent/.env. Fill in the VPS token and AD5X LAN details, save it, then run this installer again."
    notepad.exe (Join-Path $AgentDir ".env")
    exit 0
}
if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt
$TaskName = "Bendemen Production Hub Agent"
$TaskArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $AgentDir + '\run-agent.ps1"'
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $TaskArgs
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "Connects to Bendemen Production Hub and reads Flashforge AD5X telemetry." -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started '$TaskName'. It starts when this Windows user logs in."
Write-Host "Logs: $AgentDir\logs\agent.log"
