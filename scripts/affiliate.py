"""Module 5b: affiliate link insertion.

Scans published posts; when the text matches a program's keywords, appends a clearly
labelled affiliate box before the ## Sources section. Disclosure requirement: every box
is marked "Sponsored partner link" (ad-policy compliant) and site-wide AI disclosure
lives in the footer.

Run: python3 scripts/affiliate.py
"""
from __future__ import annotations

import re

from common import POSTS, load_config, log


def run() -> None:
    cfg = load_config()
    aff = cfg.get("affiliates", {})
    if not aff.get("enabled") or not aff.get("programs"):
        log("Affiliates disabled or no programs configured — skipping.")
        return

    touched = 0
    for p in POSTS.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        if "<!-- affiliate-box -->" in text:
            continue  # already processed
        hay = text.lower()
        for program in aff["programs"]:
            if any(m.lower() in hay for m in program.get("match", [])):
                label = program.get("label", "Sponsored partner link")
                box = (f'\n<!-- affiliate-box -->\n<div class="affiliate-box">'
                       f'<strong>Related:</strong> <a href="{program["url"]}" rel="sponsored noopener" '
                       f'target="_blank">{program["name"]}</a> — <em>{label}</em></div>\n')
                # insert before ## Sources if present, else append
                m = re.search(r"^##\s+Sources", text, re.MULTILINE)
                if m:
                    text = text[:m.start()] + box + "\n" + text[m.start():]
                else:
                    text += box
                p.write_text(text, encoding="utf-8")
                touched += 1
                log(f"  + affiliate box ({program['name']}): {p.name}")
                break  # one program per article max
    log(f"Affiliate pass done: {touched} article(s) updated.")


if __name__ == "__main__":
    run()
