"""Probe yesporn.vip HTML structure."""
from __future__ import annotations

import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

OUT = Path(__file__).resolve().parent
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://yesporn.vip/",
}


def main() -> None:
    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        home = client.get("https://yesporn.vip/")
        print("home", home.status_code, len(home.text))
        (OUT / "yesporn_home.html").write_text(home.text, encoding="utf-8")

        soup = BeautifulSoup(home.text, "lxml")
        for sel in [
            ".item",
            ".thumb",
            ".video-item",
            ".list-videos .item",
            ".thumb-list .item",
            "a[href*='video']",
            ".video-thumb",
            ".post",
        ]:
            els = soup.select(sel)
            if els:
                print(f"selector {sel!r}: {len(els)}")

        video_url = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = "https://yesporn.vip" + href
            if "yesporn.vip" not in href.lower():
                continue
            if re.search(r"/(video|videos)/\d+", href, re.I) or re.search(
                r"/\d{4,}/[^/]+/?$", href
            ):
                video_url = href
                print("VIDEO:", video_url, "|", a.get_text(strip=True)[:60])
                break

        if not video_url:
            print("No video link found on homepage")
            for a in soup.select("a[href]")[:30]:
                print("  ", a.get("href"), a.get_text(strip=True)[:40])
            return

        page = client.get(video_url)
        print("video page", page.status_code, len(page.text))
        (OUT / "yesporn_video.html").write_text(page.text, encoding="utf-8")

        vsoup = BeautifulSoup(page.text, "lxml")
        title = vsoup.find("title")
        print("title:", title.get_text(strip=True) if title else None)
        for prop in ("og:title", "og:image", "og:video", "og:video:url"):
            m = vsoup.find("meta", attrs={"property": prop})
            if m:
                print(prop, m.get("content", "")[:120])

        html = page.text
        if "flashvars" in html:
            print("HAS flashvars")
            m = re.search(r"var\s+flashvars\s*=\s*\{(.+?)\};", html, re.DOTALL)
            if m:
                print("flashvars block len", len(m.group(1)))
                for key in (
                    "video_url",
                    "video_alt_url",
                    "video_title",
                    "preview_url",
                    "video_id",
                ):
                    km = re.search(rf"{key}\s*:\s*'([^']*)'", m.group(1), re.I)
                    if km:
                        print(f"  {key}:", km.group(1)[:100])

        for pat in [r"get_file/", r"\.m3u8", r"\.mp4", r"kt_player", r"video_url"]:
            if re.search(pat, html, re.I):
                print("pattern found:", pat)

        # list page
        list_url = "https://yesporn.vip/?mode=async&function=get_block&block_id=list_videos_latest_videos_list&from=2"
        # try category
        cat = client.get("https://yesporn.vip/categories/cheating/")
        print("category", cat.status_code, len(cat.text))
        csoup = BeautifulSoup(cat.text, "lxml")
        cat_links = []
        for a in csoup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = "https://yesporn.vip" + href
            if re.search(r"/(video|videos)/\d+", href, re.I):
                cat_links.append(href)
        print("category video links:", len(cat_links), cat_links[:3])

        urls = [
            "https://yesporn.vip/?page=2",
            "https://yesporn.vip/latest-updates/2/",
            "https://yesporn.vip/categories/cheating/2/",
            "https://yesporn.vip/categories/cheating/?page=2",
        ]
        for u in urls:
            r = client.get(u)
            soup2 = BeautifulSoup(r.text, "lxml")
            ids = []
            for a in soup2.select('a[href*="/video/"]'):
                m = re.search(r"/video/(\d+)/", a["href"])
                if m:
                    ids.append(m.group(1))
            uniq = list(dict.fromkeys(ids))
            print("paginate", u, r.status_code, len(uniq), uniq[:3])


if __name__ == "__main__":
    main()
