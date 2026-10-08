"""Free promotion module — no accounts, no money:

1. IndexNow  -> instant indexing in Bing, Yandex, Seznam, Naver (key file served from site root)
2. PingOMatic -> broadcast update pings to blog directories/services
3. Telegram  -> each newly published article is posted to the user's chat (bot is free)

State: data/state/promoted.json remembers submitted URLs so each article is
promoted exactly once. Run: python3 scripts/promote.py
"""
from __future__ import annotations

import json
import secrets

import requests

from common import POSTS, SITE, load_config, log, load_state, save_state
from notify import send, send_channel, channel_id

INDEXNOW_ENDPOINTS = [
    "https://api.indexnow.org/indexnow",
    "https://www.bing.com/indexnow",
]
PING_URL = "https://pingomatic.com/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; CoinPulse/1.0; +https://dikapty.github.io/coinpulse/)"}


def parse_frontmatter(text: str):
    import re
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        return json.loads(m.group(1)), m.group(2)
    except json.JSONDecodeError:
        return {}, text


def get_indexnow_key(cfg: dict) -> str:
    """Persistent random key; the public key file is copied into site/ on build."""
    key = load_state("indexnow_key", None)
    if not key:
        key = secrets.token_hex(16)
        save_state("indexnow_key", key)
        log(f"Generated new IndexNow key: {key}")
    return key


def write_key_file(key: str) -> None:
    if SITE.exists():
        (SITE / f"{key}.txt").write_text(key, encoding="utf-8")


def published_urls(cfg: dict) -> dict:
    """slug -> url of every published post."""
    base = cfg["site"]["url"].rstrip("/") + "/"
    out = {}
    for p in POSTS.glob("*.md"):
        front, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        if front.get("status") == "published":
            out[p.stem] = f"{base}posts/{p.stem}.html"
    return out


def submit_indexnow(cfg: dict, urls: list, key: str) -> bool:
    host = cfg["site"]["url"].split("//")[-1].split("/")[0]
    body = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }
    ok = False
    for ep in INDEXNOW_ENDPOINTS:
        try:
            r = requests.post(ep, json=body, headers=UA, timeout=30)
            log(f"IndexNow {ep.split('//')[1].split('/')[0]}: HTTP {r.status_code} for {len(urls)} URL(s)")
            if r.status_code in (200, 202):
                ok = True  # one success is enough; endpoints share the index
        except Exception as e:
            log(f"IndexNow {ep} failed: {e}")
    return ok


def ping_o_matic(cfg: dict) -> bool:
    site = cfg["site"]
    try:
        r = requests.post(PING_URL, headers=UA, timeout=30, data={
            "title": site["name"],
            "blogurl": site["url"],
            "rssurl": site["url"].rstrip("/") + "/feed.xml",
            "chk_weblogscom": "on",
            "chk_feedburner": "on",
            "chk_newsisfree": "on",
            "chk_topicexchange": "on",
            "chk_google": "on",
            "chk_tailrank": "on",
            "chk_skygrid": "on",
            "chk_bitacoras": "on",
            "chk_audioweb": "on",
            "chk_blogdigger": "on",
            "chk_blogstreet": "on",
            "chk_moreover": "on",
            "chk_weblogalot": "on",
            "chk_icerocket": "on",
            "chk_newsgator": "on",
            "chk_pubsubcom": "on",
            "chk_feedster": "on",
            "chk_a2b": "on",
            "chk_blogshares": "on",
            "chk_blogsay": "on",
            "chk_bloggage": "on",
            "chk_blogarama": "on",
            "chk_feedblitz": "on",
            "chk_syndic8": "on",
            "chk_winning": "on",
            "chk_feedjit": "on",
        })
        ok = r.status_code == 200
        log(f"PingOMatic: HTTP {r.status_code} {'OK' if ok else '(unexpected)'}")
        return ok
    except Exception as e:
        log(f"PingOMatic failed: {e}")
        return False


def telegram_post(cfg: dict, front: dict, url: str, image_url: str = "") -> bool:
    tg = cfg.get("notification", {}).get("telegram", {})
    if not tg.get("enabled"):
        return False
    tags = " ".join(f"#{t.replace(' ', '')}" for t in (front.get("tags") or [])[:4])
    title = front.get("title", "New article")
    desc = (front.get("description", "") or "")[:200]
    text = f"📰 {title}\n\n{desc}\n\n{url}\n\n{tags}"
    to_channel = send_channel(text, preview=True, photo=image_url)
    to_owner = send(text, preview=True)  # OG card preview makes the post attractive
    return to_channel or to_owner


def run() -> None:
    cfg = load_config()
    promoted = load_state("promoted", {"urls": []})
    done = set(promoted.get("urls", []))
    home = cfg["site"]["url"].rstrip("/") + "/"
    key = get_indexnow_key(cfg)

    all_urls = published_urls(cfg)
    fresh = [(slug, url) for slug, url in all_urls.items() if url not in done]
    if not fresh:
        log("Promote: nothing new to submit.")
        write_key_file(key)
        # weekly refresh: resubmit homepage so crawlers keep returning
        import time as _t
        if _t.time() - promoted.get("last_home_submit", 0) > 6 * 86400:
            if submit_indexnow(cfg, [home], key):
                promoted["last_home_submit"] = _t.time()
                save_state("promoted", promoted)
        return

    log(f"Promote: {len(fresh)} new URL(s)")
    write_key_file(key)

    # article URLs + homepage together (keeps the root fresh in the index too)
    ok_index = submit_indexnow(cfg, [u for _, u in fresh] + [home], key)
    if ok_index:
        promoted["last_home_submit"] = __import__("time").time()

    # Telegram: post newest articles (limit per run to avoid spamming owner + channel)
    tg = cfg.get("notification", {}).get("telegram", {})
    limit = int(tg.get("channel_posts_per_run", 3)) if channel_id() else 2
    fresh_sorted = sorted(fresh, key=lambda x: x[0], reverse=True)[:limit]
    base = cfg["site"]["url"].rstrip("/") + "/"
    for slug, url in fresh_sorted:
        front, _ = parse_frontmatter((POSTS / f"{slug}.md").read_text(encoding="utf-8"))
        # sendPhoto needs JPEG/PNG; SVG hero covers -> let the OG preview handle the image
        img = front.get("image", "")
        photo = f"{base}{img}" if img.endswith((".jpg", ".jpeg", ".png")) else ""
        if telegram_post(cfg, front, url, photo):
            log(f"Telegram: posted {slug[:60]}" + (" [photo]" if photo else ""))

    if ok_index:
        promoted["urls"] = (promoted.get("urls", []) + [u for _, u in fresh])[-500:]
        save_state("promoted", promoted)
        ping_o_matic(cfg)  # only when we have genuinely new content
    else:
        log("IndexNow submission failed — will retry on next run (state not updated).")


if __name__ == "__main__":
    run()
