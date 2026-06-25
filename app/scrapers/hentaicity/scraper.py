from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.hentaicity.com/"
DEFAULT_BROWSE_URL = "https://www.hentaicity.com/"
SITE_HOST = "www.hentaicity.com"
SITE_ALIASES = frozenset({"hentaicity.com", "www.hentaicity.com"})

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
    r"^https?://(?:www\.)?hentaicity\.com/video/(?P<slug>.+)-(?P<vid>[A-Za-z0-9_-]{11})\.html/?$",
    re.IGNORECASE,
)
_CLICK_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaicity\.com/click/\d+-\d+/video/(?P<slug>.+)-(?P<vid>[A-Za-z0-9_-]{11})\.html/?$",
    re.IGNORECASE,
)
_EMBED_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaicity\.com/embed/(?P<vid>[A-Za-z0-9_-]{11})/?$",
    re.IGNORECASE,
)
_HLS_SOURCE_RE = re.compile(
    r'<source[^>]+src=["\']([^"\']+\.m3u8[^"\']*)["\']',
    re.IGNORECASE,
)
_MP4_SRC_RE = re.compile(
    r'<video[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']',
    re.IGNORECASE,
)
_FLV_PATH_RE = re.compile(r"/flv/(\d+/\d+)/")
_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_MP4_QUALITIES = ("1080p", "720p", "480p", "mobile")
_HOMEPAGE_LISTING = "videos/straight/all-popular.html"
_TAG_PAGE_SUFFIX_RE = re.compile(r"^(tags/video/.+?)/(\d+)$", re.IGNORECASE)
_HTML_PAGE_SUFFIX_RE = re.compile(r"^(.+)-(\d+)\.html$", re.IGNORECASE)
_CLICK_PAGE_PREFIX_RE = re.compile(r"/click/(\d+)-\d+/video/", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return (
        h in SITE_ALIASES
        or h.endswith(".hentaicity.com")
        or h == "hls.hentaicity.com"
    )


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
        " - Hentai City",
        " | Hentai City",
        " - Free Anime Porn Videos",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _parse_iso8601_duration(value: str | None) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip().upper()
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    if total >= 3600:
        return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def _parse_json_ld(html: str) -> dict[str, Any]:
    for match in _JSON_LD_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "VideoObject":
                    return item
    return {}


def _is_cloudflare_challenge(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    low = html.lower()
    if "sorry, you have been blocked" in low:
        return True
    if "just a moment" in low and "hentai city" not in low and "video-id" not in low:
        return True
    if "enable javascript and cookies" in low and "video-id" not in low:
        return True
    return False


def _canonical_video_url(slug: str, vid: str) -> str:
    return f"https://www.hentaicity.com/video/{slug.strip('/')}-{vid}.html"


def _resolve_video_url(url: str) -> str | None:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw:
        return None
    if _VIDEO_PAGE_RE.match(raw):
        return raw if raw.endswith(".html") else f"{raw}.html"
    click = _CLICK_PAGE_RE.match(raw)
    if click:
        return _canonical_video_url(click.group("slug"), click.group("vid"))
    embed = _EMBED_PAGE_RE.match(raw)
    if embed:
        return None
    return None


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
    if host and host != "hentaicity.com" and not host.endswith(".hentaicity.com"):
        return None
    full = href.split("#", 1)[0]
    resolved = _resolve_video_url(full)
    if resolved:
        return resolved
    if "/click/" in full and "/video/" in full and full.endswith(".html"):
        return re.sub(r"/click/\d+-\d+/video/", "/video/", full, count=1)
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str:
    from curl_cffi.requests import AsyncSession

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                text = resp.text
                if _is_cloudflare_challenge(text):
                    continue
                return text
        except Exception:
            continue
    raise ValueError(f"Failed to fetch: {url}")


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    return await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)


def _streams_from_html(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    hls_url: Optional[str] = None

    def _add_stream(url: str, quality: str, fmt: str) -> None:
        nonlocal hls_url
        clean = url.replace("&amp;", "&").strip()
        if not clean or clean in seen_urls:
            return
        seen_urls.add(clean)
        streams.append({"quality": quality, "url": clean, "format": fmt})
        if fmt == "hls" and hls_url is None:
            hls_url = clean

    hls_match = _HLS_SOURCE_RE.search(html)
    if hls_match:
        _add_stream(hls_match.group(1), "adaptive", "hls")

    mp4_match = _MP4_SRC_RE.search(html)
    flv_path: Optional[str] = None
    if mp4_match:
        mp4_url = mp4_match.group(1).replace("&amp;", "&")
        _add_stream(mp4_url, "mobile", "mp4")
        path_match = _FLV_PATH_RE.search(mp4_url)
        if path_match:
            flv_path = path_match.group(1)
    if not flv_path and hls_url:
        path_match = _FLV_PATH_RE.search(hls_url.replace(",default,mobile,480p,720p,1080p,.mp4.urlset", ""))
        if path_match:
            flv_path = path_match.group(1)
        else:
            alt = re.search(r"/_hls/flv/(\d+/\d+)/", hls_url)
            if alt:
                flv_path = alt.group(1)

    if flv_path:
        for quality in _MP4_QUALITIES:
            mp4 = f"https://www.hentaicity.com/flv/{flv_path}/{quality}.mp4"
            if mp4 not in seen_urls:
                _add_stream(mp4, quality, "mp4")

    og_mp4 = re.search(
        r'<meta property="og:video:url" content="([^"]+\.mp4[^"]*)"',
        html,
        re.IGNORECASE,
    )
    if og_mp4:
        _add_stream(og_mp4.group(1), "mobile", "mp4")

    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _normalize_list_path(path: str) -> str:
    raw = (path or "").strip().strip("/")
    if not raw:
        return ""
    m_tag = _TAG_PAGE_SUFFIX_RE.match(raw)
    if m_tag:
        return m_tag.group(1)
    if raw.endswith(".html"):
        base = raw[:-5]
        m_html = _HTML_PAGE_SUFFIX_RE.match(base)
        if m_html:
            return f"{m_html.group(1)}.html"
    return raw


def _click_matches_page(href: str, page: int) -> bool:
    if page <= 1:
        return True
    m = _CLICK_PAGE_PREFIX_RE.search(href or "")
    return bool(m and m.group(1) == str(page))


def _parse_list_items(soup: BeautifulSoup, *, limit: int, page: int = 1) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.item"):
        if len(items) >= limit:
            break
        title_link = block.select_one("a.video-title[href*='/video/'], a.video-title[href*='/click/']")
        thumb_link = block.select_one("a.thumb-img[href*='/video/'], a.thumb-img[href*='/click/']")
        link = title_link or thumb_link
        if not link:
            continue
        href = link.get("href") or ""
        if "/click/" in href and not _click_matches_page(href, page):
            continue
        url = _normalize_video_href(href)
        if not url or url in seen:
            continue
        seen.add(url)
        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                link.get_text(strip=True),
                img.get("alt") if img else None,
            )
        ) or "Unknown Video"
        duration_el = block.select_one(".time")
        duration = _first_non_empty(
            duration_el.get_text(strip=True) if duration_el else None
        )
        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": duration,
                "views": None,
                "uploader_name": None,
            }
        )

    if items:
        return items[:limit]

    for link in soup.select('a[href*="/click/"][href*="/video/"], a[href*="/video/"][href$=".html"]'):
        if len(items) >= limit:
            break
        href = link.get("href") or ""
        if "/click/" in href and not _click_matches_page(href, page):
            continue
        url = _normalize_video_href(href)
        if not url or url in seen:
            continue
        seen.add(url)
        title = _clean_title(link.get("title") or link.get_text(strip=True)) or "Unknown Video"
        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": None,
                "duration": None,
                "views": None,
                "uploader_name": None,
            }
        )
    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = _normalize_list_path(parsed.path or "")
    page_num = max(1, int(page) if page else 1)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)

    if not path:
        if query.get("s"):
            new_path = "/"
            if page_num > 1:
                query["page"] = str(page_num)
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
        path = _HOMEPAGE_LISTING

    if path.startswith("tags/video/"):
        base_path = f"/{path.rstrip('/')}"
        new_path = base_path if page_num <= 1 else f"{base_path}/{page_num}/"
    elif path.endswith(".html"):
        stem = path[:-5]
        new_path = f"/{path}" if page_num <= 1 else f"/{stem}-{page_num}.html"
    else:
        new_path = f"/{path}/" if page_num <= 1 else f"/{path}/{page_num}/"

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
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = _resolve_video_url(url) or url.rstrip("/")
    json_ld = _parse_json_ld(html)
    h1 = soup.select_one("h1")
    page_title = soup.title

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            str(json_ld.get("name") or "").strip() or None,
            h1.get_text(strip=True) if h1 else None,
            page_title.get_text(strip=True) if page_title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        json_ld.get("thumbnailUrl"),
        _best_image_url(soup.select_one("video[poster], img")),
    )
    if thumbnail and str(thumbnail).startswith("//"):
        thumbnail = f"https:{thumbnail}"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
        json_ld.get("description"),
    )

    duration = _first_non_empty(
        _parse_iso8601_duration(str(json_ld.get("duration") or "")),
        _meta(soup, prop="og:video:duration"),
    )
    if duration and duration.isdigit():
        duration = _parse_iso8601_duration(f"PT{duration}S")

    views: Optional[str] = None
    stats = json_ld.get("interactionStatistic")
    if isinstance(stats, dict):
        count = stats.get("userInteractionCount")
        if count is not None:
            views = str(count)

    uploader = _first_non_empty(json_ld.get("author"))
    if not uploader:
        profile = soup.select_one('#taglink a[href*="/profile/"]')
        if profile:
            uploader = profile.get_text(strip=True)

    tags: list[str] = []
    for a in soup.select('#taglink a[href*="/tags/video/"], #taglink a[href*="/videos/straight/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and tag.lower() != (uploader or "").lower():
            tags.append(tag)

    related = _parse_list_items(soup, limit=40, page=1)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or _streams_from_html(html)
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": _first_non_empty(json_ld.get("uploadDate")),
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    initial_url = (url or "").strip()
    fetch_url = initial_url
    resolved = _resolve_video_url(initial_url)
    if resolved:
        fetch_url = resolved
    elif embed_match := _EMBED_PAGE_RE.match(initial_url.split("#", 1)[0]):
        raise ValueError(
            f"Embed URLs require a video page slug; got embed id {embed_match.group('vid')}"
        )

    html = await fetch_page(fetch_url, referer=BASE_SITE)
    canonical = _resolve_video_url(fetch_url) or fetch_url
    canonical_match = re.search(
        r'<link rel="canonical" href="([^"]+/video/[^"]+\.html)"',
        html,
        re.IGNORECASE,
    )
    if canonical_match:
        canonical = canonical_match.group(1)

    video_data = _streams_from_html(html)
    return parse_video_page(html, canonical, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit, page=page)
