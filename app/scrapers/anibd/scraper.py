from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

BASE_SITE = "https://anibd.app/"
DEFAULT_BROWSE_URL = "https://anibd.app/"
UP_BASE = "https://anibd.app/up/"
SITE_HOST = "anibd.app"
SITE_ALIASES = frozenset({"anibd.app", "www.anibd.app"})
SINGLE_API = "https://eng.animeapps.top/api/single.php"
FILTER_API = "https://eng.animeapps.top/api/singlefilter.php"
SEARCH_API = "https://eng.animeapps.top/api/search3.php"
EPISODE_API = "https://epeng.animeapps.top/api2.php"
APILINK_API = "https://epeng.animeapps.top/apilink.php"
STREAM_HOST_SUFFIXES = (
    "animeapps.top",
    "ims1.top",
    "ims2.top",
    "1imgdarr.top",
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_UP_PATH_RE = re.compile(r"^/up/(?P<postid>\d+)(?:/watch/?)?/?$", re.IGNORECASE)
_VIDEO_URL_RE = re.compile(r'videoUrl:\s*"([^"]+)"', re.IGNORECASE)
_FILTER_PARAM_MAP = {
    "fo": "postseasontypetagid",
    "ty": "anitypestagid",
    "ge": "postanigenrestagid",
    "ye": "postyeartagid",
}


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".anibd.app"):
        return True
    return any(h == suffix or h.endswith("." + suffix) for suffix in STREAM_HOST_SUFFIXES)


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
    for suffix in (
        " - Anibd.app",
        " | Anibd.app",
        " - ANIBD.APP",
        " | ANIBD.APP",
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


def _canonical_up_url(postid: str | int) -> str:
    return f"{UP_BASE}{int(postid)}/"


def _canonical_watch_url(postid: str | int, *, server_id: str | int, slug: str) -> str:
    query = urlencode({"server": str(server_id), "slug": str(slug)})
    return f"{UP_BASE}{int(postid)}/watch/?{query}"


def _parse_target_url(url: str) -> dict[str, Any]:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host not in SITE_ALIASES and not host.endswith(".anibd.app"):
        raise ValueError(f"Unsupported Anibd URL host: {url}")

    match = _UP_PATH_RE.match(parsed.path or "")
    if not match:
        raise ValueError(f"Unsupported Anibd URL: {url}")

    qs = parse_qs(parsed.query or "")
    server = _first_non_empty((qs.get("server") or [None])[0])
    slug = _first_non_empty((qs.get("slug") or [None])[0])
    return {
        "postid": str(match.group("postid")),
        "server_id": server,
        "slug": slug,
        "page_url": _canonical_up_url(match.group("postid")),
        "watch_url": raw if "/watch" in (parsed.path or "") else None,
    }


async def _fetch_json(client: httpx.AsyncClient, url: str, *, referer: str | None = None) -> dict[str, Any] | list[Any]:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    res = await client.get(url, headers=headers)
    res.raise_for_status()
    payload = res.json()
    if isinstance(payload, (dict, list)):
        return payload
    return {}


async def _fetch_text(client: httpx.AsyncClient, url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    res = await client.get(url, headers=headers)
    res.raise_for_status()
    return res.text


async def _fetch_single_post(client: httpx.AsyncClient, postid: str) -> dict[str, Any]:
    payload = await _fetch_json(
        client,
        f"{SINGLE_API}?postid={postid}",
        referer=_canonical_up_url(postid),
    )
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError(f"Post not found: {postid}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"Invalid post payload: {postid}")
    return data


async def _fetch_servers(client: httpx.AsyncClient, anilist_id: str) -> list[dict[str, Any]]:
    payload = await _fetch_json(
        client,
        f"{EPISODE_API}?epid={anilist_id}",
        referer=BASE_SITE,
    )
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"No episode servers for anilist id: {anilist_id}")
    return [item for item in payload if isinstance(item, dict)]


def _pick_episode(
    servers: list[dict[str, Any]],
    *,
    server_id: str | None,
    slug: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    active_server: dict[str, Any] | None = None
    active_episode: dict[str, Any] | None = None

    if server_id:
        for server in servers:
            if str(server.get("id")) == str(server_id):
                active_server = server
                break

    if active_server and slug:
        for ep in active_server.get("server_data") or []:
            if isinstance(ep, dict) and str(ep.get("slug")) == str(slug):
                active_episode = ep
                break

    if not active_server:
        active_server = servers[0]
    if not active_episode:
        episodes = active_server.get("server_data") or []
        if not episodes or not isinstance(episodes[0], dict):
            raise ValueError("No episodes available for this title")
        active_episode = episodes[0]

    player_data_id = _first_non_empty(active_episode.get("link"))
    if not player_data_id:
        raise ValueError("Episode link id missing")

    episode_name = _first_non_empty(active_episode.get("name")) or "1"
    return active_server, active_episode, player_data_id, episode_name


def _extract_m3u8_from_play_page(html: str, play_url: str) -> Optional[str]:
    match = _VIDEO_URL_RE.search(html or "")
    if not match:
        return None
    raw = match.group(1).strip()
    if not raw:
        return None
    return urljoin(play_url, raw)


async def _fetch_apilink_sources(
    client: httpx.AsyncClient,
    player_data_id: str,
    *,
    referer: str,
) -> list[dict[str, str]]:
    payload = await _fetch_json(
        client,
        f"{APILINK_API}?data={player_data_id}",
        referer=referer,
    )
    if not isinstance(payload, list):
        return []

    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        play_url = _first_non_empty(item.get("link"))
        label = _first_non_empty(item.get("server")) or "Auto"
        if not play_url or play_url in seen:
            continue
        try:
            play_html = await _fetch_text(client, play_url, referer=referer)
        except Exception:
            continue
        m3u8 = _extract_m3u8_from_play_page(play_html, play_url)
        if not m3u8 or m3u8 in seen:
            continue
        seen.add(m3u8)
        streams.append({"quality": label, "url": m3u8, "format": "hls"})
    return streams


def _streams_from_sources(sources: list[dict[str, str]]) -> dict[str, Any]:
    if not sources:
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    default = sources[0]["url"]
    return {
        "streams": sources,
        "hls": default,
        "default": default,
        "has_video": True,
    }


def _item_from_api_row(item: dict[str, Any]) -> dict[str, Any]:
    postid = item.get("postid")
    title = _clean_title(_first_non_empty(item.get("postname"), item.get("english"), item.get("romaji"))) or "Unknown Video"
    thumb = _normalize_media_url(
        _first_non_empty(item.get("ani_cover_large"), item.get("ani_cover_medium"), item.get("postthum"))
    )
    url = _canonical_up_url(postid) if postid else None
    return {
        "url": url or BASE_SITE,
        "title": title,
        "thumbnail_url": thumb,
        "duration": None,
        "views": None,
        "uploader_name": None,
    }


def _build_filter_api_url(base_url: str, page: int, limit: int) -> tuple[str, str | None]:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    qs = parse_qs(parsed.query or "", keep_blank_values=True)

    keyword = _first_non_empty((qs.get("s") or [None])[0], (qs.get("keyword") or [None])[0])
    if keyword:
        params: dict[str, str] = {
            "keyword": keyword,
            "page": str(page_num),
            "limit": str(max(1, min(int(limit or 100), 100))),
        }
        return f"{SEARCH_API}?{urlencode(params)}", raw

    params = {
        "page": str(page_num),
        "limit": str(max(1, min(int(limit or 100), 100))),
    }
    pg = _first_non_empty((qs.get("pg") or [None])[0])
    if pg and page_num <= 1:
        try:
            page_num = max(1, int(pg))
            params["page"] = str(page_num)
        except ValueError:
            pass

    for short_key, api_key in _FILTER_PARAM_MAP.items():
        value = _first_non_empty((qs.get(short_key) or [None])[0])
        if value:
            params[api_key] = value

    return f"{FILTER_API}?{urlencode(params)}", raw


def parse_video_page(
    post: dict[str, Any],
    *,
    page_url: str,
    episode_name: str,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_title = _clean_title(
        _first_non_empty(post.get("postname"), post.get("english"), post.get("romaji"))
    ) or "Unknown Video"
    title = f"{base_title} - EP {episode_name}" if episode_name else base_title

    thumbnail = _normalize_media_url(
        _first_non_empty(post.get("ani_cover_large"), post.get("ani_cover_medium"), post.get("postthum"))
    )

    tags: list[str] = []
    for key in ("postanigenres", "anitags", "postseasontype", "anitypes"):
        raw = post.get(key)
        if isinstance(raw, str) and raw.strip():
            for part in raw.split(","):
                tag = part.strip()
                if tag and tag not in tags:
                    tags.append(tag)

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url.rstrip("/"),
        "title": title,
        "description": _first_non_empty(post.get("postcontent")),
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": _first_non_empty(post.get("poststudios")) or "anibd",
        "category": _first_non_empty(post.get("category")),
        "tags": tags or None,
        "upload_date": _first_non_empty(post.get("datepub"), post.get("ani_start_date")),
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": [],
    }


async def scrape(url: str) -> dict[str, Any]:
    target = _parse_target_url(url)
    postid = target["postid"]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_DEFAULT_HEADERS) as client:
        post = await _fetch_single_post(client, postid)
        anilist_id = _first_non_empty(post.get("anilist"))
        if not anilist_id:
            raise ValueError(f"Anilist id missing for post: {postid}")

        servers = await _fetch_servers(client, anilist_id)
        server, episode, player_data_id, episode_name = _pick_episode(
            servers,
            server_id=target.get("server_id"),
            slug=target.get("slug"),
        )

        watch_url = target.get("watch_url") or _canonical_watch_url(
            postid,
            server_id=str(server.get("id") or "0"),
            slug=str(episode.get("slug") or episode_name),
        )
        sources = await _fetch_apilink_sources(client, player_data_id, referer=watch_url)
        video_data = _streams_from_sources(sources)
        return parse_video_page(
            post,
            page_url=watch_url,
            episode_name=episode_name,
            video=video_data,
        )


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    api_url, referer = _build_filter_api_url(base_url, page, limit)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_DEFAULT_HEADERS) as client:
            payload = await _fetch_json(client, api_url, referer=referer or BASE_SITE)
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            rows = [item for item in data if isinstance(item, dict)]

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if len(items) >= limit:
            break
        item = _item_from_api_row(row)
        if not item.get("url") or item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)
    return items[:limit]
