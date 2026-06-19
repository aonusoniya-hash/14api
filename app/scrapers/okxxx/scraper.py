from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://ok.xxx/"
SITE_HOST = "ok.xxx"
SITE_ALIASES = frozenset({"ok.xxx", "www.ok.xxx", "static.ok.xxx"})

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
    r"ok\.xxx/(?:video|embed)/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_GET_FILE_RE = re.compile(
    r"https?://(?:www\.)?ok\.xxx/get_file/[^\s\"'<>]+",
    re.IGNORECASE,
)
_ISO_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".ok.xxx")


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
    for suffix in (" - OK.XXX", " | OK.XXX", " - OKXXX", " | OKXXX"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    t = re.sub(r"^Video\s*🌶️?\s*", "", t, flags=re.IGNORECASE).strip()
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


def _canonical_video_url(video_id: str) -> str:
    return f"https://ok.xxx/video/{video_id}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if "ok.xxx" not in href.lower():
        return None
    m = re.search(r"/video/(\d+)/?", href, re.IGNORECASE)
    if not m:
        return None
    return _canonical_video_url(m.group(1))


def _parse_iso_duration(value: str | None) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    m = _ISO_DURATION_RE.fullmatch(raw)
    if not m:
        return raw
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _quality_from_url_and_label(url: str, label: str | None) -> str:
    low = (url or "").lower()
    label_low = (label or "").lower().strip()
    if label_low and label_low not in {"auto", "default"}:
        qm = re.search(r"(\d{3,4})p?", label_low)
        if qm:
            return f"{qm.group(1)}p"
        return label_low
    qm = re.search(r"_(\d{3,4})p", low)
    if qm:
        return f"{qm.group(1)}p"
    if "_720" in low:
        return "720p"
    if "_480" in low:
        return "480p"
    if "_360" in low:
        return "360p"
    if "_1080" in low:
        return "1080p"
    return "default"


def _parse_video_object_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return {}


def _streams_from_html(html: str, video_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    video_el = soup.select_one("video")
    if video_el:
        for source in video_el.find_all("source"):
            src = _normalize_media_url(str(source.get("src") or ""))
            if not src or src in seen or "_preview" in src.lower():
                continue
            seen.add(src)
            label = _first_non_empty(source.get("label"), source.get("title"))
            fmt = "hls" if ".m3u8" in src.lower() else "mp4"
            streams.append(
                {
                    "url": src,
                    "quality": _quality_from_url_and_label(src, label),
                    "format": fmt,
                }
            )

    for media in _GET_FILE_RE.findall(html.replace("\\/", "/")):
        media = _normalize_media_url(media)
        if not media or media in seen or "_preview" in media.lower():
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
        embed = f"https://ok.xxx/embed/{video_id}"
        if embed not in seen:
            seen.add(embed)
            streams.append({"url": embed, "quality": "embed", "format": "embed"})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        qm = re.search(r"(\d{3,4})", qtxt)
        qnum = int(qm.group(1)) if qm else 0
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
                if loc and loc.startswith("http") and "ok.xxx/get_file" not in loc.lower():
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
    return f"/{vid}/" in low or f"/{vid}." in low or f"/{vid}?" in low or f"/{vid}," in low


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "").lower()
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
        s for s in streams if s.get("format") == "mp4" and "get_file" not in (s.get("url") or "").lower()
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
    for key in ("data-original", "data-src", "data-webp", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _meta_from_video_meta_list(box: Any) -> tuple[Optional[str], Optional[str]]:
    duration = None
    views = None
    for item in box.select(".video-meta li"):
        icon = item.find("i")
        span = item.find("span")
        if not span:
            continue
        text = span.get_text(strip=True)
        classes = " ".join(icon.get("class") or []) if icon else ""
        if "fa-clock" in classes or "icon-clock" in classes:
            duration = text
        elif "fa-eye" in classes or "icon-eye" in classes:
            views = text
    return duration, views


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_video_object_ld(soup)

    title = _clean_title(
        _first_non_empty(
            ld.get("name"),
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(ld.get("thumbnailUrl"), _meta(soup, prop="og:image"))
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    duration = _parse_iso_duration(str(ld.get("duration") or "")) if ld.get("duration") else None
    views = None
    interaction = ld.get("interactionStatistic")
    if isinstance(interaction, dict):
        views = _first_non_empty(interaction.get("userInteractionCount"))

    upload_date = _first_non_empty(ld.get("uploadDate"))
    uploader = _first_non_empty(ld.get("author"))

    keywords = ld.get("keywords")
    tags: list[str] = []
    if isinstance(keywords, list):
        tags = [str(t).strip() for t in keywords if str(t).strip()]
    elif isinstance(keywords, str):
        tags = [t.strip() for t in re.split(r"[,|]", keywords) if t.strip()]

    actors = ld.get("actor")
    if isinstance(actors, list):
        for actor in actors:
            name = actor if isinstance(actor, str) else (actor.get("name") if isinstance(actor, dict) else None)
            if name and str(name).strip() not in tags:
                tags.append(str(name).strip())

    preview_url = None
    preview_link = soup.select_one("a[data-preview-custom], a[data-preview]")
    if preview_link:
        preview_url = _normalize_media_url(
            str(preview_link.get("data-preview-custom") or preview_link.get("data-preview") or "")
        ) or None

    video = _streams_from_html(html, url)

    return {
        "url": url,
        "title": title,
        "description": _first_non_empty(ld.get("description"), _meta(soup, prop="og:description")),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": uploader,
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError("Could not extract OK.XXX video id from URL")

    canon = _canonical_video_url(video_id)
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
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
        return urlunparse((parsed.scheme, parsed.netloc, new_path, "", urlencode(query), ""))

    if query:
        query["page"] = str(page_num)
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
        return urlunparse((parsed.scheme, parsed.netloc, new_path, "", urlencode(query), ""))

    if parts:
        new_path = "/" + "/".join(parts + [str(page_num)]) + "/"
        return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))

    query["page"] = str(page_num)
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", urlencode(query), ""))


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    link = box.select_one("a[href*='/video/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img = box.select_one("img")
    thumb = _best_image_url(img)
    preview = _normalize_media_url(
        str(link.get("data-preview-custom") or link.get("data-preview") or "")
    ) or None

    title = _clean_list_title(
        _first_non_empty(
            link.get("title"),
            img.get("alt") if img else None,
            link.get_text(" ", strip=True),
        )
    ) or "Unknown Video"

    duration, views = _meta_from_video_meta_list(box)
    site_link = box.select_one(".content_items a[href*='/sites/']")
    uploader = site_link.get_text(" ", strip=True) if site_link else None

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "preview_url": preview,
    }


def _clean_list_title(title: str | None) -> Optional[str]:
    return _clean_title(title)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    safe_limit = min(max(1, int(limit) if limit else 60), 120)

    boxes = soup.select(".item.thumb-bl-video, .item.thumb-bl")
    for box in boxes:
        if len(items) >= safe_limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for link in soup.select("a[href*='/video/']"):
            if len(items) >= safe_limit:
                break
            href = _normalize_video_href(link.get("href") or "")
            if not href or href in seen:
                continue
            img = link.find("img")
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": _clean_list_title(
                        _first_non_empty(link.get("title"), img.get("alt") if img else None)
                    ) or "Unknown Video",
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": _normalize_media_url(
                        str(link.get("data-preview-custom") or link.get("data-preview") or "")
                    ) or None,
                }
            )

    return items[:safe_limit]
