"""Comments for a STATIC site — the backend is the Telegram bot we already own.

Why Telegram: GitHub Pages serves files only — no database, no request handler, no
email. A bot gives us all of it at $0 with no new accounts:
  * endpoint    — readers message the bot; CI polls getUpdates
  * identity    — Telegram accounts are phone-verified (stronger than an email code)
  * feedback    — the bot replies "your comment is live" with a direct link
  * storage     — comments are committed to the repo, rendered at build time

Flow:
  1. Reader clicks "Write a comment" on an article -> deep link
     https://t.me/<bot>?start=c_<shortid>   (shortid <-> article slug)
  2. The bot asks for the text; the reader's next message (within 15 min) is the comment.
  3. CI polls updates, moderates with deterministic rules, stores, renders, replies.

Privacy: the repo is PUBLIC, so raw Telegram chat ids and usernames are never stored —
only an HMAC-style digest (salt lives in state) plus the first name the reader shows
publicly anyway. Nothing links a stored comment back to a Telegram account.

State: data/state/comments.json  (offset, salt, sessions, slug<->shortid)
Data:  data/comments/<slug>.json (approved + rejected), data/comments/_members.json

Run: python3 scripts/comments.py             # poll + moderate + store
     python3 scripts/comments.py --render <slug>
Exit code 10 = at least one comment was approved (workflow then rebuilds/redeploys).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone

import requests

from common import CONTENT, DATA, POSTS, load_config, log, load_state, save_state

COMMENTS_DIR = DATA / "comments"
COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
MEMBERS_FILE = COMMENTS_DIR / "_members.json"

BOT_API = "https://api.telegram.org/bot{token}/{method}"
SESSION_TTL = 15 * 60          # how long the bot waits for the comment text
MAX_PER_USER_DAY = 5
MIN_INTERVAL_SEC = 45
MAX_LEN = 800
MIN_LEN = 2

# Deterministic moderation: crypto-site spam is highly patterned, so a rule list
# catches the bulk of it without spending scarce free-tier LLM quota.
SPAM_PATTERNS = [
    r"\bwhats\s*?app\b", r"\btelegram\.me/", r"\bt\.me/", r"\bjoin (my|our) (channel|group)\b",
    r"\bsignal[s]?\b.{0,20}\b(pump|profit|guarantee)", r"\bguaranteed (profit|return|income)\b",
    r"\bearn (\$|usd|\d)", r"\bpassive income\b", r"\b\d{3,}[%]\b", r"\binvest(ment)? (plan|scheme)\b",
    r"\bmining (platform|pool)\b.{0,20}\b(profit|return)", r"\bforex (signal|broker)\b",
    r"\bcasino\b", r"\bporno?\b", r"\bonlyfans\b", r"\bviagra\b", r"\bhttps?://t\.me/",
    r"\bclick here\b.{0,20}\b(win|earn|get free)", r"\bairdrop\b.{0,20}\b(free|claim)\b",
    r"\bdm me\b", r"\bwrite me (on|in) (telegram|whatsapp)\b",
]
PROFANITY = [
    "fuck", "shit", "bitch", "asshole", "nigger", "faggot", "cunt",
    "хуй", "пизд", "ебат", "бляд", "сука", "мудак", "говн",
]
PROFANITY_RE = re.compile("|".join(re.escape(w) for w in PROFANITY), re.IGNORECASE)
SPAM_RE = [re.compile(p, re.IGNORECASE) for p in SPAM_PATTERNS]

REJECT_MESSAGES = {
    "too_short": "Your message is too short to be a comment.",
    "too_long": f"Comments are limited to {MAX_LEN} characters — please shorten it.",
    "shouting": "Please avoid writing in ALL CAPS.",
    "too_many_links": "Comments may contain at most one link.",
    "profanity": "Your comment was rejected: please keep the discussion respectful.",
    "spam_pattern": "Your comment looks like advertising and was rejected.",
    "daily_limit": f"You have reached the limit of {MAX_PER_USER_DAY} comments per day.",
    "too_fast": "Please wait a minute before your next comment.",
    "duplicate": "You already posted this exact comment.",
}


# ---------------------------------------------------------------- bot plumbing

def _token() -> str:
    cfg = load_config()
    tg = cfg.get("notification", {}).get("telegram", {})
    return os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()


def bot_username() -> str:
    cfg = load_config()
    tg = cfg.get("notification", {}).get("telegram", {})
    return str(tg.get("bot_username", "")).strip().lstrip("@")


def tg_call(method: str, payload: dict, timeout: int = 30) -> dict:
    tok = _token()
    if not tok:
        return {"ok": False, "description": "no bot token"}
    try:
        r = requests.post(BOT_API.format(token=tok, method=method),
                          json=payload, timeout=timeout)
        try:
            return r.json()
        except ValueError:
            return {"ok": False, "description": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def reply(chat_id, text: str) -> None:
    res = tg_call("sendMessage", {"chat_id": chat_id, "text": text[:4000],
                                  "disable_web_page_preview": False})
    if not res.get("ok"):
        log(f"Comments: reply failed: {str(res.get('description', ''))[:160]}")


# ------------------------------------------------------------------- identity

def user_digest(chat_id, salt: str) -> str:
    """One-way, salted digest of a chat id — safe to commit to a public repo."""
    return hmac.new(salt.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()[:24]


def display_name(chat: dict) -> str:
    name = (chat.get("first_name") or chat.get("title") or "Reader").strip()
    name = re.sub(r"[<>\"'`\[\]{}]+", "", name)          # no markup injection
    return (name or "Reader")[:32]


# ------------------------------------------------------------- slug <-> shortid

def _b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        out += digits[n % 36]
        n //= 36
    return out or "0"


def short_id(slug: str, ids: dict) -> str:
    """Stable <=8-char id for a slug (Telegram start_param is limited to 64 chars)."""
    rev, fwd = ids.setdefault("by_id", {}), ids.setdefault("by_slug", {})
    if slug in fwd:
        return fwd[slug]
    digest = hashlib.md5(slug.encode()).hexdigest()
    for i in range(0, 24, 4):
        sid = _b36(int(digest[i:i + 4], 16))[:8].rjust(3, "0")
        if sid not in rev:
            rev[sid] = slug
            fwd[slug] = sid
            return sid
    raise RuntimeError("short id space exhausted")


def ensure_state() -> dict:
    """Load state, assign ids for every published post, and create the salt once."""
    st = load_state("comments", {})
    ids = st.setdefault("ids", {"by_id": {}, "by_slug": {}})
    for p in sorted(POSTS.glob("*.md")):
        try:
            front = parse_front(p.read_text(encoding="utf-8"))
            if front.get("status") == "published":
                short_id(p.stem, ids)
        except Exception:
            continue
    if not st.get("salt"):
        st["salt"] = secrets.token_hex(16)
    st.setdefault("sessions", {})
    st.setdefault("fingerprints", [])
    st.setdefault("ratelimit", {})
    st.setdefault("last_ts", {})
    return st


def parse_front(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def post_title(slug: str) -> str:
    try:
        front = parse_front((POSTS / f"{slug}.md").read_text(encoding="utf-8"))
        return front.get("title", slug)
    except Exception:
        return slug


# ------------------------------------------------------------------- moderation

def moderate(text: str, uid: str, st: dict) -> tuple:
    """Returns (ok, reason). Mutates rate-limit/duplicate state when approved."""
    t = text.strip()
    if len(t) < MIN_LEN:
        return False, "too_short"
    if len(t) > MAX_LEN:
        return False, "too_long"
    letters = [c for c in t if c.isalpha()]
    if letters and len(t) > 25 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
        return False, "shouting"
    if len(re.findall(r"https?://", t)) > 1:
        return False, "too_many_links"
    if PROFANITY_RE.search(t):
        return False, "profanity"
    if any(rx.search(t) for rx in SPAM_RE):
        return False, "spam_pattern"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rl = st.setdefault("ratelimit", {})
    day = rl.get(uid) or {}
    if day.get("date") != today:
        day = {"date": today, "count": 0}
    if day["count"] >= MAX_PER_USER_DAY:
        return False, "daily_limit"
    if time.time() - st.get("last_ts", {}).get(uid, 0) < MIN_INTERVAL_SEC:
        return False, "too_fast"

    fp = hashlib.md5(f"{uid}:{t.lower()}".encode()).hexdigest()[:16]
    if fp in st.setdefault("fingerprints", []):
        return False, "duplicate"

    st["fingerprints"] = (st["fingerprints"] + [fp])[-2000:]
    rl[uid] = {"date": today, "count": day["count"] + 1}
    st.setdefault("last_ts", {})[uid] = time.time()
    st["ratelimit"] = {k: v for k, v in rl.items() if v.get("date") == today}
    return True, ""


# --------------------------------------------------------------------- storage

def load_comments(slug: str) -> list:
    p = COMMENTS_DIR / f"{slug}.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("comments", []) if isinstance(d, dict) else d
    except json.JSONDecodeError:
        return []


def save_comments(slug: str, comments: list) -> None:
    (COMMENTS_DIR / f"{slug}.json").write_text(
        json.dumps({"slug": slug, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "comments": comments[-200:]}, ensure_ascii=False, indent=1),
        encoding="utf-8")


def load_members() -> list:
    if not MEMBERS_FILE.exists():
        return []
    try:
        return json.loads(MEMBERS_FILE.read_text(encoding="utf-8")).get("members", [])
    except json.JSONDecodeError:
        return []


def register_member(uid: str, name: str) -> bool:
    """First contact makes a reader a 'member' (Telegram-verified identity)."""
    members = load_members()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for m in members:
        if m.get("uid") == uid:
            changed = m.get("name") != name
            m["name"] = name
            m["last_seen"] = now
            m["comments"] = int(m.get("comments", 0))
            if changed:
                save_members(members)
            return False
    members.append({"uid": uid, "name": name, "joined": now, "last_seen": now, "comments": 0})
    save_members(members)
    log(f"Comments: new member #{len(members)}: {name}")
    return True


def save_members(members: list) -> None:
    MEMBERS_FILE.write_text(
        json.dumps({"members": members[-1000:], "count": len(members)},
                   ensure_ascii=False, indent=1), encoding="utf-8")


def bump_member_comments(uid: str) -> None:
    members = load_members()
    for m in members:
        if m.get("uid") == uid:
            m["comments"] = int(m.get("comments", 0)) + 1
            save_members(members)
            return


# ------------------------------------------------------------------ processing

def handle_update(upd: dict, st: dict, cfg: dict) -> str:
    """Process one update. Returns one of:
    approved | rejected | member | session | none."""
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = msg.get("text")
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not text or not chat_id:
        return "none"
    if chat.get("type") != "private":          # groups/channels are not comments
        return "none"

    salt = st.get("salt", "")
    uid = user_digest(chat_id, salt)
    name = display_name(chat)
    ids = st.setdefault("ids", {"by_id": {}, "by_slug": {}})
    sessions = st.setdefault("sessions", {})
    text = text.strip()

    # 1) deep link from an article -> open a comment session
    m = re.match(r"^/start\s+c_([0-9a-z]{1,8})$", text, re.IGNORECASE)
    if m:
        slug = ids.get("by_id", {}).get(m.group(1).lower())
        if not slug:
            reply(chat_id, "Sorry, this comment link is no longer valid. Open the article and "
                           "press '💬 Write a comment' again.")
            return "none"
        sessions[uid] = {"slug": slug, "ts": time.time(), "chat_id": chat_id}
        reply(chat_id, f"💬 You are commenting on:\n<b>{html.escape(post_title(slug))}</b>\n\n"
                       f"Send your comment as the next message (up to {MAX_LEN} characters). "
                       f"It appears on the article page after a quick automatic check.")
        register_member(uid, name)
        return "session"

    # 2) bare /start — greet and register
    if text.lower().startswith("/start"):
        sessions.pop(uid, None)
        reply(chat_id, "👋 Hi! I'm the CoinPulse bot.\n\n"
                       "To comment on an article, open it on the site and press "
                       "'💬 Write a comment' — that brings you here with the right context.\n\n"
                       "Anything you send me here counts as your membership — no email, "
                       "no password.")
        register_member(uid, name)
        return "member"

    # 3) a pending session -> this message is the comment
    sess = sessions.get(uid)
    if not sess or time.time() - sess.get("ts", 0) > SESSION_TTL:
        sessions.pop(uid, None)
        if len(text) >= 3 and register_member(uid, name):
            reply(chat_id, f"✅ You are now a CoinPulse member, {name}!\n\n"
                           f"To comment under an article, open it and press "
                           f"'💬 Write a comment'.")
            return "member"
        reply(chat_id, "No open comment session. Open an article on the site and press "
                       "'💬 Write a comment' to start one.")
        return "none"

    slug = sess["slug"]
    chat_id = sess.get("chat_id", chat_id)
    ok, reason = moderate(text, uid, st)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cid = hashlib.md5(f"{uid}{now}{text[:24]}".encode()).hexdigest()[:10]
    comments = load_comments(slug)
    comments.append({"id": cid, "uid": uid, "name": name, "text": text[:MAX_LEN],
                     "ts": now, "status": "approved" if ok else "rejected", "reason": reason})
    save_comments(slug, comments)
    sessions.pop(uid, None)

    if ok:
        bump_member_comments(uid)
        register_member(uid, name)
        n = sum(1 for c in comments if c.get("status") == "approved")
        link = cfg["site"]["url"].rstrip("/") + f"/posts/{slug}.html#comments"
        reply(chat_id, f"✅ Your comment is live!\n\n«{text[:300]}»\n\n"
                       f"💬 {n} comment(s) on this article: {link}\n\n"
                       f"It appears on the page within ~20 minutes — the site rebuilds "
                       f"automatically. Thanks for joining the discussion!")
        log(f"Comments: approved from {name} on {slug[:44]} ({len(text)} chars)")
        return "approved"

    reply(chat_id, "⚠️ " + REJECT_MESSAGES.get(reason, "Your comment was not published.")
                   + "\n\nYou can send a corrected version right away.")
    log(f"Comments: rejected ({reason}) from {name} on {slug[:44]}")
    return "rejected"


def run() -> int:
    """Poll Telegram; returns the number of newly APPROVED comments."""
    cfg = load_config()
    ccfg = cfg.get("comments", {})
    if not ccfg.get("enabled", True):
        log("Comments: disabled in config.")
        return 0
    if not _token():
        log("Comments: no TELEGRAM_BOT_TOKEN — skipping.")
        return 0

    st = ensure_state()
    rounds = int(ccfg.get("poll_rounds", 3))
    timeout = int(ccfg.get("poll_timeout", 10))

    # First run ever: skip the historical backlog so old messages are not replayed.
    if not st.get("offset"):
        res = tg_call("getUpdates", {"offset": -1, "timeout": 0}, timeout=30)
        if res.get("ok"):
            last = res.get("result") or []
            st["offset"] = (last[-1]["update_id"] + 1) if last else 1
            save_state("comments", st)
            log(f"Comments: initialised at update offset {st['offset']} (backlog skipped).")
        else:
            log(f"Comments: init getUpdates failed: {str(res.get('description',''))[:160]}")
            return 0

    approved = handled = 0
    for _ in range(max(1, rounds)):
        offset = int(st.get("offset", 0))
        res = tg_call("getUpdates", {"offset": offset, "timeout": timeout,
                                     "allowed_updates": ["message"]}, timeout=timeout + 20)
        if not res.get("ok"):
            desc = str(res.get("description", ""))
            if "Unauthorized" in desc:
                log("Comments: bot token invalid or revoked — create a new one via @BotFather.")
            elif "webhook" in desc.lower():
                log("Comments: a webhook is active on this bot, so getUpdates is disabled. "
                    "Either clear the webhook or use a dedicated comments bot token.")
            else:
                log(f"Comments: getUpdates failed: {desc[:160]}")
            break
        updates = res.get("result") or []
        if not updates:
            break
        for upd in updates:
            st["offset"] = max(int(st.get("offset", 0)), upd.get("update_id", 0) + 1)
            try:
                outcome = handle_update(upd, st, cfg)
            except Exception as e:
                log(f"Comments: update failed: {e}")
                continue
            if outcome != "none":
                handled += 1
            if outcome == "approved":
                approved += 1
        save_state("comments", st)
        if len(updates) < 1:
            break
    save_state("comments", st)
    log(f"Comments: poll done (offset {st.get('offset')}, {handled} handled, {approved} approved)")
    return approved


# -------------------------------------------------------------------- rendering

AVATAR_COLORS = ["#f7931a", "#3ddc84", "#7fb3ff", "#ff6b9d", "#ffc46b", "#b48cff",
                 "#4dd0e1", "#ff8a65"]


def _rel_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    if secs < 86400 * 7:
        return f"{secs // 86400} d ago"
    return dt.strftime("%b %d, %Y")


def render_comments(slug: str, base: str, bot: str = "") -> str:
    """HTML block for an article page: heading, comment box mount point, comments.

    Comments live ON the page — readers never leave the site. The `bot` argument is
    accepted for backwards compatibility but deliberately unused: no Telegram deep link
    is rendered any more. static/comments.js upgrades the mount point into an email
    sign-in + comment form; the comment list below is server-rendered, so it is visible
    even with JavaScript disabled.
    """
    comments = [c for c in load_comments(slug) if c.get("status") == "approved"]
    comments.sort(key=lambda c: c.get("ts", ""))
    n = len(comments)

    head = (f'<h2 class="cmt-title">💬 Comments'
            f'<span class="cmt-count">{n}</span></h2>')
    # Mount point for the on-page form (email sign-in -> textarea -> post).
    # Server-rendered fallback stays readable without JS.
    mount = ('<div class="cmt-mount" data-slug="' + html.escape(slug) + '">'
             '<noscript><p class="cmt-note">Enable JavaScript to sign in with your email '
             'and write a comment right here on the page.</p></noscript></div>')

    if not comments:
        body = ('<p class="cmt-empty">No comments yet — be the first to share your take '
                'on this story.</p>')
    else:
        items = []
        for c in comments[-50:]:
            name = html.escape(c.get("name") or "Reader")
            key = c.get("uid") or name
            color = AVATAR_COLORS[int(hashlib.md5(key.encode()).hexdigest()[:4], 16)
                                    % len(AVATAR_COLORS)]
            initial = html.escape((c.get("name") or "R").strip()[:1].upper() or "R")
            text = html.escape(c.get("text", "")).replace("\n", "<br>")
            items.append(
                '<div class="cmt">'
                f'<div class="cmt-avatar" style="background:{color}">{initial}</div>'
                '<div class="cmt-body">'
                f'<div class="cmt-meta"><span class="cmt-name">{name}</span>'
                f'<span class="cmt-time">{_rel_time(c.get("ts", ""))}</span></div>'
                f'<div class="cmt-text">{text}</div>'
                '</div></div>')
        body = '<div class="cmt-list">' + "".join(items) + '</div>'

    powered = ('<p class="cmt-powered">Comments stay on this page. Sign-in is by email code '
               '(no password); advertising, insults, ALL CAPS and signal links are rejected '
               'automatically.</p>')
    return f'<section class="comments" id="comments">{head}{mount}{body}{powered}</section>'


if __name__ == "__main__":
    if "--render" in sys.argv:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        _cfg = load_config()
        _slug = args[-1] if args else sorted(p.stem for p in POSTS.glob("*.md"))[-1]
        print(render_comments(_slug, _cfg["site"]["url"].rstrip("/") + "/", bot_username()))
    else:
        sys.exit(10 if run() else 0)
