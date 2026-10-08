<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
<xsl:output method="html" encoding="UTF-8" indent="yes"/>
<xsl:template match="/">
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title><xsl:value-of select="rss/channel/title"/> — RSS feed</title>
<style>
:root { --bg:#0d1119; --panel:#151b28; --border:#232b3d; --text:#e8ebf2; --muted:#8b93a7; --accent:#f7931a; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; line-height:1.6; }
.wrap { max-width:820px; margin:0 auto; padding:34px 18px 60px; }
.banner { background:var(--panel); border:1px solid var(--border); border-left:4px solid var(--accent);
  border-radius:12px; padding:22px 24px; margin-bottom:34px; }
.banner h1 { margin:0 0 8px; font-size:22px; }
.banner p { margin:0 0 14px; color:var(--muted); font-size:15px; }
.btn { display:inline-block; padding:9px 20px; background:var(--accent); color:#12161f;
  border-radius:8px; font-weight:700; text-decoration:none; font-size:14px; }
.btn:hover { filter:brightness(1.1); }
.btn.ghost { background:transparent; color:var(--accent); border:1px solid var(--accent); margin-left:8px; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:1px; color:var(--muted);
  border-bottom:1px solid var(--border); padding-bottom:10px; margin:0 0 6px; }
ul { list-style:none; padding:0; margin:0; }
li { border-bottom:1px solid var(--border); padding:16px 0; }
li a.t { color:var(--text); text-decoration:none; font-weight:600; font-size:17px; }
li a.t:hover { color:var(--accent); }
.date { display:block; color:var(--muted); font-size:12.5px; margin:4px 0 6px; }
.desc { color:var(--muted); font-size:14px; margin:0; }
code { background:var(--panel); border:1px solid var(--border); border-radius:6px;
  padding:2px 7px; font-size:13px; color:var(--accent); word-break:break-all; }
.foot { margin-top:34px; color:var(--muted); font-size:13px; text-align:center; }
.foot a { color:var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <div class="banner">
    <h1>&#128225; This is an RSS feed</h1>
    <p>You are looking at machine-readable code, not a broken page. RSS is meant to be read by a
       <strong>feed reader</strong>, which turns it into a comfortable news list that updates itself.</p>
    <p>To subscribe, copy this address into a free reader such as Feedly, Inoreader or NetNewsWire:</p>
    <p><code><xsl:value-of select="rss/channel/link"/>feed.xml</code></p>
    <p>
      <a class="btn" href="{rss/channel/link}">&#8592; Go to the website</a>
      <a class="btn ghost" href="https://feedly.com/i/subscription/feed/{rss/channel/link}feed.xml">Subscribe in Feedly</a>
    </p>
  </div>

  <h2>Latest articles (<xsl:value-of select="count(rss/channel/item)"/>)</h2>
  <ul>
    <xsl:for-each select="rss/channel/item">
      <li>
        <a class="t" href="{link}"><xsl:value-of select="title"/></a>
        <span class="date"><xsl:value-of select="substring(pubDate,1,10)"/></span>
        <p class="desc"><xsl:value-of select="description"/></p>
      </li>
    </xsl:for-each>
  </ul>

  <p class="foot">
    <xsl:value-of select="rss/channel/title"/> &#8212; <xsl:value-of select="rss/channel/description"/><br/>
    <a href="{rss/channel/link}">Homepage</a> &#183; Not financial advice.
  </p>
</div>
</body>
</html>
</xsl:template>
</xsl:stylesheet>
