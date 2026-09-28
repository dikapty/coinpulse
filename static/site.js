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
})();
