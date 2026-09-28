"""Shared helpers: paths, config loading, logging, state."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional in CI
    pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DATA = ROOT / "data"
INBOX = DATA / "inbox"
DRAFTS = DATA / "drafts"
PUBLISHED = DATA / "published"
REJECTED = DATA / "rejected"
STATE = DATA / "state"
CONTENT = ROOT / "content"
POSTS = CONTENT / "posts"
SITE = ROOT / "site"

for d in (INBOX, DRAFTS, PUBLISHED, REJECTED, STATE, POSTS):
    d.mkdir(parents=True, exist_ok=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today() -> str:
    return now_utc().strftime("%Y-%m-%d")


def log(msg: str) -> None:
    print(f"[{now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state(name: str, default=None):
    p = STATE / f"{name}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default if default is not None else {}
    return default if default is not None else {}


def save_state(name: str, obj) -> None:
    (STATE / f"{name}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def require_env(var: str) -> str:
    val = os.environ.get(var, "").strip()
    if not val:
        log(f"FATAL: environment variable {var} is empty. Set it in .env (local) or GitHub Secrets (CI).")
        sys.exit(1)
    return val
