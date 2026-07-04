from __future__ import annotations

import asyncio
import ast
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://muchohentai.com/"
DEFAULT_BROWSE_URL = "https://muchohentai.com/home"
SITE_HOST = "muchohentai.com"
SITE_ALIASES = frozenset({"muchohentai.com", "www.muchohentai.com"})
STREAM_CDN_SUFFIX = "edge.tmncdn.io"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": DEFAULT_BROWSE_URL,
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?muchohentai\.com/(?P<prefix>[a-zA-Z0-9]+)/(?P<vid>\d+)/?$",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?muchohentai\.com)?/(?P<prefix>[a-zA-Z0-9]+)/(?P<vid>\d+)/?",
    re.IGNORECASE,
)
_NON_VIDEO_PREFIXES = frozenset(
    {
        "g",
        "genre-list",
        "genres",
        "hentai-series-list",
        "home",
        "latest-hentai-posts",
        "likes",
        "page",
        "random-video",
        "series",
        "tag",
        "tags",
        "upcoming-hentai",
        "wp-admin",
        "wp-content",
        "wp-json",
    }
)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_VIEWS_RE = re.compile(r"([\d,.]+(?:K|M)?)\s*Views?", re.IGNORECASE)
_JW_SERVERS_RE = re.compile(r"var\s+servers\s*=\s*(\[[^\]]+\])\s*;", re.IGNORECASE)
_JW_FILES_RE = re.compile(r'var\s+files\s*=\s*(\[\{[^\]]+\}\])\s*;', re.IGNORECASE)
_M3U8_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.m3u8(?:\?[^\s"'<>]*)?""", re.IGNORECASE)
_MP4_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.mp4(?:\?[^\s"'<>]*)?""", re.IGNORECASE)
_LIST_PATH_ROOTS = frozenset(
    {
        "home",
        "latest-hentai-posts",
        "hentai-series-list",
        "upcoming-hentai",
        "genre-list",
        "g",
    }
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".muchohentai.com"):
        return True
    return h.endswith(STREAM_CDN_SUFFIX) or f".{STREAM_CDN_SUFFIX}" in h


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    out: list[dict] = []
    seen_urls: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        cat_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or cat_id).strip()
        url = str(item.get("url") or "").strip()
        if not cat_id or not url:
            continue
        if not url.startswith("http"):
            url = urljoin(BASE_SITE, url.lstrip("/"))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append({"id": cat_id, "name": name, "url": url})
    return out


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
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
    t = str(title).strip()
    for suffix in (
        " - MuchoHentai",
        " | MuchoHentai",
        " – MuchoHentai",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str | None) -> Optional[str]:
    if not url:
        return None
    raw = str(url).strip().replace("\\/", "/")
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return urljoin(BASE_SITE, raw)
    return raw


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    raw = str(text).strip().upper().replace(",", "")
    mult = 1
    if raw.endswith("K"):
        mult = 1_000
        raw = raw[:-1]
    elif raw.endswith("M"):
        mult = 1_000_000
        raw = raw[:-1]
    try:
        return str(int(float(raw) * mult))
    except ValueError:
        digits = re.sub(r"[^\d]", "", str(text))
        return digits or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    match = _DURATION_RE.search(str(text))
    return match.group(0) if match else None


def _is_cloudflare_challenge(page_html: str) -> bool:
    if not page_html or len(page_html) < 500:
        return True
    low = page_html.lower()
    if "sorry, you have been blocked" in low:
        return True
    if low.count("403") > 3 and "jwplayer" not in low and "var servers" not in low:
        return True
    if "just a moment" in low and "muchohentai" not in low:
        return True
    if "enable javascript and cookies" in low and "jwplayer" not in low:
        return True
    return False


def _canonical_video_url(prefix: str, vid: str) -> str:
    return f"https://{SITE_HOST}/{prefix.strip('/')}/{vid.strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    match = _VIDEO_HREF_RE.search(href or "")
    if not match:
        return None
    prefix = match.group("prefix").lower()
    if prefix in _NON_VIDEO_PREFIXES:
        return None
    return _canonical_video_url(match.group("prefix"), match.group("vid"))


def _resolve_video_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.endswith("/"):
        raw += "/"
    match = _VIDEO_PAGE_RE.match(raw.rstrip("/") + "/")
    if not match:
        raise ValueError(f"Unsupported MuchoHentai URL: {url}")
    prefix = match.group("prefix").lower()
    if prefix in _NON_VIDEO_PREFIXES:
        raise ValueError(f"Unsupported MuchoHentai URL: {url}")
    return raw if raw.endswith("/") else f"{raw}/"


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str:
    from curl_cffi import requests as cr

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    def _do_request() -> str:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                resp = cr.get(url, headers=headers, impersonate=imp, timeout=45.0)
                if resp.status_code != 200:
                    continue
                text = resp.text
                if _is_cloudflare_challenge(text):
                    continue
                return text
            except Exception:
                continue
        raise ValueError(f"Failed to fetch: {url}")

    return await asyncio.to_thread(_do_request)


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    return await _fetch_with_curl_cffi(url, referer=referer or DEFAULT_BROWSE_URL)


def _normalize_jw_file_path(path: str | None) -> Optional[str]:
    if not path:
        return None
    raw = str(path).strip().replace("\\/", "/")
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return raw if raw.startswith("/") else f"/{raw.lstrip('/')}"


def _streams_from_jwplayer(page_html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    servers: list[str] = []
    files: list[dict[str, str]] = []
    servers_match = _JW_SERVERS_RE.search(page_html or "")
    files_match = _JW_FILES_RE.search(page_html or "")
    if servers_match:
        try:
            parsed = ast.literal_eval(servers_match.group(1))
            if isinstance(parsed, list):
                servers = [str(x) for x in parsed if str(x).strip()]
        except (SyntaxError, ValueError):
            pass
    if files_match:
        try:
            parsed = json.loads(files_match.group(1))
            if not isinstance(parsed, list):
                parsed = ast.literal_eval(files_match.group(1))
            if isinstance(parsed, list):
                files = [x for x in parsed if isinstance(x, dict)]
        except (SyntaxError, ValueError, json.JSONDecodeError):
            try:
                parsed = ast.literal_eval(files_match.group(1))
                if isinstance(parsed, list):
                    files = [x for x in parsed if isinstance(x, dict)]
            except (SyntaxError, ValueError):
                pass

    if servers and files:
        for idx, server_name in enumerate(servers):
            label = f"Mirror {idx + 1}" if idx else "source"
            base = f"https://{server_name}.{STREAM_CDN_SUFFIX}"
            for file_item in files:
                rel = _normalize_jw_file_path(file_item.get("file"))
                if not rel:
                    continue
                if rel.startswith("http://") or rel.startswith("https://"):
                    src = rel
                else:
                    src = base + rel if rel.startswith("/") else f"{base}/{rel.lstrip('/')}"
                if src in seen:
                    continue
                seen.add(src)
                fmt = "hls" if ".m3u8" in src.lower() else "mp4"
                streams.append({"quality": label, "url": src, "format": fmt})
                if fmt == "hls" and hls_url is None:
                    hls_url = src

    if not streams:
        for pattern, fmt in ((_M3U8_URL_RE, "hls"), (_MP4_URL_RE, "mp4")):
            for match in pattern.finditer(page_html or ""):
                src = match.group(0).strip().replace("\\/", "/")
                if not src.startswith("http") or SITE_HOST in src or src in seen:
                    continue
                seen.add(src)
                label = "source" if not streams else f"Mirror {len(streams) + 1}"
                streams.append({"quality": label, "url": src, "format": fmt})
                if fmt == "hls" and hls_url is None:
                    hls_url = src

    default = hls_url or next(
        (s["url"] for s in streams if s.get("format") == "mp4"),
        streams[0]["url"] if streams else None,
    )
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for link in soup.select("a[href]"):
        if len(items) >= limit:
            break
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        block = link.find_parent("article") or link.find_parent("div") or link
        img = block.select_one("img") if hasattr(block, "select_one") else None
        title = _clean_title(
            _first_non_empty(
                img.get("alt") if img else None,
                link.get("title"),
                link.get("aria-label"),
            )
        )
        if not title:
            text = block.get_text(" ", strip=True) if hasattr(block, "get_text") else link.get_text(" ", strip=True)
            title = _clean_title(re.sub(_VIEWS_RE, "", text or "").strip())
        title = title or "Unknown Video"

        thumb = _normalize_media_url(
            _first_non_empty(
                img.get("src") if img else None,
                img.get("data-src") if img else None,
            )
        )
        block_text = block.get_text(" ", strip=True) if hasattr(block, "get_text") else ""
        views_match = _VIEWS_RE.search(block_text)
        views = _normalize_views(views_match.group(1)) if views_match else None

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": _extract_duration(block_text),
                "views": views,
                "uploader_name": "muchohentai",
            }
        )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = (parsed.path or "").strip("/")
    page_num = max(1, int(page) if page else 1)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("paged", None)

    m_page = _PATH_PAGE_SUFFIX_RE.match(path)
    if m_page:
        path = m_page.group(1)

    if not path:
        new_path = "/home"
    else:
        parts = path.split("/")
        root = parts[0]
        if root == "g" and len(parts) >= 2:
            new_path = "/" + "/".join(parts[:2])
        elif root in _LIST_PATH_ROOTS or root == "g":
            new_path = "/" + path
        elif query.get("s"):
            new_path = "/"
        else:
            new_path = "/home"

    if page_num > 1:
        if query.get("s"):
            query["paged"] = str(page_num)
        else:
            new_path = f"{new_path.rstrip('/')}/page/{page_num}/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path if new_path.startswith("/") else f"/{new_path}",
            "",
            urlencode(query),
            "",
        )
    )


def parse_video_page(
    page_html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")
    page_url = _resolve_video_url(url)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1.entry-title").get_text(strip=True) if soup.select_one("h1.entry-title") else None,
            soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _normalize_media_url(soup.select_one("meta[name='twitter:image']").get("content"))
        if soup.select_one("meta[name='twitter:image']")
        else None,
    )

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    page_text = soup.get_text(" ", strip=True)
    views_match = _VIEWS_RE.search(page_text)
    views = _normalize_views(views_match.group(1)) if views_match else None
    duration = _extract_duration(page_text)

    tags: list[str] = []
    for a in soup.select("a[rel='tag'], a[href*='/g/']"):
        href = a.get("href") or ""
        if "/g/preview" in href:
            continue
        tag = a.get_text(strip=True)
        if tag and tag.lower() not in {"more", "genres", "genre list"} and tag not in tags:
            tags.append(tag)

    category: Optional[str] = None
    for a in soup.select("a[href*='/series/']"):
        label = a.get_text(strip=True)
        if label:
            category = label
            break

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url.rstrip("/") + "/"]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": "muchohentai",
        "category": category,
        "tags": tags or None,
        "upload_date": None,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    page_url = _resolve_video_url(url)
    page_html = await fetch_page(page_url, referer=DEFAULT_BROWSE_URL)
    video_data = _streams_from_jwplayer(page_html)
    return parse_video_page(page_html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        page_html = await fetch_page(page_url, referer=normalized_base or DEFAULT_BROWSE_URL)
    except Exception:
        return []
    soup = BeautifulSoup(page_html, "lxml")
    return _parse_list_items(soup, limit=limit)
