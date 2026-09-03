#!/usr/bin/env python3
"""
MONITOR UPLOAD120 PRO v3.1 - ADVANCED BYPASS EDITION
=====================================================
Sistema de monitoreo con 4 estrategias de bypass anti-bot en cascada:
1. cloudscraper (Cloudflare-specific bypass engine) - MAS EFECTIVA
2. curl_cffi (TLS fingerprint impersonation - multiple Chrome versions)
3. Headers realistas (requests + Sec-Fetch + sec-ch-ua)
4. r.jina.ai (servicio proxy gratuito de respaldo)

Mejoras v3.1:
- cloudscraper ahora es Estrategia 1 (es la que funciona)
- Regex corregido: 'down' ahora requiere contexto (no matchea 'download')
- Keywords mas precisas para evitar falsos positivos
- Jitter aleatorio entre estrategias

Autor: Lyra | Fecha: 2026-09-02
"""

import requests
import re
import os
import json
import time
import hashlib
import random
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

# ============ CONFIGURACION ============

class Config:
    URL = "https://upload120.com/"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    TIMEOUT = 20
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 2

    # Regex de deteccion de mantenimiento/caida
    # NOTA: 'down' y 'error' son demasiado genericos por si solos.
    # Se usan patterns mas especificos para evitar falsos positivos.
    MAINTENANCE_KEYWORDS = [
        # Espanol - mantenimiento
        r'parcheado', r'parchado', r'metodo\s+actualiz\w*',
        r'parch\w+', r'mantenimiento',
        r'no\s+disponible', r'buscando\s+metodo', r'metodo\s+parch\w*',
        r'actualizando',
        r'problema\s+(con|de|en)', r'fuera\s+de\s+servicio',
        r'en\s+mantenimiento', r'servicio\s+interrumpido',
        r'no\s+funciona',
        # Espanol - caida
        r'ca[ií]do', r'cay[oó]',
        # Ingles - mantenimiento
        r'looking\s+for\s+new\s+method', r'new\s+method\s+coming',
        r'temporarily\s+unavailable', r'under\s+maintenance',
        r'service\s+interrup\w*', r'work\s+in\s+progress',
        r'coming\s+soon',
        # Ingles - caida (contextos especificos, no sueltos)
        r'is\s+down', r'site\s+is\s+down', r'server\s+is\s+down',
        r'currently\s+down', r'went\s+down', r'gone\s+down',
        r'offline\s+(for|due|while|temporarily)',
        r'suspended', r'disabled',
        # Estados de error del servicio (no la palabra 'error' suelta)
        r'service\s+error', r'server\s+error',
        r'out\s+of\s+service',
        r'unavailable',
        # Actualizaciones / parches
        r'updating\s+(the\s+)?(service|server|site|method)',
        r'parch\w*\s+(aplicado|nuevo|pronto|coming)',
    ]

    STATE_FILE = "state.json"
    LOG_FILE = "log.json"
    MAX_LOG_ENTRIES = 500
    HEARTBEAT_INTERVAL = 24
    NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
    NTFY_BASE_URL = "https://ntfy.sh"
    FORCE_NOTIFY = os.environ.get("FORCE_NOTIFY", "false").lower() == "true"

    # Versiones de Chrome para curl_cffi (de nueva a vieja)
    CURL_CFFI_VERSIONS = ["chrome131", "chrome124", "chrome120", "chrome116", "chrome110"]

# ============ UTILIDADES ============

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def jitter_sleep(min_s: float = 0.5, max_s: float = 2.0):
    """Pausa aleatoria para simular comportamiento humano"""
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)

def retry_with_backoff(max_retries: int, base_delay: float):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt == max_retries - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    print(f"  [RETRY] Intento {attempt + 1}/{max_retries} fallo: {e}. Reintentando en {delay}s...")
                    time.sleep(delay)
            raise last_error
        return wrapper
    return decorator

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
    }

def save_state(state: dict):
    try:
        with open(Config.STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Estado guardado: {state['last_status']} (estrategia: {state.get('last_strategy_used', 'N/A')})")
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

# ============ DETECCION ============

def compile_regex() -> re.Pattern:
    pattern = '|'.join(Config.MAINTENANCE_KEYWORDS)
    return re.compile(pattern, re.IGNORECASE)

def detect(html: Optional[str]) -> Tuple[str, str, Optional[str]]:
    if html is None:
        return "ERROR", "No se pudo obtener la pagina", None
    if not html.strip():
        return "ERROR", "Contenido vacio", None
    html_hash = hash_text(html)
    regex = compile_regex()
    match = regex.search(html)
    if match:
        # Mostrar contexto alrededor del match para debug
        start = max(0, match.start() - 30)
        end = min(len(html), match.end() + 30)
        context = html[start:end].replace('\n', ' ').strip()
        return "DOWN", f"Keyword detectado: '{match.group(0)}' | Contexto: ...{context}...", html_hash
    return "UP", "Servicio operativo", html_hash

# ============ ESTRATEGIA 1: cloudscraper (Cloudflare bypass) ============

def fetch_strategy_1() -> Optional[str]:
    """Estrategia 1: cloudscraper - engine especifico para bypass de Cloudflare"""
    try:
        import cloudscraper
    except ImportError:
        print("[ESTRATEGIA 1] cloudscraper no instalado. Saltando...")
        return None

    try:
        print(f"[ESTRATEGIA 1] GET {Config.URL} (cloudscraper)")
        scraper = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True,
            },
            delay=10,
        )
        resp = scraper.get(Config.URL, timeout=Config.TIMEOUT)
        resp.raise_for_status()
        print(f"[ESTRATEGIA 1] OK - {resp.status_code}, {len(resp.text)} chars")
        return resp.text
    except Exception as e:
        print(f"[ESTRATEGIA 1] Fallo: {e}")
        return None

# ============ ESTRATEGIA 2: curl_cffi (TLS fingerprint impersonation) ============

def fetch_strategy_2() -> Optional[str]:
    """Estrategia 2: curl_cffi - impersona TLS fingerprint de Chrome real"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        print("[ESTRATEGIA 2] curl_cffi no instalado. Saltando...")
        return None

    # Probar multiples versiones de Chrome hasta que una funcione
    for chrome_ver in Config.CURL_CFFI_VERSIONS:
        try:
            print(f"[ESTRATEGIA 2] GET {Config.URL} (curl_cffi + impersonate={chrome_ver})")
            resp = curl_requests.get(
                Config.URL,
                impersonate=chrome_ver,
                timeout=Config.TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            print(f"[ESTRATEGIA 2] OK - {resp.status_code}, {len(resp.text)} chars (chrome={chrome_ver})")
            return resp.text
        except Exception as e:
            print(f"[ESTRATEGIA 2] Fallo con {chrome_ver}: {e}")
            # Si es un error de version no soportada, probar la siguiente
            if "impersonate" in str(e).lower() or "version" in str(e).lower():
                continue
            # Si es otro error (403, timeout, etc.), no tiene sentido probar otra version
            return None

    print("[ESTRATEGIA 2] Ninguna version de Chrome funciono")
    return None

# ============ ESTRATEGIA 3: requests con headers realistas ============

def fetch_strategy_3() -> Optional[str]:
    """Estrategia 3: Headers realistas completos de Chrome 128"""
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
    print(f"[ESTRATEGIA 3] GET {Config.URL} (requests + headers realistas)")
    resp = requests.get(Config.URL, headers=headers, timeout=Config.TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    print(f"[ESTRATEGIA 3] OK - {resp.status_code}, {len(resp.text)} chars")
    return resp.text

# ============ ESTRATEGIA 4: r.jina.ai (servicio proxy gratuito) ============

def fetch_strategy_4() -> Optional[str]:
    """Estrategia 4: r.jina.ai - servicio gratuito que hace scraping por ti"""
    # r.jina.ai espera la URL completa con https:// preservado
    target_url = Config.URL
    proxy_url = f"https://r.jina.ai/{target_url}"
    print(f"[ESTRATEGIA 4] GET {proxy_url} (r.jina.ai proxy)")
    headers = {
        "User-Agent": Config.USER_AGENT,
        "Accept": "text/plain,*/*",
        "X-Return-Format": "text",
    }
    resp = requests.get(proxy_url, headers=headers, timeout=Config.TIMEOUT + 10, allow_redirects=True)
    resp.raise_for_status()
    print(f"[ESTRATEGIA 4] OK - {resp.status_code}, {len(resp.text)} chars")
    # r.jina.ai devuelve texto plano extraido, no HTML raw
    return resp.text

# ============ FETCH MASTER (cascada de 4 estrategias) ============

def fetch() -> Tuple[Optional[str], Optional[str]]:
    """
    Intenta las 4 estrategias en cascada hasta que una funcione.
    Retorna (html, strategy_name)
    """
    strategies = [
        ("cloudscraper", fetch_strategy_1),
        ("curl_cffi", fetch_strategy_2),
        ("requests_headers", fetch_strategy_3),
        ("jina_ai", fetch_strategy_4),
    ]

    last_error = None
    for name, strategy in strategies:
        try:
            result = strategy()
            if result is not None:
                print(f"[FETCH] Estrategia exitosa: {name}")
                return result, name
        except Exception as e:
            print(f"[FETCH] Estrategia {name} fallo: {e}")
            last_error = e
            # Jitter entre estrategias para parecer mas natural
            jitter_sleep(1.0, 3.0)
            continue

    if last_error:
        raise last_error
    return None, None

# ============ NTFY ============

def post_ntfy(message: str, title: str, priority: str = "default", tags: str = "") -> bool:
    if not Config.NTFY_TOPIC:
        print("[NTFY] No hay NTFY_TOPIC configurado. Notificacion saltada.")
        return False
    url = f"{Config.NTFY_BASE_URL}/{Config.NTFY_TOPIC}"
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
        "Content-Type": "text/plain",
    }
    print(f"[NTFY] POST {url}")
    resp = requests.post(url, headers=headers, data=message, timeout=10)
    resp.raise_for_status()
    print(f"[NTFY] OK")
    return True

def notify_status_change(status: str, detail: str, strategy: str = ""):
    strat_info = f"\nEstrategia: {strategy}" if strategy else ""
    if status == "DOWN":
        return post_ntfy(
            f"🔴 Upload120 - MANTENIMIENTO DETECTADO\n\n{detail}{strat_info}\n\nHora: {now_iso()}",
            "Upload120 Monitor - ALERTA",
            "high",
            "rotating_light,warning",
        )
    elif status == "UP":
        return post_ntfy(
            f"🟢 Upload120 - OPERATIVO\n\n{detail}{strat_info}\n\nHora: {now_iso()}",
            "Upload120 Monitor - RECUPERADO",
            "default",
            "white_check_mark",
        )
    else:
        return post_ntfy(
            f"⚠️ Upload120 - ERROR\n\n{detail}{strat_info}\n\nHora: {now_iso()}",
            "Upload120 Monitor - ERROR",
            "high",
            "warning",
        )

def notify_heartbeat(state: dict):
    return post_ntfy(
        f"💓 Upload120 Monitor - SIGO VIVO\n\n"
        f"Total checks: {state['total_checks']}\n"
        f"Total cambios: {state['total_changes']}\n"
        f"Ultimo estado: {state['last_status']}\n"
        f"Ultima estrategia: {state.get('last_strategy_used', 'N/A')}\n"
        f"Hora: {now_iso()}",
        "Upload120 Monitor - Heartbeat",
        "low",
        "heartbeat",
    )

# ============ MAIN ============

def main():
    print("=" * 60)
    print(f"[START] Upload120 Monitor Pro v3.1 - {now_iso()}")
    print(f"[CONFIG] FORCE_NOTIFY={Config.FORCE_NOTIFY} | NTFY_TOPIC={'set' if Config.NTFY_TOPIC else 'NOT SET'}")
    print("=" * 60)

    state = load_state()
    html = None
    error_msg = None
    notified = False
    strategy_used = None

    # 1. Obtener pagina (cascada de 4 estrategias)
    try:
        html, strategy_used = fetch()
        state["consecutive_errors"] = 0
    except Exception as e:
        error_msg = str(e)
        state["consecutive_errors"] += 1
        print(f"[ERROR] Todas las estrategias fallaron: {e}")
        if state["consecutive_errors"] >= 3:
            print(f"[ALERT] {state['consecutive_errors']} errores consecutivos")
            try:
                notify_status_change("ERROR", f"Multiples fallos: {error_msg}")
                notified = True
            except Exception as ne:
                print(f"[ERROR] Fallo al notificar error: {ne}")

    # 2. Detectar estado
    status, detail, html_hash = detect(html)
    print(f"[DETECT] Estado: {status} - {detail}")

    # 3. Comparar
    status_changed = status != state["last_status"]
    print(f"[COMPARE] Cambio: {status_changed} | Anterior: {state['last_status']} | Actual: {status}")

    # 4. Decidir notificacion
    should_notify = False
    if status_changed:
        print(f"[DECISION] CAMBIO detectado. Notificando...")
        should_notify = True
        state["total_changes"] += 1
    elif Config.FORCE_NOTIFY:
        print(f"[DECISION] FORCE_NOTIFY activo. Notificando aunque sin cambios...")
        should_notify = True
    else:
        print(f"[DECISION] Sin cambios. Silencio.")

    # 5. Notificar
    if should_notify:
        try:
            notify_status_change(status, detail, strategy_used or "")
            notified = True
            state["last_notify_timestamp"] = now_iso()
        except Exception as e:
            print(f"[ERROR] Fallo al notificar: {e}")

    # 6. Heartbeat
    state["heartbeat_counter"] += 1
    if state["heartbeat_counter"] >= Config.HEARTBEAT_INTERVAL:
        print(f"[HEARTBEAT] Enviando...")
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
        "force_notify": Config.FORCE_NOTIFY,
    })

    print(f"[DONE] Checks totales: {state['total_checks']} | Estrategia: {strategy_used}")
    print("=" * 60)

if __name__ == "__main__":
    main()
