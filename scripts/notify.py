"""Telegram notifications: daily digest + error alerts. Free, via @BotFather bot."""
from __future__ import annotations

import os
import sys

import requests

from common import load_config, log


def send(text: str) -> bool:
    cfg = load_config()
    tg = cfg.get("notification", {}).get("telegram", {})
    if not tg.get("enabled"):
        return False
    token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
    chat_id = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "").strip()
    if not token or not chat_id:
        log("Telegram enabled but token/chat_id missing — skipping notification.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            detail = r.text[:300]
            if "chat not found" in detail.lower():
                log("Telegram: chat not found — open the bot in Telegram and press /start first "
                    "(bots cannot message users who never started the chat), then re-run.")
            elif "unauthorized" in detail.lower() or "Not Found" in detail:
                log("Telegram: bot token invalid or revoked — create a new one via @BotFather.")
            else:
                log(f"Telegram send failed: HTTP {r.status_code} {detail}")
            return False
        return True
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "CoinPulse test message"
    print("sent" if send(msg) else "skipped/failed")
