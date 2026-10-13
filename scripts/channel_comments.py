"""Auto-enable the on-page Telegram Comments widget as soon as it is usable.

The widget (a real comment box rendered ON the article page, not a link away) only works
once the public channel has a LINKED DISCUSSION GROUP. That link is a ~30-second action in
the Telegram app by the channel owner — it cannot be done through the Bot API. Everything
after it is automatic:

  * this script asks Telegram for the channel (getChat) on every build;
  * when linked_chat_id appears, it flips comments.telegram_widget to true and stores the
    channel username, so the very next build renders the on-page box;
  * it never turns the widget OFF (an owner decision sticks), and it is a silent no-op
    when Telegram is unreachable or the channel is not linked yet.

Run: python3 scripts/channel_comments.py   (also invoked automatically by the build stage)
"""
from __future__ import annotations

import os
import re
import sys

import requests
import yaml

from common import ROOT, load_config, log

CONFIG_FILE = ROOT / "config.yaml"
CHANNEL_DEFAULT = "coinpulsedailynews"


def _channel_target(cfg: dict) -> str:
    """Where to ask Telegram for channel info: secret/env first, then config."""
    tg = cfg.get("notification", {}).get("telegram", {})
    env_name = tg.get("channel_id_env", "TELEGRAM_CHANNEL_ID")
    return (os.environ.get(env_name, "").strip()
            or str(tg.get("channel_id", "")).strip()
            or ("@" + CHANNEL_DEFAULT))


def _channel_username(cfg: dict) -> str:
    tg = cfg.get("notification", {}).get("telegram", {})
    cand = _channel_target(cfg)
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,32})", cand) or re.match(r"@([A-Za-z0-9_]{4,32})$", cand)
    if m:
        return m.group(1)
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,32})",
                  str((cfg.get("site", {}).get("social", {}) or {}).get("telegram_channel", "")))
    return m.group(1) if m else CHANNEL_DEFAULT


def probe_channel(cfg: dict, timeout: int = 20) -> dict:
    """Returns {} when we cannot ask Telegram (no token / network blocked / API error)."""
    tg = cfg.get("notification", {}).get("telegram", {})
    token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
    if not token:
        return {}
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getChat",
                         params={"chat_id": _channel_target(cfg)}, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            log(f"Channel probe: {str(data.get('description', ''))[:120]}")
            return {}
        return data.get("result", {}) or {}
    except Exception as e:
        log(f"Channel probe skipped (Telegram unreachable): {str(e)[:100]}")
        return {}


def enable_widget(username: str, linked_id) -> bool:
    """Persist comments.telegram_widget=true (+ channel) in config.yaml. Returns True on change."""
    raw = CONFIG_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw) or {}
    c = cfg.setdefault("comments", {})
    if c.get("telegram_widget") and c.get("telegram_channel") == username:
        return False
    c["telegram_widget"] = True
    c["telegram_channel"] = username
    c.setdefault("enabled", True)
    c.setdefault("widget_limit", 5)
    CONFIG_FILE.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=100),
                           encoding="utf-8")
    log(f"Comments widget ENABLED for @{username} (discussion group {linked_id}) — "
        f"the next build renders the on-page comment box.")
    return True


def run() -> bool:
    """Returns True when the on-page widget is active for this build."""
    cfg = load_config()
    c = cfg.get("comments", {})
    if c.get("telegram_widget"):
        return True                      # already on — nothing to do
    if c.get("telegram_widget_auto") is False:
        return False                     # owner turned auto-detection off
    info = probe_channel(cfg)
    if not info:
        return False
    linked = info.get("linked_chat_id")
    if linked:
        enable_widget(_channel_username(cfg), linked)
        return True
    log("Comments widget: channel has no linked discussion group yet — on-page box stays off "
        "(the Telegram bot deep-link CTA remains active).")
    return False


if __name__ == "__main__":
    on = run()
    print("widget_active:", on)
    sys.exit(0)
