"""Module 6: daily health monitor + Telegram digest + quota watchdog.

Collects pipeline stats (collected/written/approved/published today, inbox backlog,
LLM quota errors in logs), writes data/state/monitor.json and sends a digest.
If quality metrics drop (rejections spike), it lowers tomorrow's article quota.

Run: python3 scripts/monitor.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from common import (DRAFTS, INBOX, POSTS, REJECTED, load_config, log, load_state,
                    save_state)
from notify import send

PENDING = DRAFTS.parent / "drafts_pending"


def site_online(cfg: dict) -> bool:
    url = cfg["site"]["url"].rstrip("/") + "/"
    try:
        r = requests.head(url, timeout=15, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


def run() -> None:
    cfg = load_config()
    key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = load_state("daily_stats", {}).get(key, {})
    gate_history = load_state("gate_history", [])
    today_gate = [g for g in gate_history if g.get("file", "").startswith(key)]
    rejected_today = sum(1 for g in today_gate if g["decision"] == "reject")
    pending_today = sum(1 for g in today_gate if g["decision"] == "pending")

    report = {
        "date": key,
        "collected": stats.get("collected", 0),
        "written": stats.get("written", 0),
        "approved": stats.get("approved", 0),
        "published": stats.get("published", 0),
        "pending_review": pending_today + len(list(PENDING.glob("*.md"))),
        "rejected": rejected_today,
        "inbox_backlog": len(list(INBOX.glob("*.json"))),
        "total_posts": len(list(POSTS.glob("*.md"))),
        "site_online": site_online(cfg),
    }
    try:
        from llm import budget_remaining
        report["llm_budget_left"] = budget_remaining()
    except Exception:
        pass

    # Adaptive safety: if LLM rejected >= half of today's drafts, halve tomorrow's quota.
    quota_key = "quota_override"
    if today_gate and rejected_today >= max(2, len(today_gate) // 2):
        save_state(quota_key, {"date": key, "factor": 0.5,
                               "reason": f"{rejected_today} rejections today — quality degraded"})
        report["quota_override"] = "tomorrow halved (quality drop)"
        log("QUALITY ALERT: too many rejections — tomorrow's quota halved.")

    history = load_state("monitor_history", [])
    history.append(report)
    save_state("monitor_history", history[-90:])

    emoji = "OK" if report["site_online"] else "SITE DOWN!"
    digest = (
        f"CoinPulse daily digest — {key}\n"
        f"Site: {emoji} | Published today: {report['published']} | Total posts: {report['total_posts']}\n"
        f"Collected: {report['collected']} | Written: {report['written']} | "
        f"Below-bar drafts queued: {report['pending_review']} (auto-purged after 5 days) | "
        f"Rejected: {report['rejected']}\n"
        f"Inbox backlog: {report['inbox_backlog']}"
    )
    if "llm_budget_left" in report:
        digest += f" | LLM budget left today: {report['llm_budget_left']}"
    if report.get("quota_override"):
        digest += f"\nWARNING: {report['quota_override']}"
    log(digest)
    send(digest)


if __name__ == "__main__":
    run()
