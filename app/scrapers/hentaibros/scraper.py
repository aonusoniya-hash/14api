from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://hentaibros.net/"
DEFAULT_BROWSE_URL = "https://hentaibros.net/"
SITE_HOST = "hentaibros.net"
SITE_ALIASES = frozenset({"hentaibros.net", "www.hentaibros.net"})
STREAM_CDN_HOSTS = frozenset({"povblowjob.net"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaibros\.net/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_NON_VIDEO_SLUGS = frozenset(
    {
        "blog",
        "contact",
        "dmca",
        "feed",
        "genres",
        "hentai-list",
        "page",
        "partner",
        "privacy-policy",
        "terms-of-use",
        "wp-admin",
        "wp-content",
        "wp-json",
        "wp-login.php",
        "xmlrpc.php",
    }
)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_FLOWPLAYER_DATA_ITEM_RE = re.compile(
    r'<div[^>]+class="[^"]*flowplayer[^"]*"[^>]+data-item="([^"]+)"',
    re.IGNORECASE,
)
_MP4_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.mp4(?:\?[^\s"'<>]*)?""", re.IGNORECASE)
_M3U8_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.m3u8(?:\?[^\s"'<>]*)?""", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".hentaibros.net"):
        return True
    return h in STREAM_CDN_HOSTS or any(h.endswith("." + cdn) for cdn in STREAM_CDN_HOSTS)


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
    t = html.unescape(str(title).strip())
    for suffix in (
        " - New Hentai",
        " | Hentai Bros",
        " - Hentai Bros",
        " – Hentai Bros",
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


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-main-thumb", "data-src", "data-original", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        return _normalize_media_url(str(v).strip())
    return None


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
    if "just a moment" in low and "hentaibros" not in low and "flowplayer" not in low:
        return True
    if "enable javascript and cookies" in low and "flowplayer" not in low:
        return True
    return False


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/{slug.strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host != SITE_HOST:
        return None
    path = (parsed.path or "").strip("/")
    if not path or "/" in path:
        return None
    slug = path.split("/")[0].lower()
    if slug in _NON_VIDEO_SLUGS or slug.startswith("anime") or slug.startswith("genre"):
        return None
    if path.startswith("anime/") or path.startswith("genres/"):
        return None
    return _canonical_video_url(path)


def _resolve_video_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.endswith("/"):
        raw += "/"
    match = _VIDEO_PAGE_RE.match(raw)
    if not match:
        raise ValueError(f"Unsupported HentaiBros URL: {url}")
    slug = match.group("slug").lower()
    if slug in _NON_VIDEO_SLUGS:
        raise ValueError(f"Unsupported HentaiBros URL: {url}")
    return raw


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
    return await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)


def _parse_flowplayer_item(raw_html: str) -> dict[str, Any]:
    match = _FLOWPLAYER_DATA_ITEM_RE.search(raw_html or "")
    if match:
        try:
            payload = json.loads(html.unescape(match.group(1)))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    for node in BeautifulSoup(raw_html or "", "lxml").select(".flowplayer[data-item]"):
        raw = node.get("data-item")
        if not raw:
            continue
        try:
            payload = json.loads(html.unescape(str(raw)))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    return {}


def _streams_from_player_html(page_html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    player_item = _parse_flowplayer_item(page_html)
    for source in player_item.get("sources") or []:
        if not isinstance(source, dict):
            continue
        src = _normalize_media_url(_first_non_empty(source.get("src"), source.get("url")))
        if not src or src in seen:
            continue
        seen.add(src)
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        label = "source" if len(streams) == 0 else f"Mirror {len(streams) + 1}"
        streams.append({"quality": label, "url": src, "format": fmt})
        if fmt == "hls" and hls_url is None:
            hls_url = src

    if not streams:
        for pattern, fmt in ((_M3U8_URL_RE, "hls"), (_MP4_URL_RE, "mp4")):
            for match in pattern.finditer(page_html or ""):
                src = _normalize_media_url(match.group(0))
                if not src or src in seen:
                    continue
                seen.add(src)
                label = "source" if len(streams) == 0 else f"Mirror {len(streams) + 1}"
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

    for block in soup.select("article.loop-video, article.video-preview-item, article.post.format-video"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href]")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                img.get("alt") if img else None,
                block.get("data-main-thumb") and link.get("title"),
                link.get("title"),
                link.get_text(" ", strip=True),
            )
        )
        if title:
            title = _DURATION_RE.sub("", title).strip()
        title = title or "Unknown Video"

        duration_el = block.select_one(".duration")
        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)
        if not duration:
            duration = _extract_duration(link.get_text(" ", strip=True))

        thumb = _normalize_media_url(block.get("data-main-thumb")) or _best_image_url(img)

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": None,
                "uploader_name": "hentaibros",
            }
        )

    if len(items) < limit:
        for link in soup.select("a[href]"):
            if len(items) >= limit:
                break
            url = _normalize_video_href(link.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            img = link.select_one("img")
            title = _clean_title(
                _first_non_empty(img.get("alt") if img else None, link.get("title"), link.get_text(" ", strip=True))
            )
            if not title:
                continue
            title = _DURATION_RE.sub("", title).strip() or "Unknown Video"
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(img),
                    "duration": _extract_duration(link.get_text(" ", strip=True)),
                    "views": None,
                    "uploader_name": "hentaibros",
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
    query.pop("page", None)

    m_page = _PATH_PAGE_SUFFIX_RE.match(path)
    if m_page:
        path = m_page.group(1)

    if not path:
        new_path = "/" if page_num <= 1 else f"/page/{page_num}/"
    else:
        base_path = "/" + path.strip("/") + "/"
        new_path = base_path if page_num <= 1 else f"{base_path}page/{page_num}/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
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
        _best_image_url(soup.select_one("article img, .video-player img, img")),
    )

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    duration_el = soup.select_one(".video-player .duration, .responsive-player .duration, .duration")
    duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)

    tags: list[str] = []
    for a in soup.select(".tags a, .post-tags a, .tag-links a, a[rel='tag']"):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    category: Optional[str] = None
    for a in soup.select("a[href*='/anime/']"):
        label = a.get_text(strip=True)
        if label:
            category = label
            break

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": "hentaibros",
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
    page_html = await fetch_page(page_url, referer=BASE_SITE)
    video_data = _streams_from_player_html(page_html)
    return parse_video_page(page_html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        page_html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(page_html, "lxml")
    return _parse_list_items(soup, limit=limit)
