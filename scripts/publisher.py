"""Module 4: publish approved drafts and build the static site.

Steps:
  1. Enforce daily quota (ramp-up schedule protects against scaled-content filters).
  2. Move gate-approved drafts from data/drafts/ to content/posts/, flip status=published.
  3. Build the static site (pure Python + Markdown lib) into site/:
     index.html (latest posts), posts/*.html, archive, about, privacy, RSS, sitemap.xml.
  4. GitHub Actions deploys site/ to Cloudflare Pages / GitHub Pages.

Run: python3 scripts/publisher.py [--build-only]
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import markdown as md_lib

from common import (CONTENT, DRAFTS, POSTS, ROOT, SITE, load_config, log,
                    load_state, save_state)

TEMPLATE_DIR = ROOT / "templates"


def parse_frontmatter(text: str):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        return json.loads(m.group(1)), m.group(2)
    except json.JSONDecodeError:
        return {}, text


def daily_quota(cfg: dict) -> int:
    ramp = cfg["publishing"].get("rampup", {})
    if not ramp.get("enabled"):
        return 999
    launch = ramp.get("launch_date") or ""
    if not launch:
        launch = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ramp["launch_date"] = launch
        cfg["publishing"]["rampup"] = ramp
        import yaml
        (ROOT / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    d0 = datetime.strptime(launch, "%Y-%m-%d").date()
    week = min((datetime.now(timezone.utc).date() - d0).days // 7, len(ramp["week_limits"]) - 1)
    return int(ramp["week_limits"][week])


def publish_approved(cfg: dict) -> list:
    quota = daily_quota(cfg)
    stats = load_state("daily_stats", {})
    key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_published = stats.get(key, {}).get("published", 0)
    room = max(0, quota - today_published)

    approved = sorted(DRAFTS.glob("*.md"))
    if not approved:
        log("No gate-approved drafts waiting.")
    moved = []
    for path in approved:
        if len(moved) >= room:
            log(f"Daily quota reached ({quota}/day, week ramp-up). Rest stay queued for tomorrow.")
            break
        front, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        front["status"] = "published"
        dest = POSTS / path.name
        dest.write_text("---\n" + json.dumps(front, ensure_ascii=False, indent=2) + "\n---\n\n" + body, encoding="utf-8")
        path.unlink()
        report = path.with_suffix(".report.json")
        if report.exists():
            shutil.move(str(report), str(POSTS / report.name))
        moved.append(dest)
        log(f"  Published: {front.get('title', dest.name)}")

    if moved:
        day = stats.setdefault(key, {})
        day["published"] = day.get("published", 0) + len(moved)
        save_state("daily_stats", stats)
    return moved


def load_posts() -> list:
    posts = []
    for p in sorted(POSTS.glob("*.md"), key=lambda x: x.stem, reverse=True):
        front, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        if not front:
            continue
        front["_body"] = body
        front["_stem"] = p.stem
        posts.append(front)
    posts.sort(key=lambda f: f.get("date", ""), reverse=True)
    return posts


def render_template(name: str, **ctx) -> str:
    tpl = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    for k, v in ctx.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    # safety: any placeholder not supplied by the caller renders as empty,
    # never as literal {{TEXT}} on a live page
    return re.sub(r"\{\{[A-Z_]+\}\}", "", tpl)


def social_links(cfg: dict) -> str:
    """Footer social block from site.social: {telegram_channel, twitter, youtube...}.
    Renders nothing when empty, so it stays invisible until configured."""
    social = cfg.get("site", {}).get("social") or {}
    labels = {
        "telegram_channel": ("✈", "Telegram channel"),
        "telegram_chat": ("💬", "Telegram chat"),
        "twitter": ("𝕏", "Follow on X"),
        "youtube": ("▶", "YouTube"),
    }
    links = []
    for key, (icon, label) in labels.items():
        url = str(social.get(key, "")).strip()
        if url:
            links.append(f'<a href="{escape(url)}" target="_blank" rel="noopener" '
                         f'title="{label}"><span>{icon}</span> {label}</a>')
    if not links:
        return ""
    return f'<p class="social">{"".join(links)}</p>'


def ad_html(cfg: dict, slot: str) -> str:
    """Ad slot renderer supporting two mechanisms:
    1. AdSense: monetization.adsense_enabled + adsense_client + ad_slot_* ids
    2. Custom HTML (any other network — e.g. adsterra, propellerads, coinzilla):
       monetization.custom_html.<slot> inserted verbatim inside the ad-slot div.
    """
    mon = cfg.get("monetization", {})
    out = ""
    if mon.get("adsense_enabled") and mon.get("adsense_client") and mon.get(slot):
        out += (f'<!-- AdSense {slot} -->\n'
                f'<ins class="adsbygoogle" style="display:block" '
                f'data-ad-client="{mon["adsense_client"]}" data-ad-slot="{mon[slot]}" '
                f'data-ad-format="auto" data-full-width-responsive="true"></ins>\n'
                f'<script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>')
    custom = (mon.get("custom_html") or {}).get(slot, "").strip()
    if custom:
        out += f"<!-- custom ad {slot} -->\n{custom}"
    if not out:
        return ""
    return f'<div class="ad-slot" aria-label="Advertisement">\n{out}\n</div>'


def ads_head_html(cfg: dict) -> str:
    """Head snippet: AdSense loader + any custom head code (verification meta tags etc.)."""
    mon = cfg.get("monetization", {})
    parts = []
    if mon.get("adsense_enabled") and mon.get("adsense_client"):
        parts.append(
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
            f'?client={mon["adsense_client"]}" crossorigin="anonymous"></script>')
    head_custom = (mon.get("custom_html") or {}).get("head", "").strip()
    if head_custom:
        parts.append(head_custom)
    # Google Search Console token (legacy single-value key)
    verif = str(mon.get("site_verification", "")).strip()
    if verif:
        parts.append(f'<meta name="google-site-verification" content="{verif}">')
    # Any number of verification metas: monetization.verifications: {name: token}
    # e.g. {"google-site-verification": "...", "msvalidate.01": "...", "yandex-verification": "..."}
    for name, token in (mon.get("verifications") or {}).items():
        token = str(token).strip()
        if token and name:
            parts.append(f'<meta name="{escape(name)}" content="{escape(token)}">')
    return "\n".join(parts)


def build_ads_txt(cfg: dict) -> str:
    """ads.txt for ad-network verification (required by most networks)."""
    mon = cfg.get("monetization", {})
    lines = ["# CoinPulse ads.txt — add rows as networks approve the site"]
    if mon.get("adsense_enabled") and mon.get("adsense_client"):
        lines.append(f"google.com, {mon['adsense_client'].replace('ca-pub-', 'pub-')}, DIRECT, f08c47fec0942fa0")
    extra = mon.get("ads_txt_lines") or []
    lines.extend(extra)
    return "\n".join(lines) + "\n"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "tag"


def build_tag_pages(cfg: dict, base: str, posts: list) -> list:
    """Tag hub pages: /tags/<tag>.html listing all articles with that tag.
    Returns list of tag slugs (for sitemap)."""
    by_tag: dict = {}
    for p in posts:
        for t in p.get("tags", []):
            by_tag.setdefault(slugify(t), []).append((t, p))
    tags_dir = SITE / "tags"
    tags_dir.mkdir(exist_ok=True)
    for tag_slug, entries in by_tag.items():
        display = entries[0][0]
        items = []
        for _, p in sorted(entries, key=lambda e: e[1].get("date", ""), reverse=True):
            img = (f'<img src="{base}{p["image"]}" alt="" loading="lazy">'
                   if p.get("image") else '<div class="thumb-fallback"></div>')
            items.append(
                f'<a class="card" href="{base}posts/{p["_stem"]}.html">'
                f'<div class="thumb">{img}</div>'
                f'<div class="card-body"><h2>{escape(p.get("title",""))}</h2>'
                f'<p>{escape(p.get("description",""))}</p>'
                f'<p class="card-meta"><time>{p.get("date","")[:10]}</time></p></div></a>')
        page = render_template(
            "page.html", SITE_NAME=cfg["site"]["name"], BASE=base,
            TITLE=f"{escape(display)} News",
            HEAD_EXTRA="",
            SOCIAL=social_links(cfg),
            BODY=(f'<h1 class="tag-page-title">{escape(display)}</h1>'
                  f'<p class="tag-count">{len(items)} article(s)</p>'
                  f'<section class="cards">{"".join(items)}</section>'),
            DISCLOSURE="",
        )
        (tags_dir / f"{tag_slug}.html").write_text(page, encoding="utf-8")
    return sorted(by_tag.keys())


def breadcrumbs(base: str, title: str) -> str:
    return (f'<nav class="breadcrumbs" aria-label="Breadcrumb">'
            f'<a href="{base}">Home</a> <span>›</span> '
            f'<a href="{base}archive.html">News</a> <span>›</span> '
            f'<span>{escape(title[:70])}</span></nav>')


def reading_time(body_md: str) -> int:
    words = len(re.findall(r"\w+", body_md))
    return max(1, round(words / 220))


def build_toc(html_body: str) -> str:
    """Table of contents from rendered H2s (markdown 'toc' ext already set ids)."""
    heads = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', html_body, re.DOTALL)
    heads = [(hid, re.sub(r"<[^>]+>", "", txt).strip()) for hid, txt in heads]
    heads = [(hid, txt) for hid, txt in heads if txt.lower() != "sources"]
    if len(heads) < 2:
        return ""
    items = "".join(f'<li><a href="#{hid}">{escape(txt)}</a></li>' for hid, txt in heads)
    return f'<nav class="toc"><strong>In this article</strong><ol>{items}</ol></nav>'


def related_posts(post: dict, all_posts: list, base: str, limit: int = 3) -> str:
    """Tag-overlap ranking, freshest first as tiebreak."""
    my_tags = {t.lower() for t in post.get("tags", [])}
    scored = []
    for p in all_posts:
        if p["_stem"] == post["_stem"]:
            continue
        overlap = len(my_tags & {t.lower() for t in p.get("tags", [])})
        scored.append((overlap, p.get("date", ""), p))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    cards = []
    for _, _, p in scored[:limit]:
        img = p.get("image")
        thumb = (f'<img src="{base}{img}" alt="" loading="lazy">'
                 if img else '<div class="thumb-fallback"></div>')
        cards.append(
            f'<a class="related-card" href="{base}posts/{p["_stem"]}.html">{thumb}'
            f'<span>{escape(p.get("title", ""))}</span></a>')
    if not cards:
        return ""
    return f'<aside class="related"><h3>Keep reading</h3><div class="related-grid">{"".join(cards)}</div></aside>'


def share_buttons(post: dict, base: str, stem: str) -> str:
    from urllib.parse import quote
    url = f"{base}posts/{stem}.html"
    title = quote(post.get("title", ""))
    u = quote(url, safe="")
    return (
        '<div class="share">'
        '<span>Share:</span>'
        f'<a class="x" href="https://twitter.com/intent/tweet?text={title}&url={u}" target="_blank" rel="noopener" title="Share on X">𝕏</a>'
        f'<a class="tg" href="https://t.me/share/url?url={u}&text={title}" target="_blank" rel="noopener" title="Share on Telegram">✈</a>'
        f'<a class="rd" href="https://www.reddit.com/submit?url={u}&title={title}" target="_blank" rel="noopener" title="Share on Reddit">R</a>'
        f'<a class="wa" href="https://api.whatsapp.com/send?text={title}%20{u}" target="_blank" rel="noopener" title="Share on WhatsApp">W</a>'
        f'<a class="cp" href="#" data-copy="{escape(url)}" title="Copy link">⧉</a>'
        "</div>")


def build_site(cfg: dict) -> None:
    site_cfg = cfg["site"]
    base = site_cfg["url"].rstrip("/") + "/"
    posts = load_posts()

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "posts").mkdir(parents=True)
    shutil.copytree(ROOT / "static", SITE, dirs_exist_ok=True)

    # copy article images into site/img/
    src_images = CONTENT / "images"
    if src_images.exists():
        shutil.copytree(src_images, SITE / "img", dirs_exist_ok=True)
    # ensure a default OG image always exists (branding for social shares)
    if not (SITE / "og-default.jpg").exists():
        shutil.copy(ROOT / "static" / "og-default.jpg", SITE / "og-default.jpg")

    disclosure = ""
    if cfg.get("seo", {}).get("ai_disclosure", True):
        disclosure = ("<p class=\"disclosure\">Articles on this site are AI-assisted from public "
                      "news sources, edited and fact-checked against those sources, and are "
                      "provided for information only — this is not financial advice.</p>")

    head_extra = ads_head_html(cfg)
    social = social_links(cfg)

    # --- individual posts ---
    for post in posts:
        body_md = re.sub(r"^\s*#\s+.*\n+", "", post["_body"], count=1)  # title rendered by template
        html_body = md_lib.markdown(body_md, extensions=["tables", "toc"])
        tags = "".join(
            f'<a class="tag" href="{base}tags/{slugify(t)}.html">{escape(t)}</a>'
            for t in post.get("tags", []))
        image = post.get("image", "")
        og_image = f'{base}{image}' if image else f'{base}og-default.jpg'
        hero = (f'<figure class="hero"><img src="{base}{image}" alt="{escape(post.get("title",""))}">'
                f'<figcaption>AI-generated illustration</figcaption></figure>') if image else ""
        rtime = reading_time(post["_body"])
        page = render_template(
            "post.html",
            SITE_NAME=site_cfg["name"], BASE=base,
            TITLE=escape(post.get("title", "")),
            DESCRIPTION=escape(post.get("description", "")),
            TAGS=tags, DATE=post.get("date", "")[:10],
            AUTHOR=escape(site_cfg.get("author", "")),
            READING_TIME=f"{rtime} min read",
            HERO=hero,
            BREADCRUMBS=breadcrumbs(base, post.get("title", "")),
            TOC=build_toc(html_body),
            BODY=html_body,
            RELATED=related_posts(post, posts, base),
            SHARE=share_buttons(post, base, post["_stem"]),
            AD_TOP=ad_html(cfg, "ad_slot_top"), AD_BOTTOM=ad_html(cfg, "ad_slot_bottom"),
            AD_INLINE=ad_html(cfg, "ad_slot_inline"),
            DISCLOSURE=disclosure,
            CANONICAL=f'{base}posts/{post["_stem"]}.html',
            OG_IMAGE=og_image,
            HEAD_EXTRA=head_extra,
            SOCIAL=social,
        )
        (SITE / "posts" / f"{post['_stem']}.html").write_text(page, encoding="utf-8")

    # --- tag hub pages (SEO: topic clusters + internal linking) ---
    tag_slugs = build_tag_pages(cfg, base, posts)

    # --- index with pagination ---
    per_page = 11  # 1 featured + 10 grid
    pages = max(1, (len(posts) + per_page - 1) // per_page)
    for page_no in range(pages):
        chunk = posts[page_no * per_page:(page_no + 1) * per_page]
        featured_html = ""
        grid = chunk
        if page_no == 0 and chunk:
            f = chunk[0]
            grid = chunk[1:]
            fimg = (f'<img src="{base}{f["image"]}" alt="{escape(f.get("title",""))}">'
                    if f.get("image") else '<div class="thumb-fallback"></div>')
            featured_html = render_template(
                "featured.html", BASE=base, STEM=f["_stem"],
                TITLE=escape(f.get("title", "")), DATE=f.get("date", "")[:10],
                READING_TIME=f"{reading_time(f['_body'])} min",
                DESCRIPTION=escape(f.get("description", "")), IMAGE=fimg)
        cards = "".join(
            render_template("card.html",
                            BASE=base, STEM=p["_stem"], TITLE=escape(p.get("title", "")),
                            DATE=p.get("date", "")[:10],
                            READING_TIME=f"{reading_time(p['_body'])} min",
                            TAG=escape((p.get("tags") or ["news"])[0]),
                            IMAGE=(f'<img src="{base}{p["image"]}" alt="" loading="lazy">'
                                   if p.get("image") else '<div class="thumb-fallback"></div>'),
                            DESCRIPTION=escape(p.get("description", "")))
            for p in grid
        )
        older = f'<a href="index{page_no + 1}.html">&larr; Older</a>' if page_no < pages - 1 else ""
        if page_no > 0:
            newer_href = "index.html" if page_no == 1 else f"index{page_no - 1}.html"
            newer = f'<a href="{newer_href}">Newer &rarr;</a>'
        else:
            newer = ""
        fname = "index.html" if page_no == 0 else f"index{page_no}.html"
        (SITE / fname).write_text(render_template(
            "index.html", SITE_NAME=site_cfg["name"], BASE=base,
            TAGLINE=escape(site_cfg.get("tagline", "")),
            FEATURED=featured_html, CARDS=cards,
            PAGINATION=f'<div class="pagination">{older}{newer}</div>',
            AD_MID=ad_html(cfg, "ad_slot_index"),
            TICKER='<div class="ticker" id="ticker" aria-hidden="true"><div class="ticker-track" id="ticker-track"></div></div>',
            DISCLOSURE=disclosure,
            HEAD_EXTRA=head_extra,
            SOCIAL=social), encoding="utf-8")

    # --- archive ---
    archive_items = "".join(
        f'<li><time>{p.get("date","")[:10]}</time> <a href="{base}posts/{p["_stem"]}.html">{escape(p.get("title",""))}</a></li>'
        for p in posts)
    tag_cloud = "".join(
        f'<a class="tag" href="{base}tags/{ts}.html">{escape(ts.replace("-", " "))}</a>'
        for ts in tag_slugs)
    (SITE / "archive.html").write_text(render_template(
        "page.html", SITE_NAME=site_cfg["name"], BASE=base, TITLE="Archive",
        BODY=(f'<h1>All articles</h1><ul class="archive">{archive_items}</ul>'
              f'<h2 class="tag-cloud-title">Browse by topic</h2><p class="tag-cloud">{tag_cloud}</p>'),
        DISCLOSURE=disclosure, HEAD_EXTRA=head_extra, SOCIAL=social), encoding="utf-8")

    # --- static pages from content/pages ---
    for p in (CONTENT / "pages").glob("*.md"):
        front, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        html_body = md_lib.markdown(body)
        (SITE / f"{p.stem}.html").write_text(render_template(
            "page.html", SITE_NAME=site_cfg["name"], BASE=base,
            TITLE=escape(front.get("title", p.stem.title())), BODY=html_body,
            DISCLOSURE="", HEAD_EXTRA=head_extra, SOCIAL=social), encoding="utf-8")

    # --- RSS of our own site (styled for humans via feed.xsl) ---
    def rfc822(iso: str) -> str:
        try:
            dt = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        except Exception:
            return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    items = "".join(
        f"<item><title>{escape(p.get('title',''))}</title>"
        f"<link>{base}posts/{p['_stem']}.html</link>"
        f"<guid>{base}posts/{p['_stem']}.html</guid>"
        f"<pubDate>{rfc822(p.get('date',''))}</pubDate>"
        f"<description>{escape(p.get('description',''))}</description>"
        + (f'<enclosure url="{base}{p["image"]}" '
           f'type="{"image/svg+xml" if p["image"].endswith(".svg") else "image/jpeg"}" length="0"/>'
           if p.get('image') else "")
        + "</item>"
        for p in posts[:50])
    (SITE / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<?xml-stylesheet type="text/xsl" href="{base}feed.xsl"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{escape(site_cfg['name'])}</title><link>{base}</link>"
        f'<atom:link href="{base}feed.xml" rel="self" type="application/rss+xml"/>'
        f"<description>{escape(site_cfg.get('tagline',''))}</description>{items}"
        "</channel></rss>", encoding="utf-8")

    # --- sitemap.xml ---
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = ([f"{base}", f"{base}archive.html"]
            + [f"{base}posts/{p['_stem']}.html" for p in posts]
            + [f"{base}tags/{ts}.html" for ts in tag_slugs])
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{u}</loc><lastmod>{today_str}</lastmod></url>" for u in urls)
        + "</urlset>", encoding="utf-8")

    # --- robots.txt ---
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")

    # --- ads.txt (ad-network verification) ---
    (SITE / "ads.txt").write_text(build_ads_txt(cfg), encoding="utf-8")

    # --- llms.txt (AI search engines: ChatGPT, Perplexity, Copilot discoverability) ---
    llms_lines = [
        f"# {site_cfg['name']}",
        "",
        f"> {site_cfg.get('tagline', 'Crypto and finance news with context.')}",
        "",
        f"Fresh English-language crypto & finance news articles, AI-assisted from public",
        f"sources and fact-checked against them. Published multiple times per day.",
        "",
        "## Latest articles",
        "",
    ]
    for p in posts[:40]:
        llms_lines.append(
            f"- [{p.get('title','')}]({base}posts/{p['_stem']}.html): "
            f"{(p.get('description','') or '')[:140]}")
    llms_lines += ["", "## Topics", ""]
    for ts in tag_slugs[:40]:
        llms_lines.append(f"- [{ts.replace('-',' ').title()}]({base}tags/{ts}.html)")
    (SITE / "llms.txt").write_text("\n".join(llms_lines) + "\n", encoding="utf-8")

    # --- custom 404 (GitHub Pages serves 404.html automatically) ---
    latest_cards = "".join(
        f'<li><a href="{base}posts/{p["_stem"]}.html">{escape(p.get("title",""))}</a>'
        f' <time>{p.get("date","")[:10]}</time></li>'
        for p in posts[:8])
    (SITE / "404.html").write_text(render_template(
        "page.html", SITE_NAME=site_cfg["name"], BASE=base, TITLE="Page not found",
        HEAD_EXTRA=head_extra,
        BODY=('<div class="nf"><h1 class="nf-code">404</h1>'
              '<p class="nf-text">This page does not exist (or was renamed).</p>'
              f'<h2>Latest articles</h2><ul class="archive">{latest_cards}</ul>'
              f'<p><a class="nf-home" href="{base}">&larr; Back to homepage</a></p></div>'),
        DISCLOSURE="", SOCIAL=social), encoding="utf-8")

    # --- IndexNow key file (instant indexing) ---
    # MUST be generated here (pre-deploy) so it ships with the site; promote.py
    # later references the same key from state when submitting URLs.
    key = load_state("indexnow_key", None)
    if not key:
        import secrets
        key = secrets.token_hex(16)
        save_state("indexnow_key", key)
        log(f"Generated IndexNow key: {key}")
    (SITE / f"{key}.txt").write_text(key, encoding="utf-8")

    log(f"Site built: {SITE} ({len(posts)} posts, {len(tag_slugs)} tag pages, {pages} index page(s))")


def run(build_only: bool = False) -> None:
    cfg = load_config()
    if not build_only:
        publish_approved(cfg)
    build_site(cfg)


if __name__ == "__main__":
    run(build_only="--build-only" in sys.argv)
