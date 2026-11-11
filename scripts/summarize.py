"""Optional: rate paper relevance and write plain-language summaries with Claude.

Runs only when ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) is set and [llm].enabled is true.
Results are cached in state/llm_cache.json so a paper is never sent twice.
"""
from __future__ import annotations

import json
import os

from common import STATE, log, read_json, today, write_json

CACHE = STATE / "llm_cache.json"
BATCH = 20
CACHE_DAYS = 200  # venue papers can stay listed for ~150 days

SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevance": {"type": "integer"},
                    "summary": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "relevance", "summary", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["papers"],
    "additionalProperties": False,
}

SYSTEM = """You curate a research-paper digest for one reader.

The reader's interests:
{interests}

Topics they track: {topics}.

You get a JSON list of new papers. Return one object per paper, same ids:
- relevance: integer 0-10, how much this reader would want to read it. 9-10 squarely in their
  interests and a substantial contribution; 6-8 clearly relevant; 3-5 adjacent; 0-2 unrelated.
- summary: two plain-language sentences for a technical reader outside the subfield. First the
  problem and what the paper does, then the key result or why it matters. Do not start with
  "This paper" or restate the title; no hype words.
- tags: 1-4 short lowercase hyphenated keywords (e.g. "humanoid", "sim-to-real")."""


def enabled(cfg: dict) -> bool:
    llm = cfg.get("llm", {})
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return bool(llm.get("enabled", True)) and has_key


def _load_cache() -> dict:
    cache = read_json(CACHE, {})
    cutoff = today().toordinal() - CACHE_DAYS
    return {k: v for k, v in cache.items() if v.get("_day", cutoff) >= cutoff}


def rate(papers: list[dict], cfg: dict) -> dict[str, dict]:
    """Return {arxiv_id: {relevance, summary, tags}} for the papers Claude rated.

    Papers that fail (API error, refusal) are simply missing from the result, and the
    caller falls back to keyword scores for them.
    """
    if not papers or not enabled(cfg):
        return {}
    import anthropic  # only needed when summaries are on

    llm = cfg["llm"]
    cache = _load_cache()
    todo = [p for p in papers if p["id"] not in cache]
    log(f"  claude: {len(papers) - len(todo)} cached, {len(todo)} to rate")

    client = anthropic.Anthropic()
    topics = ", ".join(t["name"] for t in cfg.get("papers", {}).get("topics", []))
    system = SYSTEM.format(interests=llm.get("interests", "").strip(), topics=topics)

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        payload = [{"id": p["id"], "title": p["title"], "abstract": p["abstract"],
                    "comment": p.get("comment", "")} for p in batch]
        try:
            resp = client.beta.messages.create(
                model=llm.get("model", "claude-opus-5-5"),
                max_tokens=16000,
                # On a safety decline, the API retries on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={
                    "effort": llm.get("effort", "medium"),
                    "format": {"type": "json_schema", "schema": SCHEMA},
                },
                system=system,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
        except anthropic.AuthenticationError:
            log("  claude: invalid API key, skipping summaries")
            break
        except anthropic.RateLimitError:
            log("  claude: rate limited, skipping the rest of this run")
            break
        except anthropic.APIStatusError as e:
            log(f"  claude: API error {e.status_code}: {e.message}")
            continue
        except anthropic.APIConnectionError as e:
            log(f"  claude: connection error: {e}")
            continue

        if resp.stop_reason != "end_turn":
            log(f"  claude: stopped with {resp.stop_reason}, skipping batch")
            continue
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            rated = json.loads(text)["papers"]
        except (json.JSONDecodeError, KeyError) as e:
            log(f"  claude: could not parse response ({e})")
            continue
        wanted = {p["id"] for p in batch}
        for r in rated:
            if r["id"] in wanted:
                cache[r["id"]] = {
                    "relevance": max(0, min(10, int(r["relevance"]))),
                    "summary": r["summary"].strip(),
                    "tags": [t.strip().lower() for t in r["tags"]][:4],
                    "_day": today().toordinal(),
                }
        u = resp.usage
        log(f"  claude: batch {i // BATCH + 1}: {len(rated)} rated "
            f"({u.input_tokens} in / {u.output_tokens} out tokens)")

    write_json(CACHE, cache, compact=True)
    return {p["id"]: cache[p["id"]] for p in papers if p["id"] in cache}
