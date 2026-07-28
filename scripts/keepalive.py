#!/usr/bin/env python3
"""Ping the Crime AI API every 5 minutes so Render free-tier stays awake.

Usage:
  python scripts/keepalive.py
  python scripts/keepalive.py --url https://crime-ai-api.onrender.com/health
  python scripts/keepalive.py --once

Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = "https://crime-ai-api.onrender.com/health"
INTERVAL_SEC = 5 * 60


def ping(url: str, timeout: float = 60.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "crime-ai-keepalive/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")[:200]
            return True, f"HTTP {res.status} {body}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — keep-alive should never crash the loop
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep Render API awake")
    parser.add_argument("--url", default=DEFAULT_URL, help="Health endpoint URL")
    parser.add_argument("--interval", type=int, default=INTERVAL_SEC, help="Seconds between pings")
    parser.add_argument("--once", action="store_true", help="Ping once and exit")
    args = parser.parse_args()

    while True:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        ok, detail = ping(args.url)
        print(f"[{ts}] {'OK' if ok else 'FAIL'} {args.url} — {detail}", flush=True)
        if args.once:
            return 0 if ok else 1
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(0)
