"""CoinPulse pipeline CLI — the single entry point the 24/7 cron calls.

Stages:
  collect  — fetch & filter RSS news
  write    — generate N article drafts with the LLM
  gate     — quality checks + editorial scoring
  publish  — move approved drafts to content/posts
  affiliate— insert labelled affiliate boxes
  build    — render the static site into site/
  monitor  — health digest to Telegram

Usage:
  python3 scripts/pipeline.py            # full run
  python3 scripts/pipeline.py collect    # single stage
  python3 scripts/pipeline.py --dry      # collect only, no LLM calls
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone

from common import load_config, log, load_state, save_state


def bump_stat(field: str, delta: int) -> None:
    stats = load_state("daily_stats", {})
    key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day = stats.setdefault(key, {})
    day[field] = day.get(field, 0) + delta
    save_state("daily_stats", stats)


def stage_collect():
    from collector import collect
    res = collect()
    bump_stat("collected", res["saved"])
    return res["saved"]


def stage_write(cfg):
    n = articles_per_run(cfg)
    if n <= 0:
        log("Daily quota already filled — skipping writing.")
        return 0
    from writer import run as write_run
    write_run(n)
    return n


def stage_gate():
    from quality_gate import run as gate_run
    results = gate_run()
    approved = sum(1 for r in results if r["decision"] == "publish")
    bump_stat("approved", approved)
    return approved


def stage_publish(cfg):
    from publisher import publish_approved
    moved = publish_approved(cfg)
    return len(moved)


def stage_affiliate():
    from affiliate import run as aff_run
    aff_run()


def stage_images():
    from images import run as img_run
    img_run([])


def stage_build():
    from publisher import build_site
    build_site(load_config())


def stage_monitor():
    from monitor import run as mon_run
    mon_run()


def articles_per_run(cfg) -> int:
    """How many drafts to attempt this run: min(rampup quota left, sensible batch).

    Honors a quality quota-override set by monitor.py (halves the quota for the day
    after a spike in rejections).
    """
    from publisher import daily_quota
    quota = daily_quota(cfg)

    override = load_state("quota_override", {})
    if override:
        from datetime import timedelta
        odate = override.get("date", "")
        try:
            applies = odate and (
                datetime.strptime(odate, "%Y-%m-%d").date()
                >= (datetime.now(timezone.utc) - timedelta(days=1)).date()
            )
        except ValueError:
            applies = False
        if applies:
            quota = max(1, int(quota * override.get("factor", 0.5)))
            log(f"Quota override active ({override.get('reason','')}) -> {quota}/day")

    key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    published = load_state("daily_stats", {}).get(key, {}).get("published", 0)
    written = load_state("daily_stats", {}).get(key, {}).get("written", 0)
    return max(0, min(quota - published, quota - written, 3))


def main(argv):
    cfg = load_config()
    stages = [a for a in argv[1:] if not a.startswith("--")]
    dry = "--dry" in argv
    full = not stages

    try:
        if dry:
            stage_collect()
            log("Dry run complete (no LLM calls).")
            return 0
        if full or "collect" in stages:
            stage_collect()
        if full or "write" in stages:
            stage_write(cfg)
        if full or "gate" in stages:
            stage_gate()
        if full or "publish" in stages:
            stage_publish(cfg)
        if full or "affiliate" in stages:
            stage_affiliate()
        if full or "images" in stages:
            stage_images()
        if full or "build" in stages:
            stage_build()
        if full or "monitor" in stages:
            stage_monitor()
        log("PIPELINE OK")
        return 0
    except Exception as e:
        err = f"CoinPulse pipeline FAILED: {e}\n{traceback.format_exc()[-800:]}"
        log(err)
        try:
            from notify import send
            send(err)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
