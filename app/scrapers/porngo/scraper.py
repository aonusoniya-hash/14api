from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html, pool

BASE_SITE = "https://www.porngo.com/"
SITE_HOST = "porngo.com"
SITE_ALIASES = frozenset({"porngo.com", "www.porngo.com"})

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
    r"porngo\.com/videos/(?P<id>\d+)/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_DURATION_SCRIPT_RE = re.compile(r"everyX\s*=\s*Math\.floor\((\d+)\s*/\s*100\)")
_QUALITY_IN_URL_RE = re.compile(r"_(\d{3,4})[pm]\.mp4", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".porngo.com")


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
        " - PornGO.com",
        " | PornGO.com",
        " - PornGO",
        " | PornGO",
        " - porngo.com",
        " | porngo.com",
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
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u


def _format_duration(seconds: int) -> str:
    secs = max(0, int(seconds))
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _extract_duration(html: str) -> Optional[str]:
    m = _DURATION_SCRIPT_RE.search(html or "")
    if m:
        return _format_duration(int(m.group(1)))
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if "porngo.com" not in href.lower():
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    slug = m.group("slug").strip("/")
    return f"https://www.porngo.com/videos/{m.group('id')}/{slug}/"


def _quality_from_url(url: str, label: str = "") -> str:
    label = (label or "").strip()
    if label and label.lower() != "default":
        mq = re.search(r"(\d{3,4})[pP]", label)
        if mq:
            return f"{mq.group(1)}p"
        return label
    mq = _QUALITY_IN_URL_RE.search(url or "")
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"(\d{3,4})[pP]", url or "")
    if mq:
        return f"{mq.group(1)}p"
    return "default"


def _stream_key(url: str) -> str:
    parsed = urlparse(_normalize_media_url(url))
    return f"{parsed.netloc}{parsed.path}".lower()


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    seen_urls: set[str] = set()

    soup = BeautifulSoup(html, "lxml")
    for video_el in soup.select("video.video-js, video"):
        for tag in video_el.find_all("source"):
            src = _normalize_media_url(str(tag.get("src") or ""))
            if not src:
                continue
            key = _stream_key(src)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            quality = _quality_from_url(src, str(tag.get("label") or ""))
            fmt = "hls" if ".m3u8" in src else "mp4"
            streams.append({"quality": quality, "url": src, "format": fmt})
            if fmt == "hls":
                hls_url = hls_url or src

    for link in soup.select(".video-links__link[href*='/get_file/']"):
        href = _normalize_media_url(str(link.get("href") or ""))
        if not href:
            continue
        key = _stream_key(href)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        text = link.get_text(" ", strip=True)
        quality = _quality_from_url(href, text)
        streams.append({"quality": quality, "url": href, "format": "mp4"})

    if not streams:
        for match in re.finditer(
            r"https?://(?:www\.)?porngo\.com/get_file/[^\"'\s<>]+\.mp4[^\"'\s<>]*",
            html or "",
            re.IGNORECASE,
        ):
            src = _normalize_media_url(match.group(0))
            if not src:
                continue
            key = _stream_key(src)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            streams.append(
                {
                    "quality": _quality_from_url(src),
                    "url": src,
                    "format": "mp4",
                }
            )

    def _qval(s: dict) -> int:
        digits = "".join(filter(str.isdigit, str(s.get("quality", ""))))
        return int(digits) if digits else 0

    streams.sort(key=_qval, reverse=True)
    default_url = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(streams),
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

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


async def _resolve_redirect(url: str) -> str:
    headers = dict(_DEFAULT_HEADERS)
    try:
        session = await pool.get_session()
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            final_url = str(response.url)
            if any(x in final_url for x in (".mp4", ".m3u8", "ahcdn.com", "cdn", "get_file")):
                return final_url
    except Exception:
        pass
    return url


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


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1.headline__title")
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(_meta(soup, prop="og:image"))
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    video_el = soup.select_one("video.video-js, video")
    if video_el and video_el.get("poster"):
        thumbnail = thumbnail or _normalize_media_url(str(video_el.get("poster")))

    views = None
    for text_el in soup.select(".video-info__text"):
        text = text_el.get_text(" ", strip=True)
        if "view" in text.lower():
            views = text
            break

    duration = _extract_duration(html)

    tags: list[str] = []
    for link in soup.select(".video-links__row .video-links__link[href*='/categories/']"):
        txt = link.get_text(strip=True)
        if txt:
            tags.append(txt)

    model_link = soup.select_one(".video-links__link[href*='/models/']")
    site_link = soup.select_one(".video-links__link[href*='/sites/']")
    uploader = _first_non_empty(
        model_link.get_text(" ", strip=True) if model_link else None,
        site_link.get_text(" ", strip=True) if site_link else None,
    )

    added_by = None
    for row in soup.select(".video-links__row"):
        title_span = row.select_one(".video-links__title")
        if not title_span or "added by" not in title_span.get_text(strip=True).lower():
            continue
        value = row.select_one(".video-links__list")
        if value:
            added_by = value.get_text(" ", strip=True)
        break

    preview_url = None
    preview_div = soup.select_one(".thumb__img[data-preview], [data-preview]")
    if preview_div and preview_div.get("data-preview"):
        preview_url = _normalize_media_url(str(preview_div.get("data-preview")))

    video = _extract_video_streams(html)

    return {
        "url": url,
        "title": title,
        "description": _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader or added_by,
        "category": tags[0] if tags else None,
        "tags": tags,
        "upload_date": None,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=canon)
    data = parse_video_page(html, canon)

    video_data = data.get("video") or {}
    streams = video_data.get("streams") or []
    if streams:
        resolved_streams = []
        for stream in streams:
            stream = dict(stream)
            if "get_file" in stream.get("url", ""):
                stream["url"] = await _resolve_redirect(stream["url"])
                if ".m3u8" in stream["url"]:
                    stream["format"] = "hls"
                elif ".mp4" in stream["url"]:
                    stream["format"] = "mp4"
            resolved_streams.append(stream)
        video_data["streams"] = resolved_streams
        if video_data.get("default") and "get_file" in str(video_data["default"]):
            video_data["default"] = await _resolve_redirect(str(video_data["default"]))
        data["video"] = video_data

    return data


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
        return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", "", ""))

    if not parts:
        new_path = f"/latest-updates/{page_num}/"
        return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", "", ""))

    new_path = "/" + "/".join(parts + [str(page_num)]) + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", "", ""))


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    link = box.select_one("a.thumb__top[href*='/videos/'], a[href*='/videos/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img_wrap = box.select_one(".thumb__img")
    img = img_wrap.select_one("img") if img_wrap else box.select_one("img")
    thumb = _best_image_url(img)
    preview = None
    if img_wrap and img_wrap.get("data-preview"):
        preview = _normalize_media_url(str(img_wrap.get("data-preview")))

    title_el = box.select_one(".thumb__title")
    title = _clean_title(
        _first_non_empty(
            link.get("title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        )
    ) or "Unknown Video"

    duration = None
    duration_el = box.select_one(".thumb__duration")
    if duration_el:
        duration = duration_el.get_text(strip=True)

    views = None
    for text_el in box.select(".thumb__text"):
        text = text_el.get_text(" ", strip=True)
        if "view" in text.lower():
            views = text
            break

    uploader = None
    model_link = box.select_one(".thumb-models__link")
    if model_link:
        uploader = model_link.get_text(" ", strip=True)

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
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
        ".thumbs-list .thumb.item, #list_videos_videos_items .thumb.item, "
        "#list_videos_most_recent_videos_items .thumb.item, .thumb.item"
    )

    for box in boxes:
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for a in soup.select("a[href*='/videos/']"):
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
                    "title": _clean_title(a.get("title") or (img.get("alt") if img else None))
                    or "Unknown Video",
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:limit]
