"""Sync on-site (Worker/KV) comments back into the repo before a build.

The Cloudflare Worker stores comments in KV as readers post them from the browser. So the
static pages can show those comments even to visitors with JS disabled, this script pulls
them down into data/comments/<slug>.json (the same format scripts/comments.py renders),
merging with any Telegram-origin comments already stored there.

It is a no-op unless comments.api_url is configured (i.e. the Worker is deployed), so it is
always safe to run in the pipeline.

Run: python3 scripts/sync_comments.py
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import requests

from common import POSTS, load_config, log, load_state
from comments import COMMENTS_DIR, load_comments, save_comments


def fetch_worker_comments(api: str, slug: str, timeout: int = 20) -> list:
    try:
        r = requests.get(f"{api.rstrip('/')}/comments", params={"slug": slug}, timeout=timeout,
                         headers={"Accept": "application/json"})
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get("ok"):
            return []
        return data.get("comments", []) or []
    except Exception as e:
        log(f"Sync: fetch failed for {slug[:40]}: {e}")
        return []


def run() -> int:
    """Returns the number of NEW comments pulled from the worker (0 = nothing changed)."""
    cfg = load_config()
    api = str(cfg.get("comments", {}).get("api_url", "") or "").strip()
    if not api:
        return 0  # worker not deployed — Telegram comments only, nothing to sync

    published = []
    for p in sorted(POSTS.glob("*.md"), reverse=True):
        try:
            m = re.match(r"^---\n(.*?)\n---", p.read_text(encoding="utf-8"), re.DOTALL)
            front = json.loads(m.group(1)) if m else {}
            if front.get("status") == "published":
                published.append(p.stem)
        except Exception:
            continue

    new_total = 0
    for slug in published:
        remote = fetch_worker_comments(api, slug)
        if not remote:
            continue
        existing = load_comments(slug)
        seen = {c.get("id") for c in existing}
        added = 0
        for c in remote:
            cid = c.get("id")
            if not cid or cid in seen:
                continue
            # normalise into the same schema comments.py uses; hash the uid so the public
            # repo never stores a raw email
            uid = c.get("uid_key") or c.get("name") or "anon"
            digest = hashlib.md5(f"sync:{uid}:{cid}".encode()).hexdigest()[:24]
            existing.append({
                "id": cid, "uid": digest, "name": (c.get("name") or "Reader")[:32],
                "text": (c.get("text") or "")[:800], "ts": c.get("ts") or
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status": "approved", "reason": "", "source": "site",
            })
            seen.add(cid)
            added += 1
        if added:
            save_comments(slug, existing)
            new_total += added
            log(f"Sync: +{added} comment(s) from site for {slug[:44]}")

    if new_total:
        log(f"Sync: {new_total} new on-site comment(s) merged into the repo.")
    return new_total


if __name__ == "__main__":
    import sys
    n = run()
    # exit 10 signals the pipeline that a rebuild is worthwhile
    sys.exit(10 if n else 0)
