#!/usr/bin/env python3
"""Fetch new arXiv papers that match digest.toml and update site/data/papers/.

  python scripts/fetch_papers.py                  # normal daily run
  python scripts/fetch_papers.py --lookback 10    # backfill the last 10 days
  python scripts/fetch_papers.py --skip-venues    # daily papers only

Output:
  site/data/papers/index.json     {"months": [...], "updated": "YYYY-MM-DD", "llm": bool}
  site/data/papers/YYYY-MM.json   {"entries": [...]}   one file per month of submission date
  site/data/papers/venues.json    {"updated": ..., "venues": [{"name", "papers": [...]}]}

Each paper gets "fig" (an image URL from its arXiv HTML version) once one is found, or
"fig_tries" while none is.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import urljoin

import summarize
from common import SITE_DATA, http_get, load_config, log, read_json, today, write_json

DATA = SITE_DATA / "papers"
API = "https://export.arxiv.org/api/query"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "x": "http://arxiv.org/schemas/atom",
    "o": "http://a9.com/-/spec/opensearch/1.1/",
}
PAGE = 200
ARXIV_PAUSE = 3.1  # arXiv asks for at most one request every 3 seconds
GITHUB_RX = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", re.I)
URL_RX = re.compile(r"https?://[^\s,;)\]}>\"']+", re.I)
SUBMITTED_RX = re.compile(r"\b(submitted|under review|in submission|under submission|in review)\b", re.I)
WORKSHOP_RX = re.compile(r"\bworkshops?\b", re.I)
HTML_BASE = "https://arxiv.org/html/"
HTML_PAUSE = 15.0  # arxiv.org/robots.txt: Crawl-delay 15
FIG_MAX_TRIES = 3  # runs to wait for an HTML version before giving up on a paper


# ------------------------------------------------------------------ arXiv API

def term(field: str, text: str) -> str:
    text = text.replace('"', "")
    return f'{field}:"{text}"' if re.search(r"[\s\-:]", text) else f"{field}:{text}"


def keyword_clause(cfg: dict) -> str:
    kws = [k for t in cfg["papers"]["topics"] for k in t["keywords"]]
    return "(" + " OR ".join(f"{term('ti', k)} OR {term('abs', k)}" for k in kws) + ")"


def date_clause(start: dt.date, end: dt.date) -> str:
    return f"submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]"


def arxiv_search(query: str, limit: int) -> list[dict]:
    out: list[dict] = []
    start = 0
    while start < limit:
        params = {"search_query": query, "start": start, "max_results": min(PAGE, limit - start),
                  "sortBy": "submittedDate", "sortOrder": "descending"}
        for _ in range(3):  # the API sometimes returns an empty page despite a non-zero total
            root = ET.fromstring(http_get(API, params))
            time.sleep(ARXIV_PAUSE)
            entries = root.findall("a:entry", NS)
            total = int(root.findtext("o:totalResults", "0", NS))
            if entries or total <= start:
                break
        out.extend(parse_entry(e) for e in entries)
        start += len(entries)
        if not entries or start >= total:
            break
    return out


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_entry(e: ET.Element) -> dict:
    abs_url = e.findtext("a:id", "", NS)
    aid = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
    comment = norm(e.findtext("x:comment", "", NS))
    abstract = norm(e.findtext("a:summary", "", NS))
    primary = e.find("x:primary_category", NS)
    text_links = comment + " " + abstract
    code = None
    if m := GITHUB_RX.search(text_links):
        owner, name = m.group(1), m.group(2).rstrip(".")
        name = re.sub(r"\.git$", "", name)
        code = f"https://github.com/{owner}/{name}"
    project = None
    for u in URL_RX.findall(text_links):
        u = u.rstrip(".")
        if "github.com" not in u and "arxiv.org" not in u:
            project = u
            break
    return {
        "id": aid,
        "date": e.findtext("a:published", "", NS)[:10],
        "title": norm(e.findtext("a:title", "", NS)).replace("$", ""),
        "authors": [norm(a.findtext("a:name", "", NS)) for a in e.findall("a:author", NS)],
        "abstract": abstract,
        "comment": comment,
        "journal_ref": norm(e.findtext("x:journal_ref", "", NS)),
        "categories": [c.get("term") for c in e.findall("a:category", NS)],
        "primary": primary.get("term") if primary is not None else None,
        "abs_url": f"https://arxiv.org/abs/{aid}",
        "pdf_url": f"https://arxiv.org/pdf/{aid}",
        "code": code,
        "project": project,
    }


# ------------------------------------------------------------------ figures

class _FigureParser(HTMLParser):
    """Picks the image of the first figure (not table) in an arXiv HTML paper.

    In a figure made of several panels it takes the largest one. Images under 120x60 px
    (icons, logos) don't count.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.in_figure = False
        self.images: list[tuple[int, str]] = []
        self.src: str | None = None

    def handle_starttag(self, tag, attrs):
        if self.src:
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "figure":
            if self.depth == 0:
                self.in_figure = "ltx_figure" in classes and "ltx_table" not in classes
                self.images = []
            self.depth += 1
        elif tag == "img" and self.in_figure and "ltx_graphics" in classes and a.get("src"):
            try:
                w, h = int(a.get("width") or 0), int(a.get("height") or 0)
            except ValueError:
                w = h = 0
            if w and h and (w < 120 or h < 60):
                return
            self.images.append((w * h if w and h else 10**6, a["src"]))

    def handle_endtag(self, tag):
        if tag != "figure" or self.depth == 0 or self.src:
            return
        self.depth -= 1
        if self.depth == 0 and self.in_figure and self.images:
            self.src = max(self.images)[1]


def find_figure(aid: str) -> tuple[str | None, bool]:
    """Return (image URL, settled). settled=False means no HTML version yet: try again later."""
    url = HTML_BASE + aid
    try:
        page = http_get(url, retries=1).decode("utf-8", "replace")
    except Exception:  # 404 = no HTML version (yet); anything else is worth another try too
        return None, False
    parser = _FigureParser()
    parser.feed(page)
    return (urljoin(url, parser.src), True) if parser.src else (None, True)


class Figures:
    """Gives papers a figure, within a per-run budget and arXiv's crawl delay."""

    def __init__(self, cfg: dict) -> None:
        pc = cfg["papers"]
        self.on = pc.get("figures", True)
        self.left = pc.get("figure_lookups_per_run", 50)
        self.known: dict[str, dict] = {}
        self.last = 0.0
        self.found = self.looked = 0

    def remember(self, entries: list[dict]) -> None:
        for e in entries:
            if "fig" in e or "fig_tries" in e:
                self.known[e["id"]] = {k: e[k] for k in ("fig", "fig_tries") if k in e}

    def fill(self, entries: list[dict]) -> None:
        """Look up figures for the entries that still lack one, in the given order."""
        for e in entries:
            if e["id"] in self.known and "fig" not in e and "fig_tries" not in e:
                e.update(self.known[e["id"]])
            if not self.on or e.get("fig") or e.get("fig_tries", 0) >= FIG_MAX_TRIES:
                continue
            if self.left <= 0:
                continue
            self.left -= 1
            time.sleep(max(0.0, self.last + HTML_PAUSE - time.monotonic()))
            src, settled = find_figure(e["id"])
            self.last = time.monotonic()
            self.looked += 1
            if src:
                e["fig"] = src
                e.pop("fig_tries", None)
                self.found += 1
            else:
                e["fig_tries"] = FIG_MAX_TRIES if settled else e.get("fig_tries", 0) + 1
            self.remember([e])

    def report(self) -> None:
        if self.looked:
            log(f"  figures: looked up {self.looked}, found {self.found}")


# ------------------------------------------------------------------ scoring

def compile_topics(cfg: dict) -> list[tuple[str, float, re.Pattern]]:
    out = []
    for t in cfg["papers"]["topics"]:
        alts = []
        for k in t["keywords"]:
            k = re.escape(k.strip()).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]?")
            alts.append(k)
        rx = re.compile(r"\b(?:" + "|".join(alts) + r")\w{0,4}\b", re.I)
        out.append((t["name"], float(t.get("weight", 1.0)), rx))
    return out


def keyword_score(p: dict, topics, boost: list[str] = ()) -> tuple[float, list[str]]:
    score, tags = 0.0, []
    for name, weight, rx in topics:
        if rx.search(p["title"]):
            score += 2 * weight
            tags.append(name)
        elif rx.search(p["abstract"]):
            score += weight
            tags.append(name)
    if tags and p.get("primary") in boost:
        score += 1.5
    return score, tags


def is_excluded(p: dict, cfg: dict) -> bool:
    text = (p["title"] + " " + p["abstract"]).lower()
    return any(x.lower() in text for x in cfg["papers"].get("exclude", []))


def venue_matchers(cfg: dict):
    out = []
    for v in cfg.get("venues", {}).get("list", []):
        rxs = []
        for m in v["match"]:
            flags = re.I if " " in m else 0  # single words (CoRL, ICRA) must match case
            rxs.append(re.compile(r"(?<![A-Za-z])" + re.escape(m) + r"(?![A-Za-z])\s*'?(\d{4}|\d{2})?", flags))
        out.append((v["name"], rxs))
    return out


def detect_venue(p: dict, matchers) -> dict | None:
    text = f"{p.get('comment', '')} {p.get('journal_ref', '')}"
    if not text.strip():
        return None
    for name, rxs in matchers:
        for rx in rxs:
            m = rx.search(text)
            if not m:
                continue
            year = m.group(1)
            if year and len(year) == 2:
                year = "20" + year
            if year and not (2000 <= int(year) <= today().year + 2):
                year = None
            workshop = bool(WORKSHOP_RX.search(text))
            status = "submitted" if SUBMITTED_RX.search(text) else "accepted"
            label = name + (f" {year}" if year else "") + (" Workshop" if workshop else "")
            return {"name": name, "year": year, "workshop": workshop, "status": status, "label": label}
    return None


def fallback_summary(abstract: str, limit: int = 320) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    out = ""
    for s in sentences:
        if len(out) + len(s) > limit and out:
            break
        out = f"{out} {s}".strip()
    return out if len(out) <= limit + 80 else out[:limit].rsplit(" ", 1)[0] + "…"


def finalize(p: dict, kw_score: float, kw_tags: list[str], rated: dict | None, venue) -> dict:
    entry = {k: p[k] for k in ("id", "date", "title", "authors", "abstract", "categories",
                               "primary", "abs_url", "pdf_url", "code", "project", "comment")}
    entry["tags"] = kw_tags
    entry["venue"] = venue
    entry["kw_score"] = round(kw_score, 2)
    if rated:
        entry["relevance"] = rated["relevance"]
        entry["summary"] = rated["summary"]
        entry["llm_tags"] = rated["tags"]
    else:
        entry["relevance"] = None
        entry["summary"] = fallback_summary(p["abstract"])
    return entry


def rank_key(p: dict, kw: float, rated: dict | None, author_hit: bool) -> float:
    base = rated["relevance"] + kw / 100 if rated else kw
    return base + (100 if author_hit else 0)


# ------------------------------------------------------------------ daily papers

def month_of(date: str) -> str:
    return date[:7]


def load_months() -> dict[str, list[dict]]:
    index = read_json(DATA / "index.json", {"months": []})
    return {m: read_json(DATA / f"{m}.json", {"entries": []})["entries"] for m in index["months"]}


def update_daily(cfg: dict, lookback: int, figs: Figures) -> int:
    pc = cfg["papers"]
    topics = compile_topics(cfg)
    matchers = venue_matchers(cfg)
    end = today()
    start = end - dt.timedelta(days=lookback)

    cats = "(" + " OR ".join(f"cat:{c}" for c in pc["categories"]) + ")"
    query = f"{cats} AND {keyword_clause(cfg)} AND {date_clause(start, end)}"
    log(f"arXiv: {start} → {end}, {len(pc['categories'])} categories")
    found = {p["id"]: p for p in arxiv_search(query, limit=pc.get("max_candidates", 1000))}
    log(f"  {len(found)} keyword matches")

    authors = pc.get("authors", [])
    author_ids: set[str] = set()
    if authors:
        aq = "(" + " OR ".join(term("au", a) for a in authors) + f") AND {date_clause(start, end)}"
        for p in arxiv_search(aq, limit=200):
            found.setdefault(p["id"], p)
            author_ids.add(p["id"])
        log(f"  {len(author_ids)} papers by watched authors")

    months = load_months()
    known = {e["id"] for entries in months.values() for e in entries}
    per_day = defaultdict(int)
    for entries in months.values():
        for e in entries:
            per_day[e["date"]] += 1

    cands = []
    for p in found.values():
        if p["id"] in known or is_excluded(p, cfg):
            continue
        kw, tags = keyword_score(p, topics, cfg["papers"].get("boost_categories", []))
        if kw >= pc.get("min_score", 2) or p["id"] in author_ids:
            cands.append((p, kw, tags))
    log(f"  {len(cands)} new candidates above min_score")

    # Send the strongest keyword candidates per day to Claude (if enabled).
    max_day = pc.get("max_per_day", 12)
    by_day = defaultdict(list)
    for c in cands:
        by_day[c[0]["date"]].append(c)
    pool = []
    for day, cs in by_day.items():
        cs.sort(key=lambda c: (c[0]["id"] in author_ids, c[1]), reverse=True)
        pool.extend(cs[: max_day * 3])
    rated = summarize.rate([c[0] for c in pool], cfg)
    min_rel = cfg.get("llm", {}).get("min_relevance", 5)

    added = 0
    for day, cs in by_day.items():
        slots = max_day - per_day[day]
        if slots <= 0:
            continue
        ranked = []
        for p, kw, tags in cs:
            r = rated.get(p["id"])
            author_hit = p["id"] in author_ids
            if r and r["relevance"] < min_rel and not author_hit:
                continue
            if rated and not r and not author_hit:
                continue  # Claude was on but skipped this one; only keep rated papers
            ranked.append((rank_key(p, kw, r, author_hit), p, kw, tags, r))
        ranked.sort(key=lambda x: x[0], reverse=True)
        for rank, (_, p, kw, tags, r) in enumerate(ranked[:slots]):
            entry = finalize(p, kw, tags, r, detect_venue(p, matchers))
            if p["id"] in author_ids:
                entry["watched_author"] = True
            months.setdefault(month_of(day), []).append(entry)
            added += 1

    newest_first = sorted((e for es in months.values() for e in es), key=lambda e: e["date"], reverse=True)
    figs.fill(newest_first)
    figs.report()

    for m, entries in months.items():
        entries.sort(key=lambda e: (e["date"], e.get("relevance") or 0, e["kw_score"]), reverse=True)
        write_json(DATA / f"{m}.json", {"entries": entries}, compact=True)
    write_json(DATA / "index.json", {"months": sorted(months, reverse=True),
                                     "updated": str(end), "llm": summarize.enabled(cfg)})
    log(f"  added {added} papers")
    return added


# ------------------------------------------------------------------ venues

def update_venues(cfg: dict, figs: Figures) -> None:
    vc = cfg.get("venues", {})
    if not vc.get("list"):
        return
    topics = compile_topics(cfg)
    matchers = venue_matchers(cfg)
    end = today()
    start = end - dt.timedelta(days=vc.get("lookback_days", 150))
    max_n = vc.get("max_per_venue", 30)
    min_score = cfg["papers"].get("min_score", 2)

    previous = read_json(DATA / "venues.json", {"venues": []})
    prev_by_name = {v["name"]: v for v in previous.get("venues", [])}
    for v in previous.get("venues", []):
        figs.remember(v["papers"])
    out = []
    for v in vc["list"]:
        fields = " OR ".join(f"{term('co', m)} OR {term('jr', m)}" for m in v["match"])
        query = f"({fields}) AND {keyword_clause(cfg)} AND {date_clause(start, end)}"
        try:
            found = arxiv_search(query, limit=max_n * 4)
        except Exception as e:  # keep the last good list for this venue
            log(f"  {v['name']}: failed ({e}), keeping previous list")
            if v["name"] in prev_by_name:
                out.append(prev_by_name[v["name"]])
            continue
        picks = []
        for p in found:
            venue = detect_venue(p, [(n, r) for n, r in matchers if n == v["name"]])
            if not venue:
                continue
            if venue["workshop"] and not vc.get("include_workshops", False):
                continue
            if venue["status"] == "submitted" and not vc.get("include_submitted", False):
                continue
            if is_excluded(p, cfg):
                continue
            kw, tags = keyword_score(p, topics, cfg["papers"].get("boost_categories", []))
            if kw >= min_score:
                picks.append((p, kw, tags, venue))
        picks.sort(key=lambda x: x[1], reverse=True)
        picks = picks[: max_n * 2]
        rated = summarize.rate([x[0] for x in picks], cfg)
        min_rel = cfg.get("llm", {}).get("min_relevance", 5)
        papers = []
        for p, kw, tags, venue in picks:
            r = rated.get(p["id"])
            if rated and (not r or r["relevance"] < min_rel):
                continue
            papers.append((rank_key(p, kw, r, False), finalize(p, kw, tags, r, venue)))
        papers.sort(key=lambda x: (x[1]["venue"]["year"] or "", x[0]), reverse=True)
        papers = [e for _, e in papers[:max_n]]
        log(f"  {v['name']}: {len(found)} hits, {len(papers)} kept")
        out.append({"name": v["name"], "papers": papers})

    figs.fill(sorted((p for v in out for p in v["papers"]), key=lambda p: p["date"], reverse=True))
    figs.report()
    write_json(DATA / "venues.json", {"updated": str(end), "since": str(start), "venues": out}, compact=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, help="days of submissions to scan (default from config)")
    ap.add_argument("--skip-venues", action="store_true")
    ap.add_argument("--skip-daily", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    figs = Figures(cfg)
    figs.remember(e for es in load_months().values() for e in es)
    ok = True
    if not args.skip_daily:
        try:
            update_daily(cfg, args.lookback or cfg["papers"].get("lookback_days", 3), figs)
        except Exception as e:
            log(f"daily papers failed: {e!r}")
            ok = False
    if not args.skip_venues:
        log("venues:")
        try:
            update_venues(cfg, figs)
        except Exception as e:
            log(f"venues failed: {e!r}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
