import asyncio

from app.scrapers.whoreshub.scraper import fetch_page


async def main() -> None:
    html = await fetch_page("https://www.whoreshub.com/latest-updates/")
    assert "list_videos_latest_videos_list_items" in html or "/videos/" in html
    print("latest-updates bytes:", len(html))
    print("OK - no pool fallback")


if __name__ == "__main__":
    asyncio.run(main())
