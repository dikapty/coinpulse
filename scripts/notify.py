"""Telegram notifications: daily digest + error alerts. Free, via @BotFather bot."""
from __future__ import annotations

import os
import sys

import requests

from common import load_config, log


def _token() -> str:
    cfg = load_config()
    tg = cfg.get("notification", {}).get("telegram", {})
    if not tg.get("enabled"):
        return ""
    return os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()


def channel_id() -> str:
    """Public channel @username or numeric -100... id; empty when not configured."""
    cfg = load_config()
    tg = cfg.get("notification", {}).get("telegram", {})
    env = tg.get("channel_id_env", "TELEGRAM_CHANNEL_ID")
    return (os.environ.get(env, "") or str(tg.get("channel_id", ""))).strip()


def post(chat_id: str, text: str, preview: bool = False, photo: str = "") -> bool:
    """Low-level send. preview=True renders the link's OG card; photo = public image URL
    (Telegram fetches it server-side; JPEG/PNG only — SVG is unsupported by sendPhoto)."""
    token = _token()
    if not token or not chat_id:
        return False
    method = "sendPhoto" if photo else "sendMessage"
    payload = {"chat_id": chat_id}
    if photo:
        payload.update({"photo": photo, "caption": text[:1024]})
    else:
        payload.update({"text": text[:4000], "disable_web_page_preview": not preview})
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=25)
        if r.status_code != 200:
            detail = r.text[:300]
            if "chat not found" in detail.lower():
                who = ""
                try:
                    me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
                    who = me.json().get("result", {}).get("username", "")
                except Exception:
                    pass
                link = f"https://t.me/{who}" if who else "your bot chat"
                log(f"Telegram: chat not found. ACTION REQUIRED: open {link} "
                    f"(bot @{who or '?'}) in Telegram and press START — bots cannot message "
                    "users who never started the chat. Digests resume automatically afterwards.")
            elif "not a member" in detail.lower() or "administrator" in detail.lower():
                log("Telegram channel: bot is not an admin there. Add the bot to the channel "
                    "as an administrator with the 'Post messages' right.")
            elif "unauthorized" in detail.lower() or "Not Found" in detail:
                log("Telegram: bot token invalid or revoked — create a new one via @BotFather.")
            elif photo and "wrong type of file" in detail.lower():
                log("Telegram: image format rejected — retrying without photo.")
                return post(chat_id, text, preview)
            else:
                log(f"Telegram send failed: HTTP {r.status_code} {detail}")
            return False
        return True
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


def send(text: str, preview: bool = False) -> bool:
    """Owner's private chat (digests, alerts)."""
    cfg = load_config()
    tg = cfg.get("notification", {}).get("telegram", {})
    if not tg.get("enabled"):
        return False
    chat_id = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "").strip()
    if not chat_id:
        log("Telegram enabled but token/chat_id missing — skipping notification.")
        return False
    return post(chat_id, text, preview)


def send_channel(text: str, preview: bool = False, photo: str = "") -> bool:
    """Public channel broadcast. Silently skips when no channel is configured."""
    cid = channel_id()
    if not cid:
        return False
    return post(cid, text, preview, photo)


def send_poll_channel(question: str, options: list) -> bool:
    """Interactive poll in the public channel (engagement = Telegram boosts reach)."""
    token = _token()
    cid = channel_id()
    if not token or not cid:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPoll",
            json={"chat_id": cid, "question": question[:300],
                  "options": [str(o)[:100] for o in options[:10]],
                  "is_anonymous": True, "type": "regular"},
            timeout=25,
        )
        if r.status_code != 200:
            log(f"Telegram poll failed: HTTP {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log(f"Telegram poll failed: {e}")
        return False


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "CoinPulse test message"
    print("sent" if send(msg) else "skipped/failed")
