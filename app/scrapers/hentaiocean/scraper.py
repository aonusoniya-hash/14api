from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://hentaiocean.com/"
DEFAULT_BROWSE_URL = "https://hentaiocean.com/view/recent-releases"
SITE_HOST = "hentaiocean.com"
SITE_ALIASES = frozenset(
    {
        "hentaiocean.com",
        "www.hentaiocean.com",
        "w1.hentaiocean.com",
        "w2.hentaiocean.com",
    }
)
API_URL = "https://hentaiocean.com/api?action=hentai&slug="
LIST_API = "https://hentaiocean.com/api"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_JSONDATA_ASSIGN_RE = re.compile(r"var\s+jsondata\s*=\s*", re.IGNORECASE)
_WATCH_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaiocean\.com/watch/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_WATCH_HREF_RE = re.compile(r"/watch/([a-z0-9-]+)", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hentaiocean.com")


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
        " - Hentai Ocean",
        " | Hentai Ocean",
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


def _canonical_watch_url(slug: str) -> str:
    return f"https://{SITE_HOST}/watch/{slug.strip('/')}"


def _normalize_watch_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host not in SITE_ALIASES and not host.endswith(".hentaiocean.com"):
        return None
    m = _WATCH_HREF_RE.search(parsed.path or "")
    if not m:
        return None
    return _canonical_watch_url(m.group(1))


def _resolve_watch_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0].rstrip("/")
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    m = _WATCH_PAGE_RE.match(raw + "/")
    if not m:
        raise ValueError(f"Unsupported Hentai Ocean URL: {url}")
    return _canonical_watch_url(m.group("slug"))


def _cover_url(coverimg: str | None) -> Optional[str]:
    if not coverimg:
        return None
    name = str(coverimg).strip().lstrip("/")
    if not name:
        return None
    if name.startswith("http"):
        return name
    return urljoin(BASE_SITE, f"assets/cover/{name}")


def _extract_balanced_json_object(html: str, start_idx: int) -> str | None:
    brace_start = html.find("{", start_idx)
    if brace_start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(brace_start, len(html)):
        ch = html[idx]
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
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[brace_start : idx + 1]
    return None


def _extract_jsondata(html: str) -> dict[str, Any]:
    match = _JSONDATA_ASSIGN_RE.search(html or "")
    if not match:
        return {}
    raw = _extract_balanced_json_object(html, match.end())
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _mirror_sort_key(mirror_url: str) -> int:
    url = mirror_url or ""
    if ".hentaiocean.com/play?" in url:
        return 2
    if ".hentaiocean.com/universal?" in url:
        return 5
    if "//listeamed" in url or "//vidguard" in url:
        return 10
    if "//streamtape" in url:
        return 20
    if "//dooodster" in url:
        return 30
    return 100


def _mirror_label(mirror_url: str, counters: dict[str, int]) -> str:
    url = mirror_url or ""
    if ".hentaiocean.com/play?" in url:
        counters["vip"] = counters.get("vip", 0) + 1
        return f"VIP Mirror {counters['vip']}"
    if ".hentaiocean.com/universal?" in url:
        return "Universal Mirror"
    counters["other"] = counters.get("other", 0) + 1
    return f"Mirror {counters['other']}"


def _streams_from_mirror_url(mirror_url: str, label: str) -> list[dict[str, str]]:
    mirror_url = (mirror_url or "").strip()
    if not mirror_url:
        return []

    streams: list[dict[str, str]] = []
    parsed = urlparse(mirror_url)
    host = f"{parsed.scheme or 'https'}://{parsed.netloc}"

    if ".hentaiocean.com/play?" in mirror_url or ".hentaiocean.com/universal?" in mirror_url:
        from urllib.parse import parse_qs

        vid = parse_qs(parsed.query).get("vid", [None])[0]
        if vid:
            encoded = quote(vid, safe="")
            streams.append(
                {
                    "quality": label,
                    "url": f"{host}/video/{encoded}",
                    "format": "mp4",
                }
            )
            streams.append(
                {
                    "quality": f"{label} (download)",
                    "url": f"{host}/download/{encoded}",
                    "format": "mp4",
                }
            )
        streams.append({"quality": label, "url": mirror_url, "format": "embed"})
        return streams

    fmt = "hls" if ".m3u8" in mirror_url.lower() else "embed"
    if mirror_url.lower().endswith(".mp4"):
        fmt = "mp4"
    streams.append({"quality": label, "url": mirror_url, "format": fmt})
    return streams


def _streams_from_mirrors(mirrors: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        [m for m in mirrors if isinstance(m, dict) and m.get("mirrorurl")],
        key=lambda item: _mirror_sort_key(str(item.get("mirrorurl") or "")),
    )

    streams: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    counters: dict[str, int] = {}

    for mirror in ordered:
        mirror_url = str(mirror.get("mirrorurl") or "").strip()
        if not mirror_url:
            continue
        label = _mirror_label(mirror_url, counters)
        for stream in _streams_from_mirror_url(mirror_url, label):
            key = stream["url"]
            if key in seen_urls:
                continue
            seen_urls.add(key)
            streams.append(stream)

    default = next(
        (s["url"] for s in streams if s.get("format") == "mp4" and "download" not in s.get("quality", "").lower()),
        streams[0]["url"] if streams else None,
    )
    return {
        "streams": streams,
        "hls": next((s["url"] for s in streams if s.get("format") == "hls"), None),
        "default": default,
        "has_video": bool(streams),
    }


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("src", "data-src", "data-original"):
        url = _normalize_media_url(img.get(key))
        if url:
            return url
    return None


def _list_item_from_api(item: dict[str, Any]) -> dict[str, Any]:
    slug = str(item.get("urlname") or "").strip()
    title = _clean_title(_first_non_empty(item.get("videoname"))) or "Unknown Video"
    return {
        "url": _canonical_watch_url(slug) if slug else BASE_SITE,
        "title": title,
        "thumbnail_url": _cover_url(item.get("coverimg")),
        "duration": None,
        "views": None,
        "uploader_name": "hentaiocean",
    }


def _paginate_items(items: list[dict[str, Any]], page: int, limit: int) -> list[dict[str, Any]]:
    page_num = max(1, int(page) if page else 1)
    page_limit = max(1, min(int(limit) if limit else 100, 100))
    start = (page_num - 1) * page_limit
    return items[start : start + page_limit]


def _filter_api_items(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = (query or "").strip().lower()
    if not needle:
        return items
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        haystacks = (
            str(item.get("videoname") or ""),
            str(item.get("urlname") or ""),
            str(item.get("description") or ""),
        )
        if any(needle in value.lower() for value in haystacks):
            filtered.append(item)
    return filtered


def _resolve_list_api(base_url: str, page: int) -> tuple[dict[str, str], str] | None:
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = (parsed.path or "").strip("/")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    page_num = max(1, int(page) if page else 1)

    if path in ("", "view/recent-releases"):
        return {"action": "recent"}, "slice"
    if path in ("view/newly-added",):
        return {"action": "new"}, "slice"
    if path in ("view/random",):
        return {"action": "random", "page": str(page_num)}, "server"
    if path == "explore":
        search_q = _first_non_empty(query.get("q"), query.get("query"))
        if search_q:
            return {"action": "new", "_search": search_q}, "slice"
    return None


async def _fetch_list_api(params: dict[str, str], *, referer: str | None = None) -> list[dict[str, Any]]:
    from curl_cffi import requests as cr

    headers = dict(_DEFAULT_HEADERS)
    headers["Accept"] = "application/json, text/plain, */*"
    if referer:
        headers["Referer"] = referer

    api_params = {k: v for k, v in params.items() if not k.startswith("_")}
    request_url = f"{LIST_API}?{urlencode(api_params)}" if api_params else LIST_API

    def _do_request() -> list[dict[str, Any]]:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                resp = cr.get(request_url, headers=headers, impersonate=imp, timeout=45.0)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                if isinstance(payload, list):
                    return [item for item in payload if isinstance(item, dict)]
                if isinstance(payload, dict) and payload.get("error"):
                    return []
            except Exception:
                continue
        raise ValueError(f"Failed to fetch API: {request_url}")

    return await asyncio.to_thread(_do_request)


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    selectors = (
        "a.cell.card[href*='/watch/']",
        "a.compact-video-card[href*='/watch/']",
        "a[href*='hentaiocean.com/watch/']",
        "a[href*='/watch/']",
    )
    for selector in selectors:
        for link in soup.select(selector):
            if len(items) >= limit:
                break
            url = _normalize_watch_href(link.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            img = link.select_one("img")
            title = _clean_title(
                _first_non_empty(
                    img.get("alt") if img else None,
                    link.get("title"),
                    link.get_text(strip=True),
                )
            ) or "Unknown Video"
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": "hentaiocean",
                }
            )
        if items:
            break
    return items[:limit]


def _parse_all_list_items(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=10000)


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


def parse_video_page(
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
    jsondata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = _resolve_watch_url(url)
    payload = jsondata if jsondata is not None else _extract_jsondata(html)
    info_list = payload.get("info") if isinstance(payload.get("info"), list) else []
    info = info_list[0] if info_list and isinstance(info_list[0], dict) else {}

    slug = _first_non_empty(info.get("urlname"), page_url.rsplit("/", 1)[-1])
    if slug:
        page_url = _canonical_watch_url(slug)

    title = _clean_title(
        _first_non_empty(
            info.get("videoname"),
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _cover_url(info.get("coverimg")),
        _best_image_url(soup.select_one("img")),
    )

    description = _first_non_empty(
        info.get("description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    tags: list[str] = []
    raw_genres = payload.get("genres")
    genres: list[Any] = raw_genres if isinstance(raw_genres, list) else []
    for item in genres:
        if isinstance(item, dict):
            tag = _first_non_empty(item.get("genre"), item.get("name"))
        else:
            tag = str(item).strip() or None
        if tag and tag not in tags:
            tags.append(tag)
    for a in soup.select('a[href*="/genre/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

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
        "uploader_name": "hentaiocean",
        "category": None,
        "tags": tags or None,
        "upload_date": _first_non_empty(info.get("uploaddate"), info.get("releasedate")),
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    page_url = _resolve_watch_url(url)
    html = await fetch_page(page_url, referer=BASE_SITE)
    jsondata = _extract_jsondata(html)

    if not jsondata.get("mirrors"):
        slug = page_url.rsplit("/", 1)[-1]
        try:
            api_html = await _fetch_with_curl_cffi(API_URL + quote(slug, safe=""))
            api_payload = json.loads(api_html)
            if isinstance(api_payload, dict):
                if not jsondata.get("info") and api_payload.get("info"):
                    jsondata["info"] = api_payload["info"]
                if not jsondata.get("genres") and api_payload.get("genres"):
                    jsondata["genres"] = api_payload["genres"]
                if not jsondata.get("mirrors") and api_payload.get("mirrors"):
                    jsondata["mirrors"] = api_payload["mirrors"]
        except Exception:
            pass

    raw_mirrors = jsondata.get("mirrors")
    mirrors: list[dict[str, Any]] = [
        mirror
        for mirror in (raw_mirrors if isinstance(raw_mirrors, list) else [])
        if isinstance(mirror, dict)
    ]
    video_data = _streams_from_mirrors(mirrors)
    return parse_video_page(html, page_url, video=video_data, jsondata=jsondata)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    referer = normalized_base if normalized_base.startswith("http") else urljoin(BASE_SITE, normalized_base)

    api_target = _resolve_list_api(normalized_base, page)
    if api_target:
        params, mode = api_target
        try:
            items = await _fetch_list_api(params, referer=referer)
            search_q = params.get("_search")
            if search_q:
                items = _filter_api_items(items, search_q)
            if mode == "server":
                converted = [_list_item_from_api(item) for item in items]
                return converted[: max(1, min(int(limit) if limit else 100, 100))]
            converted = [_list_item_from_api(item) for item in items]
            return _paginate_items(converted, page, limit)
        except Exception:
            pass

    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=referer or BASE_SITE)
    except Exception:
        return []
    items = _parse_all_list_items(html)
    parsed = urlparse(normalized_base if normalized_base.startswith("http") else urljoin(BASE_SITE, normalized_base))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    search_q = _first_non_empty(query.get("q"), query.get("query"))
    if search_q:
        needle = search_q.lower()
        items = [
            item
            for item in items
            if needle in str(item.get("title") or "").lower()
            or needle in str(item.get("url") or "").lower()
        ]
    return _paginate_items(items, page, limit)
