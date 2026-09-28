"""Hero images for articles — free AI generation via Pollinations + local SVG fallback.

Strategy (cost: $0, robustness: always produces an image):
1. Try Pollinations (free, no key): editorial AI illustration, 16:9, cached per slug.
2. If it fails/rate-limited: generate a deterministic local SVG cover (gradient +
   abstract crypto motif seeded from the article title). The site never ships without
   a hero image; on the next run the SVG posts are retried with AI again.

Frontmatter gets "image": img/<slug>.jpg (AI) or img/<slug>.svg (fallback).

Run: python3 scripts/images.py [slug ...]   (no args = all published posts missing AI images)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time

import requests

from common import CONTENT, POSTS, load_config, log

IMAGES = CONTENT / "images"
IMAGES.mkdir(parents=True, exist_ok=True)

STYLE = ("professional financial news editorial illustration, cinematic lighting, "
         "dark blue and orange palette, modern minimal composition, high detail, "
         "no text, no words, no letters, no watermark")
URL = "https://image.pollinations.ai/prompt/{prompt}"
MAX_AI_PER_RUN = 4  # be gentle with the free community rate limit


def parse_frontmatter(text: str):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        return json.loads(m.group(1)), m.group(2)
    except json.JSONDecodeError:
        return {}, text


def save_frontmatter(path, front: dict, body: str) -> None:
    path.write_text("---\n" + json.dumps(front, ensure_ascii=False, indent=2) + "\n---\n\n" + body.lstrip("\n"),
                    encoding="utf-8")


def image_prompt(front: dict) -> str:
    title = front.get("title", "cryptocurrency market")
    title = re.sub(r"[^A-Za-z0-9 ,%$&.-]", "", title)[:110]
    tags = ", ".join(front.get("tags", [])[:3])
    subject = f"{title}" + (f", {tags}" if tags else "")
    return f"{subject}, {STYLE}"


def gen_ai_image(prompt: str, seed: int, dest, w: int = 1200, h: int = 675, retries: int = 2) -> bool:
    from urllib.parse import quote
    url = URL.format(prompt=quote(prompt))
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params={
                "width": w, "height": h, "nologo": "true", "seed": seed,
                "model": "flux", "referrer": "coinpulse-news",
            }, timeout=75)
            if r.status_code == 200 and len(r.content) > 4000 and r.content[:2] == b"\xff\xd8":
                dest.write_bytes(r.content)
                return True
            log(f"    AI image attempt {attempt}: HTTP {r.status_code}, {len(r.content)} bytes")
        except Exception as e:
            log(f"    AI image attempt {attempt} failed: {e}")
        time.sleep(5 * attempt)
    return False


PALETTES = [
    ("#101828", "#1f3a5f", "#f7931a"),  # navy -> bitcoin orange
    ("#131026", "#3b2a63", "#ff7a45"),  # violet -> ember
    ("#0c1620", "#1c4a52", "#3ddc84"),  # deep teal -> mint
    ("#1a1008", "#5a3410", "#ffc46b"),  # brown-gold
    ("#0e1526", "#26406e", "#7fb3ff"),  # steel blue
]


def gen_svg_cover(front: dict, slug: str, dest) -> None:
    """Deterministic abstract cover: layered gradients + orbiting coin motif."""
    h = int(hashlib.md5(slug.encode()).hexdigest()[:8], 16)
    bg, mid, accent = PALETTES[h % len(PALETTES)]
    rng = lambda n: (h >> n) % 1000 / 1000.0
    w, ht = 1200, 675

    circles = []
    for i in range(7):
        cx = 80 + rng(i * 3) * 1040
        cy = 40 + rng(i * 3 + 1) * 595
        r = 25 + rng(i * 3 + 2) * 95
        op = 0.05 + rng(i * 5) * 0.14
        circles.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="{accent}" opacity="{op:.2f}"/>')

    # candlestick motif (abstract, right side)
    candles = []
    base_y = ht - 90
    for i in range(9):
        x = 640 + i * 58
        up = ((h >> i) & 1) == 1
        bh = 60 + rng(i + 20) * 190
        y = base_y - bh
        col = "#3ddc84" if up else "#ff5c6c"
        candles.append(f'<rect x="{x}" y="{y:.0f}" width="22" height="{bh:.0f}" rx="4" fill="{col}" opacity="0.55"/>')
        candles.append(f'<rect x="{x + 8}" y="{y - 26:.0f}" width="6" height="{bh + 52:.0f}" rx="3" fill="{col}" opacity="0.3"/>')

    # big coin
    coin_x, coin_y, coin_r = 300, ht // 2, 150
    coin = (
        f'<circle cx="{coin_x}" cy="{coin_y}" r="{coin_r}" fill="none" stroke="{accent}" stroke-width="10" opacity="0.9"/>'
        f'<circle cx="{coin_x}" cy="{coin_y}" r="{coin_r - 34}" fill="none" stroke="{accent}" stroke-width="3" opacity="0.5"/>'
        f'<path d="M {coin_x - 40} {coin_y - 62} h 80 M {coin_x - 40} {coin_y + 62} h 80 '
        f'M {coin_x - 55} {coin_y - 20} h 110 a 32 32 0 0 1 0 64 h -110 a 32 32 0 0 1 0 -64 h 130" '
        f'fill="none" stroke="{accent}" stroke-width="12" stroke-linecap="round" opacity="0.9"/>'
    )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{ht}" viewBox="0 0 {w} {ht}">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{bg}"/><stop offset="0.55" stop-color="{mid}"/><stop offset="1" stop-color="{bg}"/>
</linearGradient>
<radialGradient id="glow" cx="0.25" cy="0.5" r="0.6">
<stop offset="0" stop-color="{accent}" stop-opacity="0.22"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/>
</radialGradient>
</defs>
<rect width="{w}" height="{ht}" fill="url(#bg)"/>
<rect width="{w}" height="{ht}" fill="url(#glow)"/>
<g>{''.join(circles)}</g>
<g opacity="0.9">{coin}</g>
<g>{''.join(candles)}</g>
<rect width="{w}" height="{ht}" fill="none" stroke="{accent}" stroke-opacity="0.18" stroke-width="2"/>
</svg>'''
    dest.write_text(svg, encoding="utf-8")


def process_post(path, ai_budget: int) -> tuple:
    """Returns (used_ai: bool, ok: bool)."""
    front, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not front:
        log(f"  ! {path.name}: no frontmatter — skipped")
        return False, False
    slug = path.stem
    jpg = IMAGES / f"{slug}.jpg"
    svg = IMAGES / f"{slug}.svg"
    current = front.get("image", "")

    # already have an AI jpg cached -> done
    if jpg.exists() and current == f"img/{slug}.jpg":
        return False, True

    used_ai = False
    if ai_budget > 0:
        prompt = image_prompt(front)
        seed = int(hashlib.md5(slug.encode()).hexdigest()[:8], 16)
        log(f"  AI hero: {front.get('title','')[:55]}...")
        if gen_ai_image(prompt, seed, jpg):
            front["image"] = f"img/{slug}.jpg"
            save_frontmatter(path, front, body)
            log(f"  + {jpg.name} ({jpg.stat().st_size // 1024} KB)")
            return True, True
        log(f"  ! AI failed — generating local SVG cover instead")

    # SVG fallback (deterministic, always works)
    gen_svg_cover(front, slug, svg)
    front["image"] = f"img/{slug}.svg"
    save_frontmatter(path, front, body)
    log(f"  + {svg.name} (SVG cover)")
    return False, True


def run(slugs: list, force: bool = False) -> None:
    targets = []
    for p in sorted(POSTS.glob("*.md"), reverse=True):
        if not slugs or p.stem in slugs:
            targets.append(p)
    ai_budget = MAX_AI_PER_RUN
    log(f"Image pass: {len(targets)} post(s), AI budget {ai_budget}")
    ok = ai_used = 0
    for p in targets:
        used, good = process_post(p, ai_budget)
        if used:
            ai_budget -= 1
            ai_used += 1
        if good:
            ok += 1
        if used:
            time.sleep(2)  # polite spacing for the free service
    log(f"Images done: {ok}/{len(targets)} ready ({ai_used} AI, rest SVG covers)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(args, force="--force" in sys.argv)
