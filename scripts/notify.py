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
        r.raise_for_status()
        return True
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "CoinPulse test message"
    print("sent" if send(msg) else "skipped/failed")
