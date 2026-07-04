import json, re
from curl_cffi import requests as cr
from bs4 import BeautifulSoup

headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.underhentai.net/"}

def card_summary(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select(".ep2-card"):
        vtype_el = card.select_one(".ep2-vtype")
        vtype = "".join(ch for ch in (vtype_el.get_text(" ", strip=True) if vtype_el else "") if ord(ch) < 0x1F000).strip()
        subs = ""
        for item in card.select(".ep2-meta-item"):
            label = item.select_one(".ep2-meta-label")
            if label and "subs" in label.get_text(strip=True).lower():
                subs = item.select_one(".ep2-meta-value").get_text(" ", strip=True)
                subs = "".join(ch for ch in subs if ord(ch) < 0x1F000).strip()
        mega = next((a.get("href") for a in card.select("a.ep2-dl") if "mega" in a.get_text(strip=True).lower()), None)
        stream = card.select_one("a.ep2-stream")
        out.append({"vtype": vtype, "subs": subs, "mega": mega, "stream": stream.get("href") if stream else None})
    return out

# scan a few recent titles for spanish
home = cr.get("https://www.underhentai.net/", headers=headers, impersonate="chrome120", timeout=45).text
soup = BeautifulSoup(home, "lxml")
slugs = []
for a in soup.select("article.data-block .article-header h2 a[href]")[:15]:
    href = a.get("href")
    if href:
        slugs.append("https://www.underhentai.net" + href if href.startswith("/") else href)

found = []
for slug in slugs:
    html = cr.get(slug, headers=headers, impersonate="chrome120", timeout=45).text
    cards = card_summary(html)
    for c in cards:
        if "spanish" in c["subs"].lower() or "espa" in c["subs"].lower():
            found.append({"url": slug, "cards": cards})
            break

with open("_spanish_scan.json", "w", encoding="utf-8") as f:
    json.dump({"found": found, "sample": card_summary(cr.get(slugs[0], headers=headers, impersonate="chrome120", timeout=45).text)}, f, ensure_ascii=False, indent=2)
