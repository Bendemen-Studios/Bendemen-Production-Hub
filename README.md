# Bendemen Production Hub

Self-hosted production and 3D-printer management. MVP v0.9 provides a Flashforge AD5X dashboard, read-only live telemetry, persistent print history, filament inventory and manual material-consumption/cost tracking. The first supported printer integration is the Flashforge AD5X.

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

## Print history

The Hub records a print session when the AD5X reports a printing state, tracks progress, and closes the session when printing stops. Sessions are classified as completed, stopped or interrupted using the final printer status and progress. The dashboard shows the latest 20 sessions. Classification depends on the status values exposed by the printer firmware and should be verified during a real print.

## Filament inventory

The dashboard includes a manual filament stock register. Add a spool with its name, brand, material, color, original spool weight and current remaining weight. Existing spools can be edited or deleted. Supported material labels are PLA, PLA+, PETG, ABS, ASA, TPU, NYLON, PC and OTHER.

Each spool stores its purchase price in EUR and can also store an optional HTTP/HTTPS shopping link to its product page. Existing inventory records are migrated automatically when the Hub starts.

## Material use and costs

After a print is recorded, select the print job and the filament spool used, then enter the consumed grams. The Hub calculates the material cost from the spool purchase price divided by its original spool weight, stores a cost snapshot for that usage entry, and subtracts the grams from the spool's remaining stock. The dashboard shows total recorded grams, total recorded material costs and recent usage entries. A spool with recorded usage cannot be deleted, so its history and cost records remain intact.

Material consumption is manually entered. The current AD5X integration does not provide verified per-print filament usage, so the Hub does not automatically infer or subtract material. Costs cover filament material only; electricity, printer depreciation, failed-print overhead and labor are not included.

Stock is currently maintained manually in grams. The Hub does not estimate or subtract filament consumption from print jobs because the current AD5X telemetry adapter does not provide a verified per-job material usage value. No spool is changed automatically when a print starts or finishes.

## Multiple printers

The Hub stores one status record per unique agent ID and shows all registered printers on the dashboard. Give every physical printer/agent its own unique `HUB_AGENT_ID` and a local agent configuration. The current printer API adapter is Flashforge AD5X; additional printer brands (such as Elegoo) need their own protocol adapter before they can report telemetry.

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


## Sales and profitability

A print job can be registered as a sale with a sale price, additional costs and optional notes. The Hub calculates revenue, material costs from the filament usage entries linked to that print, total costs and profit. The dashboard shows totals and a per-print sales table. Additional costs can include electricity, packaging, shipping or other expenses; enter the combined amount for now.

Sales are recorded once per print job. A print job can have only one sale entry. Material usage added later is reflected in the sale's material-cost and profit calculation. This is a basic production ledger, not a full accounting or VAT system. Revenue is the entered sale price; taxes, payment fees, labor and depreciation are not calculated automatically.


## Inventory valuation and low-stock alerts

Every filament spool has an editable low-stock warning threshold in grams (default: 200 g). The dashboard marks spools at or below that threshold and shows the remaining stock value, calculated proportionally from the spool purchase price and original weight. The inventory summary shows total remaining grams, remaining stock value, low-stock spool count and product count.

Spools with matching name, brand, material and color are grouped in a product overview, while each spool remains independently editable and traceable. This supports multiple rolls of the same filament product without merging their individual stock balances.

## Monthly financial reports

The dashboard includes monthly sales reports for up to the last 36 months (12 months shown by default). Each month shows the number of registered sales, revenue, filament material costs, additional costs, total costs and calculated profit. Reports are based on sale registration dates. Only registered sales are included; unsold prints and unrecorded expenses are not automatically estimated.
