"""Module 2: turn collected news items into article drafts.

Pipeline per article: pick items (cluster related ones) -> plan -> draft -> self-edit.
Hard rules baked into the prompt: facts only from sources, mandatory attribution links,
no invented numbers/quotes, added value via context & multi-source synthesis.

Run: python3 scripts/writer.py [N]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import DRAFTS, INBOX, PUBLISHED, load_config, log, load_state, save_state
from llm import BudgetExhaustedError, complete, extract_json

SYSTEM_RULES = """You are a professional financial news editor for an English-language site.
ABSOLUTE RULES (violation = article rejected):
1. Use ONLY facts present in the provided source material. NEVER invent numbers, dates,
   quotes, names, events or price levels. If a fact is not in the sources, it does not exist.
2. Every article cites its sources inline as markdown links and lists them at the end.
3. This is news journalism, NOT financial advice. No "you should buy/sell", no promises
   of returns. Where relevant, keep the neutral line: "This is not financial advice."
4. Neutral, factual tone. No clickbait, no hype words (insane, moon, explosive, guaranteed).
5. Added value: explain WHY the news matters, give context, connect related developments.
6. Structure: ## H2 subheadings every 150-250 words. Plain markdown, no HTML.
7. Length: 600-1200 words.
"""

PLAN_PROMPT = """{rules}

SOURCE MATERIAL (JSON):
{sources}

TASK: Plan one news article from the material above. Pick the single strongest story;
if two items are clearly about the same event, merge them. Reply with JSON only:
{{"angle": "one-sentence editorial angle", "title_ideas": ["3 title options, no clickbait"],
  "outline": ["4-6 H2 sections with one-line purpose each"],
  "used_item_ids": ["ids of items this article is based on"]}}"""

DRAFT_PROMPT = """{rules}

SOURCE MATERIAL (JSON):
{sources}

TASK: Write one complete news article in markdown from the material above.
Pick the single strongest story; if two items are clearly about the same event, merge them.

HARD LENGTH REQUIREMENT: the article body MUST contain 700-1200 words. Count before
answering; a draft under 700 words is REJECTED by the automated quality gate. Reach the
length with substance only: background context, what led to this event, why it matters,
related developments from the material, market/industry implications — never repetition
or filler.

Start directly with the title as "# Title" (no frontmatter). Use ## H2 subheadings every
150-250 words. End with a "## Sources" section listing every source used as a markdown link.
Reply with JSON only: {{"markdown": "...", "meta_description": "max 155 chars, factual",
"tags": ["3-5 tags"], "title": "final title", "used_item_ids": ["ids used"]}}"""

EDIT_PROMPT = """{rules}

DRAFT ARTICLE:
{draft}

TASK: Self-edit this draft. Fix factual drift (remove anything not supported by the source
material), tighten sentences, verify every claim traces to a source, ensure the ## Sources
section is complete. IMPORTANT: keep the body at 700-1200 words — expand thin sections with
context from the source material if needed; never pad with repetition. Keep used_item_ids.
Reply with JSON only: {{"markdown": "...", "meta_description": "...", "tags": ["..."],
"title": "...", "used_item_ids": ["..."]}}"""


def load_inbox_items(limit: int = 40):
    items = []
    for p in sorted(INBOX.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        if len(items) >= limit:
            break
    return items


def compact(item: dict) -> dict:
    return {
        "id": item["id"],
        "title": item["title"],
        "summary": item["summary"][:700],
        "source_name": item["source_name"],
        "source_url": item["source_url"],
        "published": item["published"],
    }


def strip_code_fence(md: str) -> str:
    md = re.sub(r"^```(?:markdown)?\s*", "", md.strip())
    md = re.sub(r"\s*```$", "", md)
    return md.strip()


def write_one(items: list, cfg: dict) -> dict | None:
    rules = SYSTEM_RULES
    sources_json = json.dumps([compact(i) for i in items], ensure_ascii=False, indent=1)

    # 2 LLM calls per article (draft + self-edit). Planning was folded into the draft
    # prompt to conserve the free-tier quota (20 requests/day).
    log("  Drafting article...")
    draft = extract_json(complete(DRAFT_PROMPT.format(rules=rules, sources=sources_json)))
    log("  Self-editing...")
    final = extract_json(complete(EDIT_PROMPT.format(rules=rules, draft=draft.get("markdown", ""))))

    markdown = strip_code_fence(final.get("markdown", ""))
    title = final.get("title") or draft.get("title") or items[0]["title"]
    if not markdown or len(markdown.split()) < 150:
        log("  ! Draft too short/empty — skipping")
        return None
    used_ids = final.get("used_item_ids") or draft.get("used_item_ids") or [i["id"] for i in items]
    used_ids = [u for u in used_ids if u in {i["id"] for i in items}] or [items[0]["id"]]

    now = datetime.now(timezone.utc)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70]
    slug = f"{now.strftime('%Y-%m-%d')}-{slug}"

    front = {
        "title": title,
        "description": (final.get("meta_description") or "")[:cfg["seo"]["meta_description_chars"]],
        "tags": final.get("tags") or [],
        "date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [i["source_url"] for i in items if i["id"] in used_ids],
        "source_names": [i["source_name"] for i in items if i["id"] in used_ids],
        "status": "draft",  # quality-gate / publisher flip this to "published"
        "used_item_ids": used_ids,
        "draft_of_batch": items[0].get("batch", ""),
    }
    body = "---\n" + json.dumps(front, ensure_ascii=False, indent=2) + "\n---\n\n" + markdown + "\n"
    draft_path = DRAFTS / f"{slug}.md"
    draft_path.write_text(body, encoding="utf-8")
    log(f"  Draft saved: {draft_path.name}")
    return {"slug": slug, "title": title, "path": str(draft_path), "used_item_ids": used_ids}


def mark_items_used(ids: list) -> None:
    """Remove inbox items consumed by an article (and record them)."""
    history = load_state("used_items", {"ids": []})
    for iid in ids:
        p = INBOX / f"{iid}.json"
        if p.exists():
            p.unlink()
    history["ids"] = (history.get("ids", []) + ids)[-2000:]
    save_state("used_items", history)


def run(n_articles: int) -> None:
    cfg = load_config()
    items = load_inbox_items()
    if not items:
        log("No fresh items in inbox — run collector.py first.")
        return
    written = load_state("daily_stats", {}).get(today_key(), {}).get("written", 0)
    log(f"Writing up to {n_articles} article(s) from {len(items)} inbox items (today already: {written})...")
    produced = []
    remaining = list(items)
    for k in range(n_articles):
        if len(remaining) < 2:
            break
        chunk, remaining = remaining[:4], remaining[4:]  # 4 items per article -> merge related
        try:
            art = write_one(chunk, cfg)
        except BudgetExhaustedError as e:
            log(f"  Stopping: {e}")
            break
        except Exception as e:
            log(f"  ! writer failed: {e}")
            continue
        if art:
            produced.append(art)
            mark_items_used(art["used_item_ids"])
    stats = load_state("daily_stats", {})
    day = stats.setdefault(today_key(), {})
    day["written"] = day.get("written", 0) + len(produced)
    save_state("daily_stats", stats)
    log(f"Writer done: {len(produced)} draft(s) in data/drafts/")


def today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
