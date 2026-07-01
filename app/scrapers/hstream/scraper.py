from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

BASE_SITE = "https://hstream.moe/"
DEFAULT_BROWSE_URL = "https://hstream.moe/search?order=recently-uploaded"
PLAYER_API = "https://hstream.moe/player/api"
SITE_HOST = "hstream.moe"
SITE_ALIASES = frozenset({"hstream.moe", "www.hstream.moe"})
STREAM_CDN_SUFFIXES = (
    "ane-h.xyz",
    "imoto-h.xyz",
    "musume-h.xyz",
    "rorikon-h.xyz",
    "shoujo-h.org",
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

_EPISODE_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hstream\.moe/hentai/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_EPISODE_SLUG_RE = re.compile(r"-\d+$", re.IGNORECASE)
_EPISODE_ID_RE = re.compile(r'id="e_id"[^>]*value="(\d+)"', re.IGNORECASE)
_JSONLD_VIDEO_RE = re.compile(
    r'<script type="application/ld\+json">\s*(\{.*?"@type"\s*:\s*"VideoObject".*?\})\s*</script>',
    re.IGNORECASE | re.DOTALL,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".hstream.moe"):
        return True
    return any(h == suffix or h.endswith("." + suffix) for suffix in STREAM_CDN_SUFFIXES)


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
        " - English Subbed 4K Stream | hstream.moe",
        " | hstream.moe",
        " - hstream.moe",
        " - English Subbed 4K Stream",
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


def _normalize_views(text: str | None) -> Optional[str]:
    if text is None:
        return None
    raw = str(text).strip().lower().replace(",", "")
    if not raw:
        return None
    mult = 1
    if raw.endswith("k"):
        mult = 1_000
        raw = raw[:-1]
    elif raw.endswith("m"):
        mult = 1_000_000
        raw = raw[:-1]
    try:
        value = float(raw) * mult
    except ValueError:
        digits = re.sub(r"[^\d]", "", str(text))
        return digits or None
    return str(int(value))


def _is_episode_slug(slug: str) -> bool:
    return bool(_EPISODE_SLUG_RE.search((slug or "").strip()))


def _canonical_episode_url(slug: str) -> str:
    return f"https://{SITE_HOST}/hentai/{slug.strip('/')}"


def _normalize_episode_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host not in SITE_ALIASES and not host.endswith(".hstream.moe"):
        return None
    path = (parsed.path or "").strip("/")
    if not path.startswith("hentai/"):
        return None
    slug = path.split("hentai/", 1)[1].split("/", 1)[0].strip()
    if not slug or not _is_episode_slug(slug):
        return None
    return _canonical_episode_url(slug)


def _extract_jsonld_video(html: str) -> dict[str, Any]:
    match = _JSONLD_VIDEO_RE.search(html or "")
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _csrf_headers(client: httpx.AsyncClient, html: str, *, referer: str) -> dict[str, str]:
    xsrf = client.cookies.get("XSRF-TOKEN")
    meta = re.search(r'name="csrf-token" content="([^"]+)"', html or "")
    live = re.search(r'data-csrf="([^"]+)"', html or "")
    token = meta.group(1) if meta else (live.group(1) if live else None)
    hdr = {
        "Referer": referer,
        "Origin": f"https://{SITE_HOST}",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }
    if xsrf:
        hdr["X-XSRF-TOKEN"] = xsrf.replace("%3D", "=")
    if token:
        hdr["X-CSRF-TOKEN"] = token
    return hdr


async def fetch_page(client: httpx.AsyncClient, url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    res = await client.get(url, headers=headers)
    res.raise_for_status()
    return res.text


async def _fetch_player_payload(
    client: httpx.AsyncClient,
    *,
    page_html: str,
    page_url: str,
    episode_id: str,
) -> dict[str, Any]:
    headers = _csrf_headers(client, page_html, referer=page_url)
    res = await client.post(
        PLAYER_API,
        json={"episode_id": episode_id},
        headers=headers,
    )
    res.raise_for_status()
    payload = res.json()
    return payload if isinstance(payload, dict) else {}


def _streams_from_player_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stream_url = str(payload.get("stream_url") or "").strip().strip("/")
    domains = payload.get("stream_domains")
    if not stream_url or not isinstance(domains, list) or not domains:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    base = str(domains[0]).rstrip("/")
    interpolated = int(payload.get("interpolated") or 0)
    interpolated_uhd = int(payload.get("interpolated_uhd") or 0)

    streams: list[dict[str, str]] = []
    mp4 = f"{base}/{stream_url}/x264.720p.mp4"
    streams.append({"quality": "720p", "url": mp4, "format": "mp4"})

    dash_qualities = [
        ("720p", "720"),
        ("1080p", "1080"),
        ("4k", "2160"),
    ]
    if interpolated:
        dash_qualities.append(("1080i", "1080i"))
    if interpolated_uhd:
        dash_qualities.append(("2160i", "2160i"))

    for label, folder in dash_qualities:
        manifest = f"{base}/{stream_url}/{folder}/manifest.mpd"
        streams.append({"quality": label, "url": manifest, "format": "dash"})

    default = mp4
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": True,
    }


def _resolve_episode_url(html: str, url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0].rstrip("/")
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    match = _EPISODE_PAGE_RE.match(raw + "/")
    if not match:
        raise ValueError(f"Unsupported hstream.moe URL: {url}")
    slug = match.group("slug")
    if _is_episode_slug(slug):
        return _canonical_episode_url(slug)

    soup = BeautifulSoup(html, "lxml")
    prefix = slug + "-"
    for link in soup.select('a[href*="/hentai/"]'):
        href = link.get("href") or ""
        episode_url = _normalize_episode_href(href)
        if not episode_url:
            continue
        ep_slug = urlparse(episode_url).path.rstrip("/").split("/")[-1]
        if ep_slug.startswith(prefix):
            return episode_url

    raise ValueError(f"No episodes found for series URL: {url}")


def _parse_list_card(link: Any, *, block: Any | None = None) -> dict[str, Any] | None:
    url = _normalize_episode_href(link.get("href") or "")
    if not url:
        return None
    scope = block if block is not None else link
    title_tag = scope.select_one("h3")
    img = scope.select_one("img")
    title = _clean_title(
        _first_non_empty(
            img.get("alt") if img else None,
            title_tag.get_text(strip=True) if title_tag else None,
            link.get("aria-label"),
            link.get("title"),
        )
    ) or "Unknown Video"
    thumb = _normalize_media_url(
        _first_non_empty(
            img.get("src") if img else None,
            img.get("data-src") if img else None,
        )
    )
    views = None
    for span in scope.select("span"):
        if span.select_one("i.fa-eye, i.fa-regular.fa-eye"):
            views = _normalize_views(span.get_text(strip=True))
            break
    return {
        "url": url,
        "title": title,
        "thumbnail_url": thumb,
        "duration": None,
        "views": views,
        "uploader_name": None,
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    blocks = soup.select("div.episode-item")
    if blocks:
        for block in blocks:
            if len(items) >= limit:
                break
            link = block.select_one('a[href*="/hentai/"]')
            if not link:
                continue
            item = _parse_list_card(link, block=block)
            if not item or item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)
        return items[:limit]

    for link in soup.select('a[href*="/hentai/"]'):
        if len(items) >= limit:
            break
        if not link.select_one("h3"):
            continue
        item = _parse_list_card(link)
        if not item or item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)
    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))

    path = (parsed.path or "").strip("/")
    if path in ("", "search"):
        if page_num <= 1:
            query_pairs.pop("page", None)
        else:
            query_pairs["page"] = str(page_num)
        if path == "" and "order" not in query_pairs:
            return DEFAULT_BROWSE_URL if page_num <= 1 else f"{DEFAULT_BROWSE_URL}&page={page_num}"
        new_path = "/search" if path == "search" or query_pairs else "/"
    else:
        if page_num <= 1:
            query_pairs.pop("page", None)
        else:
            query_pairs["page"] = str(page_num)
        new_path = f"/{path}"

    query = urlencode(query_pairs, doseq=True)
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
            "",
            query,
            "",
        )
    )


def parse_video_page(
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
    player_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = url.rstrip("/")
    jsonld = _extract_jsonld_video(html)

    title = _clean_title(
        _first_non_empty(
            jsonld.get("name"),
            _meta(soup, prop="og:title"),
            soup.select_one("h1") and soup.select_one("h1").get_text(strip=True),
            (player_payload or {}).get("title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _normalize_media_url(
        _first_non_empty(
            jsonld.get("thumbnailUrl"),
            _meta(soup, prop="og:image"),
            (player_payload or {}).get("poster"),
        )
    )

    description = _first_non_empty(
        jsonld.get("description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    views = None
    stats = jsonld.get("interactionStatistic")
    if isinstance(stats, dict):
        views = _normalize_views(str(stats.get("userInteractionCount") or ""))
    if not views:
        for node in soup.select("div.inline-flex, span"):
            if node.select_one("i.fa-eye, i.fa-regular.fa-eye"):
                views = _normalize_views(node.get_text(strip=True))
                break

    tags: list[str] = []
    genres = jsonld.get("genre")
    if isinstance(genres, list):
        tags.extend(str(g).strip() for g in genres if str(g).strip())
    for a in soup.select('a[href*="tags%5B0%5D="], a[href*="tags[0]="]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    upload_date = _first_non_empty(jsonld.get("uploadDate"))
    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": views,
        "uploader_name": "hstream",
        "category": None,
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
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=_DEFAULT_HEADERS,
    ) as client:
        initial_html = await fetch_page(client, url, referer=BASE_SITE)
        episode_url = _resolve_episode_url(initial_html, url)
        html = initial_html
        if episode_url.rstrip("/") != (url or "").strip().split("#", 1)[0].rstrip("/"):
            html = await fetch_page(client, episode_url, referer=url or BASE_SITE)

        episode_match = _EPISODE_ID_RE.search(html)
        if not episode_match:
            raise ValueError(f"Episode id not found for: {episode_url}")

        player_payload = await _fetch_player_payload(
            client,
            page_html=html,
            page_url=episode_url,
            episode_id=episode_match.group(1),
        )
        video_data = _streams_from_player_payload(player_payload)
        return parse_video_page(
            html,
            episode_url,
            video=video_data,
            player_payload=player_payload,
        )


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        ) as client:
            html = await fetch_page(client, page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
