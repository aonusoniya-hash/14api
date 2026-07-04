from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://henvids.com/"
DEFAULT_BROWSE_URL = "https://henvids.com/latest"
SITE_HOST = "henvids.com"
SITE_ALIASES = frozenset({"henvids.com", "www.henvids.com"})
STREAM_CDN_HOSTS = frozenset({"cdn.henvids.com"})

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
    r"^https?://(?:www\.)?henvids\.com/hentai/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"\b(?:\d{1,2}:){1,2}\d{2}\b")
_ISO_DURATION_RE = re.compile(
    r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$",
    re.IGNORECASE,
)
_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_M3U8_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.m3u8(?:\?[^\s"'<>]*)?""", re.IGNORECASE)
_MP4_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.mp4(?:\?[^\s"'<>]*)?""", re.IGNORECASE)
_THUMB_ALT_SUFFIX_RE = re.compile(
    r"\s+hentai video (?:thumbnail|poster)(?: in 1080p HD)?\.?$",
    re.IGNORECASE,
)
_LIST_PATH_ROOTS = frozenset({"latest", "trending", "tag", "search", "tags"})


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".henvids.com"):
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
    t = str(title).strip()
    if t.lower().startswith("watch "):
        t = t[6:].strip()
    for suffix in (
        " Hentai Video in 1080p HD | HenVids",
        " | HenVids",
        " - HenVids",
        " – HenVids",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
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


def _clean_thumb_alt(alt: str | None) -> Optional[str]:
    if not alt:
        return None
    t = _THUMB_ALT_SUFFIX_RE.sub("", str(alt).strip()).strip()
    return t or None


def _iso_duration_to_clock(value: str | None) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip().upper()
    if _DURATION_RE.fullmatch(raw):
        return raw
    match = _ISO_DURATION_RE.match(raw)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(float(match.group(3) or 0))
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    clock = _iso_duration_to_clock(text)
    if clock:
        return clock
    match = _DURATION_RE.search(str(text))
    return match.group(0) if match else None


def _normalize_views(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        return _normalize_views(value.get("userInteractionCount"))
    raw = str(value).strip().upper().replace(",", "")
    if not raw:
        return None
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
        digits = re.sub(r"[^\d]", "", str(value))
        return digits or None


def _is_cloudflare_challenge(page_html: str) -> bool:
    if not page_html or len(page_html) < 500:
        return True
    low = page_html.lower()
    if "sorry, you have been blocked" in low:
        return True
    if "just a moment" in low and "henvids" not in low:
        return True
    if "enable javascript and cookies" in low and "henvids" not in low:
        return True
    return False


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/hentai/{slug.strip('/')}"


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
    match = re.match(r"^/hentai/([^/?#]+)/?$", parsed.path or "", re.I)
    if not match:
        return None
    return _canonical_video_url(match.group(1))


def _resolve_video_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.endswith("/"):
        raw += "/"
    if _VIDEO_PAGE_RE.match(raw.rstrip("/") + "/"):
        return raw if raw.endswith("/") else f"{raw}/"
    raise ValueError(f"Unsupported HenVids URL: {url}")


def _iter_jsonld_nodes(page_html: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for match in _JSONLD_SCRIPT_RE.finditer(page_html or ""):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                nodes.extend(x for x in graph if isinstance(x, dict))
            else:
                nodes.append(data)
        elif isinstance(data, list):
            nodes.extend(x for x in data if isinstance(x, dict))
    return nodes


def _extract_video_jsonld(page_html: str) -> dict[str, Any]:
    for node in _iter_jsonld_nodes(page_html):
        node_type = node.get("@type")
        if node_type in ("VideoObject", "TVEpisode"):
            return node
    return {}


def _thumbnail_from_jsonld(value: Any) -> Optional[str]:
    if isinstance(value, list):
        for item in value:
            url = _normalize_media_url(str(item) if item is not None else None)
            if url:
                return url
        return None
    return _normalize_media_url(str(value) if value is not None else None)


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


def _streams_from_html(page_html: str, *, slug: str | None = None) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    video_ld = _extract_video_jsonld(page_html)
    for candidate in (
        video_ld.get("contentUrl"),
        _meta(BeautifulSoup(page_html, "lxml"), prop="og:video"),
        _meta(BeautifulSoup(page_html, "lxml"), prop="og:video:url"),
    ):
        src = _normalize_media_url(_first_non_empty(candidate if isinstance(candidate, str) else None))
        if not src or src in seen:
            continue
        seen.add(src)
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        streams.append({"quality": "1080p", "url": src, "format": fmt})
        if fmt == "hls" and hls_url is None:
            hls_url = src

    soup = BeautifulSoup(page_html, "lxml")
    for source in soup.select("video source[src], source[src]"):
        src = _normalize_media_url(source.get("src"))
        if not src or src in seen:
            continue
        seen.add(src)
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        streams.append({"quality": "1080p", "url": src, "format": fmt})
        if fmt == "hls" and hls_url is None:
            hls_url = src

    if not streams and slug:
        guessed = f"https://cdn.henvids.com/hentai/{slug}/playlist.m3u8"
        streams.append({"quality": "1080p", "url": guessed, "format": "hls"})
        hls_url = guessed
        seen.add(guessed)

    if not streams:
        for pattern, fmt in ((_M3U8_URL_RE, "hls"), (_MP4_URL_RE, "mp4")):
            for match in pattern.finditer(page_html or ""):
                src = _normalize_media_url(match.group(0))
                if not src or src in seen:
                    continue
                seen.add(src)
                streams.append({"quality": "1080p", "url": src, "format": fmt})
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

    for block in soup.select("article"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href^='/hentai/'], a[href*='henvids.com/hentai/']")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                _clean_thumb_alt(img.get("alt") if img else None),
                block.select_one("h3").get_text(strip=True) if block.select_one("h3") else None,
                link.get("aria-label", "").replace("Watch ", "").strip() if link.get("aria-label") else None,
            )
        ) or "Unknown Video"

        thumb = _normalize_media_url(
            _first_non_empty(
                img.get("src") if img else None,
                img.get("data-src") if img else None,
            )
        )
        block_text = block.get_text(" ", strip=True)
        duration = _extract_duration(block_text)
        views_match = re.search(r"([\d.]+[KM]?)\s*views", block_text, re.I)
        views = _normalize_views(views_match.group(1)) if views_match else None

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": "henvids",
            }
        )

    if len(items) < limit:
        for link in soup.select("a[href^='/hentai/'], a[href*='henvids.com/hentai/']"):
            if len(items) >= limit:
                break
            url = _normalize_video_href(link.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            img = link.select_one("img") or link.find_previous("img")
            title = _clean_title(
                _first_non_empty(
                    _clean_thumb_alt(img.get("alt") if img else None),
                    link.get("aria-label", "").replace("Watch ", "").strip() if link.get("aria-label") else None,
                )
            ) or "Unknown Video"
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(img),
                    "duration": _extract_duration(link.get_text(" ", strip=True)),
                    "views": None,
                    "uploader_name": "henvids",
                }
            )

    return items[:limit]


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("src", "data-src", "data-original"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        return _normalize_media_url(str(v).strip())
    return None


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = (parsed.path or "").strip("/")
    page_num = max(1, int(page) if page else 1)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)

    root = path.split("/")[0] if path else ""
    if not path or root not in _LIST_PATH_ROOTS:
        new_path = "/latest"
        query.pop("page", None)
        if page_num > 1:
            query["page"] = str(page_num)
        return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", urlencode(query), ""))

    new_path = "/" + path
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


def parse_video_page(
    page_html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")
    page_url = _resolve_video_url(url)
    slug_match = _VIDEO_PAGE_RE.match(page_url.rstrip("/") + "/")
    slug = slug_match.group("slug") if slug_match else None

    video_ld = _extract_video_jsonld(page_html)
    interaction = video_ld.get("interactionStatistic")
    views = _normalize_views(interaction.get("userInteractionCount") if isinstance(interaction, dict) else None)

    title = _clean_title(
        _first_non_empty(
            video_ld.get("name"),
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _thumbnail_from_jsonld(video_ld.get("thumbnailUrl")),
        _meta(soup, prop="og:image"),
        _normalize_media_url(soup.select_one("video").get("poster")) if soup.select_one("video") else None,
        f"https://cdn.henvids.com/hentai/{slug}/thumbnail.avif" if slug else None,
    )

    description = _first_non_empty(
        video_ld.get("description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    duration = _extract_duration(
        _first_non_empty(video_ld.get("duration"), _meta(soup, prop="og:video:duration"))
    )

    tags: list[str] = []
    for a in soup.select("a[href^='/tag/']"):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    genres = video_ld.get("genre")
    if isinstance(genres, list):
        for g in genres:
            label = str(g).strip()
            if label and label not in tags:
                tags.append(label)
    elif isinstance(genres, str) and genres.strip():
        for label in re.split(r",\s*", genres.strip()):
            if label and label not in tags:
                tags.append(label)

    category: Optional[str] = None
    production = video_ld.get("productionCompany")
    if isinstance(production, dict):
        category = _first_non_empty(production.get("name"))
    elif isinstance(production, str):
        category = production.strip() or None

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
        "uploader_name": "henvids",
        "category": category,
        "tags": tags or None,
        "upload_date": _first_non_empty(video_ld.get("uploadDate"), video_ld.get("datePublished")),
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
    slug_match = _VIDEO_PAGE_RE.match(page_url.rstrip("/") + "/")
    slug = slug_match.group("slug") if slug_match else None
    video_data = _streams_from_html(page_html, slug=slug)
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
