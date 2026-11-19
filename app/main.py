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
app = FastAPI(title=NAME, version="0.1.0")
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
    return {"status": "ok", "name": NAME, "version": "0.1.0"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(_: str = Depends(admin_auth)):
    return """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bendemen Production Hub</title>
<style>
:root{color-scheme:dark;--bg:#101114;--panel:#191b20;--muted:#a0a5b0;--accent:#c3110c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#f5f5f5;font:15px Inter,system-ui,sans-serif}
main{max-width:1050px;margin:auto;padding:38px 22px}.head{display:flex;justify-content:space-between;align-items:center;gap:16px}
h1{font-size:26px;margin:0}p,.sub{color:var(--muted)}.pill{border:1px solid #343740;border-radius:999px;padding:7px 12px;color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-top:28px}
.card{background:var(--panel);border:1px solid #292c34;border-radius:16px;padding:20px}.label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.value{font-size:23px;font-weight:650;margin-top:10px}.sub{font-size:13px;margin-top:8px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#777;margin-right:8px}.on{background:#35bd78}
.notice{margin-top:22px;padding:16px;border-radius:12px;background:#241c1b;border:1px solid #49302d;color:#e5c2bd}footer{margin-top:28px;color:#747985;font-size:12px}
</style></head><body><main><div class="head"><div><h1>Bendemen Production Hub</h1><p>Self-hosted production management</p></div><span class="pill">MVP · v0.1.0</span></div>
<div class="grid">
<section class="card"><div class="label">Local agent</div><div class="value" id="agent">Waiting</div><div class="sub" id="seen">No agent connected</div></section>
<section class="card"><div class="label">Flashforge AD5X</div><div class="value" id="printer">Unknown</div><div class="sub" id="reach">Waiting for local diagnostics</div></section>
<section class="card"><div class="label">Camera</div><div class="value">Not configured</div><div class="sub">Camera hardware and stream URL must be verified first</div></section>
</div><div class="notice"><b>Safe mode:</b> this MVP checks connectivity only. It does not send print, movement, pause or cancel commands.</div>
<footer>Bendemen Studios · Bendemen Production Hub</footer></main>
<script>
async function refresh(){try{const r=await fetch('/api/status');if(!r.ok)throw Error();const d=await r.json(),a=d.agent;
document.getElementById('agent').innerHTML=a.online?'<span class="dot on"></span>Online':'<span class="dot"></span>Offline';
document.getElementById('seen').textContent=a.last_seen?'Last seen: '+new Date(a.last_seen).toLocaleString():'No agent connected';
document.getElementById('printer').textContent=a.printer_reachable===true?'Reachable':a.printer_reachable===false?'Unreachable':'Unknown';
document.getElementById('reach').textContent=a.printer_host?(a.printer_host+':'+a.printer_port):'Waiting for local diagnostics';
}catch(e){document.getElementById('agent').textContent='Unavailable'}}
refresh();setInterval(refresh,5000);
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
        connections[agent_id] = ws
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict) or msg.get("type") != "status":
                continue
            payload = {
                "printer_host": str(msg.get("printer_host", ""))[:128],
                "printer_port": int(msg.get("printer_port", 0)),
                "printer_reachable": msg.get("printer_reachable") if isinstance(msg.get("printer_reachable"), bool) else None,
                "camera_configured": bool(msg.get("camera_configured", False)),
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
    return {"name": NAME, "version": "0.1.0"}
