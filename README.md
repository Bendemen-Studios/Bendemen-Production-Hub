# Bendemen Production Hub

Self-hosted production and 3D-printer management. MVP v0.1 provides a VPS dashboard and a secure outbound connection from a local agent on the same LAN as a Flashforge AD5X.

## VPS deployment

1. Clone this repository and enter the directory.
2. Find the Docker network used by Nginx Proxy Manager with `docker network ls`. Put its exact name in `NPM_DOCKER_NETWORK` in `.env`.
3. Copy `.env.example` to `.env`.
4. Generate two separate secrets with `openssl rand -hex 32`; set `HUB_ADMIN_PASSWORD` and `HUB_AGENT_TOKEN`. Set a strong `HUB_ADMIN_USER`.
5. Ensure the NPM container is attached to the network named by `NPM_DOCKER_NETWORK`.
6. Run `docker compose up -d --build`.
7. In Nginx Proxy Manager, create a Proxy Host for your domain:
   - Scheme: `http`
   - Forward Hostname/IP: `bendemen-production-hub`
   - Forward Port: `8000`
   - Enable WebSocket Support, request an SSL certificate and force HTTPS.

The API is only exposed to the private Docker network shared with NPM. No host port is published. Do not publish printer ports or the API directly to the internet.

## Local agent

On a Linux mini-PC on the same LAN as the AD5X, install Python 3.11+, then:

```bash
cd agent
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `HUB_WS_URL=wss://your-domain.example/ws/agent`, a unique `HUB_AGENT_ID`, the same `HUB_AGENT_TOKEN` as the VPS, and the printer's reserved LAN IP in `PRINTER_HOST`. Run `python agent.py`.

The agent initiates an outbound WSS connection, so no home-router port forwarding or VPN is needed.

## MVP scope and safety

- HTTP Basic Auth protected dashboard.
- Agent authentication over WSS.
- Agent online/offline and last-seen status.
- TCP reachability check for the configured printer host/port.
- Camera readiness placeholder.

This release does not send print, pause, resume, cancel or movement commands. The AD5X camera is optional; confirm the exact camera hardware, firmware and local stream URL before implementing streaming. No camera port is exposed publicly.

Before multi-user or commercial production use, add per-user authorization, audit logs, rate limiting, secret rotation workflow and PostgreSQL. Keep `.env` and `agent/.env` private.
