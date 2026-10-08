# CoinPulse — automated crypto & finance news site (runs 24/7 for $0)

An automated content pipeline: collects news from RSS → AI writes sourced articles → a quality gate scores them → approved articles publish to a static site → GitHub Actions runs everything on a schedule, forever, for free.

**Niche:** crypto & finance (English) · **Mode:** hybrid (strong articles auto-publish, weak ones wait for your review) · **Budget:** $0/month.

**Live:** https://dikapty.github.io/coinpulse/ · **Repo:** https://github.com/dikapty/coinpulse · **Runs:** 4×/day via GitHub Actions cron (`0 1,7,13,19 * * *` UTC) + daily monitor digest at 20:30 UTC.

```
RSS feeds ──> collector ──> writer (Gemini/Groq) ──> quality-gate ──┬─> content/posts ──> images ──> site/ ──> Pages
  (4x/day)     filter+dedupe    plan→draft→edit      score ≥ 7: auto │      (AI hero + SVG fallback)       │
                                                   score < 7:  ─────┴─> data/drafts_pending/ (you review)  │
                                                                                                          ▼
                                                          promote: IndexNow + PingOMatic + Telegram post
```

## What runs it 24/7 for free

| Piece | Free service | Limits |
|---|---|---|
| Scheduler + compute | **GitHub Actions** (public repo) | Unlimited minutes on public repos |
| Hosting | **GitHub Pages** (built-in) or **Cloudflare Pages** | Unlimited static bandwidth |
| LLM | **Google AI Studio (Gemini)** free tier | **20 requests/day per model** on fresh keys — the pipeline chains ~5 models as a quota pool (~100 req/day total), each article costs ~3 calls, so ~5–8 articles/day is the free ceiling |
| LLM backup | **Groq** free tier | generous rate limits |
| Alerts | **Telegram bot** | free |
| Article images | **Pollinations.ai** (flux) | free, no key; rate-limited → local SVG covers as automatic fallback |
| Price ticker | **CoinGecko public API** | free, 10-min client cache |
| Indexing | **IndexNow** (Bing/Yandex/Naver/Seznam) + **PingOMatic** | free, instant, no account |

---

## Phase 0 — accounts you need (all free, ~20 min)

1. **GitHub** — github.com
2. **Google AI Studio** — https://aistudio.google.com/apikey → create API key (Gemini)
3. *(optional backup)* **Groq** — https://console.groq.com/keys → create API key
4. *(optional alerts)* **Telegram** — message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token; get your chat id from [@userinfobot](https://t.me/userinfobot)

## Phase 1 — local test (before going live)

```bash
cd coinpulse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # paste your GEMINI_API_KEY (and optional keys)

python3 scripts/pipeline.py --dry     # 1. collector only, no LLM: check data/inbox/*.json
python3 scripts/pipeline.py write     # 2. generates drafts into data/drafts/
python3 scripts/pipeline.py gate      # 3. scores drafts → approved stay in data/drafts/
python3 scripts/pipeline.py publish   # 4. moves approved → content/posts/
python3 scripts/pipeline.py build     # 5. renders site/  → open site/index.html in a browser
```

Or one shot: `python3 scripts/pipeline.py`

**Reviewing pending articles** (`data/drafts_pending/`): read the `.md`, fix if needed, move it to `data/drafts/`, delete its `.report.json`, then run `gate` + `publish` again (or edit directly into `content/posts/` with `"status": "published"` in frontmatter and run `build`).

## Phase 2 — deploy 24/7 on GitHub

1. Create a **public** repository (e.g. `coinpulse`) — public = unlimited Actions minutes.
2. Push this project:
   ```bash
   cd coinpulse
   git init && git add -A && git commit -m "CoinPulse init"
   git remote add origin https://github.com/YOURNAME/coinpulse.git
   git push -u origin main
   ```
3. Add secrets: repo **Settings → Secrets and variables → Actions → New repository secret**:
   - `GEMINI_API_KEY` (required)
   - `GROQ_API_KEY` (optional backup)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional)
4. Enable hosting — pick one:
   - **GitHub Pages:** repo Settings → Pages → Source: **GitHub Actions**. Site appears at `https://YOURNAME.github.io/coinpulse/`.
   - **Cloudflare Pages:** dash.cloudflare.com → Workers & Pages → Create → connect the repo → build command `pip install -r requirements.txt && python3 scripts/publisher.py --build-only`, output dir `site`. Site at `https://coinpulse.pages.dev`.
5. Set the real URL in `config.yaml` → `site.url` (with trailing slash), commit & push. This matters for sitemap/canonical/RSS.
6. Actions tab → run **CoinPulse 24/7 Pipeline** manually once (workflow_dispatch) to verify, then the cron (`0 1,7,13,19 * * *` = 4 runs/day) takes over. Daily digest at 20:30 UTC via the monitor workflow.

## Phase 3 — free promotion (already automated)

Everything below runs by itself after each deploy (job `promote` in publish.yml):

- **IndexNow** — new article URLs submitted instantly to Bing, Yandex, Naver, Seznam.
  Key file ships with the site; already-submitted URLs tracked in `data/state/promoted.json`.
- **PingOMatic** — update pings to blog directories on every new publish.
- **Telegram** — each new article posted to your chat with rich OG preview
  (forward those to groups/channels — instant distribution).
- **SEO built into every page** — sitemap.xml (posts + tag hubs), robots.txt,
  canonical, Open Graph + Twitter cards, JSON-LD (NewsArticle + BreadcrumbList),
  breadcrumbs, tag hub pages with internal links, RSS with image enclosures.

### Manual checklist (~20 min, once — biggest ROI)

| Action | Why |
|---|---|
| **Google Search Console**: add URL-prefix property, submit sitemap | Google is still the #1 traffic source. Verification token goes to `monetization.site_verification` — it renders into every page head automatically |
| **Bing Webmaster Tools**: import from GSC | Bing powers ChatGPT/Copilot citations — AI-answer traffic |
| Create **X (Twitter)** + **Telegram channel** for the brand | The share buttons already target X/Telegram/Reddit/WhatsApp |
| Post articles **manually** in relevant subreddits (weekly discussion threads) | Reddit ranks extremely well; automated spam gets banned — human posting doesn't |
| Answer crypto questions on **Quora** with article links where genuinely relevant | Long-tail referral traffic |
| Custom domain (~$10/yr, later) | AdSense approval odds + trust |

## Phase 4 — monetization (after traffic appears)

The site is **revenue-ready from day one** — ads infrastructure is built in, you flip switches:

1. **Display ads.**
   - **Google AdSense** (apply at ~30–50 posts): in `config.yaml`:
     ```yaml
     monetization:
       adsense_enabled: true
       adsense_client: "ca-pub-XXXXXXXX"
       ad_slot_top: "1111111111"
       ad_slot_inline: "3333333333"
       ad_slot_bottom: "2222222222"
       ad_slot_index: "4444444444"
     ```
     Loader script, `<ins>` tags and **ads.txt** are generated automatically.
   - **Crypto-friendly networks with lower thresholds** (Adsterra, Monetag, Coinzilla,
     PropellerAds): paste their raw HTML snippet into `monetization.custom_html.<slot>` —
     renders in the same slots; extra ads.txt rows via `monetization.ads_txt_lines`.
2. **Affiliates** (free to join): exchanges, hardware wallets, VPNs, trading tools.
   Put real links in `config.yaml → affiliates.programs` + `affiliates.enabled: true`.
   Boxes are auto-inserted with mandatory "Affiliate" labels.
3. Realistic expectations: ~$2–8 RPM crypto traffic → 1,000 visits/day ≈ $60–240/month;
   first cents typically in month 1–3. Nobody can promise daily income from day one —
   anyone who does is selling a course.

## Quality & platform-compliance safeguards (built in)

- **No fabricated facts** — writer prompts forbid invented numbers/quotes; every article must cite source links (`## Sources`).
- **Uniqueness gate** — 4-gram similarity check against all published posts (≥85% unique), so no self-plagiarism.
- **Banned-topic filter** — advice, guaranteed returns, gambling etc. rejected before writing and again at the gate.
- **Clickbait filter** — hype-word blacklist.
- **LLM editorial score** — facts/usefulness/readability/neutrality, ≥7.0 to auto-publish (hybrid mode).
- **Ramp-up schedule** — 2 articles/day in weeks 1–2 → 4 → 8 max (Google's scaled-content-abuse policy punishes sudden floods).
- **Adaptive throttle** — if rejections spike, the monitor halves the next day's quota automatically.
- **AI disclosure** — site-wide footer + About page; required by most ad networks.
- **"Not financial advice"** — enforced in every article (YMYL niche requirement).

## Repository layout

```
config.yaml              # ALL settings: sources, quota, LLM, monetization, quality bar
scripts/
  pipeline.py            # CLI entry point (what cron runs)
  collector.py           # RSS → filter → dedupe (simhash) → data/inbox/
  writer.py              # plan → draft → self-edit via LLM → data/drafts/
  quality_gate.py        # deterministic checks + LLM scoring → publish/pending/reject
  publisher.py           # quota, posts, static site builder → site/
  images.py              # AI hero images (Pollinations) + SVG fallback covers
  promote.py             # IndexNow + PingOMatic + Telegram article posts
  affiliate.py           # labelled affiliate boxes
  monitor.py             # daily health digest + adaptive throttle
  llm.py / notify.py / common.py
.github/workflows/       # publish.yml (4x/day) + monitor.yml (daily)
content/posts/           # published articles (Markdown + JSON frontmatter)
content/pages/           # about.md, privacy.md
data/                    # inbox, drafts, drafts_pending, rejected, state
templates/ + static/     # site HTML/CSS
```

## Tuning

- **Change niche:** edit `sources`, `keywords`, `forbidden_topics` in `config.yaml`.
- **More/fewer articles:** edit `publishing.rampup.week_limits` and cron frequency.
- **Stricter quality:** raise `publishing.min_score` (8–9) or switch `mode: manual` while the site is young.
- **LLM quota exhausted:** the client walks a chain of Gemini models (each has its own 20 req/day free allowance), tracks exhausted models per UTC day, and stops cleanly when the whole pool is spent — the next cron run resumes where it left off.

## Honest limitations

- GitHub cron can be delayed 10–60 min under load — normal, harmless.
- Free Gemini tier has daily caps; at 2–8 articles/day you stay inside them.
- Finance/crypto is YMYL: Google ranks such sites cautiously. The gate's strictness is your friend — 3 excellent articles beat 10 mediocre ones.
- Income is not guaranteed and not immediate. The system guarantees *consistent, compliant output*; traffic and revenue follow quality over months.
