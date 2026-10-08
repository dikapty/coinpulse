"""Seal a GitHub Actions secret for the coinpulse repo (owner-only helper).

Usage:  .venv/bin/python scripts/_set_secret.py NAME value
Uses the token from ~/.git-credentials. Not part of the pipeline.
"""
import re
import sys
from pathlib import Path

import requests
from nacl import encoding, public

REPO = "dikapty/coinpulse"


def token() -> str:
    txt = Path.home().joinpath(".git-credentials").read_text()
    m = re.search(r"ghp_[A-Za-z0-9]+", txt)
    if not m:
        raise SystemExit("no ghp_ token in ~/.git-credentials")
    return m.group(0)


def seal(name: str, value: str) -> None:
    t = token()
    h = {"Authorization": f"token {t}", "Accept": "application/vnd.github+json"}
    pk = requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key",
                      headers=h, timeout=30)
    pk.raise_for_status()
    key_id = pk.json()["key_id"]
    import base64
    pub = public.PublicKey(pk.json()["key"].encode(), encoding.Base64Encoder)
    sealed = public.SealedBox(pub).encrypt(value.encode("utf-8"))
    r = requests.put(
        f"https://api.github.com/repos/{REPO}/actions/secrets/{name}",
        headers=h, timeout=30,
        json={"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key_id})
    if r.status_code not in (201, 204):
        raise SystemExit(f"failed {r.status_code}: {r.text[:200]}")
    print(f"secret {name} sealed OK")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: _set_secret.py NAME value")
    seal(sys.argv[1], sys.argv[2])
