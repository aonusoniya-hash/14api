import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.hentaicity.com/"}
BASE = "https://www.hentaicity.com"

cats = []
html = cr.get(f"{BASE}/categories/", headers=H, impersonate="chrome120", timeout=30).text
soup = BeautifulSoup(html, "lxml")
for a in soup.select('a[href*="/tags/video/"]'):
    href = a.get("href") or ""
    name = a.get_text(strip=True)
    if not href or not name:
        continue
    slug = href.rstrip("/").split("/tags/video/")[-1]
    cat_id = re.sub(r"[^a-z0-9]+", "-", slug.replace("+", " ").lower()).strip("-")
    url = href if href.startswith("http") else BASE + href
    cats.append({"id": cat_id, "name": name, "url": url})

main = [
    ("latest", "Latest", f"{BASE}/"),
    ("hentai", "Hentai", f"{BASE}/videos/straight/hentai-popular.html"),
    ("3d", "3D", f"{BASE}/videos/straight/3d-popular.html"),
    ("anal", "Anal", f"{BASE}/videos/straight/anal-popular.html"),
    ("big-tits", "Big Tits", f"{BASE}/videos/straight/bigtits-popular.html"),
    ("blowjob", "Blowjob", f"{BASE}/videos/straight/blowjob-popular.html"),
    ("creampie", "Creampie", f"{BASE}/videos/straight/creampie-popular.html"),
    ("futanari", "Futanari", f"{BASE}/videos/straight/futanari-popular.html"),
    ("monster", "Monster", f"{BASE}/videos/straight/monster-popular.html"),
    ("teen", "Teen", f"{BASE}/videos/straight/teen-popular.html"),
    ("ahegao", "Ahegao", f"{BASE}/tags/video/ahegao"),
    ("uncensored", "Uncensored", f"{BASE}/tags/video/uncensored"),
]
out = [{"id": cid, "name": name, "url": url} for cid, name, url in main]
seen = {c["url"] for c in out}
for c in cats:
    if c["url"] not in seen:
        seen.add(c["url"])
        out.append(c)

path = Path(__file__).resolve().parents[1] / "app" / "scrapers" / "hentaicity" / "categories.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {len(out)} categories to {path}")
