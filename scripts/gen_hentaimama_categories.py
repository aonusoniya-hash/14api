import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://hentaimama.io/"}
BASE = "https://hentaimama.io"

main = [
    ("latest", "Latest", f"{BASE}/"),
    ("recent", "Recent Episodes", f"{BASE}/recent-episodes/"),
    ("monthly", "New Monthly", f"{BASE}/new-monthly-hentai/"),
    ("hentai-list", "Hentai List", f"{BASE}/hentai-list/"),
    ("tvshows", "All Series", f"{BASE}/tvshows/"),
]
html = cr.get(f"{BASE}/genres-filter/", headers=H, impersonate="chrome120", timeout=30).text
soup = BeautifulSoup(html, "lxml")
genres = []
for a in soup.select("a[href*='/genre/']"):
    href = a.get("href") or ""
    name = a.get_text(strip=True)
    if not href or not name or name.lower() == "see all":
        continue
    url = href if href.startswith("http") else BASE + (href if href.startswith("/") else "/" + href)
    slug = url.rstrip("/").split("/genre/")[-1]
    cat_id = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    genres.append({"id": cat_id, "name": name, "url": url if url.endswith("/") else url + "/"})

out = [{"id": cid, "name": name, "url": url} for cid, name, url in main]
seen = {c["url"] for c in out}
for g in genres:
    if g["url"] not in seen:
        seen.add(g["url"])
        out.append(g)

path = Path(__file__).resolve().parents[1] / "app" / "scrapers" / "hentaimama" / "categories.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {len(out)} categories")
