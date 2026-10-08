"""SMM manager for the Telegram channel — behaves like a human content manager:

Post mix (auto-scheduled, max ~4/day, >=45min apart, spread across the day):
  1. Market snapshot  — daily live BTC/ETH/... prices from CoinGecko (engaging, no link)
  2. Article posts    — SMM-style copy (rotating hooks/emoji/CTA), UTM-tagged links
  3. Polls            — interactive, every N days, topic inferred from recent articles
  4. Weekly digest    — "This week on CoinPulse" roundup

State: data/state/smm.json (posted slugs, daily counters, last_post_ts).
Run: python3 scripts/smm.py [--dry]   (dry prints decisions without sending)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from common import CONTENT, POSTS, load_config, log, load_state, save_state
from notify import channel_id, post as tg_post, send_poll_channel

IMAGES = CONTENT / "images"
IMAGES.mkdir(parents=True, exist_ok=True)

COINGECKO = ("https://api.coingecko.com/api/v3/simple/price"
             "?ids=bitcoin,ethereum,solana,ripple,dogecoin,cardano"
             "&vs_currencies=usd&include_24hr_change=true")
FEAR_GREED = "https://api.alternative.me/fng/?limit=1"

HOOKS = ["🔥", "⚡", "👀", "📰", "🚨", "💡"]
CTAS = ["Read the full story →", "Full analysis on the site →",
        "Details and sources →", "What it means for the market →"]
POLL_QUESTIONS = {
    "bitcoin": ("Where is Bitcoin heading this week?",
                ["📈 Up", "📉 Down", "🦀 Sideways", "🔮 Hard to say"]),
    "ethereum": ("ETH: your call for this week?",
                 ["📈 Bullish", "📉 Bearish", "🦀 Flat", "🤷 Not tracking ETH"]),
    "regulation": ("Is crypto regulation getting tougher?",
                   ["Yes — it's a crackdown", "No — it brings clarity",
                    "It's all noise", "I don't follow politics"]),
    "market": ("How is your portfolio doing this week?",
               ["📈 Green", "📉 Red", "🦀 Flat", "🙈 Prefer not to look"]),
    "stablecoin": ("Do you keep savings in stablecoins?",
                   ["Yes, mostly USDT/USDC", "A small part", "No — only volatile assets",
                    "No — fiat only"]),
    "default": ("What crypto news do you want to see more of?",
                ["Bitcoin & prices", "Regulation & law", "DeFi & stablecoins",
                 "Security & hacks", "Institutional money"]),
}
POLL_ORDER = ["market", "bitcoin", "regulation", "stablecoin", "default", "ethereum"]


def parse_frontmatter(text: str):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        return json.loads(m.group(1)), m.group(2)
    except json.JSONDecodeError:
        return {}, text


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utm(url: str, cfg: dict) -> str:
    """Append UTM params so Analytics/Search Console shows channel-driven traffic."""
    q = cfg.get("smm", {}).get("utm", "utm_source=telegram&utm_medium=channel")
    if not q:
        return url
    return url + ("&" if "?" in url else "?") + q


def published_posts() -> list:
    """(slug, front) of all published articles, newest first."""
    out = []
    for p in sorted(POSTS.glob("*.md"), reverse=True):
        front, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        if front.get("status") == "published":
            out.append((p.stem, front))
    return out


def pick_variation(seed: str, options: list) -> str:
    return options[int(hashlib.md5(seed.encode()).hexdigest()[:6], 16) % len(options)]


# ---------- post builders ----------

def fetch_fear_greed() -> tuple:
    """(value, label) from the free alternative.me API; (None, '') on failure."""
    try:
        r = requests.get(FEAR_GREED, timeout=15, headers={"User-Agent": "CoinPulse/1.0"})
        r.raise_for_status()
        d = r.json()["data"][0]
        return int(d["value"]), d.get("value_classification", "")
    except Exception as e:
        log(f"SMM: Fear&Greed fetch failed: {e}")
        return None, ""


def build_snapshot(cfg: dict):
    """Returns (text, chart_jpeg_path_or_empty, fng_jpeg_path_or_empty)."""
    r = requests.get(COINGECKO, timeout=20,
                     headers={"User-Agent": "CoinPulse/1.0"})
    r.raise_for_status()
    data = r.json()
    names = {"bitcoin": ("BTC", "🟠"), "ethereum": ("ETH", "🔷"), "solana": ("SOL", "🟣"),
             "ripple": ("XRP", "⚪"), "dogecoin": ("DOGE", "🐕"), "cardano": ("ADA", "🔵")}
    lines, movers, chart_rows = [], [], []
    for cid, (sym, icon) in names.items():
        d = data.get(cid)
        if not d:
            continue
        chg = d.get("usd_24h_change") or 0
        arrow = "▲" if chg >= 0 else "▼"
        price = d["usd"]
        # adaptive precision: big coins -> whole dollars, small -> up to 4 decimals
        if price >= 1000:
            ps = f"${price:,.0f}"
        elif price >= 1:
            ps = f"${price:,.2f}"
        else:
            ps = f"${price:,.4f}".rstrip("0").rstrip(".")
        lines.append(f"{icon} {sym}  {ps}  {arrow} {chg:+.1f}%")
        movers.append((chg, sym))
        chart_rows.append((sym, price, chg))
    movers.sort()
    worst, best = movers[0], movers[-1]
    mood = ("risk-on 📈" if best[0] > 1.5 else
            "risk-off 📉" if worst[0] < -1.5 else "flat 🦀")
    header = f"📊 Market snapshot — {utcnow().strftime('%b %d, %H:%M')} UTC"

    fgv, fgl = fetch_fear_greed()
    if fgv is not None:
        fng_emoji = "😱" if fgv <= 25 else "😰" if fgv <= 45 else "😐" if fgv <= 55 else "🤑" if fgv <= 75 else "🤯"
        lines.append(f"\n{fng_emoji} Fear & Greed Index: {fgv} — {fgl}")
        footer = (f"\nLeader: {best[1]} {best[0]:+.1f}% · Laggard: {worst[1]} {worst[0]:+.1f}%\n"
                  f"Mood: {mood}\n\nFull news & analysis → "
                  f"{utm(cfg['site']['url'].rstrip('/') + '/', cfg)}")
    else:
        footer = (f"\nLeader: {best[1]} {best[0]:+.1f}% · Laggard: {worst[1]} {worst[0]:+.1f}%\n"
                  f"Mood: {mood}\n\nFull news & analysis → "
                  f"{utm(cfg['site']['url'].rstrip('/') + '/', cfg)}")
    text = header + "\n\n" + "\n".join(lines) + footer

    # chart image (always) + Fear&Greed card (when API responded)
    chart_path = ""
    try:
        from graphics import price_chart
        tmp = Path(tempfile.mkdtemp(prefix="smm_"))
        chart_path = str(tmp / f"chart_{utcnow().strftime('%Y%m%d')}.jpg")
        price_chart(chart_rows, chart_path,
                    title=f"Market snapshot — {utcnow().strftime('%b %d')}",
                    seed=utcnow().strftime("%Y%m%d"))
    except Exception as e:
        log(f"SMM: chart render failed: {e}")
        chart_path = ""
    fng_path = ""
    if fgv is not None:
        try:
            from graphics import fear_greed_card
            tmp = Path(tempfile.mkdtemp(prefix="smmfng_"))
            fng_path = str(tmp / "fng.jpg")
            fear_greed_card(fgv, fgl or "Neutral", fng_path)
        except Exception as e:
            log(f"SMM: F&G card render failed: {e}")
            fng_path = ""
    return text, chart_path, fng_path


def build_article_post(slug: str, front: dict, cfg: dict) -> str:
    base = cfg["site"]["url"].rstrip("/") + "/"
    url = utm(f"{base}posts/{slug}.html", cfg)
    title = front.get("title", "New article")
    desc = (front.get("description", "") or "").strip()
    hook = pick_variation(slug, HOOKS)
    cta = pick_variation(slug + "cta", CTAS)
    tags = " ".join(f"#{re.sub(r'[^A-Za-z0-9]', '', t)}" for t in (front.get("tags") or [])[:3])
    return f"{hook} {title}\n\n{desc}\n\n{cta} {url}\n\n{tags}"


def article_photo(slug: str, front: dict) -> str:
    """Local path of the article's hero JPEG; if absent, generate a branded cover
    on the spot so EVERY channel post carries an image."""
    img_rel = front.get("image", "")
    if img_rel.endswith((".jpg", ".jpeg", ".png")):
        p = CONTENT / img_rel if not img_rel.startswith("img/") else IMAGES / Path(img_rel).name
        if p.exists():
            return str(p)
    # last resort: render a branded cover right now
    try:
        from cover import branded_cover
        dest = IMAGES / f"{slug}.jpg"
        branded_cover(front.get("title", slug), (front.get("tags") or ["crypto"])[0], slug, dest)
        return str(dest)
    except Exception as e:
        log(f"SMM: cover render failed for {slug}: {e}")
        return ""


def build_recap_image(cfg: dict, week_titles: list) -> str:
    """Branded 'Weekly recap' card for the digest post."""
    try:
        from graphics import palette_for, gradient_bg, left_shade, find_font, wrap_px, draw_pill, save_jpg
        from PIL import Image, ImageDraw
        W, H = 1200, 675
        bg, mid, accent, _ = palette_for("weekly_recap_" + utcnow().strftime("%G-W%V"))
        img = gradient_bg(W, H, bg, mid, accent)
        img = left_shade(img, strength=0.55)
        d = ImageDraw.Draw(img, "RGBA")
        title_font = find_font(True, 52)
        d.text((60, 52), "🗞 Weekly Recap", font=title_font, fill=(244, 246, 252))
        sub_font = find_font(False, 24)
        d.text((62, 124), f"{len(week_titles)} stories you might have missed",
               font=sub_font, fill=(178, 185, 203))
        item_font = find_font(True, 27)
        y = 200
        for i, t in enumerate(week_titles[:5], 1):
            lines = wrap_px(d, f"{i}. {t}", item_font, max_w=W - 180, max_lines=2)
            for ln in lines:
                d.text((66, y), ln, font=item_font, fill=(226, 231, 242))
                y += 38
            y += 16
        pill_font = find_font(True, 18)
        draw_pill(d, (60, H - 66), "COINPULSE", pill_font, fill=accent, text_fill=(12, 15, 22))
        link_font = find_font(False, 17)
        d.text((230, H - 57), cfg["site"]["url"].rstrip("/"), font=link_font, fill=(178, 185, 203))
        tmp = Path(tempfile.mkdtemp(prefix="smmrecap_"))
        dest = str(tmp / "recap.jpg")
        save_jpg(img, dest, quality=88)
        return dest
    except Exception as e:
        log(f"SMM: recap render failed: {e}")
        return ""


def build_poll(slug_seed: str) -> tuple:
    """Pick a poll topic that matches recent articles (feels curated, not random)."""
    posts = published_posts()[:5]
    text = " ".join((f.get("title", "") + " " + " ".join(f.get("tags", []))).lower()
                    for _, f in posts)
    key = next((k for k in POLL_ORDER if k != "default" and k in text), "default")
    question, options = POLL_QUESTIONS[key]
    return question, options, slug_seed


def build_digest(cfg: dict) -> tuple:
    """Returns (text, recap_image_path_or_empty). Empty text = not enough material."""
    base = cfg["site"]["url"].rstrip("/") + "/"
    cutoff = (utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    week = [(s, f) for s, f in published_posts() if s[:10] >= cutoff][:7]
    if len(week) < 3:
        return "", ""
    lines = ["🗞 This week on CoinPulse", ""]
    for slug, front in week:
        lines.append(f"• {front.get('title', slug)}\n  {utm(base + 'posts/' + slug + '.html', cfg)}")
    total = len(published_posts())
    lines += ["", f"📚 All {total} articles: {utm(base, cfg)}"]
    recap = build_recap_image(cfg, [f.get("title", s) for s, f in week])
    return "\n".join(lines)[:4000], recap


# ---------- scheduler ----------

def run(dry: bool = False) -> None:
    cfg = load_config()
    smm_cfg = cfg.get("smm", {})
    if not smm_cfg.get("enabled", True):
        log("SMM: disabled in config.")
        return
    cid = channel_id()
    if not cid:
        log("SMM: no channel configured — skipping.")
        return

    st = load_state("smm", {"posted": [], "daily": {}, "polls": []})
    now = utcnow()
    today = now.strftime("%Y-%m-%d")
    st.setdefault("daily", {})
    count = st["daily"].get(today, 0)
    cap = int(smm_cfg.get("daily_post_cap", 4))
    gap = int(smm_cfg.get("min_gap_minutes", 45)) * 60

    if now.timestamp() - st.get("last_post_ts", 0) < gap:
        log(f"SMM: last post <{smm_cfg.get('min_gap_minutes', 45)}min ago — resting (human-like pacing).")
        return
    if count >= cap:
        log(f"SMM: daily cap reached ({count}/{cap}).")
        return

    def finish(kind: str, ok: bool):
        if dry:
            log(f"SMM(dry): would post [{kind}] ({count + 1}/{cap} today)")
            return
        if ok:
            st["daily"][today] = count + 1
            st["last_post_ts"] = now.timestamp()
            # prune old daily entries
            st["daily"] = {k: v for k, v in st["daily"].items() if k >= (now - timedelta(days=3)).strftime("%Y-%m-%d")}
            save_state("smm", st)
            log(f"SMM: posted [{kind}] ({count + 1}/{cap} today)")
        else:
            log(f"SMM: [{kind}] send failed — will retry next run.")

    # 1) daily market snapshot (text + chart image; F&G card follows next run)
    if smm_cfg.get("market_snapshot", True) and not st.get("snapshots", {}).get(today):
        try:
            text, chart_path, fng_path = build_snapshot(cfg)
            if dry:
                log("DRY snapshot:\n" + text + f"\n[chart: {chart_path}] [fng: {fng_path}]")
                ok = True
            else:
                ok = tg_post(cid, text, photo_file=chart_path)
                if ok and fng_path:
                    import time as _t
                    _t.sleep(3)  # human-like spacing between the two posts
                    if tg_post(cid, f"😨→🤑 Sentiment check: Fear & Greed Index is at "
                                    f"{utcnow().strftime('%b %d')}. What's your move?",
                               photo_file=fng_path):
                        st["daily"][today] = st["daily"].get(today, 0) + 1
                        st["last_post_ts"] = utcnow().timestamp()
                        log("SMM: posted [fear-greed card]")
            if ok:
                st.setdefault("snapshots", {})[today] = True
                st["snapshots"] = {k: v for k, v in st["snapshots"].items() if k >= (now - timedelta(days=3)).strftime("%Y-%m-%d")}
            finish("snapshot", ok)
            return
        except Exception as e:
            log(f"SMM: snapshot failed ({e}) — will post an article instead.")

    # 2) article post with hero image (max 3/day) — photo uploaded multipart from disk
    articles_today = st.get("articles_today", {}).get(today, 0)
    if articles_today < int(smm_cfg.get("articles_per_day", 3)):
        posted = set(st.get("posted", []))
        fresh = [(s, f) for s, f in published_posts() if s not in posted][:1]
        if fresh:
            slug, front = fresh[0]
            text = build_article_post(slug, front, cfg)
            photo_local = "" if dry else article_photo(slug, front)
            ok = (log("DRY article:\n" + text) or True) if dry else tg_post(
                cid, text, photo_file=photo_local, preview=not photo_local)
            if ok and not dry:
                st["posted"] = (st.get("posted", []) + [slug])[-300:]
                st.setdefault("articles_today", {})[today] = articles_today + 1
                st["articles_today"] = {k: v for k, v in st["articles_today"].items() if k >= (now - timedelta(days=3)).strftime("%Y-%m-%d")}
            elif dry:
                st.setdefault("articles_today", {})[today] = articles_today + 1
            finish("article+photo" if photo_local else "article", bool(ok))
            return

    # 3) poll every N days
    every = int(smm_cfg.get("poll_every_days", 2))
    last_poll = st.get("polls", [])
    poll_due = (not last_poll) or (now - datetime.fromisoformat(last_poll[-1])).days >= every
    if poll_due:
        seed = today
        question, options, _ = build_poll(seed)
        ok = (log(f"DRY poll: {question} {options}") or True) if dry else send_poll_channel(question, options)
        if ok:
            st["polls"] = (st.get("polls", []) + [now.isoformat()])[-30:]
        finish("poll", bool(ok))
        return

    # 4) weekly digest (configured weekday, default Sunday=6) with recap image
    wd = now.weekday()  # Mon=0..Sun=6
    if wd == int(smm_cfg.get("digest_weekday", 6)):
        week_key = now.strftime("%G-W%V")
        if st.get("last_digest_week") != week_key:
            text, recap = build_digest(cfg)
            if text:
                if dry:
                    log("DRY digest:\n" + text + f"\n[recap: {recap}]")
                    ok = True
                else:
                    ok = tg_post(cid, text, photo_file=recap)
                if ok:
                    st["last_digest_week"] = week_key
                finish("digest+recap", bool(ok))
                return

    log("SMM: nothing due this run (pacing).")


if __name__ == "__main__":
    run(dry="--dry" in sys.argv)
