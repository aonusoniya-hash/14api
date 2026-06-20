"""Probe whoreshub.com HTML structure."""
from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.whoreshub.com/",
}


def first_ids(html: str, n: int = 5) -> list[str]:
    ids: list[str] = []
    for a in BeautifulSoup(html, "lxml").select("a[href]"):
        href = a.get("href", "")
        for pat in [
            r"/videos/(\d+)/",
            r"/video/(\d+)/",
            r"/watch/(\d+)",
            r"/v/(\d+)/",
        ]:
            m = re.search(pat, href, re.I)
            if m:
                ids.append(m.group(1))
                break
    return list(dict.fromkeys(ids))[:n]


def main() -> None:
    with httpx.Client(timeout=60, follow_redirects=True, headers=HEADERS) as client:
        home = client.get("https://www.whoreshub.com/")
        print("home", home.status_code, len(home.text))
        open(r"c:\Users\Google11\Desktop\apphub3\backend\scripts\whoreshub_home.html", "w", encoding="utf-8").write(
            home.text
        )

        soup = BeautifulSoup(home.text, "lxml")
        for sel in [".item", ".thumb", "a[href*='video']", ".video-item", ".video"]:
            els = soup.select(sel)
            if els:
                print(f"{sel!r}: {len(els)}")

        video_url = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = "https://www.whoreshub.com" + href
            if "whoreshub.com" not in href.lower():
                continue
            if re.search(r"/(video|videos)/\d+", href, re.I) or re.search(r"/watch/\d+", href, re.I):
                video_url = href
                print("VIDEO:", video_url)
                break

        if not video_url:
            print("sample hrefs:")
            for a in soup.select("a[href]")[:30]:
                print(" ", a.get("href", "")[:100])
            return

        page = client.get(video_url)
        print("video", page.status_code, len(page.text))
        open(r"c:\Users\Google11\Desktop\apphub3\backend\scripts\whoreshub_video.html", "w", encoding="utf-8").write(
            page.text
        )
        html = page.text
        for key in ("flashvars", "kt_player", "embed/", "get_file/", "video_url", "embedUrl"):
            print(key, key in html.lower() or key in html)

        urls = [
            "https://www.whoreshub.com/?page=2",
            "https://www.whoreshub.com/latest-updates/2/",
            "https://www.whoreshub.com/latest-updates/?from=2",
            "https://www.whoreshub.com/videos/2/",
            "https://www.whoreshub.com/page/2/",
            "https://www.whoreshub.com/categories/anal/2/",
            "https://www.whoreshub.com/categories/anal/?from=2",
        ]
        base_ids = first_ids(home.text)
        print("home ids", base_ids)
        for u in urls:
            r = client.get(u)
            print(u.split("whoreshub.com")[-1], r.status_code, first_ids(r.text)[:3])

        for m in re.finditer(r'id="((?:list|custom_list)_videos[^"]+)"', home.text):
            print("block id", m.group(1))


if __name__ == "__main__":
    main()
