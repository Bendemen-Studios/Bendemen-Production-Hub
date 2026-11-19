# Bendemen Production Hub

Self-hosted production and 3D-printer management. MVP v0.2 provides read-only live telemetry from a Flashforge AD5X through a local agent.

## VPS deployment

1. Clone this repository.
2. Find the Docker network used by Nginx Proxy Manager with \`docker network ls\`; set \`NPM_DOCKER_NETWORK\` in \`.env\` to that exact network name.
3. Copy \`.env.example\` to \`.env\`.
4. Generate separate secrets with \`openssl rand -hex 32\`; set \`HUB_ADMIN_PASSWORD\` and \`HUB_AGENT_TOKEN\`. Set a strong \`HUB_ADMIN_USER\`.
5. Ensure NPM is attached to the same Docker network.
6. Run \`docker compose up -d --build\`.
7. In NPM create a Proxy Host: scheme \`http\`, hostname \`bendemen-production-hub\`, port \`8000\`; enable WebSocket Support and SSL, and force HTTPS.

The API is exposed only to the private Docker network shared with NPM. No host port is published. Do not publish printer ports to the internet.

## Local agent — Windows 10/11

The Windows PC must be on the same home network as the AD5X. It connects outbound to the Hub over secure WebSocket (WSS), so no port forwarding or VPN is required.

1. Install Python 3.12 for Windows from https://www.python.org/downloads/windows/. Enable **Add python.exe to PATH**.
2. Download this repository (Code → Download ZIP), extract it, and open PowerShell in the repository's \`agent\` folder.
3. If Windows blocks local scripts, run \`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned\`.
4. Run \`./install-windows.ps1\`. On first run it creates \`.env\` and opens it in Notepad.
5. Fill in the settings below, save \`.env\`, and run \`./install-windows.ps1\` again.

Configure:
- \`HUB_WS_URL\`: public Hub URL ending in \`/ws/agent\`, using \`wss://\`.
- \`HUB_AGENT_ID\`: unique name, e.g. \`ad5x-home\`.
- \`HUB_AGENT_TOKEN\`: exactly the same secret as \`HUB_AGENT_TOKEN\` in the VPS \`.env\`.
- \`PRINTER_HOST\`: AD5X reserved LAN IP.
- \`PRINTER_SERIAL\` and \`PRINTER_CHECK_CODE\`: values from printer device information while LAN Mode is enabled.
- \`PRINTER_API_PORT\`: normally \`8898\`.

The installer registers a Windows Task Scheduler task that starts the agent when your Windows user logs in. It runs in the background and reconnects after network interruptions. Keep the PC powered on and disable sleep; the screen can turn off and Windows can be locked. Sleep, hibernate, shutdown or logging out stops the agent.

Useful PowerShell commands:
\`\`\`powershell
Get-ScheduledTask -TaskName "Bendemen Production Hub Agent"
Start-ScheduledTask -TaskName "Bendemen Production Hub Agent"
Stop-ScheduledTask -TaskName "Bendemen Production Hub Agent"
Get-Content .\\logs\\agent.log -Wait
Unregister-ScheduledTask -TaskName "Bendemen Production Hub Agent" -Confirm:$false
\`\`\`

## Local Linux agent

The agent can also run on a Linux mini-PC on the same LAN as the AD5X. It initiates outbound WSS, so no home-router port forwarding or VPN is required.

\`\`\`bash
sudo useradd --system --create-home --home-dir /opt/bendemen-agent bendemen-agent
sudo apt update && sudo apt install -y git python3 python3-venv
sudo mkdir -p /opt/bendemen-production-hub
sudo chown bendemen-agent:bendemen-agent /opt/bendemen-production-hub
sudo -u bendemen-agent git clone https://github.com/Bendemen-Studios/Bendemen-Production-Hub.git /opt/bendemen-production-hub
cd /opt/bendemen-production-hub/agent
sudo -u bendemen-agent python3 -m venv .venv
sudo -u bendemen-agent .venv/bin/pip install -r requirements.txt
sudo -u bendemen-agent cp .env.example .env
sudo nano .env
\`\`\`

Configure the same values listed in the Windows section. Protect credentials with \`chmod 600 .env\`.

Install the service:
\`\`\`bash
sudo cp bendemen-agent.service /etc/systemd/system/bendemen-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now bendemen-agent
sudo journalctl -u bendemen-agent -f
\`\`\`

If you cloned elsewhere, update the paths in the service unit first.

## AD5X API

With LAN Mode enabled, the agent makes an authenticated read-only \`POST /detail\` request to the printer's HTTP API on port 8898, using serial number and check code. It reads state, progress, current/target nozzle and bed temperatures, layers, remaining time, firmware and camera URL availability. Credentials remain on the local agent and are never sent to the VPS.

The AD5X does not have a built-in camera. An optional compatible camera is required. This version detects whether the printer reports a camera stream URL; live video relay is a later milestone and is not yet implemented.

## Safety and current scope

- Dashboard protected by HTTP Basic Authentication.
- Agent authenticated over outbound WSS.
- Printer telemetry is read-only.
- No print start/pause/resume/cancel, movement, heater or other write commands.
- No camera stream URL or printer credentials are exposed in the dashboard/API.
- Keep both \`.env\` files private and use unique random secrets.

Before multi-user production use, add per-user authorization, audit logging, rate limiting, token rotation and PostgreSQL.
