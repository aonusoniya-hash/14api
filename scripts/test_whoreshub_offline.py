"""Offline tests for whoreshub scraper using saved HTML."""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from app.scrapers.whoreshub.scraper import (
    _build_list_page_url,
    _list_root,
    _parse_list_item,
    parse_video_page,
)

ROOT = Path(__file__).resolve().parent


def test_home_list() -> None:
    html = (ROOT / "whoreshub_home.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    page_url = _build_list_page_url("https://www.whoreshub.com/", 1)
    assert page_url == "https://www.whoreshub.com/"
    root = _list_root(soup, page_url)
    assert root is not None, "home list root missing"
    root_id = root.get("id")
    print("home root id:", root_id)
    assert root_id == "list_videos_recently_added_videos_items"

    boxes = root.select(":scope > .thumb") or root.select(".thumb")
    items = []
    for box in boxes:
        parsed = _parse_list_item(box)
        if parsed:
            items.append(parsed)
    print("home items:", len(items))
    assert len(items) >= 10, f"expected >=10 home items, got {len(items)}"
    assert all("/videos/" in i["url"] for i in items)
    assert items[0]["duration"]
    assert items[0]["thumbnail_url"]


def test_home_pagination_urls() -> None:
    assert _build_list_page_url("https://www.whoreshub.com/", 1) == "https://www.whoreshub.com/"
    assert _build_list_page_url("https://www.whoreshub.com/", 2) == (
        "https://www.whoreshub.com/latest-updates/?from=2"
    )
    assert _build_list_page_url("https://www.whoreshub.com/latest-updates/", 2) == (
        "https://www.whoreshub.com/latest-updates/?from=2"
    )
    assert _build_list_page_url("https://www.whoreshub.com/categories/anal/", 2) == (
        "https://www.whoreshub.com/categories/anal/?from=2"
    )


def test_video_page() -> None:
    html = (ROOT / "whoreshub_video.html").read_text(encoding="utf-8")
    url = "https://www.whoreshub.com/videos/666036/11a2ee86699c370289053b212f5c6d24/"
    result = parse_video_page(html, url)
    print("video has_video:", result["video"]["has_video"])
    print("embed:", result["video"]["default"])
    print("duration:", result["duration"])
    print("uploader:", result["uploader_name"])
    print("thumb:", (result["thumbnail_url"] or "")[:80])
    assert result["video"]["has_video"]
    assert "embed/666036" in (result["video"]["default"] or "")
    assert result["duration"] == "26:44"
    assert result["uploader_name"] == "Lisa Ann"


if __name__ == "__main__":
    test_home_pagination_urls()
    test_home_list()
    test_video_page()
    print("OFFLINE OK")
