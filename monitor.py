#!/usr/bin/env python3
import requests
import re
import os
import json
import time
import hashlib
from datetime import datetime, timezone

class Config:
    URL = "https://upload120.com/"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 2
    MAINTENANCE_KEYWORDS = [
        r'parcheado', r'parchado', r'método.*actualiz', r'parch', r'mantenimiento',
        r'no disponible', r'buscando.*método', r'método.*parch', r'actualizando',
        r'offline', r'down', r'service.*interrupt', r'updating', r'looking for new',
        r'new method coming', r'temporarily', r'suspended', r'disabled', r'error',
        r'problema', r'caído', r'fuera de servicio', r'out of service',
        r'unavailable', r'under maintenance',
    ]
    STATE_FILE = "state.json"
    LOG_FILE = "log.json"
    MAX_LOG_ENTRIES = 500
    HEARTBEAT_INTERVAL = 24
    NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
    NTFY_BASE_URL = "https://ntfy.sh"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def hash_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def retry_with_backoff(max_retries, base_delay):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    print(f"[RETRY] Intento {attempt + 1}/{max_retries} fallo: {e}. Reintentando en {delay}s...")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

def load_state():
    try:
        if os.path.exists(Config.STATE_FILE):
            with open(Config.STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
    except Exception as e:
        print(f"[WARN] Error cargando estado: {e}")
    return {
        "last_status": "UNKNOWN", "last_check_timestamp": "",
        "last_notify_timestamp": None, "total_checks": 0,
        "total_changes": 0, "consecutive_errors": 0,
        "last_html_hash": None, "heartbeat_counter": 0
    }

def save_state(state):
    try:
        with open(Config.STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Estado guardado: {state['last_status']}")
    except Exception as e:
        print(f"[ERROR] Error guardando estado: {e}")
        raise

def load_log():
    try:
        if os.path.exists(Config.LOG_FILE):
            with open(Config.LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Error cargando log: {e}")
    return []

def append_log(entry):
    try:
        log = load_log()
        log.append(entry)
        if len(log) > Config.MAX_LOG_ENTRIES:
            log = log[-Config.MAX_LOG_ENTRIES:]
        with open(Config.LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Error guardando log: {e}")

def compile_regex():
    pattern = '|'.join(Config.MAINTENANCE_KEYWORDS)
    return re.compile(pattern, re.IGNORECASE)

def detect(html):
    if html is None:
        return "ERROR", "No se pudo obtener la pagina", None
    if not html.strip():
        return "ERROR", "Contenido vacio", None
    html_hash = hash_text(html)
    regex = compile_regex()
    match = regex.search(html)
    if match:
        return "DOWN", f"Keyword detectado: '{match.group(0)}'", html_hash
    return "UP", "Servicio operativo", html_hash

@retry_with_backoff(max_retries=Config.MAX_RETRIES, base_delay=Config.RETRY_DELAY_BASE)
def fetch():
    headers = {
        "User-Agent": Config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
    }
    print(f"[HTTP] GET {Config.URL}")
    resp = requests.get(Config.URL, headers=headers, timeout=Config.TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    print(f"[HTTP] OK - {resp.status_code}, {len(resp.text)} chars")
    return resp.text

@retry_with_backoff(max_retries=Config.MAX_RETRIES, base_delay=Config.RETRY_DELAY_BASE)
def post_ntfy(message, title, priority="default", tags=""):
    url = f"{Config.NTFY_BASE_URL}/{Config.NTFY_TOPIC}"
    headers = {
        "Title": title, "Priority": priority, "Tags": tags,
        "Content-Type": "text/plain"
    }
    print(f"[NTFY] POST {url}")
    resp = requests.post(url, headers=headers, data=message, timeout=10)
    resp.raise_for_status()
    print(f"[NTFY] OK")
    return True

def notify_status_change(status, detail):
    if status == "DOWN":
        return post_ntfy(
            f"🔴 Upload120 - MANTENIMIENTO DETECTADO\n\n{detail}\n\nHora: {now_iso()}",
            "Upload120 Monitor - ALERTA", "high", "rotating_light,warning")
    elif status == "UP":
        return post_ntfy(
            f"🟢 Upload120 - OPERATIVO\n\n{detail}\n\nHora: {now_iso()}",
            "Upload120 Monitor - RECUPERADO", "default", "white_check_mark")
    else:
        return post_ntfy(
            f"⚠️ Upload120 - ERROR\n\n{detail}\n\nHora: {now_iso()}",
            "Upload120 Monitor - ERROR", "high", "warning")

def notify_heartbeat(state):
    return post_ntfy(
        f"💓 Upload120 Monitor - SIGO VIVO\n\n"
        f"Total checks: {state['total_checks']}\n"
        f"Total cambios: {state['total_changes']}\n"
        f"Ultimo estado: {state['last_status']}\n"
        f"Hora: {now_iso()}",
        "Upload120 Monitor - Heartbeat", "low", "heartbeat")

def main():
    print("=" * 60)
    print(f"[START] Upload120 Monitor Pro - {now_iso()}")
    print("=" * 60)
    
    state = load_state()
    html = None
    error_msg = None
    notified = False
    
    try:
        html = fetch()
        state["consecutive_errors"] = 0
    except Exception as e:
        error_msg = str(e)
        state["consecutive_errors"] += 1
        print(f"[ERROR] Fallo al obtener pagina: {e}")
        if state["consecutive_errors"] >= 3:
            print(f"[ALERT] {state['consecutive_errors']} errores consecutivos")
            notify_status_change("ERROR", f"Multiples fallos: {error_msg}")
            notified = True
    
    status, detail, html_hash = detect(html)
    print(f"[DETECT] Estado: {status} - {detail}")
    
    status_changed = status != state["last_status"]
    print(f"[COMPARE] Cambio: {status_changed} | Anterior: {state['last_status']} | Actual: {status}")
    
    should_notify = False
    if status_changed:
        print(f"[DECISION] CAMBIO detectado. Notificando...")
        should_notify = True
        state["total_changes"] += 1
    else:
        print(f"[DECISION] Sin cambios. Silencio.")
    
    if should_notify:
        try:
            notify_status_change(status, detail)
            notified = True
            state["last_notify_timestamp"] = now_iso()
        except Exception as e:
            print(f"[ERROR] Fallo al notificar: {e}")
    
    state["heartbeat_counter"] += 1
    if state["heartbeat_counter"] >= Config.HEARTBEAT_INTERVAL:
        print(f"[HEARTBEAT] Enviando...")
        try:
            notify_heartbeat(state)
            state["heartbeat_counter"] = 0
        except Exception as e:
            print(f"[ERROR] Heartbeat fallo: {e}")
    
    state["last_status"] = status
    state["last_check_timestamp"] = now_iso()
    state["last_html_hash"] = html_hash
    state["total_checks"] += 1
    
    save_state(state)
    append_log({
        "timestamp": now_iso(), "status": status, "detail": detail,
        "html_hash": html_hash, "notified": notified, "error": error_msg
    })
    
    print(f"[DONE] Checks totales: {state['total_checks']}")
    print("=" * 60)

if __name__ == "__main__":
    main()
