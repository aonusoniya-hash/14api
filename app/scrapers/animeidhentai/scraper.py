from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

BASE_SITE = "https://animeidhentai.com/"
DEFAULT_BROWSE_URL = "https://animeidhentai.com/"
SITE_HOST = "animeidhentai.com"
SITE_ALIASES = frozenset({"animeidhentai.com", "www.animeidhentai.com"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_EPISODE_OBJECT_RE = re.compile(
    r'\{"id":"[^"]+","wpId":\d+,"slug":"[^"]+-episode-\d+"[^}]*\}',
    re.IGNORECASE,
)
_SERIES_PAGE_RE = re.compile(
    r"^/series/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_EPISODE_SLUG_RE = re.compile(
    r"^/series/(?P<slug>[a-z0-9-]+-episode-\d+)/?$",
    re.IGNORECASE,
)
_EPISODE_PAGE_RE = re.compile(
    r"^/(?P<wp_id>\d+)/(?P<slug>[a-z0-9-]+(?:-episode-\d+)?)/?$",
    re.IGNORECASE,
)
_NHPLAYER_DATA_ID_RE = re.compile(r'data-id="([^"]+)"', re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".animeidhentai.com")


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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    if t.lower().startswith("watch "):
        t = t[6:].strip()
    for suffix in (
        " - AnimeIDHentai",
        " | AnimeIDHentai",
        " - Animeidhentai",
        " | Animeidhentai",
        " | Watch HD Hentai Episodes | AnimeIDHentai",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _abs_media(path: str | None) -> Optional[str]:
    if not path:
        return None
    raw = str(path).strip()
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return urljoin(BASE_SITE, raw.lstrip("/"))


def _series_url(title_slug: str) -> str:
    return f"https://{SITE_HOST}/series/{title_slug.strip('/')}"


def _episode_page_url(item: dict[str, Any]) -> Optional[str]:
    wp_id = item.get("wpId")
    slug = str(item.get("slug") or "").strip()
    if wp_id and slug:
        return f"https://{SITE_HOST}/{int(wp_id)}/{slug}"
    title_slug = str(item.get("titleSlug") or "").strip()
    ep = int(item.get("ep") or 1)
    if title_slug:
        return f"{_series_url(title_slug)}?ep={ep}"
    return None


def _normalize_html_payload(html: str) -> str:
    return html.replace('\\"', '"').replace("\\/", "/")


def _extract_episode_objects(html: str) -> list[dict[str, Any]]:
    text = _normalize_html_payload(html)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _EPISODE_OBJECT_RE.finditer(text):
        chunk = match.group(0)
        slug_m = re.search(r'"slug":"([^"]+)"', chunk)
        if not slug_m:
            continue
        slug = slug_m.group(1)
        if slug in seen:
            continue
        try:
            item = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        seen.add(slug)
        out.append(item)
    return out


def _episode_display_title(item: dict[str, Any]) -> str:
    base = _clean_title(_first_non_empty(item.get("title"), item.get("seoTitle"))) or "Unknown Video"
    ep = item.get("ep")
    if ep is not None and f"episode {ep}".lower() not in base.lower():
        return f"{base} Episode {ep}"
    return base


def _parse_requested_episode(
    url: str,
) -> tuple[Optional[str], Optional[int], Optional[int], Optional[str]]:
    parsed = urlparse(url.strip())
    path = (parsed.path or "").rstrip("/") + "/"

    m = _EPISODE_PAGE_RE.match(path)
    if m:
        slug = m.group("slug")
        wp_id = int(m.group("wp_id"))
        ep_m = re.search(r"-episode-(\d+)$", slug, re.I)
        title_slug = slug[: ep_m.start()] if ep_m else slug
        ep = int(ep_m.group(1)) if ep_m else None
        return title_slug, ep, wp_id, slug

    m = _EPISODE_SLUG_RE.match(path)
    if m:
        slug = m.group("slug")
        ep_m = re.search(r"-episode-(\d+)$", slug, re.I)
        title_slug = slug[: ep_m.start()] if ep_m else slug
        ep = int(ep_m.group(1)) if ep_m else None
        return title_slug, ep, None, slug

    m = _SERIES_PAGE_RE.match(path)
    if m:
        title_slug = m.group("slug")
        if "-episode-" in title_slug:
            ep_m = re.search(r"-episode-(\d+)$", title_slug, re.I)
            if ep_m:
                return title_slug[: ep_m.start()], int(ep_m.group(1)), None, title_slug
        qs = parse_qs(parsed.query or "")
        ep_raw = (qs.get("ep") or [None])[0]
        ep = int(ep_raw) if ep_raw and str(ep_raw).isdigit() else None
        return title_slug, ep, None, None

    return None, None, None, None


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


def _decode_nhplayer_mp4(player_path: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(f"http://local{player_path}").query)
        vid_raw = (qs.get("vid") or [None])[0]
        if not vid_raw:
            return None
        decoded = base64.b64decode(vid_raw).decode("utf-8", errors="replace")
        return decoded.split("|")[0].strip() or None
    except Exception:
        return None


async def _resolve_streams_from_embed(embed_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    default: Optional[str] = None
    hls: Optional[str] = None

    if embed_url:
        streams.append({"quality": "embed", "url": embed_url, "format": "embed"})
        default = embed_url

    try:
        html = await fetch_page(embed_url, referer=BASE_SITE)
        data_id_m = _NHPLAYER_DATA_ID_RE.search(html)
        if data_id_m:
            player_path = data_id_m.group(1).strip()
            if player_path.startswith("//"):
                player_url = f"https:{player_path}"
            elif player_path.startswith("http://") or player_path.startswith("https://"):
                player_url = player_path
            elif player_path.startswith("/"):
                player_url = urljoin("https://nhplayer.com/", player_path.lstrip("/"))
            else:
                player_url = urljoin("https://nhplayer.com/", player_path)
            mp4_url = _decode_nhplayer_mp4(player_path)
            if mp4_url:
                streams.insert(0, {"quality": "1080p", "url": mp4_url, "format": "mp4"})
                if ".m3u8" in mp4_url.lower():
                    hls = mp4_url
                    streams[0]["format"] = "hls"
                else:
                    default = mp4_url
            streams.append({"quality": "player", "url": player_url, "format": "embed"})
    except Exception:
        pass

    if not default and streams:
        default = streams[0]["url"]

    return {
        "streams": streams,
        "hls": hls,
        "default": default,
        "has_video": bool(streams),
    }


def _pick_episode(
    episodes: list[dict[str, Any]],
    *,
    title_slug: str,
    ep: Optional[int],
    wp_id: Optional[int] = None,
    slug: Optional[str] = None,
) -> dict[str, Any]:
    if wp_id is not None:
        for item in episodes:
            if int(item.get("wpId") or 0) == wp_id:
                return item
    if slug:
        for item in episodes:
            if str(item.get("slug") or "") == slug:
                return item

    scoped = [e for e in episodes if str(e.get("titleSlug") or "") == title_slug]
    if not scoped:
        scoped = episodes
    if ep is not None:
        for item in scoped:
            if int(item.get("ep") or 0) == ep:
                return item
        slug_target = f"{title_slug}-episode-{ep}"
        for item in scoped:
            if str(item.get("slug") or "") == slug_target:
                return item
    if scoped:
        return sorted(scoped, key=lambda x: int(x.get("ep") or 0), reverse=True)[0]
    raise ValueError(f"No episodes found for series: {title_slug}")


def _episode_to_list_item(item: dict[str, Any]) -> dict[str, Any]:
    page_url = _episode_page_url(item) or BASE_SITE
    return {
        "url": page_url,
        "title": _episode_display_title(item),
        "thumbnail_url": _abs_media(
            _first_non_empty(item.get("thumb"), item.get("cover"), item.get("featureImage"), item.get("backdrop"))
        ),
        "duration": _first_non_empty(item.get("duration")),
        "views": str(item.get("views")) if item.get("views") is not None else None,
        "uploader_name": _first_non_empty(item.get("brand")),
    }


def _episode_to_scrape(item: dict[str, Any], url: str, *, video: dict[str, Any]) -> dict[str, Any]:
    page_url = _episode_page_url(item) or url
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    return {
        "url": page_url,
        "title": _episode_display_title(item),
        "description": _first_non_empty(item.get("description"), item.get("seoDescription")),
        "thumbnail_url": _abs_media(
            _first_non_empty(item.get("featureImage"), item.get("cover"), item.get("thumb"), item.get("backdrop"))
        ),
        "duration": _first_non_empty(item.get("duration")),
        "views": str(item.get("views")) if item.get("views") is not None else None,
        "uploader_name": _first_non_empty(item.get("brand")),
        "category": _first_non_empty(item.get("language")),
        "tags": tags or None,
        "upload_date": _first_non_empty(item.get("releasedAt")),
        "video": {
            k: v
            for k, v in video.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": [],
    }


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    qs = parse_qs(parsed.query or "")
    qs["page"] = [str(max(1, int(page)))]
    query = "&".join(f"{k}={v[0]}" for k, v in qs.items() if v and v[0])
    path = parsed.path or "/"
    if not path.endswith("/") and "." not in path.split("/")[-1]:
        path = f"{path}/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, path, "", query, ""))


async def scrape(url: str) -> dict[str, Any]:
    title_slug, ep, wp_id, slug = _parse_requested_episode(url)
    if not title_slug and not (wp_id and slug):
        raise ValueError(f"Unsupported AnimeIDHentai URL: {url}")

    if wp_id and slug:
        fetch_url = f"https://{SITE_HOST}/{wp_id}/{slug}"
    else:
        fetch_url = _series_url(title_slug or "")

    html = await fetch_page(fetch_url, referer=BASE_SITE)
    episodes = _extract_episode_objects(html)
    if not episodes:
        raise ValueError(f"No episode data found for: {fetch_url}")

    episode = _pick_episode(
        episodes,
        title_slug=title_slug or "",
        ep=ep,
        wp_id=wp_id,
        slug=slug,
    )
    embed_url = _first_non_empty(episode.get("embedUrl"))
    if not embed_url:
        raise ValueError(f"No embed URL found for episode: {episode.get('slug')}")

    video = await _resolve_streams_from_embed(embed_url)
    return _episode_to_scrape(episode, url, video=video)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    episodes = _extract_episode_objects(html)
    if not episodes and page_url.rstrip("/") != BASE_SITE.rstrip("/"):
        try:
            html = await fetch_page(BASE_SITE, referer=BASE_SITE)
            episodes = _extract_episode_objects(html)
        except Exception:
            return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ep in episodes:
        if len(items) >= limit:
            break
        title_slug = str(ep.get("titleSlug") or "").strip()
        if not title_slug:
            continue
        key = f"{title_slug}:{ep.get('ep')}"
        if key in seen:
            continue
        seen.add(key)
        items.append(_episode_to_list_item(ep))
    return items[:limit]
