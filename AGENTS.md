# Upload120 Monitor — Base44 Dev Environment

## What this project is
A Python CLI monitoring script (`monitor.py`) that checks whether upload120.com is UP or DOWN. It uses 4 fetch strategies (cloudscraper, curl_cffi, requests, jina.ai) with content validation to bypass Cloudflare, detects status via a cascading method (status page > outage popup > keyword fallback), and persists results to `state.json` + `log.json`. Optionally sends push notifications via ntfy.sh.

Originally designed to run as a GitHub Action (`.github/workflows/monitor.yml`) on a schedule.

## How it runs in Base44
- `docker-compose.base44.yml` defines two services:
  - **monitor** — `python:3.11-slim`, installs `requirements.txt`, runs `monitor.py` in a loop (every 10 min).
  - **web** — `python:3.11-slim`, serves `dashboard.py` (stdlib only, no deps) on port 3000. Reads `state.json`/`log.json` to render a live dashboard.
- Both services bind-mount the repo root so they share `state.json` and `log.json`.

## Secrets
- `NTFY_TOPIC` (optional) — ntfy.sh topic for push notifications. Empty = notifications skipped. Not required to boot.

## Verification
- `curl -s http://localhost:3000/` returns the dashboard HTML (200).
- `curl -s http://localhost:3000/api/state` returns current monitor state as JSON.
- Monitor logs: `docker compose -f docker-compose.base44.yml logs monitor --tail 30`.

## Notes
- `dashboard.py` was added by Base44 to provide a web view for the preview — it does NOT modify `monitor.py` or the monitoring logic.
- The monitor needs outbound internet access to fetch upload120.com.
