$ErrorActionPreference = "Continue"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AgentDir
New-Item -ItemType Directory -Force -Path (Join-Path $AgentDir "logs") | Out-Null
& (Join-Path $AgentDir ".venv\Scripts\python.exe") -u (Join-Path $AgentDir "agent.py") *>> (Join-Path $AgentDir "logs\agent.log")
