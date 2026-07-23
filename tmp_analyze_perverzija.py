from __future__ import annotations

import re
from bs4 import BeautifulSoup

with open("tmp_perverzija_video.html", encoding="utf-8") as f:
    html = f.read()
    soup = BeautifulSoup(html, "lxml")

print("iframes", [i.get("src") for i in soup.select("iframe[src]")[:5]])
for a in soup.select(".multilink-btn[href]"):
    print("multilink", a.get_text(" ", strip=True), a.get("href"))
for h in soup.select("input[name=main_video_url], input[name=main_video_type]"):
    print(h.get("name"), h.get("value"))
info = soup.select_one(".item-info")
print("item-info", info.get_text(" ", strip=True) if info else None)
for s in soup.find_all("script", type="application/ld+json"):
    print("ld", s.get_text()[:400])
for m in re.findall(r"https?://[^\s\"'<>]+\.(?:mp4|m3u8)[^\s\"'<>]*", html):
    print("media", m[:140])
for a in soup.select("a.quickview[data-embed]"):
    print("embed attr", a.get("data-embed", "")[:200])
