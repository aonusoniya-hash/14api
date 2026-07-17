from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.thepornbang.com/"
SITE_HOST = "thepornbang.com"
SITE_ALIASES = frozenset({"thepornbang.com", "www.thepornbang.com"})

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
    r"thepornbang\.com/video/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"thepornbang\.com/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_id|video_title|video_categories|video_tags|video_url|video_url_text|"
    r"video_alt_url|video_alt_url_text|video_alt_url2|video_alt_url2_text|"
    r"video_alt_url3|video_alt_url3_text|preview_url)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_GET_STREAM_QUALITY_RE = re.compile(r"get_stream/\d+-(\d{3,4})\.mp4", re.IGNORECASE)

_STREAM_FIELD_PAIRS = (
    ("video_url", "video_url_text"),
    ("video_alt_url", "video_alt_url_text"),
    ("video_alt_url2", "video_alt_url2_text"),
    ("video_alt_url3", "video_alt_url3_text"),
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".thepornbang.com")


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
        " | ThePornBang",
        " - ThePornBang",
        " | ThePornBang.com",
        " - ThePornBang.com",
        " | PornBang",
        " - PornBang",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    t = re.sub(r"\s*#\S+$", "", t).strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin("https://www.thepornbang.com/", u.lstrip("/"))
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin("https://www.thepornbang.com/", href.lstrip("/"))
    lower = href.lower()
    if "thepornbang.com" not in lower:
        return None
    if "/videos_" in lower or lower.rstrip("/").endswith("/videos"):
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    slug = m.group("slug").strip("/")
    return f"https://www.thepornbang.com/video/{slug}/"


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_HREF_RE.search(url or ""))


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = _FLASHVARS_BLOCK_RE.search(html or "")
    if not m:
        return out
    for key, value in _FLASHVARS_PAIR_RE.findall(m.group(1)):
        out[key.lower()] = value.strip()
    return out


def _normalize_quality_label(label: str | None) -> str:
    text = str(label or "").strip()
    if not text:
        return "unknown"
    if text.isdigit():
        return f"{text}p"
    lower = text.lower()
    if lower in {"4k", "uhd", "2160"}:
        return "2160p"
    mq = re.search(r"(\d{3,4})[pP]", text)
    if mq:
        return f"{mq.group(1)}p"
    return text


def _quality_rank(label: str | None) -> int:
    normalized = _normalize_quality_label(label).lower()
    digits = "".join(filter(str.isdigit, normalized))
    if digits:
        return int(digits)
    if normalized == "embed":
        return 0
    return 0


def _quality_from_url(url: str, label: str = "") -> str:
    normalized = _normalize_quality_label(label)
    if normalized not in {"unknown", "default", "auto"}:
        return normalized
    mq = _GET_STREAM_QUALITY_RE.search(url or "")
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"-(\d{3,4})\.mp4", url or "")
    if mq:
        return f"{mq.group(1)}p"
    return "default"


def _stream_key(url: str) -> str:
    parsed = urlparse(_normalize_media_url(url))
    path = (parsed.path or "/").rstrip("/").lower()
    return f"{parsed.netloc.lower()}{path}"


def _dedupe_streams(streams: list[dict[str, str]]) -> list[dict[str, str]]:
    by_url: dict[str, dict[str, str]] = {}
    for stream in streams:
        src_url = _normalize_media_url(str(stream.get("url") or ""))
        if not src_url:
            continue
        entry = dict(stream)
        entry["url"] = src_url
        entry["quality"] = _normalize_quality_label(str(stream.get("quality") or "default"))
        key = _stream_key(src_url)
        existing = by_url.get(key)
        if existing is None or _quality_rank(entry["quality"]) >= _quality_rank(existing.get("quality")):
            by_url[key] = entry

    by_quality: dict[str, dict[str, str]] = {}
    for stream in sorted(by_url.values(), key=lambda s: _quality_rank(s.get("quality")), reverse=True):
        quality = _normalize_quality_label(str(stream.get("quality") or "default"))
        if quality not in by_quality:
            by_quality[quality] = stream

    return sorted(by_quality.values(), key=lambda s: _quality_rank(s.get("quality")), reverse=True)


def _extract_video_streams(html: str) -> dict[str, Any]:
    flash = _parse_flashvars(html)
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for url_key, label_key in _STREAM_FIELD_PAIRS:
        src = _normalize_media_url(flash.get(url_key) or "")
        if not src or "get_stream" not in src:
            continue
        key = _stream_key(src)
        if key in seen:
            continue
        seen.add(key)
        label = flash.get(label_key) or ""
        streams.append(
            {
                "url": src,
                "quality": _quality_from_url(src, label),
                "format": "mp4",
            }
        )

    if not streams:
        for match in re.finditer(
            r"https?://(?:www\.)?thepornbang\.com/get_stream/[^\"'\s<>]+\.mp4[^\"'\s<>]*",
            html or "",
            re.IGNORECASE,
        ):
            src = _normalize_media_url(match.group(0))
            if not src:
                continue
            key = _stream_key(src)
            if key in seen:
                continue
            seen.add(key)
            streams.append(
                {
                    "url": src,
                    "quality": _quality_from_url(src),
                    "format": "mp4",
                }
            )

    for video_el in BeautifulSoup(html, "lxml").select("video source"):
        src = _normalize_media_url(str(video_el.get("src") or ""))
        if not src:
            continue
        key = _stream_key(src)
        if key in seen:
            continue
        seen.add(key)
        streams.append(
            {
                "url": src,
                "quality": _quality_from_url(src, str(video_el.get("label") or "")),
                "format": "hls" if ".m3u8" in src else "mp4",
            }
        )

    streams = _dedupe_streams(streams)
    default_url = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
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
                await client.get(BASE_SITE)
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.text) > 2000:
                    return resp.text
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


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-original", "data-webp", "data-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _video_info_scope(soup: BeautifulSoup) -> Any:
    return soup.select_one(".video-info") or soup


def _parse_video_stats(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    scope = _video_info_scope(soup)
    views_el = scope.select_one(".views")
    views = None
    if views_el:
        views = re.sub(r"\s+", " ", views_el.get_text(" ", strip=True)).strip()
        views = re.sub(r"\s*views\s*$", "", views, flags=re.IGNORECASE).strip()

    duration = None
    for el in scope.select(".duration"):
        text = el.get_text(" ", strip=True)
        if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", text):
            duration = text
            break
    return duration, views


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    flash = _parse_flashvars(html)

    title_el = soup.select_one("h1")
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            flash.get("video_title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        flash.get("preview_url"),
    )
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)
    video_id = flash.get("video_id")
    if not thumbnail and video_id:
        thumbnail = _normalize_media_url(f"/images/thumb/{video_id}.webp")

    duration, views = _parse_video_stats(soup)

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]
    if not tags:
        for link in soup.select(".video-info a[href*='/category/'], .video-info a[href*='/tag/']"):
            txt = link.get_text(" ", strip=True)
            if txt:
                tags.append(txt)

    model_link = soup.select_one(".video-info a[href*='/model/'], .video-info a[href*='/models/']")
    channel_link = soup.select_one(".video-info a[href*='/channel/'], .video-info a[href*='/channels/']")
    uploader = _first_non_empty(
        model_link.get_text(" ", strip=True) if model_link else None,
        channel_link.get_text(" ", strip=True) if channel_link else None,
    )

    preview_url = None
    if video_id:
        preview_url = _normalize_media_url(f"/images/video/{video_id}.webm")
    img_preview = soup.select_one("img[data-preview]")
    if img_preview and img_preview.get("data-preview"):
        preview_url = _normalize_media_url(str(img_preview.get("data-preview")))

    video = _extract_video_streams(html)
    if not video.get("has_video") and video_id:
        embed_url = f"https://www.thepornbang.com/embed/{video_id}/"
        video = {
            "streams": [{"url": embed_url, "quality": "embed", "format": "embed"}],
            "hls": None,
            "default": embed_url,
            "has_video": True,
        }

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
        "upload_date": None,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def _resolve_scrape_url(url: str) -> str:
    if _is_embed_url(url):
        html = await fetch_page(url, referer=BASE_SITE)
        flash = _parse_flashvars(html)
        video_id = flash.get("video_id")
        if video_id:
            for match in re.finditer(
                rf"https?://(?:www\.)?thepornbang\.com/video/[^\"'\s<>]+",
                html or "",
                re.IGNORECASE,
            ):
                canon = _normalize_video_href(match.group(0))
                if canon:
                    return canon
    return _normalize_video_href(url) or url


async def scrape(url: str) -> dict[str, Any]:
    canon = await _resolve_scrape_url(url)
    html = await fetch_page(canon, referer=BASE_SITE)
    return parse_video_page(html, canon)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin("https://www.thepornbang.com/", raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if not parts:
        parts = ["home36"]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
        return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))

    new_path = "/" + "/".join(parts + [str(page_num)]) + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))


def _list_root(soup: BeautifulSoup) -> Any:
    for section_id in (
        "list_videos_latest_videos_list_items",
        "list_videos_latest_videos_items",
        "list_videos_most_recent_videos_items",
        "list_videos_popular_videos_items",
        "list_videos_recommended_videos_items",
        "list_videos_video_premium_items",
    ):
        root = soup.select_one(f"#{section_id}")
        if root is not None and root.select("a[href*='/video/']"):
            return root

    for root in soup.select("[id$='_items']"):
        if root.select("a.thumb[href*='/video/'], a[href*='/video/']"):
            return root
    return None


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    link = box.select_one("a.thumb[href*='/video/'], a[href*='/video/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img = box.select_one("img.thumb-img, img[data-original], img")
    thumb = _best_image_url(img)
    preview = None
    if img and img.get("data-preview"):
        preview = _normalize_media_url(str(img.get("data-preview")))

    title_el = box.select_one(".text, .description .text, .title")
    title = _clean_title(
        _first_non_empty(
            link.get("title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        )
    ) or "Unknown Video"

    duration_el = box.select_one(".duration")
    duration = duration_el.get_text(" ", strip=True) if duration_el else None

    views_el = box.select_one(".views")
    views = views_el.get_text(" ", strip=True) if views_el else None

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
    root = _list_root(soup)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    boxes: list[Any] = []
    if root is not None:
        boxes = root.select(".row.item, .item")

    for box in boxes:
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        scope = root or soup
        for a in scope.select("a[href*='/video/']"):
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
