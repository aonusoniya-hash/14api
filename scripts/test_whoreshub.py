import asyncio

from app.scrapers import whoreshub


async def main() -> None:
    print("can_handle:", whoreshub.can_handle("www.whoreshub.com"))
    print("categories:", len(whoreshub.get_categories()))

    pages: dict[int, list[str]] = {}
    for page in (1, 2, 3):
        items = await whoreshub.list_videos(
            "https://www.whoreshub.com/",
            page=page,
            limit=8,
        )
        ids = [i["url"].split("/videos/")[1].split("/")[0] for i in items]
        pages[page] = ids
        print(f"page {page}: {len(items)} items", ids[:5])

    overlap = set(pages[1]) & set(pages[2])
    print("page1/page2 overlap:", len(overlap))

    cat_items = await whoreshub.list_videos(
        "https://www.whoreshub.com/categories/anal/",
        page=1,
        limit=5,
    )
    print("anal category:", len(cat_items))

    result = await whoreshub.scrape(
        "https://www.whoreshub.com/videos/666036/11a2ee86699c370289053b212f5c6d24/"
    )
    print("title len:", len(result["title"]))
    print("duration:", result["duration"])
    print("uploader:", result["uploader_name"])
    print("video:", result["video"])
    assert result["video"]["has_video"]
    assert result["video"]["default"].startswith("https://www.whoreshub.com/embed/")
    assert all(s["format"] == "embed" for s in result["video"]["streams"])
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
