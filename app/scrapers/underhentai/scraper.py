from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.underhentai.net/"
DEFAULT_BROWSE_URL = "https://www.underhentai.net/"
SITE_HOST = "www.underhentai.net"
SITE_ALIASES = frozenset({"underhentai.net", "www.underhentai.net"})
STREAM_HOSTS = frozenset(
    {
        "static.underhentai.net",
        "krakenfiles.com",
        "krakencloud.net",
        "luluvdo.com",
        "lulucdn.com",
    }
)

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
    r"^https?://(?:www\.)?underhentai\.net/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_WATCH_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?underhentai\.net/watch/?\?(?P<query>[^#]+)$",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?underhentai\.net)?/(?P<slug>[a-z0-9][a-z0-9-]+)/?",
    re.IGNORECASE,
)
_NON_VIDEO_SLUGS = frozenset(
    {
        "cat",
        "dmca",
        "embed",
        "feed",
        "go",
        "index",
        "out",
        "page",
        "pop",
        "random",
        "refer",
        "recommend",
        "recommends",
        "releases",
        "tag",
        "top",
        "uncensored",
        "watch",
        "wp-admin",
        "wp-content",
        "wp-json",
        "wp-login.php",
        "xmlrpc.php",
    }
)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$", re.IGNORECASE)
_KRAKEN_EMBED_RE = re.compile(
    r"""https?://krakenfiles\.com/embed-video/([A-Za-z0-9]+)""",
    re.IGNORECASE,
)
_LULU_EMBED_RE = re.compile(
    r"""https?://luluvdo\.com/embed/([A-Za-z0-9]+)""",
    re.IGNORECASE,
)
_M3U8_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.m3u8(?:\?[^\s"'<>]*)?""", re.IGNORECASE)
_MP4_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.mp4(?:\?[^\s"'<>]*)?""", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".underhentai.net"):
        return True
    if h in STREAM_HOSTS:
        return True
    return any(h.endswith("." + cdn) for cdn in STREAM_HOSTS)


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
        " - UnderHentai",
        " | UnderHentai",
        " – UnderHentai",
        " &#8211; UnderHentai",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
        elif t.endswith(suffix):
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
    for key in ("src", "data-src", "data-original"):
        url = _normalize_media_url(img.get(key))
        if url:
            return url
    return None


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/{slug.strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href in ("/", "#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host not in SITE_ALIASES and not host.endswith(".underhentai.net"):
        return None
    path = (parsed.path or "").strip("/")
    if not path or "/" in path:
        return None
    slug = path.lower()
    if slug in _NON_VIDEO_SLUGS:
        return None
    if parsed.query or parsed.fragment:
        return None
    return _canonical_video_url(path)


def _resolve_video_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    if _WATCH_PAGE_RE.match(raw):
        return raw
    if not raw.endswith("/"):
        raw += "/"
    match = _VIDEO_PAGE_RE.match(raw.rstrip("/") + "/")
    if not match:
        raise ValueError(f"Unsupported UnderHentai URL: {url}")
    slug = match.group("slug").lower()
    if slug in _NON_VIDEO_SLUGS:
        raise ValueError(f"Unsupported UnderHentai URL: {url}")
    return raw if raw.endswith("/") else f"{raw}/"


def _is_watch_url(url: str) -> bool:
    return bool(_WATCH_PAGE_RE.match((url or "").strip().split("#", 1)[0]))


def _extract_watch_links(soup: BeautifulSoup) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a.ep2-stream[href], a[href*='/watch/']"):
        href = _normalize_media_url(a.get("href") or "")
        if not href or "/watch/" not in href or href in seen:
            continue
        seen.add(href)
        links.append(href)
    return links


def _extract_embed_urls(page_html: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, label in ((_KRAKEN_EMBED_RE, "KrakenFiles"), (_LULU_EMBED_RE, "LuluStream")):
        for match in pattern.finditer(page_html or ""):
            embed_url = match.group(0).strip().replace("\\/", "/")
            if embed_url in seen:
                continue
            seen.add(embed_url)
            found.append((label, embed_url))
    return found


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
                return resp.text
            except Exception:
                continue
        raise ValueError(f"Failed to fetch: {url}")

    return await asyncio.to_thread(_do_request)


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    return await _fetch_with_curl_cffi(url, referer=referer or DEFAULT_BROWSE_URL)


async def _resolve_kraken_embed(embed_url: str) -> list[dict[str, str]]:
    try:
        page_html = await fetch_page(embed_url, referer=DEFAULT_BROWSE_URL)
    except Exception:
        return []
    soup = BeautifulSoup(page_html, "lxml")
    streams: list[dict[str, str]] = []
    for source in soup.select("video source[src], source[src]"):
        src = _normalize_media_url(source.get("src"))
        if not src:
            continue
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        streams.append({"quality": "KrakenFiles", "url": src, "format": fmt})
    if not streams:
        for match in _MP4_URL_RE.finditer(page_html):
            src = _normalize_media_url(match.group(0))
            if src:
                streams.append({"quality": "KrakenFiles", "url": src, "format": "mp4"})
                break
    return streams


async def _streams_from_watch_html(page_html: str, watch_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    for label, embed_url in _extract_embed_urls(page_html):
        resolved = await _resolve_kraken_embed(embed_url) if "krakenfiles.com" in embed_url else []
        if resolved:
            for stream in resolved:
                key = stream["url"]
                if key in seen:
                    continue
                seen.add(key)
                streams.append(stream)
                if stream.get("format") == "hls" and hls_url is None:
                    hls_url = key
        else:
            if embed_url in seen:
                continue
            seen.add(embed_url)
            streams.append({"quality": label, "url": embed_url, "format": "embed"})

    if watch_url not in seen:
        seen.add(watch_url)
        streams.append({"quality": "Watch Page", "url": watch_url, "format": "embed"})

    default = next(
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

    for block in soup.select("article.data-block"):
        if len(items) >= limit:
            break
        link = block.select_one(".article-header h2 a[href], .article-section a[href]")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                link.get_text(strip=True),
                img.get("title") if img else None,
                img.get("alt") if img else None,
            )
        ) or "Unknown Video"

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": None,
                "views": None,
                "uploader_name": "underhentai",
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
                _first_non_empty(
                    img.get("alt") if img else None,
                    img.get("title") if img else None,
                    link.get("title"),
                )
            )
            if not title:
                continue
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": "underhentai",
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

    m_page = _PATH_PAGE_SUFFIX_RE.match(path)
    if m_page:
        path = m_page.group(1)

    if page_num > 1:
        if query.get("s"):
            query["page"] = str(page_num)
            new_path = "/" if not path else f"/{path}/"
        else:
            new_path = f"/{path}/page/{page_num}/" if path else f"/page/{page_num}/"
    else:
        query.pop("page", None)
        new_path = "/" if not path else f"/{path}/"

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
            soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _normalize_media_url(
            soup.select_one(".content-head img.img-responsive, .content-box img.img-responsive").get("src")
        )
        if soup.select_one(".content-head img.img-responsive, .content-box img.img-responsive")
        else None,
    )

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    tags: list[str] = []
    for a in soup.select('a[href*="/tag/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    category: Optional[str] = None
    for a in soup.select('a[href*="/cat/brand/"]'):
        label = a.get_text(strip=True)
        if label:
            category = label
            break

    upload_date: Optional[str] = None
    for box in soup.select(".content-box.sidebar-light.content-foot, .content-box.content-foot.sidebar-light"):
        label = box.select_one("p")
        if not label or "aired" not in label.get_text(" ", strip=True).lower():
            continue
        value = box.select_one(".label-primary")
        if value:
            upload_date = value.get_text(strip=True)
            break

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": "underhentai",
        "category": category,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    raw_url = (url or "").strip().split("#", 1)[0]
    if _is_watch_url(raw_url):
        watch_url = raw_url if raw_url.startswith("http") else urljoin(BASE_SITE, raw_url.lstrip("/"))
        watch_html = await fetch_page(watch_url, referer=DEFAULT_BROWSE_URL)
        video_data = await _streams_from_watch_html(watch_html, watch_url)
        soup = BeautifulSoup(watch_html, "lxml")
        title = _clean_title(
            _first_non_empty(
                soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
                _meta(soup, prop="og:title"),
                soup.title.get_text(strip=True) if soup.title else None,
            )
        ) or "Unknown Video"
        return {
            "url": watch_url,
            "title": title,
            "description": _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description")),
            "thumbnail_url": _first_non_empty(
                _meta(soup, prop="og:image"),
                _normalize_media_url(soup.select_one("img.img-responsive").get("src"))
                if soup.select_one("img.img-responsive")
                else None,
            ),
            "duration": None,
            "views": None,
            "uploader_name": "underhentai",
            "category": None,
            "tags": None,
            "upload_date": None,
            "video": {
                k: v
                for k, v in video_data.items()
                if k in ("streams", "hls", "default", "has_video")
            },
            "related_videos": _parse_list_items(soup, limit=20),
        }

    page_url = _resolve_video_url(url)
    page_html = await fetch_page(page_url, referer=DEFAULT_BROWSE_URL)
    soup = BeautifulSoup(page_html, "lxml")
    watch_links = _extract_watch_links(soup)
    video_data: dict[str, Any] = {"streams": [], "hls": None, "default": None, "has_video": False}
    if watch_links:
        watch_html = await fetch_page(watch_links[0], referer=page_url)
        video_data = await _streams_from_watch_html(watch_html, watch_links[0])
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
