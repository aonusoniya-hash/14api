import asyncio
import re

from curl_cffi.requests import AsyncSession


async def main() -> None:
    async with AsyncSession(impersonate="chrome120") as c:
        r = await c.get("https://www.whoreshub.com/latest-updates/")
        ids = sorted(set(re.findall(r'id="(list_videos[^"]+)"', r.text)))
        for i in ids:
            print(i)
        print("videos links", len(re.findall(r"/videos/\d+/", r.text)))


if __name__ == "__main__":
    asyncio.run(main())
