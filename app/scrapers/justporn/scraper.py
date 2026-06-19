from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.justporn.com/"
SITE_HOST = "justporn.com"
SITE_ALIASES = frozenset({"justporn.com", "www.justporn.com"})

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
    r"justporn\.com/video/(?P<id>\d+)/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_models|video_tags|video_categories|preview_url|video_title|video_id)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_EMBED_URL_RE = re.compile(
    r"https?://(?:www\.)?justporn\.com/embed/(?P<id>\d+)",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".justporn.com")


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
        " | Just Porn",
        " - Just Porn",
        " | JustPorn",
        " - JustPorn",
        " | justporn.com",
        " - justporn.com",
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
    if "justporn.com" not in href.lower():
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    slug = m.group("slug").strip("/")
    return f"https://www.justporn.com/video/{m.group('id')}/{slug}/"


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
        return f"https://www.justporn.com/embed/{video_id}"

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
        return f"https://www.justporn.com/embed/{match.group('id')}"
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


def _count_item_value(soup: BeautifulSoup, icon_class: str) -> Optional[str]:
    scope = soup.select_one(".video-holder") or soup
    for item in scope.select(".count-item"):
        if not item.select_one(f".{icon_class}"):
            continue
        text = re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
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
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    duration = _count_item_value(soup, "icon-oclock")
    views = _count_item_value(soup, "icon-eye")
    upload_date = _count_item_value(soup, "icon-calendar")

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]

    model_link = soup.select_one(".video-holder a[href*='/pornstars/'], .video-holder a[href*='/models/']")
    channel_link = soup.select_one(".video-holder a[href*='/channels/']")
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
        "description": _meta(soup, prop="og:description"),
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


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
        return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))

    if not parts:
        new_path = f"/latest-updates/{page_num}/"
        return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))

    new_path = "/" + "/".join(parts + [str(page_num)]) + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    classes = box.get("class") or []
    if "item--adv-thumb" in classes or "avd-video-item" in classes:
        return None

    link = box.select_one("a[href*='/video/']")
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
    time_el = box.select_one(".time")
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
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    boxes = soup.select(
        ".thumbs .thumb.item, #list_videos_most_recent_videos_items .thumb.item, .thumb.thumb_rel.item"
    )
    if not boxes:
        boxes = soup.select(".thumb.item")

    for box in boxes:
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for a in soup.select("a[href*='/video/']"):
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
