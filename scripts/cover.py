"""Branded raster cover: 1200x675 JPEG for articles and channel posts.

Deterministic per slug. Used as the primary hero when AI (Pollinations) is unavailable,
and — unlike SVG — it renders correctly as og:image in Telegram/X/Facebook and works
with Telegram's sendPhoto.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from graphics import (candlesticks, coin_motif, draw_pill, find_font, gradient_bg,
                      left_shade, palette_for, rng_for, save_jpg, wrap_px)

W, H = 1200, 675


def branded_cover(title: str, tag: str, slug: str, dest, accent_word: str = "COINPULSE") -> int:
    bg, mid, accent, h = palette_for(slug)
    rng = rng_for(h)
    img = gradient_bg(W, H, bg, mid, accent)
    d = ImageDraw.Draw(img, "RGBA")

    # decorative motif (right half) — soft circles + candles + coin
    for i in range(6):
        cx = int(620 + rng(i * 3) * 560)
        cy = int(40 + rng(i * 3 + 1) * 600)
        r = int(30 + rng(i * 3 + 2) * 110)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (18,), outline=accent + (26,))
    candlesticks(d, W, H, accent, h, x_start=0.60, n=8)
    coin_motif(d, 1010, 520, 118, accent, width=9)

    # legibility shade behind the left text column
    img = left_shade(img, strength=0.66)
    d = ImageDraw.Draw(img, "RGBA")

    # masthead pill
    pill_font = find_font(True, 20)
    draw_pill(d, (66, 50), accent_word, pill_font, fill=accent, text_fill=(12, 15, 22))

    # category tag pill
    tag_txt = (tag or "CRYPTO").upper()[:20]
    tag_font = find_font(True, 18)
    draw_pill(d, (66, 150), tag_txt, tag_font,
              fill=accent + (40,), text_fill=accent)

    # headline
    title_font = find_font(True, 50)
    lines = wrap_px(d, title, title_font, max_w=W - 470, max_lines=4)
    ty = 232
    for ln in lines:
        # subtle shadow then text
        d.text((68, ty + 2), ln, font=title_font, fill=(0, 0, 0, 150))
        d.text((66, ty), ln, font=title_font, fill=(244, 246, 252))
        ty += 62

    # accent underline
    d.rounded_rectangle([66, H - 74, 166, H - 66], radius=4, fill=accent)
    return save_jpg(img, dest, quality=86)


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "Bitcoin Holds Key Support as Traders Watch the Fed"
    tag = sys.argv[2] if len(sys.argv) > 2 else "Bitcoin"
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/cover_demo.jpg"
    size = branded_cover(t, tag, out.replace(".jpg", ""), out)
    print(f"cover written: {out} ({size//1024} KB)")
