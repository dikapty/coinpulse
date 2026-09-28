"""Module 3: automated quality checks before publication.

Checks (deterministic):
  - word count within limits
  - at least N source links + ## Sources section
  - n-gram uniqueness vs all previously published posts (protects against scaled-content penalties)
  - forbidden-topic scan (advice, guaranteed returns, banned categories)
  - clickbait/hype word scan
Check (LLM): editorial score 1-10 (facts, usefulness, readability, neutrality).

Decision per config publishing.mode:
  auto   -> score >= min_score: publish
  hybrid -> score >= min_score: publish; else move to drafts_pending/ for human review
  manual -> everything to drafts_pending/

Run: python3 scripts/quality_gate.py
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from collections import Counter

from common import (DRAFTS, POSTS, PUBLISHED, REJECTED, load_config, log,
                    load_state, save_state)
from llm import complete, extract_json

PENDING = DRAFTS.parent / "drafts_pending"
PENDING.mkdir(parents=True, exist_ok=True)

HYPE_WORDS = [
    "insane", "moon", "explosive", "guaranteed", "skyrocket", "unmissable",
    "you won't believe", "shocking", "get rich", "100x", "1000x", "to the moon",
    "life-changing", "secret trick",
]

SCORING_PROMPT = """You are a strict financial-news editor. Score this article 1-10 on each axis:
- facts: every claim traceable, no invented numbers/quotes
- usefulness: does it explain WHY the news matters, with context?
- readability: clear structure, no fluff, no clickbait
- neutrality: no financial advice, no hype

ARTICLE:
{article}

Reply with JSON only: {{"facts": N, "usefulness": N, "readability": N, "neutrality": N,
"verdict": "one sentence", "overall": N}} where overall is the average (1 decimal)."""


def parse_frontmatter(text: str):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        return json.loads(m.group(1)), m.group(2)
    except json.JSONDecodeError:
        return {}, text


def ngrams(text: str, n: int = 4):
    words = re.findall(r"[a-z]+", text.lower())
    return Counter(tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1)))


def uniqueness(body: str, threshold: float):
    """Similarity = overlap of 4-gram multisets against each published post."""
    cand = ngrams(body)
    if not cand:
        return 1.0, ""
    worst, worst_name = 0.0, ""
    for p in POSTS.glob("*.md"):
        _, prev_body = parse_frontmatter(p.read_text(encoding="utf-8"))
        prev = ngrams(prev_body)
        if not prev:
            continue
        inter = sum(min(c, prev[g]) for g, c in cand.items())
        sim = inter / sum(cand.values())
        if sim > worst:
            worst, worst_name = sim, p.name
    return 1.0 - worst, worst_name


def check_article(path, cfg: dict) -> dict:
    text = path.read_text(encoding="utf-8")
    front, body = parse_frontmatter(text)
    report = {"file": path.name, "title": front.get("title", "?"), "checks": {}, "score": None}

    words = len(body.split())
    q = cfg["quality"]
    report["checks"]["length"] = {
        "ok": q["min_words"] <= words <= q["max_words"] * 2,
        "detail": f"{words} words (min {q['min_words']})",
    }

    links = re.findall(r"https?://", body)
    has_sources_section = bool(re.search(r"^##\s*sources", body, re.MULTILINE | re.IGNORECASE))
    report["checks"]["sources"] = {
        "ok": len(links) >= q["min_sources"] and has_sources_section,
        "detail": f"{len(links)} links, sources section: {has_sources_section}",
    }

    uniq, similar_to = uniqueness(body, q["uniqueness_threshold"])
    report["checks"]["uniqueness"] = {
        "ok": uniq >= q["uniqueness_threshold"],
        "detail": f"{uniq:.0%} unique" + (f" (most similar: {similar_to})" if similar_to else ""),
    }

    hay = body.lower()
    banned = cfg.get("forbidden_topics", [])
    hits = [t for t in banned if t.lower() in hay]
    advice = bool(re.search(r"\b(you should (buy|sell|invest)|guaranteed (profit|return)|buy this now)\b", hay))
    if advice and "personal financial advice" not in hits:
        hits.append("personal financial advice (regex)")
    report["checks"]["forbidden"] = {"ok": not hits, "detail": ", ".join(hits) or "clean"}

    hype = [w for w in HYPE_WORDS if w in hay]
    report["checks"]["clickbait"] = {"ok": not hype, "detail": ", ".join(hype) or "clean"}
    report["word_count"] = words
    return report, front, body


def llm_score(body: str, title: str) -> dict:
    try:
        result = extract_json(complete(SCORING_PROMPT.format(article=body[:12000])))
        result["overall"] = float(result.get("overall", 0))
        return result
    except Exception as e:
        log(f"  ! LLM scoring failed ({e}) — treated as score 0 (goes to pending review)")
        return {"overall": 0.0, "verdict": f"scoring error: {e}"}


def decide(report: dict, score: dict, cfg: dict, path) -> str:
    """Returns 'publish' | 'pending' | 'reject'."""
    hard = report["checks"]
    if not hard["forbidden"]["ok"]:
        return "reject"  # banned topic — never publish
    hard_ok = all(c["ok"] for c in hard.values())
    overall = score.get("overall", 0)
    min_score = cfg["publishing"]["min_score"]
    mode = cfg["publishing"]["mode"]

    if mode == "manual":
        return "pending"
    if hard_ok and overall >= min_score:
        return "publish"  # auto & hybrid both auto-publish articles that pass the bar
    return "pending"


def run() -> list:
    cfg = load_config()
    drafts = sorted(DRAFTS.glob("*.md"))
    if not drafts:
        log("No drafts to review.")
        return []
    results = []
    for path in drafts:
        report, front, body = check_article(path, cfg)
        log(f"Reviewing: {report['title']}")
        score = llm_score(body, report["title"])
        report["score"] = score
        decision = decide(report, score, cfg, path)
        report["decision"] = decision
        log(f"  score={score.get('overall')} verdict={score.get('verdict','')[:90]} -> {decision.upper()}")

        report_path = REJECTED if decision == "reject" else PENDING
        if decision == "reject":
            shutil.move(str(path), REJECTED / path.name)
            (REJECTED / (path.stem + ".report.json")).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif decision == "pending":
            shutil.move(str(path), PENDING / path.name)
            (PENDING / (path.stem + ".report.json")).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            (path.with_suffix(".report.json")).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(report)

    history = load_state("gate_history", [])
    history.extend([{k: r[k] for k in ("file", "title", "decision")} | {"score": r["score"].get("overall")} for r in results])
    save_state("gate_history", history[-500:])
    published_n = sum(1 for r in results if r["decision"] == "publish")
    log(f"Quality gate done: {published_n} approved, {len(results) - published_n} pending/rejected")
    return results


if __name__ == "__main__":
    run()
