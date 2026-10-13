/* CoinPulse email sign-in + on-site comments.
 *
 * Progressive enhancement: the server-rendered section (Telegram CTA + existing
 * comments) works without JS. When window.__CP_API__ is set (the Cloudflare Worker is
 * deployed and configured), this script upgrades the section with an email code sign-in
 * and a comment box that posts straight to the API.
 *
 * No tracking, no third-party scripts. The session token lives in localStorage only.
 */
(function () {
  "use strict";

  var API = (window.__CP_API__ || "").replace(/\/+$/, "");
  var section = document.getElementById("comments");
  if (!section) return;

  var slug = (document.body.getAttribute("data-slug") ||
              (location.pathname.split("/").pop() || "").replace(/\.html$/, ""));
  var TOKEN_KEY = "cp_token";
  var EMAIL_KEY = "cp_email";

  function token() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } }
  function setSession(t, email) {
    try { localStorage.setItem(TOKEN_KEY, t); if (email) localStorage.setItem(EMAIL_KEY, email); } catch (e) {}
  }
  function clearSession() {
    try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EMAIL_KEY); } catch (e) {}
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function el(html) {
    var d = document.createElement("div");
    d.innerHTML = html;
    return d.firstElementChild;
  }
  function relTime(iso) {
    var t = Date.parse(iso);
    if (!t) return "";
    var s = Math.floor((Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    if (s < 604800) return Math.floor(s / 86400) + " d ago";
    return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }
  var COLORS = ["#f7931a", "#3ddc84", "#7fb3ff", "#ff6b9d", "#ffc46b", "#b48cff", "#4dd0e1", "#ff8a65"];
  function colorFor(seed) {
    var h = 0, i;
    for (i = 0; i < String(seed).length; i++) h = (h * 31 + String(seed).charCodeAt(i)) >>> 0;
    return COLORS[h % COLORS.length];
  }
  async function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    var res = await fetch(API + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    var data = {};
    try { data = await res.json(); } catch (e) {}
    return { status: res.status, data: data };
  }

  var MESSAGES = {
    invalid_email: "Please enter a valid email address.",
    rate_limited: "Too many attempts. Please try again later.",
    cooldown: "A code was sent recently. Wait a moment and try again.",
    email_send_failed: "Could not send the email right now. Please try again.",
    email_not_configured: "Email sign-in is not configured on this site yet.",
    code_expired: "That code expired. Send a new one.",
    wrong_code: "Wrong code.",
    too_many_attempts: "Too many wrong codes. Request a new one.",
    unauthorized: "Please sign in first.",
    too_short: "Your comment is too short.",
    too_long: "Comments are limited to 800 characters.",
    shouting: "Please avoid writing in ALL CAPS.",
    too_many_links: "At most one link per comment.",
    profanity: "Please keep the discussion respectful.",
    spam_pattern: "That looks like advertising and was rejected.",
    daily_limit: "You have reached the daily comment limit.",
    bad_slug: "This article cannot accept comments right now.",
  };
  function humanError(code, fallback) {
    return MESSAGES[code] || fallback || "Something went wrong. Please try again.";
  }

  // ---------- comment list ----------
  function renderList(comments) {
    var old = section.querySelector(".cmt-list");
    if (old) old.remove();
    var wrap = el('<div class="cmt-list"></div>');
    if (!comments.length) {
      wrap.appendChild(el('<p class="cmt-empty">No comments yet — be the first to share your take on this story.</p>'));
    } else {
      comments.forEach(function (c) {
        var name = esc(c.name || "Reader");
        var initial = esc((c.name || "R").trim().charAt(0).toUpperCase() || "R");
        var color = colorFor(c.name || c.id || "x");
        var text = esc(c.text || "").replace(/\n/g, "<br>");
        wrap.appendChild(el(
          '<div class="cmt">' +
            '<div class="cmt-avatar" style="background:' + color + '">' + initial + '</div>' +
            '<div class="cmt-body">' +
              '<div class="cmt-meta"><span class="cmt-name">' + name + '</span>' +
              '<span class="cmt-time">' + esc(relTime(c.ts)) + '</span></div>' +
              '<div class="cmt-text">' + text + '</div>' +
            '</div>' +
          '</div>'));
      });
    }
    // insert after the heading / CTA block, before the "powered" note
    var powered = section.querySelector(".cmt-powered");
    if (powered) section.insertBefore(wrap, powered); else section.appendChild(wrap);
    var badge = section.querySelector(".cmt-count");
    if (badge) badge.textContent = String(comments.length);
  }

  async function loadComments() {
    var r = await api("/comments?slug=" + encodeURIComponent(slug));
    if (r.status === 200 && r.data.ok && Array.isArray(r.data.comments)) {
      renderList(r.data.comments);
    }
  }

  // ---------- sign-in / comment box ----------
  function signInForm() {
    var box = el(
      '<div class="cmt-auth">' +
        '<p class="cmt-auth-title">Sign in with email to comment</p>' +
        '<form class="cmt-auth-form" novalidate>' +
          '<input type="email" class="cmt-input" name="email" placeholder="you@example.com" autocomplete="email" required>' +
          '<button type="submit" class="cmt-btn">Send code</button>' +
        '</form>' +
        '<p class="cmt-msg" role="status"></p>' +
        '<p class="cmt-note">No password. We email you a 6-digit code; it expires in 10 minutes. ' +
        'Your email is never shown publicly and never shared.</p>' +
      '</div>');
    var form = box.querySelector("form");
    var msg = box.querySelector(".cmt-msg");
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      msg.textContent = "";
      var email = (form.email.value || "").trim();
      var btn = form.querySelector("button");
      btn.disabled = true; btn.textContent = "Sending…";
      var r = await api("/auth/send-code", { method: "POST", body: { email: email } });
      btn.disabled = false; btn.textContent = "Send code";
      if (r.status === 200 && r.data.ok) {
        try { localStorage.setItem(EMAIL_KEY, email); } catch (e) {}
        showCodeForm(email, box);
      } else {
        msg.className = "cmt-msg err";
        msg.textContent = humanError(r.data.error);
      }
    });
    return box;
  }

  function showCodeForm(email, replaceNode) {
    var box = el(
      '<div class="cmt-auth">' +
        '<p class="cmt-auth-title">Enter the code sent to <b>' + esc(email) + '</b></p>' +
        '<form class="cmt-auth-form" novalidate>' +
          '<input type="text" class="cmt-input cmt-code" name="code" placeholder="6-digit code" inputmode="numeric" ' +
                 'autocomplete="one-time-code" maxlength="6" required>' +
          '<button type="submit" class="cmt-btn">Verify</button>' +
          '<button type="button" class="cmt-btn ghost" name="resend">Resend</button>' +
        '</form>' +
        '<p class="cmt-msg" role="status"></p>' +
      '</div>');
    var form = box.querySelector("form");
    var msg = box.querySelector(".cmt-msg");
    replaceNode.replaceWith(box);
    box.querySelector("input[name=code]").focus();

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      msg.textContent = "";
      var code = (form.code.value || "").trim();
      var btn = form.querySelector("button[type=submit]");
      btn.disabled = true; btn.textContent = "Checking…";
      var r = await api("/auth/verify", { method: "POST", body: { email: email, code: code } });
      btn.disabled = false; btn.textContent = "Verify";
      if (r.status === 200 && r.data.ok) {
        setSession(r.data.token, email);
        box.replaceWith(commentForm(r.data.name));
      } else {
        msg.className = "cmt-msg err";
        var extra = r.data.attempts_left != null ? " Attempts left: " + r.data.attempts_left + "." : "";
        msg.textContent = humanError(r.data.error) + extra;
      }
    });
    form.querySelector("button[name=resend]").addEventListener("click", async function () {
      msg.className = "cmt-msg"; msg.textContent = "Sending a new code…";
      var r = await api("/auth/send-code", { method: "POST", body: { email: email } });
      msg.className = r.data.ok ? "cmt-msg ok" : "cmt-msg err";
      msg.textContent = r.data.ok ? "New code sent." : humanError(r.data.error);
    });
  }

  function commentForm(name) {
    var box = el(
      '<div class="cmt-auth signed">' +
        '<p class="cmt-auth-title">Commenting as <b>' + esc(name || "you") + '</b> ' +
          '<button type="button" class="cmt-link" name="signout">Sign out</button></p>' +
        '<form class="cmt-form" novalidate>' +
          '<textarea class="cmt-input cmt-area" name="text" rows="3" maxlength="800" ' +
                    'placeholder="Share your take on this story…"></textarea>' +
          '<div class="cmt-form-row">' +
            '<span class="cmt-counter">0 / 800</span>' +
            '<button type="submit" class="cmt-btn">Post comment</button>' +
          '</div>' +
        '</form>' +
        '<p class="cmt-msg" role="status"></p>' +
      '</div>');
    var form = box.querySelector("form");
    var area = form.text;
    var counter = box.querySelector(".cmt-counter");
    var msg = box.querySelector(".cmt-msg");
    area.addEventListener("input", function () { counter.textContent = area.value.length + " / 800"; });
    box.querySelector("button[name=signout]").addEventListener("click", function () {
      clearSession();
      box.replaceWith(signInForm());
    });
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      msg.textContent = "";
      var text = area.value.trim();
      if (text.length < 2) { msg.className = "cmt-msg err"; msg.textContent = "Write something first."; return; }
      var btn = form.querySelector("button[type=submit]");
      btn.disabled = true; btn.textContent = "Posting…";
      var r = await api("/comments", { method: "POST", body: { slug: slug, text: text } });
      btn.disabled = false; btn.textContent = "Post comment";
      if (r.status === 401) { clearSession(); box.replaceWith(signInForm()); return; }
      if (r.status === 200 && r.data.ok) {
        area.value = ""; counter.textContent = "0 / 800";
        msg.className = "cmt-msg ok";
        msg.textContent = "✅ Your comment is live.";
        await loadComments();
      } else {
        msg.className = "cmt-msg err";
        msg.textContent = humanError(r.data.error);
      }
    });
    return box;
  }

  async function mount() {
    // Replace the Telegram-only CTA with the on-site box (Telegram stays as fallback below).
    var cta = section.querySelector(".cmt-cta");
    var note = section.querySelector(".cmt-note");
    var box;
    if (token()) {
      var me = await api("/me");
      if (me.status === 200 && me.data.ok) {
        box = commentForm(me.data.name);
      } else {
        clearSession();
        box = signInForm();
      }
    } else {
      box = signInForm();
    }
    if (cta && cta.parentNode) cta.parentNode.insertBefore(box, cta);
    else if (note && note.parentNode) note.parentNode.insertBefore(box, note);
    else section.insertBefore(box, section.querySelector(".cmt-powered"));
    // keep the Telegram option as an alternative
    if (cta) {
      cta.classList.add("cmt-alt");
      if (note) note.textContent = "Prefer Telegram? One tap, no email needed.";
    }
    await loadComments();
  }

  if (API) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
    else mount();
  }
})();
