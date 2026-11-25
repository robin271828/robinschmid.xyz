#!/usr/bin/env python3
"""Find new and rising GitHub repos that match digest.toml and update site/data/repos/.

  GITHUB_TOKEN=... python scripts/fetch_repos.py

Output: site/data/repos/repos.json
  {"updated": "YYYY-MM-DD", "items": [{full_name, url, description, stars, language, topics,
    created, pushed, first_seen, history: [[date, stars], ...], stars_week, week_estimated,
    sources: [{"type": "query"|"watch"|"paper", "label", "paper_id"?, "paper_title"?}]}]}
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error

from common import SITE_DATA, http_get, load_config, log, read_json, today, write_json

OUT = SITE_DATA / "repos" / "repos.json"
PAPERS = SITE_DATA / "papers"
API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
SEARCH_PAUSE = 2.2 if TOKEN else 6.5  # search API: 30 req/min with a token, 10 without
HISTORY_POINTS = 40


def gh(path: str, params: dict | None = None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return json.loads(http_get(API + path, params, headers))


def search(q: str, per_page: int = 50) -> list[dict]:
    log(f"  search: {q}")
    res = gh("/search/repositories", {"q": q, "sort": "stars", "order": "desc", "per_page": per_page})
    time.sleep(SEARCH_PAUSE)
    return res.get("items", [])


def snapshot(r: dict) -> dict:
    return {
        "full_name": r["full_name"],
        "url": r["html_url"],
        "description": (r.get("description") or "").strip(),
        "stars": r["stargazers_count"],
        "forks": r.get("forks_count", 0),
        "language": r.get("language"),
        "topics": r.get("topics", [])[:8],
        "homepage": r.get("homepage") or None,
        "license": (r.get("license") or {}).get("spdx_id"),
        "avatar": (r.get("owner") or {}).get("avatar_url"),
        "created": r["created_at"][:10],
        "pushed": r["pushed_at"][:10],
        "archived": r.get("archived", False),
        "fork": r.get("fork", False),
    }


def week_gain(item: dict, now: dt.date) -> tuple[int, bool]:
    """Stars gained over ~7 days from our own history, else an estimate from repo age."""
    hist = item.get("history", [])
    target = now - dt.timedelta(days=7)
    older = [(d, s) for d, s in hist if dt.date.fromisoformat(d) <= target]
    if older:
        return item["stars"] - older[-1][1], False
    if len(hist) >= 2 and (now - dt.date.fromisoformat(hist[0][0])).days >= 2:
        span = (now - dt.date.fromisoformat(hist[0][0])).days
        return round((item["stars"] - hist[0][1]) * 7 / span), True
    age = max(1, (now - dt.date.fromisoformat(item["created"])).days)
    return round(item["stars"] * min(7, age) / age), True


def paper_code_links(limit_months: int = 2) -> list[tuple[str, str, str]]:
    index = read_json(PAPERS / "index.json", {"months": []})
    links = []
    for m in index["months"][:limit_months]:
        for e in read_json(PAPERS / f"{m}.json", {"entries": []})["entries"]:
            if e.get("code"):
                links.append((e["code"], e["id"], e["title"]))
    venues = read_json(PAPERS / "venues.json", {"venues": []})
    for v in venues.get("venues", []):
        for e in v["papers"]:
            if e.get("code"):
                links.append((e["code"], e["id"], e["title"]))
    return links


def main() -> int:
    cfg = load_config()["repos"]
    now = today()
    since = now - dt.timedelta(days=cfg.get("created_within_days", 30))
    store = read_json(OUT, {"items": []})
    items = {i["full_name"].lower(): i for i in store["items"]}
    seen_now: dict[str, dict] = {}

    def add(r: dict, source: dict) -> None:
        snap = snapshot(r)
        if snap["archived"] or snap["fork"]:
            return
        key = snap["full_name"].lower()
        item = items.get(key) or {"first_seen": str(now), "history": [], "sources": []}
        item.update(snap)
        if source not in item["sources"]:
            item["sources"].append(source)
        items[key] = item
        seen_now[key] = item

    failures = searches = 0
    for q in cfg.get("queries", []):
        min_stars = q.get("min_stars", cfg.get("min_stars", 25))
        for qs in [q["q"]] if isinstance(q["q"], str) else q["q"]:
            searches += 1
            try:
                for r in search(f"{qs} created:>={since} stars:>={min_stars}"):
                    add(r, {"type": "query", "label": q["label"]})
            except urllib.error.HTTPError as e:
                failures += 1
                log(f"  query '{qs}' failed: HTTP {e.code}")

    for owner in cfg.get("watch_owners", []):
        searches += 1
        try:
            for r in search(f"user:{owner} created:>={since}", per_page=20):
                add(r, {"type": "watch", "label": owner})
        except urllib.error.HTTPError as e:
            failures += 1
            log(f"  owner '{owner}' failed: HTTP {e.code}")

    if cfg.get("include_paper_code", True):
        budget = 40 if TOKEN else 10  # core API: 60 req/h without a token
        for url, pid, title in paper_code_links():
            full = url.removeprefix("https://github.com/")
            key = full.lower()
            if key in seen_now or budget <= 0:
                continue
            src = {"type": "paper", "label": "paper", "paper_id": pid, "paper_title": title}
            if key in items and src in items[key]["sources"] and not TOKEN:
                continue
            budget -= 1
            try:
                add(gh(f"/repos/{full}"), src)
            except urllib.error.HTTPError as e:
                log(f"  {full}: HTTP {e.code}")

    # Refresh star counts for tracked repos the searches did not return this time.
    keep_after = now - dt.timedelta(days=cfg.get("keep_days", 60))
    stale = [k for k, i in items.items()
             if k not in seen_now and dt.date.fromisoformat(i["first_seen"]) >= keep_after]
    if TOKEN:
        for k in stale[:200]:
            try:
                r = gh(f"/repos/{items[k]['full_name']}")
                items[k].update(snapshot(r))
                seen_now[k] = items[k]
            except urllib.error.HTTPError as e:
                if e.code in (404, 451):
                    items.pop(k, None)
                log(f"  refresh {k}: HTTP {e.code}")

    out = []
    for k, item in items.items():
        if dt.date.fromisoformat(item["first_seen"]) < keep_after:
            continue
        hist = [h for h in item.get("history", []) if h[0] != str(now)]
        if k in seen_now:
            hist.append([str(now), item["stars"]])
        item["history"] = hist[-HISTORY_POINTS:]
        item["stars_week"], item["week_estimated"] = week_gain(item, now)
        item.pop("archived", None)
        item.pop("fork", None)
        out.append(item)

    out.sort(key=lambda i: i["stars_week"], reverse=True)
    out = out[: cfg.get("max_items", 150)]
    write_json(OUT, {"updated": str(now), "since": str(since), "items": out}, compact=True)
    log(f"repos: {len(out)} tracked, {len(seen_now)} refreshed this run")
    return 1 if searches and failures == searches else 0


if __name__ == "__main__":
    sys.exit(main())
