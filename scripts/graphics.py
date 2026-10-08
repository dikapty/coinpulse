"""Shared Pillow graphics helpers — branded covers and channel images.

Everything here is deterministic (seeded by slug) and dependency-light: only Pillow
plus a system TTF font. Raster output (JPEG) is required because Telegram's sendPhoto
and social OG cards (X, Telegram, Facebook, LinkedIn) do not render SVG.
"""
from __future__ import annotations

import hashlib
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PALETTES = [
    ("#101828", "#1f3a5f", "#f7931a"),  # navy -> bitcoin orange
    ("#131026", "#3b2a63", "#ff7a45"),  # violet -> ember
    ("#0c1620", "#1c4a52", "#3ddc84"),  # deep teal -> mint
    ("#1a1008", "#5a3410", "#ffc46b"),  # brown-gold
    ("#0e1526", "#26406e", "#7fb3ff"),  # steel blue
    ("#16101c", "#4a2340", "#ff6b9d"),  # plum -> rose
]

_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",                      # Linux / GitHub runners
    "/usr/share/fonts/truetype/liberation",
    "/System/Library/Fonts/Supplemental",                     # macOS
    "/Library/Fonts",
    "C:/Windows/Fonts",
]
_BOLD_NAMES = ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"]
_REG_NAMES = ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]
_FONT_CACHE: dict = {}


def find_font(bold: bool, size: int):
    """Resolve a usable TTF; fall back to Pillow's built-in bitmap font."""
    key = (bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = _BOLD_NAMES if bold else _REG_NAMES
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    _FONT_CACHE[key] = f
                    return f
                except Exception:
                    continue
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def hexrgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def palette_for(seed: str) -> tuple:
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    bg, mid, accent = PALETTES[h % len(PALETTES)]
    return hexrgb(bg), hexrgb(mid), hexrgb(accent), h


def rng_for(h: int):
    return lambda n: (h >> n) % 1000 / 1000.0


def gradient_bg(w: int, ht: int, bg: tuple, mid: tuple, accent: tuple) -> Image.Image:
    """Diagonal gradient + soft radial glow of the accent colour."""
    img = Image.new("RGB", (w, ht))
    px = img.load()
    for y in range(ht):
        for x in range(0, w, 1):
            t = (x / w * 0.55 + y / ht * 0.45)
            px[x, y] = mix(bg, mid, min(1.0, t * 1.15))
    glow = Image.new("L", (w, ht), 0)
    gd = ImageDraw.Draw(glow)
    r = int(min(w, ht) * 0.75)
    gd.ellipse([w - r, ht - r, w + r // 2, ht + r // 2], fill=190)
    glow = glow.filter(ImageFilter.GaussianBlur(r // 3))
    tint = Image.new("RGB", (w, ht), accent)
    img = Image.composite(tint, img, glow.point(lambda v: int(v * 0.16)))
    return img


def wrap_px(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int = 4) -> list:
    """Word-wrap by real pixel width; ellipsis on overflow."""
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_w and " " in last:
            last = last.rsplit(" ", 1)[0]
        lines[-1] = last.rstrip(",;:") + "…"
    return lines


def draw_pill(draw, xy, text, font, fill, text_fill, pad_x=18, pad_y=9, radius=None):
    """Rounded label; returns the drawn width."""
    x, y = xy
    tw = draw.textlength(text, font=font)
    th = font.size if hasattr(font, "size") else 18
    w = int(tw + pad_x * 2)
    h = int(th + pad_y * 2)
    r = radius if radius is not None else h // 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill)
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=text_fill)
    return w


def candlesticks(draw, w: int, ht: int, accent: tuple, h: int, x_start: float = 0.58,
                 n: int = 8, alpha_layer: Image.Image = None):
    """Abstract green/red candle motif along the bottom-right."""
    rng = rng_for(h)
    base_y = int(ht * 0.92)
    span = w - int(w * x_start)
    step = span // max(1, n)
    up_c, dn_c = (61, 220, 132), (255, 92, 108)
    for i in range(n):
        x = int(w * x_start) + i * step
        up = ((h >> i) & 1) == 1
        bh = int(60 + rng(i + 20) * (ht * 0.42))
        y = base_y - bh
        col = up_c if up else dn_c
        bw = max(10, step // 3)
        draw.rounded_rectangle([x, y, x + bw, base_y], radius=4, fill=col)
        draw.rounded_rectangle([x + bw // 2 - 2, y - 22, x + bw // 2 + 2, base_y + 22],
                               radius=2, fill=col)


def coin_motif(draw, cx: int, cy: int, r: int, accent: tuple, width: int = 9):
    """Stylised coin with a currency-ish glyph (abstract, no letterforms)."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=width)
    draw.ellipse([cx - r + 28, cy - r + 28, cx + r - 28, cy + r - 28],
                 outline=accent, width=max(2, width // 3))
    inner = r - 46
    draw.line([cx - inner, cy - inner // 2, cx + inner, cy - inner // 2], fill=accent, width=width - 1)
    draw.line([cx - inner, cy + inner // 2, cx + inner, cy + inner // 2], fill=accent, width=width - 1)
    draw.arc([cx - inner, cy - inner // 3, cx + inner, cy + inner // 3 + 2],
             start=200, end=520, fill=accent, width=width - 1)


def left_shade(img: Image.Image, strength: float = 0.62) -> Image.Image:
    """Darken the left portion so overlaid text stays legible."""
    w, ht = img.size
    mask = Image.new("L", (w, ht), 0)
    md = ImageDraw.Draw(mask)
    for x in range(w):
        t = max(0.0, 1 - x / (w * 0.72))
        md.line([(x, 0), (x, ht)], fill=int(255 * t * strength))
    dark = Image.new("RGB", (w, ht), (8, 11, 18))
    return Image.composite(dark, img, mask)


def save_jpg(img: Image.Image, dest, quality: int = 86) -> int:
    img.convert("RGB").save(dest, "JPEG", quality=quality, optimize=True)
    return os.path.getsize(dest)


def price_chart(prices: list, dest, w: int = 1200, h: int = 675,
                title: str = "Market snapshot", seed: str = "chart") -> int:
    """Dark branded chart card: 24h-change bars for a list of (symbol, usd, change_pct)."""
    bg, mid, accent, hsh = palette_for(seed)
    img = gradient_bg(w, h, bg, mid, accent)
    img = left_shade(img, strength=0.45)
    d = ImageDraw.Draw(img, "RGBA")

    title_font = find_font(True, 44)
    d.text((56, 42), title, font=title_font, fill=(244, 246, 252))
    sub_font = find_font(False, 22)
    d.text((58, 100), "24h change · live prices from CoinGecko", font=sub_font, fill=(178, 185, 203))

    # bars area
    top, bottom = 180, h - 90
    mid_y = (top + bottom) // 2
    left, right = 150, w - 60
    d.line([(left, mid_y), (right, mid_y)], fill=(255, 255, 255, 90), width=2)

    n = len(prices)
    if n:
        step = (right - left) // n
        sym_font = find_font(True, 26)
        val_font = find_font(True, 24)
        prc_font = find_font(False, 20)
        max_abs = max(abs(p[2]) for p in prices) or 1
        for i, (sym, usd, chg) in enumerate(prices):
            cx = left + step * i + step // 2
            bw = min(step // 2, 64)
            bh = int(abs(chg) / max_abs * ((bottom - top) // 2 - 10))
            col = (61, 220, 132) if chg >= 0 else (255, 92, 108)
            y0 = mid_y - bh if chg >= 0 else mid_y
            d.rounded_rectangle([cx - bw // 2, y0, cx + bw // 2, y0 + bh], radius=6, fill=col)
            # labels
            pct = f"{chg:+.1f}%"
            tw = d.textlength(sym, font=sym_font)
            d.text((cx - tw / 2, bottom + 12), sym, font=sym_font, fill=(244, 246, 252))
            tw = d.textlength(pct, font=val_font)
            ly = (y0 - 34) if chg >= 0 else (y0 + bh + 10)
            d.text((cx - tw / 2, ly), pct, font=val_font, fill=col)
            price = f"${usd:,.0f}" if usd >= 1 else f"${usd:.4f}".rstrip("0")
            tw = d.textlength(price, font=prc_font)
            d.text((cx - tw / 2, bottom + 44), price, font=prc_font, fill=(150, 158, 178))

    # brand pill bottom-left
    pill_font = find_font(True, 18)
    draw_pill(d, (56, h - 64), "COINPULSE", pill_font, fill=accent, text_fill=(12, 15, 22))
    return save_jpg(img, dest, quality=88)


def fear_greed_card(value: int, label: str, dest, w: int = 1000, h: int = 560) -> int:
    """Gauge card for the Crypto Fear & Greed Index."""
    bg, mid, accent, _ = palette_for("feargreed")
    img = gradient_bg(w, h, bg, mid, accent)
    img = left_shade(img, strength=0.4)
    d = ImageDraw.Draw(img, "RGBA")

    title_font = find_font(True, 34)
    d.text((46, 36), "Crypto Fear & Greed Index", font=title_font, fill=(244, 246, 252))

    # semicircular gauge
    cx, cy, r = w // 2, h - 120, 190
    d.arc([cx - r, cy - r, cx + r, cy + r], start=180, end=360, width=34,
          fill=(255, 92, 108))  # red half backdrop
    # color segments: fear red -> greed green over 180°
    seg = 18
    for i in range(seg):
        t = i / seg
        col = mix((255, 92, 108), (61, 220, 132), t)
        a0 = 180 + i * (180 // seg)
        d.arc([cx - r, cy - r, cx + r, cy + r], start=a0, end=a0 + (180 // seg) + 1,
              width=34, fill=col)
    # needle
    import math as _m
    ang = _m.radians(180 + value / 100 * 180)
    nx = cx + int((r - 46) * _m.cos(ang))
    ny = cy - int((r - 46) * _m.sin(ang))
    d.line([(cx, cy), (nx, ny)], fill=(244, 246, 252), width=8)
    d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=(244, 246, 252))

    big_font = find_font(True, 76)
    txt = str(value)
    tw = d.textlength(txt, font=big_font)
    d.text((cx - tw / 2, cy - 150), txt, font=big_font, fill=(244, 246, 252))
    lab_font = find_font(True, 30)
    tw = d.textlength(label.upper(), font=lab_font)
    col = mix((255, 92, 108), (61, 220, 132), value / 100)
    d.text((cx - tw / 2, cy - 66), label.upper(), font=lab_font, fill=col)

    pill_font = find_font(True, 18)
    draw_pill(d, (46, h - 62), "COINPULSE", pill_font, fill=accent, text_fill=(12, 15, 22))
    return save_jpg(img, dest, quality=88)
