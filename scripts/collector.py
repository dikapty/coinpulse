"""Module 1: collect fresh news from RSS, filter by niche, dedupe, save to data/inbox/.

Run: python3 scripts/collector.py
Cron: 4x/day via GitHub Actions (see .github/workflows/publish.yml).
"""
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from common import INBOX, load_config, log, load_state, save_state

UA = {"User-Agent": "Mozilla/5.0 (compatible; CoinPulseBot/1.0; news aggregator)"}
MAX_AGE_HOURS = 26  # ignore items older than this


def clean_text(raw: str, limit: int = 1200) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def simhash(text: str, bits: int = 64) -> int:
    """Cheap near-duplicate fingerprint (word shingles)."""
    words = re.findall(r"\w+", text.lower())
    shingles = [" ".join(words[i:i + 3]) for i in range(max(0, len(words) - 2))]
    vec = [0] * bits
    for sh in shingles:
        h = int(hashlib.md5(sh.encode()).hexdigest(), 16)
        for b in range(bits):
            vec[b] += 1 if (h >> b) & 1 else -1
    out = 0
    for b in range(bits):
        if vec[b] > 0:
            out |= 1 << b
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def matches_niche(title: str, summary: str, keywords) -> bool:
    hay = (title + " " + summary).lower()
    return any(k in hay for k in keywords)


def touches_forbidden(title: str, summary: str, forbidden) -> bool:
    hay = (title + " " + summary).lower()
    return any(f.lower() in hay for f in forbidden)


def parse_date(entry) -> datetime:
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_feed(source: dict, timeout: int = 25):
    try:
        r = requests.get(source["url"], headers=UA, timeout=timeout)
        r.raise_for_status()
        return feedparser.parse(r.content)
    except Exception as e:
        log(f"  ! {source['name']}: fetch failed ({e})")
        return None


def collect() -> dict:
    cfg = load_config()
    keywords = [k.lower() for k in cfg.get("keywords", [])]
    forbidden = cfg.get("forbidden_topics", [])
    seen = load_state("seen", {"urls": [], "hashes": []})
    seen_urls = set(seen.get("urls", []))
    seen_hashes = [int(h) for h in seen.get("hashes", [])]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    saved, skipped_dup, skipped_filter = 0, 0, 0
    new_urls, new_hashes = [], []

    log(f"Collecting from {len(cfg['sources'])} RSS sources...")
    for source in cfg["sources"]:
        parsed = fetch_feed(source)
        if not parsed:
            continue
        count = 0
        for entry in parsed.entries[:15]:
            title = clean_text(entry.get("title", ""), 300)
            link = (entry.get("link") or "").strip()
            if not title or not link or link in seen_urls:
                skipped_dup += 1
                continue
            published = parse_date(entry)
            if published < cutoff:
                continue
            summary = clean_text(entry.get("summary", ""), 1200)
            if not matches_niche(title, summary, keywords):
                skipped_filter += 1
                continue
            if touches_forbidden(title, summary, forbidden):
                skipped_filter += 1
                continue
            fp = simhash(title + " " + summary[:400])
            if any(hamming(fp, h) <= 6 for h in seen_hashes):
                skipped_dup += 1
                continue
            item = {
                "id": hashlib.sha1(link.encode()).hexdigest()[:16],
                "title": title,
                "summary": summary,
                "source_name": source["name"],
                "source_url": link,
                "category": source.get("category", "news"),
                "published": published.isoformat(),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "batch": batch_id,
            }
            (INBOX / f"{item['id']}.json").write_text(
                __import__("json").dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            seen_urls.add(link)
            seen_hashes.append(fp)
            new_urls.append(link)
            new_hashes.append(str(fp))
            saved += 1
            count += 1
        if count:
            log(f"  + {source['name']}: {count} new items")

    # keep state bounded
    seen["urls"] = list(seen_urls)[-5000:]
    seen["hashes"] = [str(h) for h in seen_hashes[-5000:]]
    save_state("seen", seen)
    log(f"Collector done: {saved} saved | {skipped_dup} duplicates skipped | {skipped_filter} off-topic/banned skipped")
    return {"saved": saved, "duplicates": skipped_dup, "off_topic": skipped_filter}


if __name__ == "__main__":
    collect()
