import asyncio
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

NAME = "Bendemen Production Hub"
DB_PATH = os.getenv("HUB_DATABASE_PATH", "/data/hub.sqlite3")
ADMIN_USER = os.getenv("HUB_ADMIN_USER", "")
ADMIN_PASSWORD = os.getenv("HUB_ADMIN_PASSWORD", "")
AGENT_TOKEN = os.getenv("HUB_AGENT_TOKEN", "")
app = FastAPI(title=NAME, version="0.2.0")
security = HTTPBasic()
connections: dict[str, WebSocket] = {}
live_state: dict[str, dict[str, Any]] = {}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def database():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.on_event("startup")
async def startup():
    if not all((ADMIN_USER, ADMIN_PASSWORD, AGENT_TOKEN)):
        raise RuntimeError("Set HUB_ADMIN_USER, HUB_ADMIN_PASSWORD and HUB_AGENT_TOKEN.")
    with database() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY, last_seen TEXT NOT NULL, payload TEXT NOT NULL
        )""")


def admin_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    valid_user = hmac.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    valid_pass = hmac.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (valid_user and valid_pass):
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/health")
async def health():
    return {"status": "ok", "name": NAME, "version": "0.2.0"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(_: str = Depends(admin_auth)):
    return """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bendemen Production Hub</title>
<style>
:root{color-scheme:dark;--bg:#101114;--panel:#191b20;--muted:#a0a5b0;--accent:#c3110c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#f5f5f5;font:15px Inter,system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:36px 22px}.head{display:flex;justify-content:space-between;align-items:center;gap:16px}
h1{font-size:26px;margin:0}p,.muted{color:var(--muted)}.pill{border:1px solid #343740;border-radius:999px;padding:7px 12px;color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-top:26px}
.card{background:var(--panel);border:1px solid #292c34;border-radius:15px;padding:18px}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.09em}
.value{font-size:23px;font-weight:650;margin-top:9px}.sub{font-size:13px;color:var(--muted);margin-top:7px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#777;margin-right:8px}.on{background:#35bd78}.bar{height:8px;background:#30333b;border-radius:20px;margin-top:12px;overflow:hidden}.fill{height:100%;background:var(--accent);width:0;transition:width .3s}
.notice{margin-top:20px;padding:14px 16px;border-radius:12px;background:#241c1b;border:1px solid #49302d;color:#e5c2bd}
.section{margin-top:28px;font-size:16px}footer{margin-top:28px;color:#747985;font-size:12px}
@media(max-width:520px){main{padding:24px 14px}.head{align-items:flex-start;flex-direction:column}h1{font-size:22px}}
</style></head><body><main>
<div class="head"><div><h1>Bendemen Production Hub</h1><p>Self-hosted production management</p></div><span class="pill">MVP · v0.2.0</span></div>
<h2 class="section">Printer overview</h2>
<div class="grid">
<section class="card"><div class="label">Local agent</div><div class="value" id="agent">Waiting</div><div class="sub" id="seen">No agent connected</div></section>
<section class="card"><div class="label">Printer state</div><div class="value" id="state">Unknown</div><div class="sub" id="model">Flashforge AD5X · LAN API</div></section>
<section class="card"><div class="label">Print progress</div><div class="value" id="progress">—</div><div class="bar"><div class="fill" id="progressbar"></div></div><div class="sub" id="job">No active job</div></section>
<section class="card"><div class="label">Nozzle temperature</div><div class="value" id="nozzle">—</div><div class="sub" id="nozzleTarget">Target —</div></section>
<section class="card"><div class="label">Bed temperature</div><div class="value" id="bed">—</div><div class="sub" id="bedTarget">Target —</div></section>
<section class="card"><div class="label">Layers</div><div class="value" id="layers">—</div><div class="sub" id="time">Remaining —</div></section>
<section class="card"><div class="label">Camera accessory</div><div class="value" id="camera">Unknown</div><div class="sub">AD5X has no built-in camera; optional accessory required</div></section>
<section class="card"><div class="label">Printer connection</div><div class="value" id="reach">Unknown</div><div class="sub" id="endpoint">Waiting for agent diagnostics</div></section>
</div>
<div class="notice"><b>Read-only mode:</b> printer status and temperatures are read from the local AD5X API. No print, movement, pause or cancel commands are sent.</div>
<footer>Bendemen Studios · Bendemen Production Hub</footer></main>
<script>
const el=id=>document.getElementById(id), temp=v=>Number.isFinite(v)?Math.round(v)+' °C':'—';
async function refresh(){try{const r=await fetch('/api/status');if(!r.ok)throw Error();const a=(await r.json()).agent||{};
el('agent').innerHTML=a.online?'<span class="dot on"></span>Online':'<span class="dot"></span>Offline';
el('seen').textContent=a.last_seen?'Last seen: '+new Date(a.last_seen).toLocaleString():'No agent connected';
el('state').textContent=a.printer_status||'Unknown';
el('model').textContent=(a.printer_name||'Flashforge AD5X')+(a.firmware?' · '+a.firmware:'');
const p=Number(a.progress);el('progress').textContent=Number.isFinite(p)?Math.round(p*100)+'%':'—';el('progressbar').style.width=Number.isFinite(p)?Math.max(0,Math.min(100,p*100))+'%':'0%';
el('job').textContent=a.file_name||'No active job';
el('nozzle').textContent=temp(a.nozzle_temp);el('nozzleTarget').textContent='Target '+temp(a.nozzle_target);
el('bed').textContent=temp(a.bed_temp);el('bedTarget').textContent='Target '+temp(a.bed_target);
el('layers').textContent=(a.layer??'—')+' / '+(a.total_layers??'—');
el('time').textContent='Remaining '+(Number.isFinite(a.remaining_seconds)?Math.ceil(a.remaining_seconds/60)+' min':'—');
el('camera').textContent=a.camera_available?'Accessory detected':'Not detected';
el('reach').textContent=a.printer_reachable?'Connected':a.printer_reachable===false?'Unreachable':'Unknown';
el('endpoint').textContent=a.printer_host?(a.printer_host+':8898'):(a.api_error||'Waiting for agent diagnostics');
}catch(e){el('agent').textContent='Unavailable'}}
refresh();setInterval(refresh,4000);
</script></body></html>"""


@app.get("/api/status")
async def status(_: str = Depends(admin_auth)):
    latest: dict[str, Any] = {"online": False, "last_seen": None, "printer_reachable": None}
    if live_state:
        agent_id, state = next(iter(live_state.items()))
        latest = {"agent_id": agent_id, "online": agent_id in connections, **state}
    else:
        with database() as conn:
            row = conn.execute("SELECT agent_id,last_seen,payload FROM agents ORDER BY last_seen DESC LIMIT 1").fetchone()
        if row:
            latest = {"agent_id": row["agent_id"], "online": False, "last_seen": row["last_seen"], **json.loads(row["payload"])}
    return JSONResponse({"application": NAME, "agent": latest})


@app.websocket("/ws/agent")
async def agent_ws(ws: WebSocket):
    await ws.accept()
    agent_id = None
    try:
        auth = await asyncio.wait_for(ws.receive_json(), timeout=10)
        supplied = str(auth.get("token", "")) if isinstance(auth, dict) else ""
        if not isinstance(auth, dict) or auth.get("type") != "auth" or not hmac.compare_digest(supplied.encode(), AGENT_TOKEN.encode()):
            await ws.close(code=1008, reason="Authentication failed")
            return
        agent_id = str(auth.get("agent_id", "")).strip()
        if not agent_id or len(agent_id) > 64:
            await ws.close(code=1008, reason="Invalid agent id")
            return
        old = connections.get(agent_id)
        if old and old is not ws:
            await old.close(code=4001, reason="Agent reconnected")
        connections[agent_id] = ws
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict) or msg.get("type") != "status":
                continue
            payload = {
                "printer_host": str(msg.get("printer_host", ""))[:128],
                "printer_reachable": msg.get("printer_reachable") if isinstance(msg.get("printer_reachable"), bool) else None,
                "api_error": str(msg.get("api_error", ""))[:160],
                "printer_name": str(msg.get("printer_name", "Flashforge AD5X"))[:64],
                "printer_status": str(msg.get("printer_status", "unknown"))[:32],
                "firmware": str(msg.get("firmware", ""))[:32],
                "progress": msg.get("progress") if isinstance(msg.get("progress"), (int, float)) else None,
                "file_name": str(msg.get("file_name", ""))[:128],
                "nozzle_temp": msg.get("nozzle_temp") if isinstance(msg.get("nozzle_temp"), (int, float)) else None,
                "nozzle_target": msg.get("nozzle_target") if isinstance(msg.get("nozzle_target"), (int, float)) else None,
                "bed_temp": msg.get("bed_temp") if isinstance(msg.get("bed_temp"), (int, float)) else None,
                "bed_target": msg.get("bed_target") if isinstance(msg.get("bed_target"), (int, float)) else None,
                "layer": msg.get("layer") if isinstance(msg.get("layer"), int) else None,
                "total_layers": msg.get("total_layers") if isinstance(msg.get("total_layers"), int) else None,
                "remaining_seconds": msg.get("remaining_seconds") if isinstance(msg.get("remaining_seconds"), (int, float)) else None,
                "camera_available": bool(msg.get("camera_available", False)),
                "agent_version": str(msg.get("agent_version", "unknown"))[:32],
            }
            seen = utcnow()
            live_state[agent_id] = {**payload, "last_seen": seen}
            with database() as conn:
                conn.execute("INSERT INTO agents(agent_id,last_seen,payload) VALUES(?,?,?) "
                             "ON CONFLICT(agent_id) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload",
                             (agent_id, seen, json.dumps(payload)))
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if agent_id and connections.get(agent_id) is ws:
            connections.pop(agent_id, None)


@app.get("/api/version")
async def version(_: str = Depends(admin_auth)):
    return {"name": NAME, "version": "0.2.0"}
