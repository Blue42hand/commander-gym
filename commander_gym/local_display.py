"""Read-only kiosk dashboard for a Commander Gym host.

Uses only local host information and the existing sidecar provenance JSONL.  It does
not add an Argentum protocol or expose hidden game state.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SERVICES = ("argentum-gym.service", "commander-gym-gateway.service")
MAX_PROVENANCE_LINES = 500

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Commander Gym Display</title>
<style>
:root{color-scheme:dark;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#080b10;color:#e9edf3}
*{box-sizing:border-box} body{margin:0;padding:2.2vw;background:radial-gradient(circle at 50% 0,#162231,#080b10 55%)}
header{display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid #33404f;padding-bottom:1.3vw}
h1{font-size:4vw;letter-spacing:.16em;margin:0} .sub{color:#8fa0b4;font-size:1.2vw}.ok{color:#9dd7ad}.bad{color:#ff9d9d}
.grid{display:grid;grid-template-columns:1.05fr 1.95fr;gap:1.5vw;margin-top:1.5vw}.panel{background:#0d131bdb;border:1px solid #293646;border-radius:14px;padding:1.4vw}
h2{font-size:1.25vw;letter-spacing:.13em;color:#9fb3c8;margin:0 0 1vw}.big{font-size:2.3vw}.metric{display:flex;justify-content:space-between;border-bottom:1px solid #202b37;padding:.55vw 0}
.players{display:grid;grid-template-columns:repeat(2,1fr);gap:1vw}.player{border:1px solid #293646;border-radius:10px;padding:1vw}.life{font-size:2.6vw}
.log{font-size:1.05vw;line-height:1.55;max-height:14vw;overflow:hidden}.decision{font-size:1.15vw;line-height:1.5;min-height:6vw}
footer{position:fixed;bottom:.7vw;right:1.2vw;color:#617286;font-size:.8vw}
</style></head><body>
<header><div><h1>COMMANDER GYM</h1><div class="sub">LOCAL DISPLAY // RESEARCH NODE</div></div><div id="clock" class="big"></div></header>
<div class="grid">
<section class="panel"><h2>NODE STATUS</h2><div id="host"></div><h2 style="margin-top:1.4vw">TRAINING / POLICY</h2><div id="stats"></div></section>
<section><div class="panel"><h2>LIVE GAME</h2><div id="game" class="big">Awaiting pilot traffic…</div><div id="players" class="players"></div></div>
<div class="panel" style="margin-top:1.5vw"><h2>LATEST PILOT DECISION</h2><div id="decision" class="decision">No decision recorded.</div><h2 style="margin-top:1vw">RECENT GAME LOG</h2><div id="log" class="log"></div></div></section>
</div><footer>read-only · local telemetry · refresh 2s</footer>
<script>
const esc=x=>String(x??"—").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function rows(o){return Object.entries(o).map(([k,v])=>`<div class="metric"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}
function statePlayers(s){
 const ps=s.players||s.playerStates||[]; if(Array.isArray(ps)) return ps;
 if(s.playerStates&&typeof s.playerStates==="object") return Object.values(s.playerStates); return [];
}
async function tick(){
 document.getElementById("clock").textContent=new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
 try{
  const d=await (await fetch("/api/status",{cache:"no-store"})).json();
  document.getElementById("host").innerHTML=rows({"uptime":d.host.uptime,"load":d.host.load,"memory":d.host.memory,"temperature":d.host.temperature,...d.services,...d.revisions});
  document.getElementById("stats").innerHTML=rows({"policy calls":d.policy.calls,"active seats":d.policy.seats,"actions":d.policy.actions,"decisions":d.policy.decisions,"mulligans":d.policy.mulligans});
  const s=d.live.state||{}; const phase=s.phase||s.currentPhase||s.step?.phase; const turn=s.turnNumber||s.turn||"—";
  document.getElementById("game").innerHTML=`TURN ${esc(turn)} &nbsp;·&nbsp; ${esc(phase||"IDLE")}`;
  const ps=statePlayers(s); document.getElementById("players").innerHTML=ps.slice(0,4).map((p,i)=>`<div class="player"><b>${esc(p.name||p.playerName||p.playerId||"Seat "+(i+1))}</b><div class="life">${esc(p.life??p.lifeTotal??"—")}</div><small>hand ${esc(p.handSize??p.hand?.length??"—")} · battlefield ${esc(p.battlefieldSize??p.battlefield?.length??"—")}</small></div>`).join("");
  const c=d.live.choice||{}; const meta=c.metadata||{}; document.getElementById("decision").innerHTML=`<b>${esc(d.live.playerId||"—")}</b> · ${esc(d.live.callback||"—")}<br>${esc(meta.reasoning||meta.reason||meta.rationale||c.channel||JSON.stringify(c))}`;
  document.getElementById("log").innerHTML=(d.live.recentGameLog||[]).slice(-8).reverse().map(x=>`<div>${esc(x)}</div>`).join("");
 }catch(e){document.getElementById("game").innerHTML='<span class="bad">dashboard data unavailable</span>'}
}
tick(); setInterval(tick,2000);
</script></body></html>"""

def _run(*args: str) -> str | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=1, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None

def _human_bytes(n: int) -> str:
    for unit in ("B","KiB","MiB","GiB","TiB"):
        if n < 1024 or unit == "TiB": return f"{n:.1f} {unit}"
        n /= 1024
    return str(n)

def _host() -> dict[str, str]:
    uptime = _run("uptime","-p") or "unknown"
    load = Path("/proc/loadavg").read_text().split()[:3] if Path("/proc/loadavg").exists() else []
    mem = "unknown"
    if Path("/proc/meminfo").exists():
        vals={}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k,_,v=line.partition(":"); vals[k]=int(v.strip().split()[0])*1024
        if vals.get("MemTotal"): mem=f"{_human_bytes(vals['MemTotal']-vals.get('MemAvailable',0))} / {_human_bytes(vals['MemTotal'])}"
    temp="n/a"
    temps=list(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    if temps:
        try: temp=f"{int(temps[0].read_text().strip())/1000:.0f}°C"
        except ValueError: pass
    return {"uptime":uptime,"load":" ".join(load) or "unknown","memory":mem,"temperature":temp}

def _services() -> dict[str,str]:
    names=tuple(filter(None,os.getenv("COMMANDER_GYM_DISPLAY_SERVICES",",".join(DEFAULT_SERVICES)).split(",")))
    if not shutil.which("systemctl"): return {n:"unknown" for n in names}
    return {n:(_run("systemctl","is-active",n) or "inactive") for n in names}

def _revisions() -> dict[str,str]:
    result={}
    for label,env in (("commander-gym","COMMANDER_GYM_DISPLAY_REPO_DIR"),("argentum","COMMANDER_GYM_DISPLAY_ARGENTUM_DIR")):
        path=os.getenv(env)
        if path:
            rev=_run("git","-C",path,"rev-parse","--short=10","HEAD")
            if rev: result[label]=rev
    return result

def _records(path: Path | None) -> list[Mapping[str,Any]]:
    if path is None or not path.exists(): return []
    lines=deque(maxlen=MAX_PROVENANCE_LINES)
    try:
        with path.open(encoding="utf-8") as f:
            for line in f: lines.append(line)
    except OSError: return []
    out=[]
    for line in lines:
        try:
            item=json.loads(line)
            if isinstance(item,dict) and item.get("event")=="game_server_policy": out.append(item)
        except json.JSONDecodeError: pass
    return out

def build_status(provenance_path: Path | None) -> dict[str,Any]:
    records=_records(provenance_path)
    latest=records[-1] if records else {}
    callbacks=Counter(r.get("callback") for r in records)
    choices=Counter((r.get("choice") or {}).get("channel") for r in records if isinstance(r.get("choice"),dict))
    observation=latest.get("observation") if isinstance(latest.get("observation"),dict) else {}
    return {
        "timestamp":time.time(),"host":_host(),"services":_services(),"revisions":_revisions(),
        "policy":{"calls":len(records),"seats":len({r.get("playerId") for r in records if r.get("playerId")}),
                  "actions":choices["action"],"decisions":choices["decision"],
                  "mulligans":callbacks["decideMulligan"]},
        "live":{"playerId":latest.get("playerId"),"callback":latest.get("callback"),
                "state":observation.get("state") if isinstance(observation,dict) else {},
                "choice":latest.get("choice") or {},
                "recentGameLog":observation.get("recentGameLog",[]) if isinstance(observation,dict) else []},
    }

class Handler(BaseHTTPRequestHandler):
    provenance_path: Path | None = None
    def do_GET(self) -> None:
        if self.path == "/":
            data=HTML.encode(); ctype="text/html; charset=utf-8"
        elif self.path == "/api/status":
            data=json.dumps(build_status(self.provenance_path),separators=(",",":")).encode(); ctype="application/json"
        else:
            self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type",ctype); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, _format: str, *_args: Any) -> None: pass

def main() -> int:
    Handler.provenance_path=Path(os.environ["COMMANDER_GYM_DISPLAY_PROVENANCE"]).expanduser() if os.getenv("COMMANDER_GYM_DISPLAY_PROVENANCE") else None
    host=os.getenv("COMMANDER_GYM_DISPLAY_HOST","127.0.0.1"); port=int(os.getenv("COMMANDER_GYM_DISPLAY_PORT","8090"))
    server=ThreadingHTTPServer((host,port),Handler)
    print(f"Commander Gym display listening on http://{host}:{port}",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0

if __name__=="__main__": raise SystemExit(main())
