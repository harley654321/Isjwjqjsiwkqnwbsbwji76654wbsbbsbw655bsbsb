@retry_with_backoff(max_retries=Config.MAX_RETRIES, base_delay=Config.RETRY_DELAY_BASE)
def fetch():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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
    }
    print(f"[HTTP] GET {Config.URL}")
    resp = requests.get(Config.URL, headers=headers, timeout=Config.TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    print(f"[HTTP] OK - {resp.status_code}, {len(resp.text)} chars")
    return resp.text
