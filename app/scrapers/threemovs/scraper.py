from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.3movs.com/"
SITE_HOST = "3movs.com"
SITE_ALIASES = frozenset({"3movs.com", "www.3movs.com", "img.3movs.com"})

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
    r"3movs\.com/videos/(?P<id>\d+)/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_url|video_alt_url|video_alt_url2|video_url_text|video_alt_url_text|"
    r"video_models|video_tags|video_categories|preview_url|video_title)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_DOWNLOAD_HREF_RE = re.compile(
    r'href="(https?://(?:www\.)?3movs\.com/get_file/[^"]+\.mp4[^"]*)"[^>]*>\s*(\d{3,4}p)',
    re.IGNORECASE,
)
_GET_FILE_RE = re.compile(
    r"https?://(?:www\.)?3movs\.com/get_file/[^\s\"'<>]+",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".3movs.com")


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
    for suffix in (" - 3movs.com", " | 3movs.com", " - 3Movs", " | 3Movs"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
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
    if "3movs.com" not in href.lower():
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    slug = m.group("slug").strip("/")
    return f"https://www.3movs.com/videos/{m.group('id')}/{slug}/"


def _quality_from_url_and_label(url: str, label: str | None) -> str:
    low = (url or "").lower()
    if "_lq" in low or "low quality" in (label or "").lower():
        return "360p"
    if "_720" in low or "720" in (label or ""):
        return "720p"
    if "_480" in low:
        return "480p"
    if "_1080" in low or "1080" in (label or ""):
        return "1080p"
    qm = re.search(r"_(\d{3,4})p", low)
    if qm:
        return f"{qm.group(1)}p"
    qm2 = re.search(r"(\d{3,4})p", label or "", re.I)
    if qm2:
        return f"{qm2.group(1)}p"
    if "high quality" in (label or "").lower():
        return "720p"
    return "default"


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = _FLASHVARS_BLOCK_RE.search(html or "")
    if not m:
        return out
    block = m.group(1)
    for key, value in _FLASHVARS_PAIR_RE.findall(block):
        out[key.lower()] = value.strip()
    return out


def _streams_from_html(html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    flash = _parse_flashvars(html)

    stream_keys = (
        ("video_url", "video_url_text"),
        ("video_alt_url", "video_alt_url_text"),
        ("video_alt_url2", "video_alt_url_text"),
    )
    for url_key, label_key in stream_keys:
        raw = flash.get(url_key)
        if not raw:
            continue
        media = _normalize_media_url(raw)
        if media in seen or "/get_file/" not in media:
            continue
        if "_preview" in media.lower():
            continue
        seen.add(media)
        label = flash.get(label_key)
        fmt = "hls" if ".m3u8" in media.lower() else "mp4"
        streams.append(
            {
                "url": media,
                "quality": _quality_from_url_and_label(media, label),
                "format": fmt,
            }
        )

    for href, quality_label in _DOWNLOAD_HREF_RE.findall(html):
        media = _normalize_media_url(href.split("&download=", 1)[0])
        if media in seen or "_preview" in media.lower():
            continue
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _quality_from_url_and_label(media, quality_label),
                "format": "mp4",
            }
        )

    for media in _GET_FILE_RE.findall(html.replace("\\/", "/")):
        media = _normalize_media_url(media)
        if media in seen or "_preview" in media.lower():
            continue
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _quality_from_url_and_label(media, None),
                "format": "hls" if ".m3u8" in media.lower() else "mp4",
            }
        )

    video_id = _extract_video_id(video_url)
    if video_id:
        embed = f"https://www.3movs.com/embed/{video_id}"
        if embed not in seen:
            seen.add(embed)
            streams.append({"url": embed, "quality": "embed", "format": "embed"})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, qnum)

    streams.sort(key=_score, reverse=True)
    hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
    default = next((s["url"] for s in streams if s.get("format") == "mp4"), None)
    if not default:
        default = hls or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls,
        "default": default,
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

    from app.core.pool import fetch_html as pool_fetch_html

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


async def _resolve_get_file_url(get_file_url: str, *, referer: str) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    raw = get_file_url.strip()
    if not raw:
        return None
    headers = {
        "User-Agent": _DEFAULT_HEADERS["User-Agent"],
        "Referer": referer if referer.startswith("http") else BASE_SITE,
        "Accept": "*/*",
    }

    async def _attempt(target: str) -> Optional[str]:
        async with AsyncSession(impersonate="chrome120", headers=headers, timeout=20.0) as client:
            resp = await client.get(target, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location") or resp.headers.get("location")
                if loc and loc.startswith("http") and "3movs.com/get_file" not in loc:
                    return loc
        return None

    candidates = [raw]
    if not raw.endswith("/"):
        candidates.append(raw + "/")

    for target in candidates:
        try:
            resolved = await asyncio.wait_for(_attempt(target), timeout=16.0)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


def _url_contains_video_id(url: str, video_id: str) -> bool:
    low = (url or "").lower()
    vid = str(video_id).lower()
    return f"/{vid}/" in low or f"/{vid}." in low or f"/{vid}?" in low


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")
    ]
    if not get_file_streams:
        return

    video_id = _extract_video_id(referer)
    unique_by_url = {s["url"]: s for s in get_file_streams}

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _resolve_get_file_url(stream["url"], referer=referer)
        return stream, resolved

    pairs = await asyncio.gather(*[_resolve_one(s) for s in unique_by_url.values()])
    for stream, resolved in pairs:
        if resolved:
            if video_id and not _url_contains_video_id(resolved, video_id):
                if stream in streams:
                    streams.remove(stream)
                continue
            stream["url"] = resolved
        elif stream in streams:
            streams.remove(stream)

    remote_mp4 = [
        s
        for s in streams
        if s.get("format") == "mp4" and "get_file" not in (s.get("url") or "")
    ]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if remote_mp4:
        video["default"] = remote_mp4[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(remote_mp4) or bool(hls) or bool(embed)


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-webp", "data-original", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
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

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1.title").get_text(" ", strip=True) if soup.select_one("h1.title") else None,
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

    duration = None
    views = None
    upload_date = None
    for item in soup.select("ul.list_info li.item"):
        icon = item.find("i")
        span = item.find("span")
        if not icon or not span:
            continue
        text = span.get_text(strip=True)
        classes = " ".join(icon.get("class") or [])
        if "icon-clock" in classes:
            duration = text
        elif "icon-eye" in classes:
            views = text
        elif "icon-calendar" in classes:
            upload_date = text

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]

    uploader = flash.get("video_models") or None
    preview_url = None
    img_preview = soup.select_one(".wrap_image img[data-preview], img[data-preview]")
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
    data = parse_video_page(html, canon)
    await _resolve_video_streams(data.get("video", {}), referer=canon)
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    path = parsed.path or "/"

    if page_num > 1 and path.rstrip("/") in ("", "/"):
        path = "/latest-updates/"

    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if not parts:
        parts = ["latest-updates"]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
    else:
        new_path = "/" + "/".join(parts + [str(page_num)]) + "/"

    return urlunparse((parsed.scheme or "https", parsed.netloc or "www.3movs.com", new_path, "", "", ""))


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    link = box.select_one("a.wrap_image[href], a.title[href]")
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

    title_el = box.select_one("a.title")
    title = _clean_list_title(
        _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            link.get("title"),
            img.get("alt") if img else None,
        )
    ) or "Unknown Video"

    duration = None
    time_el = box.select_one(".time")
    if time_el:
        duration = time_el.get_text(strip=True)

    views = None
    for item in box.select(".list_item"):
        if item.select_one(".icon-eye"):
            span = item.find("span")
            if span:
                views = span.get_text(strip=True)
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

    boxes = soup.select(".thumbs .item.thumb, #custom_list_videos_most_recent_videos_items .item.thumb")
    if not boxes:
        boxes = soup.select(".item.thumb")

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
                    "title": _clean_list_title(a.get("title") or (img.get("alt") if img else None)) or "Unknown Video",
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:limit]
