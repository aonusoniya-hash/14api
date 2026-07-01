from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://hentaverse.com/"
DEFAULT_BROWSE_URL = "https://hentaverse.com/newest"
CDN_SITE = "https://cdn.hentaverse.com/"
SITE_HOST = "hentaverse.com"
SITE_ALIASES = frozenset(
    {
        "hentaverse.com",
        "www.hentaverse.com",
        "cdn.hentaverse.com",
    }
)

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
    r"^https?://(?:www\.)?hentaverse\.com/video/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_SERIES_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaverse\.com/hentai/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_VIDEO_SLUG_RE = re.compile(r"-episode-\d+$", re.IGNORECASE)
_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_VIDEO_PATH_RE = re.compile(
    r'"videoPath":"(uploads/videos/[a-z0-9-]+/renditions)"',
    re.IGNORECASE,
)
_EPISODE_CARD_RE = re.compile(
    r'\{"slug":"([^"]+)","quality":"[^"]*","likerate":[^,]*,"duration":"([^"]*)",'
    r'"thumbnail":"([^"]*)"(?:,"videoPreview":"[^"]*")?,"title":"((?:\\.|[^"\\])*)"',
    re.IGNORECASE,
)
_MP4_QUALITIES = ("1080p", "720p", "480p", "360p")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hentaverse.com")


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
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (
        " - Hentaverse",
        " | Hentaverse",
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
    if raw.startswith("http://"):
        return "https://" + raw[7:]
    return raw.replace("https://cdn.hentaverse.com//", "https://cdn.hentaverse.com/")


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/video/{slug.strip('/')}"


def _canonical_series_url(slug: str) -> str:
    return f"https://{SITE_HOST}/hentai/{slug.strip('/')}"


def _decode_flight_chunks(html: str) -> list[str]:
    chunks: list[str] = []
    for match in _FLIGHT_CHUNK_RE.finditer(html or ""):
        try:
            chunks.append(match.group(1).encode("utf-8").decode("unicode_escape"))
        except Exception:
            chunks.append(match.group(1))
    return chunks


def _extract_balanced_json(raw: str, start_idx: int, open_ch: str, close_ch: str) -> str | None:
    idx = raw.find(open_ch, start_idx)
    if idx < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for pos in range(idx, len(raw)):
        ch = raw[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return raw[idx : pos + 1]
    return None


def _extract_initial_videos(html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in _decode_flight_chunks(html):
        marker = '"initialVideos":'
        start = 0
        while True:
            idx = chunk.find(marker, start)
            if idx < 0:
                break
            raw = _extract_balanced_json(chunk, idx + len(marker), "[", "]")
            start = idx + len(marker)
            if not raw:
                continue
            try:
                videos = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(videos, list):
                continue
            for video in videos:
                if not isinstance(video, dict):
                    continue
                slug = str(video.get("slug") or "").strip()
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                items.append(video)
    return items


def _extract_episode_cards(html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in _decode_flight_chunks(html):
        for match in _EPISODE_CARD_RE.finditer(chunk):
            slug, duration, thumbnail, title = match.groups()
            if slug in seen:
                continue
            seen.add(slug)
            items.append(
                {
                    "slug": slug,
                    "duration": duration,
                    "thumbnail": thumbnail,
                    "title": title.replace("\\", ""),
                }
            )
    return items


def _list_item_from_video(video: dict[str, Any]) -> dict[str, Any]:
    slug = str(video.get("slug") or "").strip()
    title = _clean_title(
        _first_non_empty(video.get("title"), video.get("name"))
    ) or "Unknown Video"
    thumb = _normalize_media_url(
        _first_non_empty(video.get("thumbnail"), video.get("image"))
    )
    if thumb and not thumb.startswith("http"):
        thumb = _normalize_media_url(urljoin(CDN_SITE, thumb.lstrip("/")))
    duration = _first_non_empty(video.get("duration"))
    views = video.get("views")
    uploader = None
    user = video.get("user")
    if isinstance(user, dict):
        uploader = _first_non_empty(user.get("username"), user.get("url"))
    return {
        "url": _canonical_video_url(slug),
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": str(views) if views is not None else None,
        "uploader_name": uploader or "hentaverse",
    }


def _parse_list_items(html: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for video in _extract_initial_videos(html):
        if len(items) >= limit:
            break
        slug = str(video.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        items.append(_list_item_from_video(video))

    if len(items) < limit:
        for video in _extract_episode_cards(html):
            if len(items) >= limit:
                break
            slug = str(video.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            items.append(_list_item_from_video(video))

    if len(items) < limit:
        soup = BeautifulSoup(html, "lxml")
        for link in soup.select('a[href*="/hentai/"]'):
            if len(items) >= limit:
                break
            href = link.get("href") or ""
            m = re.search(r"/hentai/([a-z0-9-]+)", href, re.I)
            if not m:
                continue
            slug = m.group(1)
            if _VIDEO_SLUG_RE.search(slug):
                url = _canonical_video_url(slug)
            else:
                url = _canonical_series_url(slug)
            if url in seen:
                continue
            seen.add(url)
            img = link.select_one("img")
            title = _clean_title(
                _first_non_empty(
                    img.get("alt") if img else None,
                    link.get("title"),
                    link.get_text(strip=True),
                )
            ) or "Unknown Series"
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _normalize_media_url(img.get("src") if img else None),
                    "duration": None,
                    "views": None,
                    "uploader_name": "hentaverse",
                }
            )
    return items[:limit]


def _ld_json_nodes(html: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                nodes.extend(n for n in graph if isinstance(n, dict))
            else:
                nodes.append(data)
    return nodes


def _video_object(html: str) -> dict[str, Any]:
    for node in _ld_json_nodes(html):
        if node.get("@type") == "VideoObject":
            return node
    return {}


def _series_object(html: str) -> dict[str, Any]:
    for node in _ld_json_nodes(html):
        if node.get("@type") == "TVSeries":
            return node
    return {}


def _extract_video_path(html: str) -> Optional[str]:
    for match in _VIDEO_PATH_RE.finditer(html or ""):
        return match.group(1).strip()
    video_obj = _video_object(html)
    content_url = _normalize_media_url(video_obj.get("contentUrl"))
    if content_url:
        path = (urlparse(content_url).path or "").lstrip("/")
        if path.endswith("renditions"):
            return path
    for chunk in _decode_flight_chunks(html):
        match = re.search(
            r'"videoPath":"(uploads/videos/[a-z0-9-]+/renditions)"',
            chunk,
            re.I,
        )
        if match:
            return match.group(1)
    return None


def _streams_from_video_path(video_path: str) -> dict[str, Any]:
    base = _normalize_media_url(urljoin(CDN_SITE, video_path.lstrip("/")))
    if not base:
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    base = base.rstrip("/")
    streams: list[dict[str, str]] = []
    for quality in _MP4_QUALITIES:
        url = f"{base}/{quality}.mp4"
        streams.append({"quality": quality, "url": url, "format": "mp4"})
    default = streams[1]["url"] if len(streams) > 1 else streams[0]["url"]
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": True,
    }


def _views_from_video_object(video_obj: dict[str, Any]) -> Optional[str]:
    stats = video_obj.get("interactionStatistic")
    if not isinstance(stats, list):
        return None
    for stat in stats:
        if not isinstance(stat, dict):
            continue
        interaction = str(stat.get("interactionType") or "")
        if "WatchAction" in interaction:
            count = stat.get("userInteractionCount")
            if count is not None:
                return str(count)
    return None


def _resolve_video_url(html: str, url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0].rstrip("/")
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    if _VIDEO_PAGE_RE.match(raw + "/"):
        return raw

    series_match = _SERIES_PAGE_RE.match(raw + "/")
    if series_match:
        episodes = _extract_episode_cards(html)
        if not episodes:
            initial = _extract_initial_videos(html)
            if initial:
                slug = str(initial[0].get("slug") or "").strip()
                if slug:
                    return _canonical_video_url(slug)
        else:
            return _canonical_video_url(str(episodes[0]["slug"]))
        raise ValueError(f"No episodes found for series: {url}")

    raise ValueError(f"Unsupported Hentaverse URL: {url}")


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    page_num = max(1, int(page) if page else 1)
    if page_num > 1:
        query["page"] = str(page_num)
    else:
        query.pop("page", None)
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            parsed.path or "/",
            "",
            urlencode(query),
            "",
        )
    )


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
    return await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = _resolve_video_url(html, url)
    video_obj = _video_object(html)

    title = _clean_title(
        _first_non_empty(
            video_obj.get("name"),
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _normalize_media_url(
        _first_non_empty(
            video_obj.get("thumbnailUrl"),
            _meta(soup, prop="og:image"),
        )
    )

    description = _first_non_empty(
        video_obj.get("description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    tags: list[str] = []
    genres = video_obj.get("genre")
    if isinstance(genres, list):
        for tag in genres:
            label = str(tag).strip()
            if label and label not in tags:
                tags.append(label)
    keywords = video_obj.get("keywords")
    if isinstance(keywords, str):
        for tag in keywords.split(","):
            label = tag.strip()
            if label and label not in tags and label.lower() != title.lower():
                tags.append(label)

    author = video_obj.get("author")
    uploader = "hentaverse"
    if isinstance(author, dict):
        uploader = _first_non_empty(author.get("name")) or uploader

    related = _parse_list_items(html, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": _views_from_video_object(video_obj),
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": _first_non_empty(video_obj.get("uploadDate")),
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    initial_html = await fetch_page(url, referer=BASE_SITE)
    video_url = _resolve_video_url(initial_html, url)
    html = initial_html
    if video_url.rstrip("/") != (url or "").strip().split("#", 1)[0].rstrip("/"):
        html = await fetch_page(video_url, referer=url or BASE_SITE)

    video_path = _extract_video_path(html)
    video_data = _streams_from_video_path(video_path) if video_path else {
        "streams": [],
        "hls": None,
        "default": None,
        "has_video": False,
    }
    return parse_video_page(html, video_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    return _parse_list_items(html, limit=limit)
