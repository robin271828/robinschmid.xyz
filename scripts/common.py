"""Shared helpers for the digest scrapers (standard library only)."""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "site" / "data"
STATE = ROOT / "state"
USER_AGENT = "robinschmid.xyz-digest/1.0 (+https://robinschmid.xyz)"


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


def load_config() -> dict:
    with open(ROOT / "digest.toml", "rb") as f:
        return tomllib.load(f)


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             retries: int = 4, timeout: int = 60) -> bytes:
    """GET with retries on 429/5xx and network errors. Raises on final failure."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    delay = 5.0
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code >= 500 or (
                e.code == 403 and e.headers.get("x-ratelimit-remaining") == "0")
            if not retryable or attempt == retries:
                raise
            wait = e.headers.get("retry-after")
            reset = e.headers.get("x-ratelimit-reset")
            if wait and wait.isdigit():
                sleep_for = int(wait)
            elif reset and reset.isdigit():
                sleep_for = max(1, int(reset) - int(time.time()) + 1)
            else:
                sleep_for = delay
            sleep_for = min(sleep_for, 90)
            log(f"  HTTP {e.code}, retrying in {sleep_for:.0f}s")
            time.sleep(sleep_for)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise
            log(f"  network error ({e}), retrying in {delay:.0f}s")
            time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text + "\n")
