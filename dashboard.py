#!/usr/bin/env python3
"""
Dashboard web server for the Upload120 Monitor.
Serves a read-only HTML view of state.json and log.json on port 3000.
Uses only the Python standard library — no extra dependencies.
"""

import json
import os
import html
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

PORT = int(os.environ.get("PORT", "3000"))
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
LOG_FILE = os.environ.get("LOG_FILE", "log.json")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fmt_ts(ts):
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts


def status_badge(status):
    s = (status or "UNKNOWN").upper()
    colors = {
        "UP": ("#16a34a", "🟢"),
        "DOWN": ("#dc2626", "🔴"),
        "ERROR": ("#ea580c", "⚠️"),
        "UNKNOWN": ("#6b7280", "❓"),
    }
    color, icon = colors.get(s, ("#6b7280", "❓"))
    return f'<span class="badge" style="background:{color}">{icon} {s}</span>'


def render_page():
    state = load_json(STATE_FILE, {})
    log = load_json(LOG_FILE, [])
    # Show newest first
    log_recent = list(reversed(log))[:50]

    rows = ""
    for entry in log_recent:
        rows += f"""
        <tr>
          <td>{status_badge(entry.get("status"))}</td>
          <td><code>{html.escape(str(entry.get("strategy", "—")))}</code></td>
          <td><code>{html.escape(str(entry.get("detection_method", "—")))}</code></td>
          <td>{html.escape(str(entry.get("detail", "—")))[:120]}</td>
          <td>{fmt_ts(entry.get("timestamp"))}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload120 Monitor — Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
  :root {{ --bg:#0f172a; --card:#1e293b; --border:#334155; --text:#e2e8f0; --muted:#94a3b8; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text); padding:2rem; }}
  h1 {{ font-size:1.6rem; margin-bottom:.3rem; }}
  .subtitle {{ color:var(--muted); margin-bottom:2rem; font-size:.9rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1rem; margin-bottom:2rem; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:.75rem; padding:1.25rem; }}
  .card .label {{ color:var(--muted); font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; margin-bottom:.4rem; }}
  .card .value {{ font-size:1.5rem; font-weight:700; }}
  .badge {{ display:inline-block; padding:.15em .6em; border-radius:999px; font-size:.85rem; font-weight:600; color:#fff; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:.75rem; overflow:hidden; }}
  th, td {{ text-align:left; padding:.7rem .9rem; border-bottom:1px solid var(--border); font-size:.85rem; }}
  th {{ background:#0f172a; color:var(--muted); text-transform:uppercase; font-size:.72rem; letter-spacing:.05em; }}
  td code {{ background:#0f172a; padding:.15em .4em; border-radius:.3rem; font-size:.78rem; color:#93c5fd; }}
  .footer {{ margin-top:1.5rem; color:var(--muted); font-size:.78rem; text-align:center; }}
</style>
</head>
<body>
  <h1>📡 Upload120 Monitor Pro</h1>
  <p class="subtitle">Dashboard en vivo · auto-actualiza cada 30s</p>

  <div class="grid">
    <div class="card"><div class="label">Estado Actual</div><div class="value">{status_badge(state.get("last_status"))}</div></div>
    <div class="card"><div class="label">Última Verificación</div><div class="value" style="font-size:1rem">{fmt_ts(state.get("last_check_timestamp"))}</div></div>
    <div class="card"><div class="label">Total de Chequeos</div><div class="value">{state.get("total_checks", 0)}</div></div>
    <div class="card"><div class="label">Cambios Detectados</div><div class="value">{state.get("total_changes", 0)}</div></div>
    <div class="card"><div class="label">Estrategia Usada</div><div class="value" style="font-size:1rem"><code>{html.escape(str(state.get("last_strategy_used", "—")))}</code></div></div>
    <div class="card"><div class="label">Método de Detección</div><div class="value" style="font-size:1rem"><code>{html.escape(str(state.get("last_detection_method", "—")))}</code></div></div>
  </div>

  <h2 style="font-size:1.1rem;margin-bottom:.8rem">📋 Registros Recientes</h2>
  <table>
    <thead><tr><th>Estado</th><th>Estrategia</th><th>Detección</th><th>Detalle</th><th>Timestamp</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <p class="footer">Upload120 Monitor Pro v3.3 · Dashboard servido por Base44</p>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = render_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            body = json.dumps(load_json(STATE_FILE, {})).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/log":
            body = json.dumps(load_json(LOG_FILE, [])).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # quiet


if __name__ == "__main__":
    print(f"[dashboard] serving on 0.0.0.0:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
