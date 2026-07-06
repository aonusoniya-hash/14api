import asyncio
import sys
sys.path.insert(0, ".")

from app.scrapers.letsporn import scraper as lp

async def main():
    urls = [
        "https://letsporn.com/",
        "https://letsporn.com/categories/teen",
        "https://letsporn.com/mia-khalifa-wants-bbc-to-bang-her-brutally-once-again-5477",
    ]
    for url in urls:
        try:
            html = await lp.fetch_page(url)
            print(url, "OK", len(html))
            if "flashvars" in html:
                print("  has flashvars")
            if "get_file" in html:
                print("  has get_file")
            vids = []
            for h in __import__('re').findall(r'https?://letsporn\.com/[a-z0-9-]{10,}-\d+', html):
                if h not in vids:
                    vids.append(h)
            print("  video links", len(vids), vids[:3])
        except Exception as e:
            print(url, "FAIL", e)

    try:
        items = await lp.list_videos("https://letsporn.com/categories/teen", page=1, limit=5)
        print("list items", len(items))
        for it in items[:3]:
            print(" ", it.get("title"), it.get("url"))
    except Exception as e:
        print("list FAIL", e)

    try:
        data = await lp.scrape("https://letsporn.com/mia-khalifa-wants-bbc-to-bang-her-brutally-once-again-5477")
        print("scrape title", data.get("title"))
        print("streams", len(data.get("video", {}).get("streams", [])))
        for s in data.get("video", {}).get("streams", [])[:3]:
            print(" ", s)
    except Exception as e:
        print("scrape FAIL", e)

asyncio.run(main())
