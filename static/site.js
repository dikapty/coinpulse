/* CoinPulse site enhancements: live ticker, reading progress, copy-link. No tracking. */
(function () {
  "use strict";

  /* --- Live crypto ticker (CoinGecko free API, 10-min cache) --- */
  var track = document.getElementById("ticker-track");
  if (track) {
    var CACHE_KEY = "cp_prices", CACHE_TTL = 10 * 60 * 1000;
    var COINS = ["bitcoin", "ethereum", "solana", "ripple", "dogecoin", "cardano"];

    function fmt(n) {
      return n >= 1 ? n.toLocaleString("en-US", { maximumFractionDigits: 2 })
                    : n.toFixed(6);
    }

    function render(data) {
      var html = data.map(function (c) {
        var up = c.change >= 0;
        return '<span class="tk"><b>' + c.sym + "</b> $" + fmt(c.price) +
               ' <i class="' + (up ? "up" : "down") + '">' +
               (up ? "▲" : "▼") + " " + Math.abs(c.change).toFixed(1) + "%</i></span>";
      }).join("");
      // duplicate for seamless marquee loop
      track.innerHTML = html + html;
      track.parentElement.classList.add("ticker-live");
    }

    function load() {
      var cached = null;
      try { cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch (e) {}
      if (cached && Date.now() - cached.t < CACHE_TTL) { render(cached.d); return; }

      fetch("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=" + COINS.join(",") +
            "&price_change_percentage=24h&order=market_cap_desc")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (rows) {
          if (!rows || !rows.length) { if (cached) render(cached.d); return; }
          var d = rows.map(function (r) {
            return { sym: r.symbol.toUpperCase(), price: r.current_price,
                     change: r.price_change_percentage_24h || 0 };
          });
          try { localStorage.setItem(CACHE_KEY, JSON.stringify({ t: Date.now(), d: d })); } catch (e) {}
          render(d);
        })
        .catch(function () { if (cached) render(cached.d); });
    }
    load();
  }

  /* --- Reading progress bar on articles --- */
  var bar = document.getElementById("progress");
  var article = document.querySelector(".article-page article");
  if (bar && article) {
    window.addEventListener("scroll", function () {
      var h = article.scrollHeight - window.innerHeight;
      var pct = h > 0 ? Math.min(100, Math.max(0, (window.scrollY / h) * 100)) : 0;
      bar.style.width = pct + "%";
    }, { passive: true });
  }

  /* --- Copy link button --- */
  document.querySelectorAll(".cp[data-copy]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      var url = a.getAttribute("data-copy");
      (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject())
        .then(function () {
          var old = a.textContent; a.textContent = "✓";
          setTimeout(function () { a.textContent = old; }, 1500);
        })
        .catch(function () {});
    });
  });

  /* --- Client-side search (search.html): small JSON index, instant results --- */
  var q = document.getElementById("q");
  var res = document.getElementById("results");
  var statusEl = document.getElementById("search-status");
  if (q && res) {
    var INDEX = null;
    var base = window.__CP_BASE__ || "";
    function esc(s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function norm(s) { return String(s || "").toLowerCase(); }
    function score(item, terms) {
      var t = norm(item.t), d = norm(item.d), k = norm(item.k);
      var sc = 0;
      for (var i = 0; i < terms.length; i++) {
        var term = terms[i];
        if (!term) continue;
        var hit = false;
        if (t.indexOf(term) !== -1) { sc += 10; if (t.indexOf(term) === 0) sc += 5; hit = true; }
        if (k.indexOf(term) !== -1) { sc += 6; hit = true; }
        if (d.indexOf(term) !== -1) { sc += 3; hit = true; }
        if (!hit) return 0;
      }
      return sc;
    }
    function run() {
      var raw = q.value.trim();
      if (!raw) {
        res.innerHTML = "";
        statusEl.textContent = INDEX ? INDEX.length + " articles indexed" : "";
        return;
      }
      var terms = norm(raw).split(/\s+/).filter(Boolean);
      var hits = [];
      for (var i = 0; i < INDEX.length; i++) {
        var s = score(INDEX[i], terms);
        if (s > 0) hits.push({ item: INDEX[i], s: s });
      }
      hits.sort(function (a, b) { return b.s - a.s || (a.item.g < b.item.g ? 1 : -1); });
      hits = hits.slice(0, 30);
      statusEl.textContent = hits.length
        ? hits.length + " result" + (hits.length > 1 ? "s" : "") + " for “" + raw + "”"
        : "No results for “" + raw + "”. Try a shorter or different word.";
      res.innerHTML = hits.map(function (h) {
        return '<a class="search-item" href="' + esc(h.item.u) + '">' +
               '<span class="search-title">' + esc(h.item.t) + "</span>" +
               '<span class="search-desc">' + esc(h.item.d) + "</span>" +
               '<span class="search-meta">' + esc(h.item.g) + (h.item.k ? " · " + esc(h.item.k) : "") + "</span>" +
               "</a>";
      }).join("");
    }
    fetch(base + "search-index.json")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) {
        INDEX = data;
        statusEl.textContent = data.length + " articles indexed";
        var params = new URLSearchParams(location.search);
        var initial = params.get("q");
        if (initial) { q.value = initial; run(); }
      })
      .catch(function () { statusEl.textContent = "Search index failed to load. Refresh the page."; });
    var timer = null;
    q.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { if (INDEX) run(); }, 120);
    });
    q.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); if (INDEX) run(); } });
    q.focus();
  }

  /* --- Lazy dimension safety for thumbnails (avoid layout shift when CSS lacks aspect-ratio) --- */
  document.querySelectorAll(".thumb img, .related-grid img").forEach(function (img) {
    if (!img.getAttribute("width")) { img.setAttribute("width", "400"); img.setAttribute("height", "225"); }
    img.setAttribute("decoding", "async");
  });
})();
