"""Hero images for articles — free AI generation via Pollinations + branded raster covers.

Strategy (cost: $0, robustness: always produces an image):
1. Try Pollinations (free, no key): editorial AI illustration, 16:9, cached per slug.
2. If it fails/rate-limited: generate a deterministic branded JPEG cover with Pillow
   (gradient + crypto motif + article headline). Raster (not SVG) because SVG does NOT
   render as og:image in Telegram/X/Facebook and is rejected by Telegram's sendPhoto.
   The site never ships without a hero; AI-jpg posts are never retried, branded covers
   are upgraded to AI art on a later run when the free service recovers.

Frontmatter: "image": img/<slug>.jpg  and  "image_ai": true  only for real AI art.

Run: python3 scripts/images.py [slug ...]   (no args = all published posts)
"""
from __future__ import annotations

import hashlib
import json
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


def branded_cover(front: dict, slug: str, dest) -> int:
    """Raster branded cover via cover.branded_cover (Pillow). Returns byte size."""
    from cover import branded_cover as _bc
    title = front.get("title") or slug.replace("-", " ").title()
    tag = (front.get("tags") or ["crypto"])[0]
    return _bc(title, tag, slug, dest)


def process_post(path, ai_budget: int) -> tuple:
    """Returns (used_ai: bool, ok: bool).

    AI art is tried only while the budget lasts and only for posts that don't already
    have AI art (front['image_ai']). Otherwise a branded raster JPEG is produced, which
    renders everywhere (site hero, og:image, Telegram sendPhoto).
    """
    front, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not front:
        log(f"  ! {path.name}: no frontmatter — skipped")
        return False, False
    slug = path.stem
    jpg = IMAGES / f"{slug}.jpg"
    current = front.get("image", "")

    # already have a raster jpg wired up -> done (no re-generation, no AI spend)
    if jpg.exists() and current == f"img/{slug}.jpg":
        return False, True

    # try AI art only when budget remains AND this post doesn't already have AI art
    if ai_budget > 0 and not front.get("image_ai"):
        prompt = image_prompt(front)
        seed = int(hashlib.md5(slug.encode()).hexdigest()[:8], 16)
        log(f"  AI hero: {front.get('title','')[:55]}...")
        if gen_ai_image(prompt, seed, jpg):
            front["image"] = f"img/{slug}.jpg"
            front["image_ai"] = True
            save_frontmatter(path, front, body)
            log(f"  + {jpg.name} (AI, {jpg.stat().st_size // 1024} KB)")
            return True, True
        log(f"  ! AI failed — generating branded raster cover instead")

    # branded raster cover (deterministic, always works, renders in socials)
    size = branded_cover(front, slug, jpg)
    front["image"] = f"img/{slug}.jpg"
    front.pop("image_ai", None)  # not AI art — allow a later AI upgrade
    save_frontmatter(path, front, body)
    log(f"  + {jpg.name} (branded cover, {size // 1024} KB)")
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
    log(f"Images done: {ok}/{len(targets)} ready ({ai_used} AI, rest branded raster covers)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(args, force="--force" in sys.argv)
