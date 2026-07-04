"""Inspect underhentai episode card structure."""
import json
from curl_cffi import requests as cr
from bs4 import BeautifulSoup

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.underhentai.net/",
}

def inspect_video(slug_url: str) -> list[dict]:
    html = cr.get(slug_url, headers=headers, impersonate="chrome120", timeout=45).text
    soup = BeautifulSoup(html, "lxml")
    cards = []
    for card in soup.select(".ep2-card"):
        meta = {}
        for item in card.select(".ep2-meta-item"):
            label = item.select_one(".ep2-meta-label")
            value = item.select_one(".ep2-meta-value")
            if label and value:
                meta[label.get_text(strip=True).lower()] = value.get_text(" ", strip=True)
        vtype_el = card.select_one(".ep2-vtype")
        vtype_text = vtype_el.get_text(" ", strip=True) if vtype_el else ""
        # strip emoji
        vtype_text = "".join(ch for ch in vtype_text if ord(ch) < 0x1F000).strip()
        cards.append(
            {
                "vtype": vtype_text,
                "meta": meta,
                "downloads": [
                    {"label": a.get_text(strip=True), "href": a.get("href")}
                    for a in card.select("a.ep2-dl")
                ],
                "stream": card.select_one("a.ep2-stream").get("href") if card.select_one("a.ep2-stream") else None,
            }
        )
    return cards

out = {
    "cheat": inspect_video("https://www.underhentai.net/cheat-item-kanrikyoku-no-oshigoto-ex/"),
}
with open("_episode_inspect.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

# watch page embeds
for ep in (0, 1):
    wh = cr.get(
        f"https://www.underhentai.net/watch/?id=11135&ep={ep}",
        headers=headers,
        impersonate="chrome120",
        timeout=45,
    ).text
    import re
    embeds = {
        "kraken": re.findall(r"krakenfiles\.com/embed-video/[A-Za-z0-9]+", wh),
        "lulu": re.findall(r"luluvdo\.com/embed/[A-Za-z0-9]+", wh),
    }
    out[f"watch_ep{ep}"] = embeds

with open("_episode_inspect.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
