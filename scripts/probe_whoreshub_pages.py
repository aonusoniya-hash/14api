import httpx
import re

URLS = [
    "https://www.whoreshub.com/top-rated/",
    "https://www.whoreshub.com/most-popular/",
    "https://www.whoreshub.com/latest-updates/",
    "https://www.whoreshub.com/latest-updates/?from=2",
]
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.whoreshub.com/"}

with httpx.Client(timeout=90, follow_redirects=True, headers=HEADERS) as client:
    for url in URLS:
        r = client.get(url)
        blocks = sorted(set(re.findall(r'id="(list_videos[^"]+)"', r.text)))
        ids = list(dict.fromkeys(re.findall(r"/videos/(\d+)/", r.text)))[:5]
        print(url)
        print("  status", r.status_code, "blocks", blocks[:8])
        print("  ids", ids)
