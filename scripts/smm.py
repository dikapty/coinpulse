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
from datetime import datetime, timedelta, timezone

import requests

from common import POSTS, load_config, log, load_state, save_state
from notify import channel_id, post as tg_post, send_poll_channel

COINGECKO = ("https://api.coingecko.com/api/v3/simple/price"
             "?ids=bitcoin,ethereum,solana,ripple,dogecoin,cardano"
             "&vs_currencies=usd&include_24hr_change=true")

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

def build_snapshot(cfg: dict) -> str:
    r = requests.get(COINGECKO, timeout=20,
                     headers={"User-Agent": "CoinPulse/1.0"})
    r.raise_for_status()
    data = r.json()
    names = {"bitcoin": ("BTC", "🟠"), "ethereum": ("ETH", "🔷"), "solana": ("SOL", "🟣"),
             "ripple": ("XRP", "⚪"), "dogecoin": ("DOGE", "🐕"), "cardano": ("ADA", "🔵")}
    lines, movers = [], []
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
    movers.sort()
    worst, best = movers[0], movers[-1]
    mood = ("risk-on 📈" if best[0] > 1.5 else
            "risk-off 📉" if worst[0] < -1.5 else "flat 🦀")
    header = f"📊 Market snapshot — {utcnow().strftime('%b %d, %H:%M')} UTC"
    footer = (f"\nLeader: {best[1]} {best[0]:+.1f}% · Laggard: {worst[1]} {worst[0]:+.1f}%\n"
              f"Mood: {mood}\n\nFull news & analysis → "
              f"{cfg['site']['url'].rstrip('/')}/?utm_source=telegram&utm_medium=snapshot")
    return header + "\n\n" + "\n".join(lines) + footer


def build_article_post(slug: str, front: dict, cfg: dict) -> str:
    base = cfg["site"]["url"].rstrip("/") + "/"
    url = utm(f"{base}posts/{slug}.html", cfg)
    title = front.get("title", "New article")
    desc = (front.get("description", "") or "").strip()
    hook = pick_variation(slug, HOOKS)
    cta = pick_variation(slug + "cta", CTAS)
    tags = " ".join(f"#{re.sub(r'[^A-Za-z0-9]', '', t)}" for t in (front.get("tags") or [])[:3])
    return f"{hook} {title}\n\n{desc}\n\n{cta} {url}\n\n{tags}"


def build_poll(slug_seed: str) -> tuple:
    """Pick a poll topic that matches recent articles (feels curated, not random)."""
    posts = published_posts()[:5]
    text = " ".join((f.get("title", "") + " " + " ".join(f.get("tags", []))).lower()
                    for _, f in posts)
    key = next((k for k in POLL_ORDER if k != "default" and k in text), "default")
    question, options = POLL_QUESTIONS[key]
    return question, options, slug_seed


def build_digest(cfg: dict) -> str:
    base = cfg["site"]["url"].rstrip("/") + "/"
    cutoff = (utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    week = [(s, f) for s, f in published_posts() if s[:10] >= cutoff][:7]
    if len(week) < 3:
        return ""
    lines = ["🗞 This week on CoinPulse", ""]
    for slug, front in week:
        lines.append(f"• {front.get('title', slug)}\n  {utm(base + 'posts/' + slug + '.html', cfg)}")
    total = len(published_posts())
    lines += ["", f"📚 All {total} articles: {utm(base, cfg)}"]
    return "\n".join(lines)[:4000]


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

    # 1) daily market snapshot
    if smm_cfg.get("market_snapshot", True) and not st.get("snapshots", {}).get(today):
        try:
            text = build_snapshot(cfg)
            if dry:
                log("DRY snapshot:\n" + text)
                ok = True
            else:
                ok = tg_post(cid, text)
            if ok:
                st.setdefault("snapshots", {})[today] = True
                st["snapshots"] = {k: v for k, v in st["snapshots"].items() if k >= (now - timedelta(days=3)).strftime("%Y-%m-%d")}
            finish("snapshot", ok)
            return
        except Exception as e:
            log(f"SMM: snapshot failed ({e}) — will post an article instead.")

    # 2) article post (max 3/day)
    articles_today = st.get("articles_today", {}).get(today, 0)
    if articles_today < int(smm_cfg.get("articles_per_day", 3)):
        posted = set(st.get("posted", []))
        fresh = [(s, f) for s, f in published_posts() if s not in posted][:1]
        if fresh:
            slug, front = fresh[0]
            text = build_article_post(slug, front, cfg)
            img = front.get("image", "")
            base = cfg["site"]["url"].rstrip("/") + "/"
            photo = base + img if img.endswith((".jpg", ".jpeg", ".png")) else ""
            ok = (log("DRY article:\n" + text) or True) if dry else tg_post(
                cid, text, preview=not photo, photo=photo)
            if ok and not dry:
                st["posted"] = (st.get("posted", []) + [slug])[-300:]
                st.setdefault("articles_today", {})[today] = articles_today + 1
                st["articles_today"] = {k: v for k, v in st["articles_today"].items() if k >= (now - timedelta(days=3)).strftime("%Y-%m-%d")}
            elif dry:
                st.setdefault("articles_today", {})[today] = articles_today + 1
            finish("article", bool(ok))
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

    # 4) weekly digest (configured weekday, default Sunday=6)
    wd = now.weekday()  # Mon=0..Sun=6
    if wd == int(smm_cfg.get("digest_weekday", 6)):
        week_key = now.strftime("%G-W%V")
        if st.get("last_digest_week") != week_key:
            text = build_digest(cfg)
            if text:
                ok = (log("DRY digest:\n" + text) or True) if dry else tg_post(cid, text)
                if ok:
                    st["last_digest_week"] = week_key
                finish("digest", bool(ok))
                return

    log("SMM: nothing due this run (pacing).")


if __name__ == "__main__":
    run(dry="--dry" in sys.argv)
