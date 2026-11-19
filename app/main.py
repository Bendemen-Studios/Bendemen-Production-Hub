import asyncio
import hmac
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

NAME = "Bendemen Production Hub"
VERSION = "0.5.0"
DB_PATH = os.getenv("HUB_DATABASE_PATH", "/data/hub.sqlite3")
ADMIN_USER = os.getenv("HUB_ADMIN_USER", "")
ADMIN_PASSWORD = os.getenv("HUB_ADMIN_PASSWORD", "")
AGENT_TOKEN = os.getenv("HUB_AGENT_TOKEN", "")
app = FastAPI(title=NAME, version=VERSION)
security = HTTPBasic()
connections: dict[str, WebSocket] = {}
live_state: dict[str, dict[str, Any]] = {}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def safe_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def clean_text(value, limit=128):
    return str(value or "").strip()[:limit]



def is_printing(status: str) -> bool:
    value = clean_text(status, 32).lower()
    return value in {"printing", "print", "running"} or "printing" in value


def finish_result(status: str, progress) -> str:
    value = clean_text(status, 32).lower()
    if any(word in value for word in ("complete", "finished", "finish", "success")):
        return "completed"
    if progress is not None and progress >= 0.999:
        return "completed"
    if any(word in value for word in ("cancel", "abort", "stop", "error", "fail")):
        return "stopped"
    return "interrupted"


def record_print_update(conn, agent_id: str, payload: dict, seen: str):
    active = conn.execute(
        "SELECT id,file_name FROM print_jobs WHERE agent_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    printing = is_printing(payload.get("printer_status", ""))
    file_name = clean_text(payload.get("file_name"), 128)
    if printing:
        if active and active["file_name"] != (file_name or "Unknown file"):
            conn.execute("UPDATE print_jobs SET ended_at=?, result='interrupted' WHERE id=?", (seen, active["id"]))
            active = None
        if active is None:
            conn.execute(
                "INSERT INTO print_jobs(agent_id,file_name,started_at,last_progress) VALUES(?,?,?,?)",
                (agent_id, file_name or "Unknown file", seen, payload.get("progress")),
            )
        else:
            conn.execute("UPDATE print_jobs SET last_progress=? WHERE id=?", (payload.get("progress"), active["id"]))
    elif active:
        conn.execute(
            "UPDATE print_jobs SET ended_at=?, result=? WHERE id=?",
            (seen, finish_result(payload.get("printer_status", ""), payload.get("progress")), active["id"]),
        )



def normalize_filament(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    name = clean_text(data.get("name"), 80)
    brand = clean_text(data.get("brand"), 80)
    material = clean_text(data.get("material"), 24).upper()
    color = clean_text(data.get("color"), 40)
    notes = clean_text(data.get("notes"), 500)
    allowed_materials = {"PLA", "PLA+", "PETG", "ABS", "ASA", "TPU", "NYLON", "PC", "OTHER"}
    if not name:
        raise ValueError("Filament name is required.")
    if material not in allowed_materials:
        raise ValueError("Unsupported material type.")
    spool_weight = safe_number(data.get("spool_weight_g"))
    remaining = safe_number(data.get("remaining_weight_g"))
    if spool_weight is None or remaining is None or spool_weight <= 0 or remaining < 0:
        raise ValueError("Weights must be valid positive gram values.")
    if remaining > spool_weight:
        raise ValueError("Remaining weight cannot exceed spool weight.")
    return {
        "name": name, "brand": brand, "material": material, "color": color,
        "spool_weight_g": float(spool_weight), "remaining_weight_g": float(remaining),
        "notes": notes,
    }


def public_filament(row) -> dict:
    return {key: row[key] for key in (
        "id", "name", "brand", "material", "color",
        "spool_weight_g", "remaining_weight_g", "notes", "created_at", "updated_at"
    )}


def public_agent(agent_id: str, payload: dict, last_seen: str, online: bool) -> dict:
    return {
        "agent_id": agent_id,
        "online": online,
        "last_seen": last_seen,
        "printer_host": payload.get("printer_host"),
        "printer_reachable": payload.get("printer_reachable"),
        "api_error": payload.get("api_error", ""),
        "printer_name": payload.get("printer_name", "Unknown printer"),
        "printer_status": payload.get("printer_status", "unknown"),
        "firmware": payload.get("firmware", ""),
        "progress": payload.get("progress"),
        "file_name": payload.get("file_name", ""),
        "nozzle_temp": payload.get("nozzle_temp"),
        "nozzle_target": payload.get("nozzle_target"),
        "bed_temp": payload.get("bed_temp"),
        "bed_target": payload.get("bed_target"),
        "layer": payload.get("layer"),
        "total_layers": payload.get("total_layers"),
        "remaining_seconds": payload.get("remaining_seconds"),
        "camera_available": payload.get("camera_available", False),
        "agent_version": payload.get("agent_version", "unknown"),
    }


@app.on_event("startup")
async def startup():
    if not all((ADMIN_USER, ADMIN_PASSWORD, AGENT_TOKEN)):
        raise RuntimeError("Set HUB_ADMIN_USER, HUB_ADMIN_PASSWORD and HUB_AGENT_TOKEN.")
    with database() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY, last_seen TEXT NOT NULL, payload TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS print_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            result TEXT,
            last_progress REAL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_started ON print_jobs(started_at DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS filaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT NOT NULL DEFAULT '',
            material TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '',
            spool_weight_g REAL NOT NULL,
            remaining_weight_g REAL NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_filaments_updated ON filaments(updated_at DESC)")


def admin_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    valid_user = hmac.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    valid_pass = hmac.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (valid_user and valid_pass):
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/health")
async def health():
    return {"status": "ok", "name": NAME, "version": VERSION}


@app.get("/", response_class=HTMLResponse)
async def dashboard(_: str = Depends(admin_auth)):
    return """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bendemen Production Hub</title>
<style>
:root{color-scheme:dark;--bg:#101114;--panel:#191b20;--muted:#a0a5b0;--accent:#c3110c;--line:#292c34}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#f5f5f5;font:15px Inter,system-ui,sans-serif}
main{max-width:1240px;margin:auto;padding:36px 22px}.head{display:flex;justify-content:space-between;align-items:center;gap:16px}
h1{font-size:26px;margin:0}p,.muted{color:var(--muted)}.pill{border:1px solid #343740;border-radius:999px;padding:7px 12px;color:var(--muted);font-size:12px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:24px}
.metric,.printer{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:17px}
.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.09em}
.metric-value{font-size:23px;font-weight:650;margin-top:8px}.sub{font-size:13px;color:var(--muted);margin-top:7px}
.section{margin:30px 0 12px;font-size:17px}.printers{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}
.printer-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.printer-name{font-size:18px;font-weight:650;margin-top:6px}
.status{font-size:12px;padding:6px 9px;border-radius:99px;background:#30333b;color:#d2d5dc;white-space:nowrap}
.status.online{background:#163528;color:#9ce2bb}.status.offline{background:#3b2525;color:#efaaaa}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:18px}.stat{background:#14161a;border-radius:10px;padding:12px}
.stat-value{font-size:20px;font-weight:650;margin-top:5px}.bar{height:7px;background:#30333b;border-radius:20px;margin-top:10px;overflow:hidden}.fill{height:100%;background:var(--accent);width:0}
.job{margin-top:14px;color:#e0e2e7;overflow-wrap:anywhere}.muted-line{font-size:12px;color:var(--muted);margin-top:6px}
.history-wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px}
table{width:100%;border-collapse:collapse;min-width:620px}th,td{text-align:left;padding:13px 15px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}tr:last-child td{border-bottom:0}
.result{display:inline-block;border-radius:99px;padding:5px 8px;background:#30333b;font-size:11px}
.result.completed{background:#163528;color:#9ce2bb}.result.stopped{background:#3b2525;color:#efaaaa}
.inventory-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.filament{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
.filament-top{display:flex;gap:12px;align-items:center}.swatch{width:34px;height:34px;border-radius:10px;background:#777;border:1px solid #ffffff33;flex:none}
.filament-title{font-weight:650}.filament-meta{font-size:12px;color:var(--muted);margin-top:4px}
.stockbar{height:8px;border-radius:10px;background:#30333b;overflow:hidden;margin-top:13px}.stockfill{height:100%;background:var(--accent)}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.field label{display:block;color:var(--muted);font-size:12px;margin:0 0 6px}
.field input,.field select,.field textarea{width:100%;background:#111318;color:#f5f5f5;border:1px solid #3a3d46;border-radius:8px;padding:10px;font:inherit}
.field textarea{min-height:65px;resize:vertical}.actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.action{border:1px solid #41444d;background:#24272e;color:#fff;border-radius:8px;padding:8px 11px;cursor:pointer}
.action.primary{background:var(--accent);border-color:var(--accent)}.action.danger{color:#ffaaa5}
.inventory-form{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
.form-message{font-size:13px;color:var(--muted);margin-top:10px}
.empty{padding:26px;border:1px dashed #41444d;border-radius:14px;color:var(--muted);text-align:center}
.notice{margin-top:22px;padding:13px 15px;border-radius:12px;background:#241c1b;border:1px solid #49302d;color:#e5c2bd}
footer{margin-top:28px;color:#747985;font-size:12px}
@media(max-width:520px){main{padding:24px 14px}.head{align-items:flex-start;flex-direction:column}h1{font-size:22px}}
</style></head><body><main>
<div class="head"><div><h1>Bendemen Production Hub</h1><p>Self-hosted production management</p></div><span class="pill">MVP · v0.5.0</span></div>
<div class="summary">
<section class="metric"><div class="label">Printers registered</div><div class="metric-value" id="count">0</div></section>
<section class="metric"><div class="label">Agents online</div><div class="metric-value" id="online">0</div></section>
<section class="metric"><div class="label">Active prints</div><div class="metric-value" id="printing">0</div></section>
<section class="metric"><div class="label">Hub status</div><div class="metric-value" id="hub">Online</div><div class="sub" id="updated">Waiting for first update</div></section>
</div>
<h2 class="section">Printers</h2>
<div class="printers" id="printers"><div class="empty">Waiting for a local agent to connect…</div></div>
<h2 class="section">Filament inventory</h2>
<div class="inventory-form">
 <div class="form-grid">
  <div class="field"><label for="fil-name">Name *</label><input id="fil-name" maxlength="80" placeholder="eSUN PLA+ Black"></div>
  <div class="field"><label for="fil-brand">Brand</label><input id="fil-brand" maxlength="80" placeholder="eSUN"></div>
  <div class="field"><label for="fil-material">Material *</label><select id="fil-material"><option>PLA</option><option>PLA+</option><option>PETG</option><option>ABS</option><option>ASA</option><option>TPU</option><option>NYLON</option><option>PC</option><option>OTHER</option></select></div>
  <div class="field"><label for="fil-color">Color</label><input id="fil-color" maxlength="40" placeholder="Matte black"></div>
  <div class="field"><label for="fil-spool">Original spool weight (g) *</label><input id="fil-spool" type="number" min="1" step="1" value="1000"></div>
  <div class="field"><label for="fil-remaining">Remaining filament (g) *</label><input id="fil-remaining" type="number" min="0" step="1" value="1000"></div>
 </div>
 <div class="field" style="margin-top:10px"><label for="fil-notes">Notes</label><textarea id="fil-notes" maxlength="500" placeholder="Nozzle temperature, storage location…"></textarea></div>
 <div class="actions"><button class="action primary" id="fil-save">Add filament</button><button class="action" id="fil-cancel" hidden>Cancel edit</button></div>
 <div class="form-message" id="fil-message">Keep track of spool stock manually. Automatic consumption is not enabled yet.</div>
</div>
<div class="inventory-grid" id="filaments"><div class="empty">Loading filament stock…</div></div>
<h2 class="section">Print history</h2><div class="history-wrap" id="history"><div class="empty">No print jobs recorded yet.</div></div><div class="notice"><b>Read-only mode:</b> the Hub reads printer telemetry only. No print, movement, pause, cancel or heater commands are sent.</div>
<footer>Bendemen Studios · Bendemen Production Hub</footer></main>
<script>
const $=id=>document.getElementById(id);
const fmtTemp=v=>typeof v==='number'&&Number.isFinite(v)?Math.round(v)+' °C':'—';
const fmtTime=v=>typeof v==='number'&&Number.isFinite(v)?Math.ceil(v/60)+' min':'—';
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function printerCard(a){
 const card=node('article','printer'),head=node('div','printer-head'),left=node('div','');
 left.append(node('div','label',a.agent_id||'Local agent'));
 left.append(node('div','printer-name',a.printer_name||'Unknown printer'));
 if(a.firmware)left.append(node('div','muted-line','Firmware '+a.firmware));
 const badge=node('span','status '+(a.online?'online':'offline'),a.online?'Agent online':'Agent offline');
 head.append(left,badge);card.append(head);
 const stats=node('div','stats');
 const items=[
  ['Printer status',a.printer_reachable===false?'Unreachable':(a.printer_status||'Unknown')],
  ['Nozzle',fmtTemp(a.nozzle_temp)+' / '+fmtTemp(a.nozzle_target)],
  ['Bed',fmtTemp(a.bed_temp)+' / '+fmtTemp(a.bed_target)],
  ['Layers',(a.layer??'—')+' / '+(a.total_layers??'—')],
  ['Remaining',fmtTime(a.remaining_seconds)],
  ['Camera',a.camera_available?'Detected':'Not detected']
 ];
 for(const [label,value] of items){const s=node('div','stat');s.append(node('div','label',label),node('div','stat-value',String(value)));stats.append(s)}
 card.append(stats);
 const p=typeof a.progress==='number'&&Number.isFinite(a.progress)?Math.max(0,Math.min(100,a.progress*100)):null;
 const job=node('div','job',a.file_name||'No active print');
 card.append(job);
 const bar=node('div','bar'),fill=node('div','fill');fill.style.width=p===null?'0%':p+'%';bar.append(fill);card.append(bar);
 card.append(node('div','muted-line',p===null?'Print progress unavailable':Math.round(p)+'% complete'));
 card.append(node('div','muted-line',a.last_seen?'Last update: '+new Date(a.last_seen).toLocaleString():'No telemetry received'));
 if(a.api_error)card.append(node('div','muted-line','API: '+a.api_error));
 if(a.printer_host)card.append(node('div','muted-line','LAN: '+a.printer_host+':8898'));
 return card;
}

let editingFilament=null;
function filamentCard(f){
 const card=node('article','filament'),top=node('div','filament-top'),swatch=node('div','swatch');
 const colors={"black":"#222","white":"#eee","red":"#c3110c","blue":"#2563eb","green":"#16a34a","yellow":"#eab308","orange":"#f97316","gold":"#d4a017","silver":"#9ca3af"};
 const key=(f.color||'').toLowerCase();swatch.style.background=colors[key]||(/^#[0-9a-f]{6}$/i.test(f.color||'')?f.color:'#777');
 const info=node('div','');info.append(node('div','filament-title',f.name),node('div','filament-meta',[f.brand,f.material,f.color].filter(Boolean).join(' · ')));
 top.append(swatch,info);card.append(top);
 const percent=f.spool_weight_g>0?Math.max(0,Math.min(100,f.remaining_weight_g/f.spool_weight_g*100)):0;
 card.append(node('div','filament-meta',Math.round(f.remaining_weight_g)+' g remaining · '+Math.round(percent)+'% of '+Math.round(f.spool_weight_g)+' g'));
 const bar=node('div','stockbar'),fill=node('div','stockfill');fill.style.width=percent+'%';bar.append(fill);card.append(bar);
 if(f.notes)card.append(node('div','filament-meta',f.notes));
 const actions=node('div','actions'),edit=node('button','action','Edit'),del=node('button','action danger','Delete');
 edit.type=del.type='button';edit.addEventListener('click',()=>editFilament(f));del.addEventListener('click',()=>deleteFilament(f.id));actions.append(edit,del);card.append(actions);
 return card;
}
async function refreshFilaments(){
 const target=$('filaments');
 try{const r=await fetch('/api/filaments',{cache:'no-store'});if(!r.ok)throw Error();const data=await r.json();target.replaceChildren();
  if(!data.filaments.length){target.append(node('div','empty','No filament spools added yet.'));return}
  data.filaments.forEach(f=>target.append(filamentCard(f)));
 }catch(e){target.textContent='Could not load filament inventory'}
}
function resetFilamentForm(){
 editingFilament=null;
 ['fil-name','fil-brand','fil-color','fil-notes'].forEach(id=>$(id).value='');
 $('fil-material').value='PLA';$('fil-spool').value='1000';$('fil-remaining').value='1000';
 $('fil-save').textContent='Add filament';$('fil-cancel').hidden=true;
}
function editFilament(f){
 editingFilament=f.id;
 $('fil-name').value=f.name;$('fil-brand').value=f.brand;$('fil-material').value=f.material;
 $('fil-color').value=f.color;$('fil-spool').value=f.spool_weight_g;$('fil-remaining').value=f.remaining_weight_g;$('fil-notes').value=f.notes;
 $('fil-save').textContent='Save changes';$('fil-cancel').hidden=false;$('fil-message').textContent='Editing '+f.name;
 $('fil-name').focus();window.scrollTo({top:0,behavior:'smooth'});
}
async function saveFilament(){
 const payload={name:$('fil-name').value,brand:$('fil-brand').value,material:$('fil-material').value,color:$('fil-color').value,
  spool_weight_g:Number($('fil-spool').value),remaining_weight_g:Number($('fil-remaining').value),notes:$('fil-notes').value};
 $('fil-save').disabled=true;
 try{const r=await fetch(editingFilament?'/api/filaments/'+editingFilament:'/api/filaments',{method:editingFilament?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await r.json();if(!r.ok)throw Error(data.detail||'Could not save filament');
  $('fil-message').textContent=editingFilament?'Filament updated.':'Filament added.';resetFilamentForm();await refreshFilaments();
 }catch(e){$('fil-message').textContent=e.message}finally{$('fil-save').disabled=false}
}
async function deleteFilament(id){
 if(!confirm('Delete this filament spool?'))return;
 try{const r=await fetch('/api/filaments/'+id,{method:'DELETE'});if(!r.ok)throw Error();await refreshFilaments()}
 catch(e){$('fil-message').textContent='Could not delete filament'}
}
$('fil-save').addEventListener('click',saveFilament);
$('fil-cancel').addEventListener('click',()=>{resetFilamentForm();$('fil-message').textContent='Edit cancelled.'});
async function refreshHistory(){
 try{
  const r=await fetch('/api/history?limit=20',{cache:'no-store'});if(!r.ok)throw Error();
  const data=await r.json(),target=$('history');target.replaceChildren();
  if(!data.jobs.length){target.append(node('div','empty','No print jobs recorded yet.'));return}
  const table=node('table',''),thead=node('thead',''),hr=node('tr','');
  ['Printer','File','Started','Finished','Result'].forEach(t=>hr.append(node('th','',t)));
  thead.append(hr);table.append(thead);const body=node('tbody','');
  data.jobs.forEach(j=>{
   const tr=node('tr','');
   [j.printer_name||j.agent_id,j.file_name,new Date(j.started_at).toLocaleString(),j.ended_at?new Date(j.ended_at).toLocaleString():'In progress'].forEach(v=>tr.append(node('td','',v)));
   const td=node('td',''),badge=node('span','result '+(j.result||'active'),j.result||'active');td.append(badge);tr.append(td);body.append(tr);
  });
  table.append(body);target.append(table);
 }catch(e){$('history').textContent='Could not load print history'}
}
async function refresh(){
 try{
  const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('Status request failed');
  const data=await r.json(),agents=Array.isArray(data.agents)?data.agents:[];
  $('count').textContent=agents.length;
  $('online').textContent=agents.filter(a=>a.online).length;
  $('printing').textContent=agents.filter(a=>a.online&&String(a.printer_status||'').toLowerCase().includes('print')).length;
  $('hub').textContent='Online';$('updated').textContent='Updated '+new Date().toLocaleTimeString();
  const target=$('printers');target.replaceChildren();
  if(!agents.length)target.append(node('div','empty','No printers registered yet. Start the Local Agent to connect your AD5X.'));
  else agents.forEach(a=>target.append(printerCard(a)));
 }catch(e){$('hub').textContent='Unavailable';$('updated').textContent='Could not load printer status'}
}
refresh();refreshHistory();refreshFilaments();setInterval(refresh,4000);setInterval(refreshHistory,15000);setInterval(refreshFilaments,30000);
</script></body></html>"""


@app.get("/api/status")
async def status(_: str = Depends(admin_auth)):
    records: dict[str, dict] = {}
    with database() as conn:
        rows = conn.execute("SELECT agent_id,last_seen,payload FROM agents ORDER BY agent_id").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        records[row["agent_id"]] = public_agent(row["agent_id"], payload, row["last_seen"], row["agent_id"] in connections)
    for agent_id, state in live_state.items():
        records[agent_id] = public_agent(agent_id, state, state.get("last_seen", utcnow()), agent_id in connections)
    agents = sorted(records.values(), key=lambda item: item["printer_name"].lower())
    return JSONResponse({"application": NAME, "version": VERSION, "agents": agents, "agent": agents[0] if agents else None})



@app.get("/api/filaments")
async def list_filaments(_: str = Depends(admin_auth)):
    with database() as conn:
        rows = conn.execute("SELECT * FROM filaments ORDER BY updated_at DESC, id DESC").fetchall()
    return {"filaments": [public_filament(row) for row in rows]}


@app.post("/api/filaments")
async def create_filament(payload: dict, _: str = Depends(admin_auth)):
    try:
        item = normalize_filament(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    now = utcnow()
    with database() as conn:
        cursor = conn.execute(
            """INSERT INTO filaments(name,brand,material,color,spool_weight_g,remaining_weight_g,notes,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (item["name"], item["brand"], item["material"], item["color"], item["spool_weight_g"],
             item["remaining_weight_g"], item["notes"], now, now),
        )
        row = conn.execute("SELECT * FROM filaments WHERE id=?", (cursor.lastrowid,)).fetchone()
    return {"filament": public_filament(row)}


@app.put("/api/filaments/{filament_id}")
async def update_filament(filament_id: int, payload: dict, _: str = Depends(admin_auth)):
    try:
        item = normalize_filament(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    now = utcnow()
    with database() as conn:
        cursor = conn.execute(
            """UPDATE filaments SET name=?,brand=?,material=?,color=?,spool_weight_g=?,
               remaining_weight_g=?,notes=?,updated_at=? WHERE id=?""",
            (item["name"], item["brand"], item["material"], item["color"], item["spool_weight_g"],
             item["remaining_weight_g"], item["notes"], now, filament_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Filament not found")
        row = conn.execute("SELECT * FROM filaments WHERE id=?", (filament_id,)).fetchone()
    return {"filament": public_filament(row)}


@app.delete("/api/filaments/{filament_id}")
async def delete_filament(filament_id: int, _: str = Depends(admin_auth)):
    with database() as conn:
        cursor = conn.execute("DELETE FROM filaments WHERE id=?", (filament_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Filament not found")
    return {"deleted": True}


@app.get("/api/history")
async def history(limit: int = 20, _: str = Depends(admin_auth)):
    limit = max(1, min(limit, 100))
    with database() as conn:
        rows = conn.execute(
            """SELECT p.id,p.agent_id,p.file_name,p.started_at,p.ended_at,p.result,p.last_progress,
                      a.payload
               FROM print_jobs p LEFT JOIN agents a ON a.agent_id=p.agent_id
               ORDER BY p.started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    jobs = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        jobs.append({
            "id": row["id"], "agent_id": row["agent_id"],
            "printer_name": payload.get("printer_name", row["agent_id"]),
            "file_name": row["file_name"], "started_at": row["started_at"],
            "ended_at": row["ended_at"], "result": row["result"] or "active",
            "last_progress": row["last_progress"],
        })
    return {"jobs": jobs}


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
        agent_id = clean_text(auth.get("agent_id"), 64)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", agent_id):
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
                "printer_host": clean_text(msg.get("printer_host"), 128),
                "printer_reachable": msg.get("printer_reachable") if isinstance(msg.get("printer_reachable"), bool) else None,
                "api_error": clean_text(msg.get("api_error"), 160),
                "printer_name": clean_text(msg.get("printer_name"), 64) or "Unknown printer",
                "printer_status": clean_text(msg.get("printer_status"), 32) or "unknown",
                "firmware": clean_text(msg.get("firmware"), 32),
                "progress": safe_number(msg.get("progress")),
                "file_name": clean_text(msg.get("file_name"), 128),
                "nozzle_temp": safe_number(msg.get("nozzle_temp")),
                "nozzle_target": safe_number(msg.get("nozzle_target")),
                "bed_temp": safe_number(msg.get("bed_temp")),
                "bed_target": safe_number(msg.get("bed_target")),
                "layer": msg.get("layer") if isinstance(msg.get("layer"), int) and not isinstance(msg.get("layer"), bool) else None,
                "total_layers": msg.get("total_layers") if isinstance(msg.get("total_layers"), int) and not isinstance(msg.get("total_layers"), bool) else None,
                "remaining_seconds": safe_number(msg.get("remaining_seconds")),
                "camera_available": bool(msg.get("camera_available", False)),
                "agent_version": clean_text(msg.get("agent_version"), 32) or "unknown",
            }
            if payload["progress"] is not None and not 0 <= payload["progress"] <= 1:
                payload["progress"] = None
            seen = utcnow()
            live_state[agent_id] = {**payload, "last_seen": seen}
            with database() as conn:
                record_print_update(conn, agent_id, payload, seen)
                conn.execute(
                    "INSERT INTO agents(agent_id,last_seen,payload) VALUES(?,?,?) "
                    "ON CONFLICT(agent_id) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload",
                    (agent_id, seen, json.dumps(payload, allow_nan=False)),
                )
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if agent_id and connections.get(agent_id) is ws:
            connections.pop(agent_id, None)


@app.get("/api/version")
async def version(_: str = Depends(admin_auth)):
    return {"name": NAME, "version": VERSION}
