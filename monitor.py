#!/usr/bin/env python3
"""
MONITOR UPLOAD120 PRO v3.2 - STRUCTURED DETECTION
===================================================
Sistema de monitoreo con 4 estrategias de bypass + deteccion estructurada.

Cambios v3.2:
- Deteccion basada en indicadores estructurados del HTML (no keywords sueltas)
- Primario: pagina /status/ con data-status attribute
- Secundario: popup de outage en pagina principal (clase 'hidden')
- Fallback: keyword matching con regex preciso (sin falsos positivos)
- cloudscraper como Estrategia 1 (mas efectiva contra Cloudflare)

Autor: Lyra | Fecha: 2026-09-02
"""

import requests
import re
import os
import json
import time
import hashlib
import random
from datetime import datetime, timezone
from typing import Optional, Tuple

# ============ CONFIGURACION ============

class Config:
    URL = "https://upload120.com/"
    STATUS_URL = "https://upload120.com/status/"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    TIMEOUT = 20

    STATE_FILE = "state.json"
    LOG_FILE = "log.json"
    MAX_LOG_ENTRIES = 500
    HEARTBEAT_INTERVAL = 24
    NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
    NTFY_BASE_URL = "https://ntfy.sh"
    FORCE_NOTIFY = os.environ.get("FORCE_NOTIFY", "false").lower() == "true"

    # Versiones de Chrome para curl_cffi
    CURL_CFFI_VERSIONS = ["chrome131", "chrome124", "chrome120", "chrome116", "chrome110"]

    # Fallback: keywords precisas (sin palabras sueltas como 'down' o 'error')
    MAINTENANCE_KEYWORDS = [
        r'parcheado', r'parchado', r'metodo\s+actualiz\w*',
        r'parch\w+', r'mantenimiento',
        r'no\s+disponible', r'buscando\s+metodo', r'metodo\s+parch\w*',
        r'actualizando',
        r'problema\s+(con|de|en)', r'fuera\s+de\s+servicio',
        r'en\s+mantenimiento', r'servicio\s+interrumpido',
        r'no\s+funciona',
        r'ca[ií]do', r'cay[oó]',
        r'looking\s+for\s+new\s+method', r'new\s+method\s+coming',
        r'temporarily\s+unavailable', r'under\s+maintenance',
        r'service\s+interrup\w*', r'work\s+in\s+progress',
        r'coming\s+soon',
        r'is\s+down', r'site\s+is\s+down', r'server\s+is\s+down',
        r'currently\s+down', r'went\s+down', r'gone\s+down',
        r'offline\s+(for|due|while|temporarily)',
        r'service\s+suspended', r'site\s+suspended',
        r'service\s+disabled', r'site\s+disabled',
        r'service\s+error', r'server\s+error',
        r'out\s+of\s+service',
        r'updating\s+(the\s+)?(service|server|site|method)',
    ]

# ============ UTILIDADES ============

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def jitter_sleep(min_s: float = 0.5, max_s: float = 2.0):
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)

# ============ PERSISTENCIA ============

def load_state() -> dict:
    try:
        if os.path.exists(Config.STATE_FILE):
            with open(Config.STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Error cargando estado: {e}")
    return {
        "last_status": "UNKNOWN",
        "last_check_timestamp": "",
        "last_notify_timestamp": None,
        "total_checks": 0,
        "total_changes": 0,
        "consecutive_errors": 0,
        "last_html_hash": None,
        "heartbeat_counter": 0,
        "last_strategy_used": None,
        "last_detection_method": None,
    }

def save_state(state: dict):
    try:
        with open(Config.STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Estado guardado: {state['last_status']} | estrategia: {state.get('last_strategy_used', 'N/A')} | deteccion: {state.get('last_detection_method', 'N/A')}")
    except Exception as e:
        print(f"[ERROR] Error guardando estado: {e}")
        raise

def append_log(entry: dict):
    try:
        log = []
        if os.path.exists(Config.LOG_FILE):
            with open(Config.LOG_FILE, 'r', encoding='utf-8') as f:
                log = json.load(f)
        log.append(entry)
        if len(log) > Config.MAX_LOG_ENTRIES:
            log = log[-Config.MAX_LOG_ENTRIES:]
        with open(Config.LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Error guardando log: {e}")

# ============ DETECCION STRUCTURADA ============

def detect_via_status_page(html: str) -> Optional[Tuple[str, str]]:
    """
    Deteccion primaria: parsear la pagina /status/ en busca de data-status.
    Retorna (status, detail) o None si no se puede determinar.
    """
    if not html or not html.strip():
        return None

    # Buscar data-status attribute en el status card
    match = re.search(r'data-status=["\'](\w+)["\']', html, re.IGNORECASE)
    if match:
        status_val = match.group(1).lower()
        # Extraer el texto del status title
        title_match = re.search(r'id=["\']statusTitle["\'][^>]*>([^<]+)', html, re.IGNORECASE)
        title_text = title_match.group(1).strip() if title_match else status_val

        if status_val in ("live", "operational", "ok", "up"):
            return "UP", f"Status page: {title_text}"
        elif status_val in ("down", "error", "outage", "maintenance", "degraded"):
            return "DOWN", f"Status page: {title_text}"
        else:
            # Unknown status value, report it
            return "DOWN", f"Status page valor desconocido: data-status={status_val} | {title_text}"

    # Buscar data-state en los sistemas individuales
    states = re.findall(r'data-state=["\'](\w+)["\']', html, re.IGNORECASE)
    if states:
        non_operational = [s for s in states if s.lower() not in ("operational", "ok", "live", "up")]
        if non_operational:
            return "DOWN", f"Sistemas no operativos: {', '.join(set(non_operational))}"
        elif len(states) > 0:
            return "UP", f"Todos los sistemas operational ({len(states)} checks)"

    return None  # No se pudo determinar via status page

def detect_via_outage_popup(html: str) -> Optional[Tuple[str, str]]:
    """
    Deteccion secundaria: revisar el popup de outage en la pagina principal.
    Si el div tiene clase 'hidden' -> UP. Si no tiene 'hidden' -> DOWN.
    """
    if not html or not html.strip():
        return None

    # Buscar el div del popup de outage
    match = re.search(
        r'class=["\']([^"\']*site-outage-popup[^"\']*)["\']',
        html, re.IGNORECASE
    )
    if match:
        classes = match.group(1).lower()
        if "hidden" in classes:
            return "UP", "Popup de outage oculto (hidden)"
        else:
            # El popup es visible -> sitio DOWN
            # Extraer el titulo del popup
            title_match = re.search(
                r'id=["\']siteOutagePopupTitle["\'][^>]*>([^<]+)',
                html, re.IGNORECASE
            )
            title = title_match.group(1).strip() if title_match else "Popup visible"
            return "DOWN", f"Popup de outage visible: {title}"

    return None  # No se encontro el popup

def detect_via_keywords(html: str) -> Tuple[str, str, Optional[str]]:
    """
    Deteccion fallback: keyword matching con regex preciso.
    Solo se usa si los metodos estructurados no funcionan.
    """
    if html is None:
        return "ERROR", "No se pudo obtener la pagina", None
    if not html.strip():
        return "ERROR", "Contenido vacio", None

    html_hash = hash_text(html)
    pattern = '|'.join(Config.MAINTENANCE_KEYWORDS)
    regex = re.compile(pattern, re.IGNORECASE)
    match = regex.search(html)
    if match:
        start = max(0, match.start() - 30)
        end = min(len(html), match.end() + 30)
        context = html[start:end].replace('\n', ' ').strip()
        return "DOWN", f"Keyword: '{match.group(0)}' | Contexto: ...{context}...", html_hash
    return "UP", "Servicio operativo (keyword fallback)", html_hash

def detect(html: Optional[str], status_html: Optional[str] = None) -> Tuple[str, str, Optional[str], Optional[str]]:
    """
    Deteccion en cascada:
    1. Status page (data-status attribute)
    2. Outage popup (hidden class)
    3. Keyword fallback
    
    Retorna (status, detail, html_hash, detection_method)
    """
    if html is None and status_html is None:
        return "ERROR", "No se pudo obtener ninguna pagina", None, None

    # 1. Intentar status page primero
    if status_html:
        result = detect_via_status_page(status_html)
        if result:
            status, detail = result
            html_hash = hash_text(status_html)
            print(f"[DETECT] Metodo: status_page | {status} | {detail}")
            return status, detail, html_hash, "status_page"

    # 2. Intentar outage popup en pagina principal
    if html:
        result = detect_via_outage_popup(html)
        if result:
            status, detail = result
            html_hash = hash_text(html)
            print(f"[DETECT] Metodo: outage_popup | {status} | {detail}")
            return status, detail, html_hash, "outage_popup"

    # 3. Fallback: keyword matching
    status, detail, html_hash = detect_via_keywords(html or status_html)
    print(f"[DETECT] Metodo: keyword_fallback | {status} | {detail}")
    return status, detail, html_hash, "keyword_fallback"

# ============ ESTRATEGIAS DE FETCH ============

def fetch_strategy_cloudscraper(url: str) -> Optional[str]:
    """cloudscraper - engine especifico para bypass de Cloudflare"""
    try:
        import cloudscraper
    except ImportError:
        return None
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True},
            delay=10,
        )
        resp = scraper.get(url, timeout=Config.TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  cloudscraper fallo: {e}")
        return None

def fetch_strategy_curl_cffi(url: str) -> Optional[str]:
    """curl_cffi - impersona TLS fingerprint de Chrome"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None
    for chrome_ver in Config.CURL_CFFI_VERSIONS:
        try:
            resp = curl_requests.get(url, impersonate=chrome_ver, timeout=Config.TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if "impersonate" in str(e).lower() or "version" in str(e).lower():
                continue
            return None
    return None

def fetch_strategy_requests(url: str) -> Optional[str]:
    """requests con headers realistas completos"""
    headers = {
        "User-Agent": Config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Not)A;Brand";v="99", "Google Chrome";v="128", "Chromium";v="128"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    resp = requests.get(url, headers=headers, timeout=Config.TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    return resp.text

def fetch_strategy_jina(url: str) -> Optional[str]:
    """r.jina.ai - servicio proxy gratuito"""
    proxy_url = f"https://r.jina.ai/{url}"
    headers = {"User-Agent": Config.USER_AGENT, "Accept": "text/plain,*/*", "X-Return-Format": "text"}
    resp = requests.get(proxy_url, headers=headers, timeout=Config.TIMEOUT + 10, allow_redirects=True)
    resp.raise_for_status()
    return resp.text

def fetch_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Intenta las 4 estrategias en cascada para una URL. Retorna (html, strategy_name)."""
    strategies = [
        ("cloudscraper", fetch_strategy_cloudscraper),
        ("curl_cffi", fetch_strategy_curl_cffi),
        ("requests_headers", fetch_strategy_requests),
        ("jina_ai", fetch_strategy_jina),
    ]
    for name, strategy in strategies:
        try:
            print(f"  Probando: {name}...")
            result = strategy(url)
            if result is not None:
                print(f"  ✅ {name} OK ({len(result)} chars)")
                return result, name
        except Exception as e:
            print(f"  ❌ {name} fallo: {e}")
            jitter_sleep(1.0, 3.0)
    return None, None

# ============ NTFY ============

def post_ntfy(message: str, title: str, priority: str = "default", tags: str = "") -> bool:
    if not Config.NTFY_TOPIC:
        print("[NTFY] No hay NTFY_TOPIC configurado. Saltando.")
        return False
    url = f"{Config.NTFY_BASE_URL}/{Config.NTFY_TOPIC}"
    headers = {"Title": title, "Priority": priority, "Tags": tags, "Content-Type": "text/plain"}
    print(f"[NTFY] POST -> {title}")
    resp = requests.post(url, headers=headers, data=message, timeout=10)
    resp.raise_for_status()
    print(f"[NTFY] OK")
    return True

def notify_status_change(status: str, detail: str, strategy: str = "", method: str = ""):
    extra = ""
    if strategy:
        extra += f"\nEstrategia: {strategy}"
    if method:
        extra += f"\nDeteccion: {method}"
    if status == "DOWN":
        post_ntfy(
            f"🔴 Upload120 - MANTENIMIENTO DETECTADO\n\n{detail}{extra}\n\nHora: {now_iso()}",
            "Upload120 Monitor - ALERTA", "high", "rotating_light,warning")
    elif status == "UP":
        post_ntfy(
            f"🟢 Upload120 - OPERATIVO\n\n{detail}{extra}\n\nHora: {now_iso()}",
            "Upload120 Monitor - RECUPERADO", "default", "white_check_mark")
    else:
        post_ntfy(
            f"⚠️ Upload120 - ERROR\n\n{detail}{extra}\n\nHora: {now_iso()}",
            "Upload120 Monitor - ERROR", "high", "warning")

def notify_heartbeat(state: dict):
    post_ntfy(
        f"💓 Upload120 Monitor - SIGO VIVO\n\n"
        f"Total checks: {state['total_checks']}\n"
        f"Total cambios: {state['total_changes']}\n"
        f"Ultimo estado: {state['last_status']}\n"
        f"Ultima estrategia: {state.get('last_strategy_used', 'N/A')}\n"
        f"Ultima deteccion: {state.get('last_detection_method', 'N/A')}\n"
        f"Hora: {now_iso()}",
        "Upload120 Monitor - Heartbeat", "low", "heartbeat")

# ============ MAIN ============

def main():
    print("=" * 60)
    print(f"[START] Upload120 Monitor Pro v3.2 - {now_iso()}")
    print(f"[CONFIG] FORCE_NOTIFY={Config.FORCE_NOTIFY} | NTFY_TOPIC={'set' if Config.NTFY_TOPIC else 'NOT SET'}")
    print("=" * 60)

    state = load_state()
    main_html = None
    status_html = None
    error_msg = None
    notified = False
    strategy_used = None
    detection_method = None

    # 1. Obtener paginas (cascada de estrategias)
    print("\n[FETCH] Obteniendo pagina principal...")
    try:
        main_html, strategy_used = fetch_url(Config.URL)
        if main_html:
            state["consecutive_errors"] = 0
            print(f"[FETCH] Pagina principal OK via {strategy_used}")
    except Exception as e:
        error_msg = str(e)
        state["consecutive_errors"] += 1
        print(f"[ERROR] Pagina principal fallo: {e}")

    # Intentar pagina de status tambien
    if strategy_used:
        print(f"\n[FETCH] Obteniendo pagina de status (probando misma estrategia: {strategy_used})...")
        try:
            # Usar la misma estrategia que funciono
            strategies_map = {
                "cloudscraper": fetch_strategy_cloudscraper,
                "curl_cffi": fetch_strategy_curl_cffi,
                "requests_headers": fetch_strategy_requests,
                "jina_ai": fetch_strategy_jina,
            }
            status_html = strategies_map[strategy_used](Config.STATUS_URL)
            if status_html:
                print(f"[FETCH] Status page OK ({len(status_html)} chars)")
            else:
                print(f"[FETCH] Status page no disponible, usando solo pagina principal")
        except Exception as e:
            print(f"[FETCH] Status page fallo: {e}, usando solo pagina principal")

    # 2. Detectar estado (cascada: status_page > outage_popup > keywords)
    print(f"\n[DETECT] Analizando...")
    status, detail, html_hash, detection_method = detect(main_html, status_html)

    # 3. Comparar
    status_changed = status != state["last_status"]
    print(f"\n[COMPARE] Cambio: {status_changed} | Anterior: {state['last_status']} | Actual: {status}")

    # 4. Decidir notificacion
    should_notify = False
    if status_changed:
        print("[DECISION] CAMBIO detectado. Notificando...")
        should_notify = True
        state["total_changes"] += 1
    elif Config.FORCE_NOTIFY:
        print("[DECISION] FORCE_NOTIFY activo. Notificando...")
        should_notify = True
    else:
        print("[DECISION] Sin cambios. Silencio.")

    # 5. Notificar
    if should_notify:
        try:
            notify_status_change(status, detail, strategy_used or "", detection_method or "")
            notified = True
            state["last_notify_timestamp"] = now_iso()
        except Exception as e:
            print(f"[ERROR] Fallo al notificar: {e}")

    # Error persistente
    if error_msg and state["consecutive_errors"] >= 3:
        print(f"[ALERT] {state['consecutive_errors']} errores consecutivos")
        try:
            notify_status_change("ERROR", f"Multiples fallos: {error_msg}")
            notified = True
        except:
            pass

    # 6. Heartbeat
    state["heartbeat_counter"] += 1
    if state["heartbeat_counter"] >= Config.HEARTBEAT_INTERVAL:
        print("[HEARTBEAT] Enviando...")
        try:
            notify_heartbeat(state)
            state["heartbeat_counter"] = 0
        except Exception as e:
            print(f"[ERROR] Heartbeat fallo: {e}")

    # 7. Actualizar estado
    state["last_status"] = status
    state["last_check_timestamp"] = now_iso()
    state["last_html_hash"] = html_hash
    state["last_strategy_used"] = strategy_used
    state["last_detection_method"] = detection_method
    state["total_checks"] += 1

    # 8. Guardar
    save_state(state)
    append_log({
        "timestamp": now_iso(),
        "status": status,
        "detail": detail,
        "html_hash": html_hash,
        "notified": notified,
        "error": error_msg,
        "strategy": strategy_used,
        "detection_method": detection_method,
        "force_notify": Config.FORCE_NOTIFY,
    })

    print(f"\n[DONE] Checks: {state['total_checks']} | Estrategia: {strategy_used} | Deteccion: {detection_method}")
    print("=" * 60)

if __name__ == "__main__":
    main()
