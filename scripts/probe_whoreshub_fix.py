"""Probe whoreshub list/pagination issues."""
from __future__ import annotations

import asyncio
import re

from curl_cffi.requests import AsyncSession

from app.scrapers.whoreshub.scraper import (
    _build_list_page_url,
    _list_root,
    _normalize_list_path,
    list_videos,
)
from bs4 import BeautifulSoup


async def block_ids(url: str) -> list[str]:
    async with AsyncSession(impersonate="chrome120") as c:
        r = await c.get(url)
        return sorted(set(re.findall(r'id="(list_videos[^"]+)"', r.text)))


async def ids_from_url(url: str, n: int = 5) -> list[str]:
    async with AsyncSession(impersonate="chrome120") as c:
        r = await c.get(url)
        out: list[str] = []
        for m in re.finditer(r"/videos/(\d+)/", r.text):
            out.append(m.group(1))
        return list(dict.fromkeys(out))[:n]


async def main() -> None:
    bases = [
        "https://www.whoreshub.com/",
        "https://www.whoreshub.com/latest-updates/",
        "https://www.whoreshub.com/top-rated/",
        "https://www.whoreshub.com/most-popular/",
        "https://www.whoreshub.com/categories/anal/",
    ]
    for base in bases:
        norm = _normalize_list_path(base)
        p1 = _build_list_page_url(base, 1)
        p2 = _build_list_page_url(base, 2)
        print("BASE", base)
        print("  norm", norm)
        print("  p1", p1)
        print("  p2", p2)
        print("  blocks p1", await block_ids(p1))
        print("  ids p1", await ids_from_url(p1))
        print("  ids p2", await ids_from_url(p2))
        items1 = await list_videos(base, page=1, limit=5)
        items2 = await list_videos(base, page=2, limit=5)
        print("  scraper p1", len(items1), [x["url"].split("/videos/")[1].split("/")[0] for x in items1])
        print("  scraper p2", len(items2), [x["url"].split("/videos/")[1].split("/")[0] for x in items2])
        print()


if __name__ == "__main__":
    asyncio.run(main())
