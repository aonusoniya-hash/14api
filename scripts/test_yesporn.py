"""Smoke test for yesporn.vip scraper."""
from __future__ import annotations

import asyncio
import json

from app.scrapers import yesporn


async def main() -> None:
    print("can_handle:", yesporn.can_handle("yesporn.vip"))
    cats = yesporn.get_categories()
    print("categories:", len(cats), cats[0]["name"] if cats else None)

    items = await yesporn.list_videos("https://yesporn.vip/", page=1, limit=5)
    print("list page1:", len(items))
    if items:
        print(" first:", items[0]["title"][:60], items[0]["url"])

    items2 = await yesporn.list_videos("https://yesporn.vip/", page=2, limit=5)
    print("list page2:", len(items2))
    if items2:
        print(" first p2:", items2[0]["url"])

    cat_items = await yesporn.list_videos("https://yesporn.vip/categories/cheating/", page=1, limit=3)
    print("category list:", len(cat_items))

    if items:
        data = await yesporn.scrape(items[0]["url"])
        print("scrape title:", data.get("title"))
        print("duration:", data.get("duration"), "views:", data.get("views"))
        print("uploader:", data.get("uploader_name"))
        video = data.get("video") or {}
        print("has_video:", video.get("has_video"), "streams:", len(video.get("streams") or []))
        if video.get("default"):
            print("default:", video["default"][:100])
        print(json.dumps({k: data[k] for k in ("url", "title", "thumbnail_url", "duration", "views")}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
