/**
 * CoinPulse API Worker — email sign-in (OTP) + on-site comments for a STATIC site.
 *
 * Runs on Cloudflare Workers free tier (100k req/day) with a KV namespace for storage.
 * The static GitHub Pages / pages.dev site calls this API from the browser; comments and
 * member lists are ALSO synced to data/ by scripts/sync_comments.py at build time so the
 * pages stay correct even for visitors with JS disabled.
 *
 * Email delivery uses Resend (free: 100/day, 3k/mo). Swap sendEmail() for Mailgun/Brevo/
 * SendGrid by changing that one function. RESEND_API_KEY is a Worker secret.
 *
 * Endpoints (all JSON, CORS-enabled for the site origin):
 *   POST /auth/send-code   {email}                -> {ok}            emails a 6-digit code
 *   POST /auth/verify      {email, code}          -> {ok, token}     returns a signed session
 *   GET  /me               (Bearer token)         -> {email, name, joined, comments}
 *   POST /comments         {slug, text} + Bearer   -> {ok, id}        moderated + stored
 *   GET  /comments?slug=   -> [{id,name,text,ts}]  (approved only, public)
 *   GET  /members/count    -> {count}              (public, for a footer badge)
 *
 * Security:
 *   - Session token = base64url(email) + "." + HMAC_SHA256(email, SECRET). No server-side
 *     session store needed; tokens are self-verifying and can be revoked by rotating SECRET.
 *   - Codes: 6 digits, 10-min TTL, max 5 attempts, per-email + per-IP rate limits.
 *   - Comment moderation mirrors scripts/comments.py (spam/profanity/limits) server-side.
 *   - Raw emails are NEVER returned to the client; comment author shows first-name + initial.
 *
 * Deploy (owner, one time — see ИНСТРУКЦИЯ.md Этап 4.7):
 *   npm i -g wrangler
 *   wrangler login
 *   wrangler kv:namespace create COMMENTS            # put the id into wrangler.toml
 *   wrangler secret put RESEND_API_KEY
 *   wrangler secret put SESSION_SECRET
 *   wrangler deploy
 *   Then tell the agent:  "воркер: https://coinpulse-api.<you>.workers.dev"
 */

const CODE_TTL_SEC = 600;          // 10 minutes
const CODE_MAX_ATTEMPTS = 5;
const CODE_SEND_COOLDOWN_SEC = 90; // min gap between codes to the same email
const DAY = 86400;
const MAX_PER_USER_DAY = 5;
const MIN_INTERVAL_SEC = 45;
const MAX_LEN = 800;
const MIN_LEN = 2;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

// --- moderation (kept in sync with scripts/comments.py) ---
const SPAM_PATTERNS = [
  /whats\s?app/i, /telegram\.me\//i, /t\.me\//i, /join (my|our) (channel|group)/i,
  /signal.{0,20}(pump|profit|guarantee)/i, /guaranteed (profit|return|income)/i,
  /earn (\$|usd|\d)/i, /passive income/i, /\d{3,}%/, /invest(ment)? (plan|scheme)/i,
  /mining (platform|pool).{0,20}(profit|return)/i, /forex (signal|broker)/i,
  /casino/i, /porno?/i, /onlyfans/i, /viagra/i,
  /click here.{0,20}(win|earn|get free)/i, /airdrop.{0,20}(free|claim)/i,
  /\bdm me\b/i, /write me (on|in) (telegram|whatsapp)/i,
];
const PROFANITY = /(fuck|shit|bitch|asshole|nigger|faggot|cunt|хуй|пизд|ебат|бляд|сука|мудак|говн)/i;

// ---------- helpers ----------
function b64url(bytes) {
  let s = "";
  const arr = bytes instanceof Uint8Array ? bytes : new TextEncoder().encode(bytes);
  for (const b of arr) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function fromB64url(str) {
  str = str.replace(/-/g, "+").replace(/_/g, "/");
  while (str.length % 4) str += "=";
  const bin = atob(str);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}
async function hmac(key, msg) {
  const k = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg)));
}
async function signToken(email, secret) {
  const sig = await hmac(secret, email);
  return b64url(email) + "." + b64url(sig);
}
async function verifyToken(token, secret) {
  if (!token || !token.includes(".")) return null;
  const [e, s] = token.split(".");
  let email;
  try { email = new TextDecoder().decode(fromB64url(e)); } catch (_) { return null; }
  if (!EMAIL_RE.test(email)) return null;
  const expect = b64url(await hmac(secret, email));
  // constant-time-ish compare
  if (s.length !== expect.length) return null;
  let diff = 0;
  for (let i = 0; i < s.length; i++) diff |= s.charCodeAt(i) ^ expect.charCodeAt(i);
  return diff === 0 ? email : null;
}
function json(obj, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extraHeaders },
  });
}
function displayName(email) {
  const local = email.split("@")[0].replace(/[._-]+/g, " ").trim();
  const name = local ? local.charAt(0).toUpperCase() + local.slice(1) : "Reader";
  return name.slice(0, 32);
}
function userKey(email) { return "user:" + email.toLowerCase(); }
function commentsKey(slug) { return "comments:" + slug; }
function codeKey(email) { return "code:" + email.toLowerCase(); }
function rlKey(scope, id) { return `rl:${scope}:${id}:${Math.floor(Date.now() / DAY)}`; }

async function sendEmail(env, to, code) {
  // Resend free tier. Returns { ok } or { ok:false, error } so the caller can log why.
  // NOTE on `from`: it MUST be an address on a domain verified in Resend. The shared
  // test sender onboarding@resend.dev always works; a custom no-reply@yourdomain only
  // works after that domain is added + verified in Resend (set MAIL_FROM then).
  const from = env.MAIL_FROM || "CoinPulse <onboarding@resend.dev>";
  let res;
  try {
    res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { "Authorization": `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from, to, subject: `${code} is your CoinPulse sign-in code`,
        html: `<div style="font-family:system-ui,Arial,sans-serif;background:#0b0e14;color:#dde1ec;padding:28px;border-radius:14px;max-width:460px">` +
          `<h2 style="color:#f7931a;margin:0 0 8px">CoinPulse</h2>` +
          `<p style="margin:0 0 18px;color:#8a90a3">Your sign-in code is:</p>` +
          `<div style="font-size:34px;font-weight:800;letter-spacing:6px;background:#141824;border:1px solid #242a3c;border-radius:10px;padding:18px;text-align:center;color:#fff">${code}</div>` +
          `<p style="margin:18px 0 0;color:#8a90a3;font-size:13px">It expires in 10 minutes. If you didn't request this, ignore the email.</p>` +
          `</div>`,
      }),
    });
  } catch (e) {
    return { ok: false, error: `fetch_failed: ${e && e.message || e}` };
  }
  if (res.ok) return { ok: true };
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body.message || body.statusCode || JSON.stringify(body).slice(0, 200);
  } catch (_) {}
  return { ok: false, error: detail };
}

async function rateLimited(env, scope, id, limit) {
  const k = rlKey(scope, id);
  const n = parseInt((await env.COMMENTS.get(k)) || "0", 10);
  if (n >= limit) return true;
  await env.COMMENTS.put(k, String(n + 1), { expirationTtl: DAY });
  return false;
}

function moderate(text) {
  const t = (text || "").trim();
  if (t.length < MIN_LEN) return "too_short";
  if (t.length > MAX_LEN) return "too_long";
  const letters = [...t].filter(c => /[a-zа-я]/i.test(c));
  if (letters.length && t.length > 25 &&
      letters.filter(c => c === c.toUpperCase() && c !== c.toLowerCase()).length / letters.length > 0.7)
    return "shouting";
  if ((t.match(/https?:\/\//g) || []).length > 1) return "too_many_links";
  if (PROFANITY.test(t)) return "profanity";
  for (const rx of SPAM_PATTERNS) if (rx.test(t)) return "spam_pattern";
  return "";
}

function bearer(req) {
  const h = req.headers.get("Authorization") || "";
  return h.startsWith("Bearer ") ? h.slice(7).trim() : "";
}

// Which browser origins may call the API. GitHub Pages and Cloudflare Pages hosts are
// always allowed (any subdomain of them), so the comment form keeps working after the
// site moves to a short domain without redeploying the Worker. Extra domains can be
// added through the ALLOWED_ORIGIN var (comma-separated, with or without https://).
const AUTO_HOSTS = ["github.io", "pages.dev"];
function originAllowed(origin, env) {
  if (!origin) return false;
  let host;
  try { host = new URL(origin).hostname.toLowerCase(); } catch (_) { return false; }
  if (AUTO_HOSTS.some(d => host === d || host.endsWith("." + d))) return true;
  return String(env.ALLOWED_ORIGIN || "")
    .split(",").map(s => s.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "").toLowerCase())
    .filter(Boolean)
    .some(d => host === d || host.endsWith("." + d));
}

// ---------- main handler ----------
export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": originAllowed(origin, env) ? origin : "null",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    const secret = env.SESSION_SECRET || "insecure-dev-secret-change-me";
    const ip = req.headers.get("CF-Connecting-IP") || "0.0.0.0";

    try {
      // ---- POST /auth/send-code ----
      if (url.pathname === "/auth/send-code" && req.method === "POST") {
        const { email } = await req.json().catch(() => ({}));
        const e = (email || "").trim().toLowerCase();
        if (!EMAIL_RE.test(e)) return json({ ok: false, error: "invalid_email" }, 400, cors);
        if (!env.RESEND_API_KEY) return json({ ok: false, error: "email_not_configured" }, 503, cors);
        if (await rateLimited(env, "code-email", e, 5))
          return json({ ok: false, error: "rate_limited" }, 429, cors);
        if (await rateLimited(env, "code-ip", ip, 20))
          return json({ ok: false, error: "rate_limited" }, 429, cors);
        // cooldown between codes
        const existing = await env.COMMENTS.get(codeKey(e), "json");
        if (existing && existing.sentAt && Date.now() - existing.sentAt < CODE_SEND_COOLDOWN_SEC * 1000)
          return json({ ok: false, error: "cooldown", retry_in:
            Math.ceil((CODE_SEND_COOLDOWN_SEC * 1000 - (Date.now() - existing.sentAt)) / 1000) }, 429, cors);

        const code = String(Math.floor(100000 + Math.random() * 900000)); // 6 digits
        const sent = await sendEmail(env, e, code);
        if (!sent.ok) {
          // full provider reason goes to `wrangler tail`; the client gets a neutral error
          console.error(`send-code failed for ${e}: ${sent.error}`);
          return json({ ok: false, error: "email_send_failed" }, 502, cors);
        }
        await env.COMMENTS.put(codeKey(e), JSON.stringify({ code, attempts: 0, sentAt: Date.now() }),
                               { expirationTtl: CODE_TTL_SEC });
        return json({ ok: true }, 200, cors);
      }

      // ---- POST /auth/verify ----
      if (url.pathname === "/auth/verify" && req.method === "POST") {
        const { email, code } = await req.json().catch(() => ({}));
        const e = (email || "").trim().toLowerCase();
        if (!EMAIL_RE.test(e)) return json({ ok: false, error: "invalid_email" }, 400, cors);
        const rec = await env.COMMENTS.get(codeKey(e), "json");
        if (!rec) return json({ ok: false, error: "code_expired" }, 400, cors);
        if ((rec.attempts || 0) >= CODE_MAX_ATTEMPTS) {
          await env.COMMENTS.delete(codeKey(e));
          return json({ ok: false, error: "too_many_attempts" }, 429, cors);
        }
        if (String(code || "").trim() !== rec.code) {
          rec.attempts = (rec.attempts || 0) + 1;
          await env.COMMENTS.put(codeKey(e), JSON.stringify(rec), { expirationTtl: CODE_TTL_SEC });
          return json({ ok: false, error: "wrong_code", attempts_left: CODE_MAX_ATTEMPTS - rec.attempts }, 400, cors);
        }
        // success
        await env.COMMENTS.delete(codeKey(e));
        const now = new Date().toISOString();
        const existed = await env.COMMENTS.get(userKey(e), "json");
        const u = existed || { email: e, joined: now, comments: 0 };
        u.last_seen = now;
        await env.COMMENTS.put(userKey(e), JSON.stringify(u));
        if (!existed) {
          // bump the public member counter (best-effort, race-tolerant)
          const n = parseInt((await env.COMMENTS.get("meta:member_count")) || "0", 10);
          await env.COMMENTS.put("meta:member_count", String(n + 1));
        }
        const token = await signToken(e, secret);
        return json({ ok: true, token, name: displayName(e), joined: u.joined }, 200, cors);
      }

      // ---- GET /me ----
      if (url.pathname === "/me" && req.method === "GET") {
        const email = await verifyToken(bearer(req), secret);
        if (!email) return json({ ok: false, error: "unauthorized" }, 401, cors);
        const u = (await env.COMMENTS.get(userKey(email), "json")) || { email, comments: 0 };
        return json({ ok: true, name: displayName(email), joined: u.joined, comments: u.comments || 0 }, 200, cors);
      }

      // ---- POST /comments ----
      if (url.pathname === "/comments" && req.method === "POST") {
        const email = await verifyToken(bearer(req), secret);
        if (!email) return json({ ok: false, error: "unauthorized" }, 401, cors);
        const { slug, text } = await req.json().catch(() => ({}));
        if (!slug || !/^[\w.-]{1,120}$/.test(slug)) return json({ ok: false, error: "bad_slug" }, 400, cors);
        const reason = moderate(text);
        if (reason) return json({ ok: false, error: reason }, 400, cors);
        const uid = email;                                   // internal only, never returned
        if (await rateLimited(env, "cmt-day", uid, MAX_PER_USER_DAY))
          return json({ ok: false, error: "daily_limit" }, 429, cors);
        if (await rateLimited(env, "cmt-ip", ip, 20))
          return json({ ok: false, error: "rate_limited" }, 429, cors);

        const list = (await env.COMMENTS.get(commentsKey(slug), "json")) || [];
        const now = new Date().toISOString();
        const id = b64url(await hmac(secret, uid + now + text.slice(0, 16))).slice(0, 10);
        list.push({ id, name: displayName(email), uid_key: uid.slice(0, 6) + "***", text: text.trim().slice(0, MAX_LEN), ts: now, status: "approved" });
        await env.COMMENTS.put(commentsKey(slug), JSON.stringify(list.slice(-200)));
        const u = (await env.COMMENTS.get(userKey(email), "json")) || { email, comments: 0 };
        u.comments = (u.comments || 0) + 1; u.last_seen = now;
        await env.COMMENTS.put(userKey(email), JSON.stringify(u));
        return json({ ok: true, id, ts: now, name: displayName(email) }, 200, cors);
      }

      // ---- GET /comments?slug= ----
      if (url.pathname === "/comments" && req.method === "GET") {
        const slug = url.searchParams.get("slug") || "";
        if (!slug || !/^[\w.-]{1,120}$/.test(slug)) return json({ ok: false, error: "bad_slug" }, 400, cors);
        const list = (await env.COMMENTS.get(commentsKey(slug), "json")) || [];
        const pub = list.filter(c => c.status === "approved")
                        .map(({ id, name, text, ts }) => ({ id, name, text, ts }));
        return json({ ok: true, comments: pub.slice(-50) }, 200, { ...cors, "Cache-Control": "public, max-age=30" });
      }

      // ---- GET /members/count ----
      if (url.pathname === "/members/count" && req.method === "GET") {
        const n = parseInt((await env.COMMENTS.get("meta:member_count")) || "0", 10);
        return json({ ok: true, count: n }, 200, { ...cors, "Cache-Control": "public, max-age=300" });
      }

      return json({ ok: false, error: "not_found", paths: ["/auth/send-code", "/auth/verify", "/me", "/comments", "/members/count"] }, 404, cors);
    } catch (err) {
      return json({ ok: false, error: "server_error", detail: String(err && err.message || err) }, 500, cors);
    }
  },
};
