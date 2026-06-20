from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.whoreshub.com/"
SITE_HOST = "whoreshub.com"
SITE_ALIASES = frozenset({"whoreshub.com", "www.whoreshub.com"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_HREF_RE = re.compile(
    r"whoreshub\.com/videos/(?P<id>\d+)/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_models|video_tags|video_categories|preview_url|video_title|video_id)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_EMBED_URL_RE = re.compile(
    r"https?://(?:www\.)?whoreshub\.com/embed/(?P<id>\d+)",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".whoreshub.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (
        " | WhoresHub",
        " - WhoresHub",
        " | WhoresHub.com",
        " - WhoresHub.com",
        " | whoreshub.com",
        " - whoreshub.com",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    return u


def _extract_video_id(url: str) -> Optional[str]:
    m = _VIDEO_HREF_RE.search(url or "")
    return m.group("id") if m else None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if "whoreshub.com" not in href.lower():
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    slug = m.group("slug").strip("/")
    return f"https://www.whoreshub.com/videos/{m.group('id')}/{slug}/"


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = _FLASHVARS_BLOCK_RE.search(html or "")
    if not m:
        return out
    block = m.group(1)
    for key, value in _FLASHVARS_PAIR_RE.findall(block):
        out[key.lower()] = value.strip()
    return out


def _extract_embed_url(html: str, video_url: str) -> Optional[str]:
    flash = _parse_flashvars(html)
    video_id = _extract_video_id(video_url) or flash.get("video_id")
    if video_id and str(video_id).isdigit():
        return f"https://www.whoreshub.com/embed/{video_id}"

    for script in BeautifulSoup(html, "lxml").find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            embed = data.get("embedUrl")
            if embed:
                return _normalize_media_url(str(embed))

    match = _EMBED_URL_RE.search(html or "")
    if match:
        return f"https://www.whoreshub.com/embed/{match.group('id')}"
    return None


def _streams_from_html(html: str, video_url: str) -> dict[str, Any]:
    embed = _extract_embed_url(html, video_url)
    streams: list[dict[str, str]] = []
    if embed:
        streams.append({"url": embed, "quality": "embed", "format": "embed"})
    return {
        "streams": streams,
        "hls": None,
        "default": embed,
        "has_video": bool(embed),
    }


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if text:
        return text

    from app.core.pool import fetch_html as pool_fetch_html

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-webp", "data-original", "data-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _info_value(soup: BeautifulSoup, icon_id: str) -> Optional[str]:
    for li in soup.select(".list-info li.wrap"):
        use = li.select_one("svg use")
        if not use:
            continue
        ref = str(use.get("xlink:href") or use.get("href") or "")
        if icon_id not in ref:
            continue
        val = li.select_one(".value")
        if val:
            text = re.sub(r"\s+", " ", val.get_text(" ", strip=True)).strip()
            return text or None
    return None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s+\d{1,3}%\s+\d[\d\.\s]*[kKmMbB]?\s*$", "", t).strip()
    return t or None


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    flash = _parse_flashvars(html)

    title_el = soup.select_one("h1.title")
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            flash.get("video_title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        flash.get("preview_url"),
    )
    if not thumbnail:
        for link in soup.select('link[rel="preload"][as="image"][href*="videos_screenshots"]'):
            thumbnail = str(link.get("href") or "")
            if thumbnail:
                break
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    duration = _info_value(soup, "icon-duration")
    views = _info_value(soup, "icon-view")
    upload_date = _info_value(soup, "icon-calendar")

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]

    model_link = soup.select_one(
        ".video-holder a[href*='/models/'], .info-top a[href*='/models/'], "
        ".video-holder a[href*='/pornstars/'], .info-top a[href*='/pornstars/']"
    )
    channel_link = soup.select_one(
        ".video-holder a[href*='/channels/'], .info-top a[href*='/channels/']"
    )
    uploader = _first_non_empty(
        flash.get("video_models"),
        model_link.get_text(" ", strip=True) if model_link else None,
        channel_link.get_text(" ", strip=True) if channel_link else None,
    )

    preview_url = None
    img_preview = soup.select_one("img[data-preview]")
    if img_preview and img_preview.get("data-preview"):
        preview_url = _normalize_media_url(str(img_preview.get("data-preview")))

    video = _streams_from_html(html, url)

    return {
        "url": url,
        "title": title,
        "description": _meta(soup, prop="og:description") or _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": flash.get("video_categories"),
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=canon)
    return parse_video_page(html, canon)


_SKIP_LIST_BLOCK_RE = re.compile(
    r"related|watched_right_now|most_recent|album|trend|search_results|aside|popular_tags",
    re.IGNORECASE,
)


def _list_path_parts(base_url: str) -> list[str]:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]
    if parts and parts[-1].isdigit():
        parts = parts[:-1]
    return parts


def _is_homepage(base_url: str) -> bool:
    return not _list_path_parts(base_url)


def _normalize_list_path(base_url: str) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    parsed = urlparse(raw)
    parts = _list_path_parts(base_url)

    if not parts:
        path = "/"
    else:
        path = "/" + "/".join(parts) + "/"

    return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", path, "", "", ""))


def _pagination_list_path(base_url: str) -> str:
    parts = _list_path_parts(base_url)
    if not parts:
        return "/latest-updates/"
    return "/" + "/".join(parts) + "/"


def _page_path_key(page_url: str) -> str:
    parsed = urlparse(page_url)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]
    if parts and parts[-1].isdigit():
        parts = parts[:-1]
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def _list_section_candidates(page_url: str) -> list[str]:
    path = _page_path_key(page_url).lower().rstrip("/") or "/"

    if path == "/":
        return [
            "list_videos_recently_added_videos_items",
            "custom_list_videos_recently_added_videos_items",
        ]
    if path == "/latest-updates":
        return [
            "list_videos_latest_videos_list_items",
            "custom_list_videos_latest_videos_list_items",
            "list_videos_common_videos_list_items",
            "custom_list_videos_common_videos_list_items",
        ]
    if path == "/top-rated":
        return [
            "list_videos_top_rated_videos_list_items",
            "custom_list_videos_top_rated_videos_list_items",
            "list_videos_common_videos_list_items",
            "custom_list_videos_common_videos_list_items",
        ]
    if path in ("/most-popular", "/most-viewed"):
        return [
            "list_videos_most_popular_videos_list_items",
            "list_videos_most_viewed_videos_list_items",
            "custom_list_videos_most_popular_videos_list_items",
            "custom_list_videos_most_viewed_videos_list_items",
            "list_videos_common_videos_list_items",
            "custom_list_videos_common_videos_list_items",
        ]
    if path.startswith("/categories/") or path.startswith("/search/") or path.startswith("/tags/"):
        return [
            "list_videos_common_videos_list_items",
            "custom_list_videos_common_videos_list_items",
        ]
    if path.startswith("/models/"):
        return [
            "list_videos_common_videos_list_items",
            "custom_list_videos_common_videos_list_items",
        ]
    return [
        "list_videos_common_videos_list_items",
        "custom_list_videos_common_videos_list_items",
        "list_videos_latest_videos_list_items",
    ]


def _block_has_videos(root: Any) -> bool:
    return bool(root and root.select("a[href*='/videos/']"))


def _list_root(soup: BeautifulSoup, page_url: str) -> Any:
    for section_id in _list_section_candidates(page_url):
        if _SKIP_LIST_BLOCK_RE.search(section_id):
            continue
        root = soup.select_one(f"#{section_id}")
        if _block_has_videos(root):
            return root
        base_id = section_id.removesuffix("_items")
        if _SKIP_LIST_BLOCK_RE.search(base_id):
            continue
        root = soup.select_one(f"#{base_id}")
        if _block_has_videos(root):
            return root

    for pag in soup.select("[id*='pagination']"):
        pag_id = (pag.get("id") or "").lower()
        if _SKIP_LIST_BLOCK_RE.search(pag_id):
            continue
        container = pag.find_parent("section") or pag.find_parent("div", class_=re.compile(r"section-row|thumbs"))
        if _block_has_videos(container):
            return container

    for thumbs in soup.select(".thumbs:not(.thumbs--albums)"):
        thumbs_id = (thumbs.get("id") or "").lower()
        if _SKIP_LIST_BLOCK_RE.search(thumbs_id):
            continue
        if _block_has_videos(thumbs):
            return thumbs
    return None


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)

    if _is_homepage(base_url):
        if page_num <= 1:
            return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", "/", "", "", ""))
        paginated = urlparse(urljoin(BASE_SITE, _pagination_list_path(base_url)))
        query = {"from": str(page_num)}
        return urlunparse((paginated.scheme, paginated.netloc, paginated.path, "", urlencode(query), ""))

    paginated_path = _pagination_list_path(base_url)
    paginated = urlparse(urljoin(BASE_SITE, paginated_path))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)
    query.pop("from", None)

    if page_num <= 1:
        return urlunparse((paginated.scheme, paginated.netloc, paginated.path, "", urlencode(query), ""))

    query["from"] = str(page_num)
    return urlunparse((paginated.scheme, paginated.netloc, paginated.path, "", urlencode(query), ""))


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    classes = box.get("class") or []
    if "item--adv-thumb" in classes or "avd-video-item" in classes:
        return None

    link = box.select_one("a.item[href*='/videos/'], a[href*='/videos/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img = box.select_one("img")
    thumb = _best_image_url(img)
    preview = None
    if img and img.get("data-preview"):
        preview = _normalize_media_url(str(img.get("data-preview")))

    title_el = box.select_one(".title")
    title = _clean_list_title(
        _first_non_empty(
            link.get("title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        )
    ) or "Unknown Video"

    duration = None
    time_el = box.select_one(".duration, .time")
    if time_el:
        duration = time_el.get_text(strip=True)

    views = None
    for item in box.select(".thumb-item"):
        if item.select_one(".icon-eye"):
            views = re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
            break

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "preview_url": preview,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    list_base = _normalize_list_path(base_url)
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=list_base)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = _list_root(soup, page_url)
    if root is None:
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    boxes = root.select(":scope > .thumb")
    if not boxes:
        boxes = root.select(".thumb")
    if not boxes:
        boxes = root.select(".item")

    for box in boxes:
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for a in root.select("a.item[href*='/videos/'], a[href*='/videos/']"):
            if len(items) >= limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href or href in seen:
                continue
            img = a.find("img")
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": _clean_list_title(a.get("title") or (img.get("alt") if img else None))
                    or "Unknown Video",
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:limit]
