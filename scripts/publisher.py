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
    return tpl


def ad_html(cfg: dict, slot: str) -> str:
    mon = cfg.get("monetization", {})
    if not mon.get("adsense_enabled") or not mon.get(slot):
        return ""
    return (f'<div class="ad-slot"><!-- AdSense {slot} -->\n'
            f'<ins class="adsbygoogle" data-ad-client="{mon["adsense_client"]}" '
            f'data-ad-slot="{mon[slot]}"></ins></div>')


def build_site(cfg: dict) -> None:
    site_cfg = cfg["site"]
    base = site_cfg["url"].rstrip("/") + "/"
    posts = load_posts()

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "posts").mkdir(parents=True)
    shutil.copytree(ROOT / "static", SITE, dirs_exist_ok=True)

    disclosure = ""
    if cfg.get("seo", {}).get("ai_disclosure", True):
        disclosure = ("<p class=\"disclosure\">Articles on this site are AI-assisted from public "
                      "news sources, edited and fact-checked against those sources, and are "
                      "provided for information only — this is not financial advice.</p>")

    # --- individual posts ---
    for post in posts:
        html_body = md_lib.markdown(post["_body"], extensions=["tables", "toc"])
        tags = "".join(f'<span class="tag">{escape(t)}</span>' for t in post.get("tags", []))
        page = render_template(
            "post.html",
            SITE_NAME=site_cfg["name"], BASE=base,
            TITLE=escape(post.get("title", "")),
            DESCRIPTION=escape(post.get("description", "")),
            TAGS=tags, DATE=post.get("date", "")[:10],
            AUTHOR=escape(site_cfg.get("author", "")),
            BODY=html_body,
            AD_TOP=ad_html(cfg, "ad_slot_top"), AD_BOTTOM=ad_html(cfg, "ad_slot_bottom"),
            DISCLOSURE=disclosure,
            CANONICAL=f'{base}posts/{post["_stem"]}.html',
        )
        (SITE / "posts" / f"{post['_stem']}.html").write_text(page, encoding="utf-8")

    # --- index with pagination ---
    per_page = 12
    pages = max(1, (len(posts) + per_page - 1) // per_page)
    for page_no in range(pages):
        chunk = posts[page_no * per_page:(page_no + 1) * per_page]
        cards = "".join(
            render_template("card.html",
                            BASE=base, STEM=p["_stem"], TITLE=escape(p.get("title", "")),
                            DATE=p.get("date", "")[:10],
                            DESCRIPTION=escape(p.get("description", "")))
            for p in chunk
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
            TAGLINE=escape(site_cfg.get("tagline", "")), CARDS=cards,
            PAGINATION=f'<div class="pagination">{older}{newer}</div>',
            DISCLOSURE=disclosure), encoding="utf-8")

    # --- archive ---
    archive_items = "".join(
        f'<li><time>{p.get("date","")[:10]}</time> <a href="{base}posts/{p["_stem"]}.html">{escape(p.get("title",""))}</a></li>'
        for p in posts)
    (SITE / "archive.html").write_text(render_template(
        "page.html", SITE_NAME=site_cfg["name"], BASE=base, TITLE="Archive",
        BODY=f'<h1>All articles</h1><ul class="archive">{archive_items}</ul>',
        DISCLOSURE=disclosure), encoding="utf-8")

    # --- static pages from content/pages ---
    for p in (CONTENT / "pages").glob("*.md"):
        front, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        html_body = md_lib.markdown(body)
        (SITE / f"{p.stem}.html").write_text(render_template(
            "page.html", SITE_NAME=site_cfg["name"], BASE=base,
            TITLE=escape(front.get("title", p.stem.title())), BODY=html_body,
            DISCLOSURE=""), encoding="utf-8")

    # --- RSS of our own site ---
    items = "".join(
        f"<item><title>{escape(p.get('title',''))}</title>"
        f"<link>{base}posts/{p['_stem']}.html</link>"
        f"<guid>{base}posts/{p['_stem']}.html</guid>"
        f"<pubDate>{p.get('date','')}</pubDate>"
        f"<description>{escape(p.get('description',''))}</description></item>"
        for p in posts[:50])
    (SITE / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{escape(site_cfg['name'])}</title><link>{base}</link>"
        f"<description>{escape(site_cfg.get('tagline',''))}</description>{items}"
        "</channel></rss>", encoding="utf-8")

    # --- sitemap.xml ---
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [f"{base}", f"{base}archive.html"] + [f"{base}posts/{p['_stem']}.html" for p in posts]
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{u}</loc><lastmod>{today_str}</lastmod></url>" for u in urls)
        + "</urlset>", encoding="utf-8")

    # --- robots.txt ---
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")

    log(f"Site built: {SITE} ({len(posts)} posts, {pages} index page(s))")


def run(build_only: bool = False) -> None:
    cfg = load_config()
    if not build_only:
        publish_approved(cfg)
    build_site(cfg)


if __name__ == "__main__":
    run(build_only="--build-only" in sys.argv)
