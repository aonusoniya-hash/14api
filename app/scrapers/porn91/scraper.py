from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

DEFAULT_BASE_SITE = "https://0511.sp2026.dev/"
SITE_ALIASES = frozenset({"91porn.com", "www.91porn.com", "sp2026.dev", "9p9.xyz"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN,zh;q=0.8",
    "Referer": DEFAULT_BASE_SITE,
}

_VIDEO_PAGE_PATHS = frozenset({"view_video.php", "view_video_hd.php"})
_VIEWKEY_RE = re.compile(r"viewkey=([^&\"'\s<>]+)", re.IGNORECASE)
_STRENCODE2_RE = re.compile(r"""strencode2\s*\(\s*["']([^"']+)["']\s*\)""", re.IGNORECASE)
_SOURCE_SRC_RE = re.compile(
    r"""<source[^>]+src\s*=\s*["'](?P<url>https?://[^"']+)["']""",
    re.IGNORECASE,
)
_VIEWS_RE = re.compile(r"Views:\s*</span>\s*(\d[\d,\s]*)", re.IGNORECASE)
_FROM_RE = re.compile(r"From:\s*</span>\s*([^<\n]+)", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".91porn.com"):
        return True
    if h == "sp2026.dev" or h.endswith(".sp2026.dev"):
        return True
    if h == "9p9.xyz" or h.endswith(".9p9.xyz"):
        return True
    return False


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _site_origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return DEFAULT_BASE_SITE


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str | None:
    try:
        from curl_cffi.requests import Session
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or _site_origin(url)

    def _do_request() -> str:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                with Session(impersonate=imp, headers=headers, verify=False) as session:
                    resp = session.get(url, timeout=45.0)
                if resp.status_code == 200:
                    return resp.text
            except Exception:
                continue
        raise ValueError(f"Failed to fetch {url}")

    try:
        return await asyncio.to_thread(_do_request)
    except Exception:
        return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer)
    if text:
        return text

    import ssl

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or _site_origin(url)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return await pool_fetch_html(url, headers=headers, ssl=ssl_ctx)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    text = html.unescape(str(title).strip())
    for suffix in (" - 91porn", " | 91porn", " – 91porn"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return match.group(0) if match else None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _extract_viewkey(url: str) -> Optional[str]:
    match = _VIEWKEY_RE.search(url or "")
    return match.group(1) if match else None


def _is_video_page_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower().lstrip("/")
    return path in _VIDEO_PAGE_PATHS and bool(_extract_viewkey(url))


def _normalize_video_href(href: str, *, origin: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(origin, href.lstrip("/"))
    viewkey = _extract_viewkey(href)
    if not viewkey:
        return None
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if host and not can_handle(host):
        return None
    return urljoin(origin, f"view_video.php?viewkey={viewkey}")


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "src"):
        value = img.get(key)
        if not value:
            continue
        url = str(value).strip()
        if not url:
            continue
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return url
        return url
    return None


def _is_blocked_stream_url(url: str) -> bool:
    low = (url or "").lower()
    if not low.startswith("http"):
        return True
    blocked = (
        "kwai.net",
        "ad-i18n-dsp",
        "googlesyndication",
        "doubleclick",
        "trafficjunky",
        "exoclick",
        "preview.mp4.jpg",
        ".gif",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    )
    return any(marker in low for marker in blocked)


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    if _is_blocked_stream_url(url):
        return None
    if ".m3u8" in low:
        return "hls"
    if ".mp4" in low:
        return "mp4"
    return None


def _extract_source_urls_from_html_fragment(fragment: str) -> list[str]:
    urls: list[str] = []
    for match in _SOURCE_SRC_RE.finditer(fragment):
        url = match.group("url").replace("\\/", "/").strip()
        if url and not _is_blocked_stream_url(url):
            urls.append(url)
    return urls


def _extract_strencode2_sources(page_html: str) -> list[str]:
    urls: list[str] = []
    for match in _STRENCODE2_RE.finditer(page_html):
        decoded = unquote(match.group(1))
        urls.extend(_extract_source_urls_from_html_fragment(decoded))
    return urls


def _strip_html_comments(html: str) -> str:
    return re.sub(r"<!--[\s\S]*?-->", "", html or "")


def _extract_streams(soup: BeautifulSoup, page_html: str, page_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        url = url.replace("\\/", "/").strip()
        if not url or url in seen or _is_blocked_stream_url(url):
            return
        fmt = _detect_media_format(url)
        if fmt not in ("mp4", "hls"):
            return
        seen.add(url)
        streams.append({"url": url, "quality": "source", "format": fmt})

    for url in _extract_strencode2_sources(page_html):
        _add(url)

    if not streams:
        cleaned_html = _strip_html_comments(page_html)
        cleaned_soup = BeautifulSoup(cleaned_html, "lxml")
        for source in cleaned_soup.select("video source[src], source[src]"):
            _add(str(source.get("src") or ""))

    mp4_streams = [s for s in streams if s.get("format") == "mp4"]
    hls_streams = [s for s in streams if s.get("format") == "hls"]

    default_url = mp4_streams[0]["url"] if mp4_streams else None
    if not default_url and hls_streams:
        default_url = hls_streams[0]["url"]

    final_streams = mp4_streams[:1] if mp4_streams else hls_streams[:1]

    return {
        "streams": final_streams,
        "hls": hls_streams[0]["url"] if hls_streams else None,
        "default": default_url,
        "has_video": bool(default_url),
    }


def parse_video_page(page_html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")

    title = "Unknown Video"
    for header in soup.select("h4.login_register_header"):
        header_text = header.get_text(" ", strip=True)
        low = header_text.lower()
        if not header_text or "video information" in low or "comment on this video" in low:
            continue
        cleaned = _clean_title(header_text)
        if cleaned:
            title = cleaned
            break
    if title == "Unknown Video" and soup.title:
        title = _clean_title(soup.title.get_text(strip=True)) or title

    thumbnail = None
    video_el = soup.select_one("video#player_one, video.video-js, video")
    if video_el is not None and video_el.get("poster"):
        thumbnail = str(video_el.get("poster")).strip()
        if thumbnail.startswith("//"):
            thumbnail = f"https:{thumbnail}"

    duration = None
    runtime_el = soup.select_one(".video-info-span")
    if runtime_el:
        duration = _extract_duration(runtime_el.get_text(" ", strip=True))
    if not duration:
        duration = _extract_duration(soup.get_text(" ", strip=True))

    views = None
    for span in soup.select("span.video-info-span"):
        parent_text = span.parent.get_text(" ", strip=True) if span.parent else ""
        if "views" in parent_text.lower():
            views = _normalize_views(span.get_text(" ", strip=True))
            break
    if not views:
        match = _VIEWS_RE.search(page_html)
        if match:
            views = _normalize_views(match.group(1))

    uploader = None
    from_block = soup.select_one("#videodetails-content")
    if from_block is not None:
        match = _FROM_RE.search(str(from_block))
        if match:
            uploader = html.unescape(match.group(1)).strip()
    if not uploader:
        uploader_el = soup.select_one("#videodetails-content span.title-yakov a span.title")
        if uploader_el:
            uploader = html.unescape(uploader_el.get_text(" ", strip=True))

    tags: list[str] = []
    keywords = soup.find("meta", attrs={"name": "keywords"})
    if keywords and keywords.get("content"):
        tags = [t.strip() for t in str(keywords.get("content")).split(",") if t.strip()]

    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "91porn",
        "tags": tags,
        "video": _extract_streams(soup, page_html, url),
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    canonical = url
    if not _is_video_page_url(url):
        viewkey = _extract_viewkey(url)
        if viewkey:
            canonical = urljoin(_site_origin(url), f"view_video.php?viewkey={viewkey}")
        else:
            raise ValueError(f"Unsupported 91porn URL: {url}")

    page_html = await fetch_page(canonical, referer=_site_origin(canonical))
    return parse_video_page(page_html, canonical)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or DEFAULT_BASE_SITE).strip()
    if not raw.startswith("http"):
        raw = urljoin(DEFAULT_BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    path = parsed.path or "/"
    if path in ("/", "") and not query.get("category"):
        path = "/v.php"
        query.setdefault("category", "ori")
        query.setdefault("viewtype", "basic")
    if page > 1:
        query["page"] = str(page)
    else:
        query.pop("page", None)
    path = path or "/index.php"
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", urlencode(query), ""))


def _parse_list_item(well: Any, *, origin: str) -> Optional[dict[str, Any]]:
    link = well.select_one("a[href*='view_video.php']")
    if link is None:
        return None

    href = _normalize_video_href(link.get("href") or "", origin=origin)
    if not href:
        return None

    title_el = well.select_one(".video-title")
    title = _clean_title(title_el.get_text(" ", strip=True) if title_el else link.get_text(" ", strip=True))
    if not title:
        title = "Unknown Video"

    img = well.select_one(".thumb-overlay img, img.img-responsive")
    thumb = _best_image_url(img)

    duration_el = well.select_one(".duration")
    duration = duration_el.get_text(strip=True) if duration_el else _extract_duration(well.get_text(" ", strip=True))

    views = None
    views_match = re.search(r"Views:\s*</span>\s*(\d[\d,\s]*)", str(well), flags=re.IGNORECASE)
    if views_match:
        views = _normalize_views(views_match.group(1))
    if not views:
        views = _extract_views_from_text(well.get_text(" ", strip=True))

    uploader = None
    from_match = _FROM_RE.search(str(well))
    if from_match:
        uploader = html.unescape(from_match.group(1)).strip()

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
    }


def _extract_views_from_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"Views:\s*(\d[\d,\s]*)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return _normalize_views(match.group(1))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    origin = _site_origin(base_url or DEFAULT_BASE_SITE)
    page_url = _build_list_page_url(base_url or DEFAULT_BASE_SITE, page)
    try:
        page_html = await fetch_page(page_url, referer=origin)
    except Exception:
        return []

    soup = BeautifulSoup(page_html, "lxml")
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for well in soup.select("div.well.well-sm.videos-text-align"):
        if len(items) >= limit:
            break
        parsed = _parse_list_item(well, origin=origin)
        if not parsed:
            continue
        viewkey = _extract_viewkey(parsed["url"])
        if not viewkey or viewkey in seen_keys:
            continue
        seen_keys.add(viewkey)
        items.append(parsed)

    if items:
        return items[:limit]

    for link in soup.select("a[href*='view_video.php']"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(link.get("href") or "", origin=origin)
        if not href:
            continue
        viewkey = _extract_viewkey(href)
        if not viewkey or viewkey in seen_keys:
            continue
        seen_keys.add(viewkey)
        container = link.find_parent("div") or link
        img = link.select_one("img") or (container.find("img") if container else None)
        title_el = link.select_one(".video-title")
        items.append(
            {
                "url": href,
                "title": _clean_title(title_el.get_text(" ", strip=True) if title_el else link.get_text(" ", strip=True))
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": _extract_duration(container.get_text(" ", strip=True) if container else None),
                "views": _extract_views_from_text(container.get_text(" ", strip=True) if container else None),
                "uploader_name": None,
            }
        )

    return items[:limit]
