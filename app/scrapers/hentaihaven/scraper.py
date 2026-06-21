from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://hentaihaven.xxx/"
DEFAULT_BROWSE_URL = "https://hentaihaven.xxx/"
SITE_HOST = "hentaihaven.xxx"
SITE_ALIASES = frozenset({"hentaihaven.xxx", "www.hentaihaven.xxx"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_ROT13_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
)

_EPISODE_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaihaven\.xxx/watch/(?P<slug>[^/]+)/episode-(?P<ep>\d+)/?$",
    re.IGNORECASE,
)
_SERIES_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaihaven\.xxx/watch/(?P<slug>[^/]+)/?$",
    re.IGNORECASE,
)
_PLAYER_IFRAME_RE = re.compile(
    r'<iframe[^>]+src=["\']([^"\']+player-logic/player\.php\?[^"\']+)["\']',
    re.IGNORECASE,
)
_SECURE_TOKEN_RE = re.compile(
    r'<meta name="x-secure-token" content="([^"]+)"',
    re.IGNORECASE,
)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".hentaihaven.xxx")


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
        url = url if url.endswith("/") else f"{url}/"
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
        " - Hentai Haven | Watch free Hentai HD",
        " | Hentai Haven | Watch free Hentai HD",
        " - Hentai Haven",
        " | Hentai Haven",
        " | Watch Free HD Hentai Online",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _is_cloudflare_challenge(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    low = html.lower()
    if "sorry, you have been blocked" in low:
        return True
    if "just a moment" in low and "hentai haven" not in low and "player logic" not in low:
        return True
    if "cf_chl_opt" in low and "page-item-detail" not in low and "player-logic" not in low:
        return True
    if "enable javascript and cookies" in low and "page-item-detail" not in low:
        return True
    return False


def _rot13(s: str) -> str:
    return s.translate(_ROT13_TABLE)


def _decode_secure_token(token: str) -> dict[str, Any] | None:
    try:
        raw = token.replace("sha512-", "")
        for _ in range(3):
            raw = _rot13(raw)
            raw = base64.b64decode(raw).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize_plugin_uri(uri: str | None) -> str:
    value = (uri or "").strip() or "//hentaihaven.xxx/wp-content/plugins/player-logic/"
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/"):
        value = urljoin(BASE_SITE, value)
    if not value.endswith("/"):
        value += "/"
    return value


def _canonical_episode_url(slug: str, episode: int | str) -> str:
    return f"https://{SITE_HOST}/watch/{slug.strip('/')}/episode-{int(episode)}"


def _canonical_series_url(slug: str) -> str:
    return f"https://{SITE_HOST}/watch/{slug.strip('/')}/"


def _resolve_watch_url(url: str) -> str | None:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.endswith("/"):
        raw += "/"
    if _EPISODE_PAGE_RE.match(raw):
        return raw.rstrip("/")
    m = _SERIES_PAGE_RE.match(raw)
    if m:
        return _canonical_episode_url(m.group("slug"), 1)
    return None


async def _fetch_with_curl_cffi(
    url: str,
    *,
    referer: str | None = None,
    method: str = "GET",
    data: dict[str, str] | None = None,
) -> str:
    from curl_cffi import requests as cr

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    def _do_request() -> str:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                if method.upper() == "POST":
                    resp = cr.post(
                        url,
                        headers=headers,
                        data=data,
                        impersonate=imp,
                        timeout=45.0,
                    )
                else:
                    resp = cr.get(url, headers=headers, impersonate=imp, timeout=45.0)
                if resp.status_code != 200:
                    continue
                text = resp.text
                if method.upper() == "GET" and _is_cloudflare_challenge(text):
                    continue
                return text
            except Exception:
                continue
        raise ValueError(f"Failed to fetch: {url}")

    return await asyncio.to_thread(_do_request)


async def _post_json_with_curl_cffi(
    url: str,
    *,
    referer: str | None = None,
    data: dict[str, str] | None = None,
) -> dict[str, Any]:
    from curl_cffi import requests as cr

    headers = dict(_DEFAULT_HEADERS)
    headers["Accept"] = "application/json, text/plain, */*"
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = "https://hentaihaven.xxx"

    def _do_request() -> dict[str, Any]:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                resp = cr.post(
                    url,
                    headers=headers,
                    data=data,
                    impersonate=imp,
                    timeout=45.0,
                )
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                return payload if isinstance(payload, dict) else {}
            except Exception:
                continue
        raise ValueError(f"Failed to POST: {url}")

    return await asyncio.to_thread(_do_request)


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    return await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)


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
    if host and host not in SITE_ALIASES and not host.endswith(".hentaihaven.xxx"):
        return None
    path = (parsed.path or "").rstrip("/")
    if "/episode-" in path:
        return None
    if not path.startswith("/watch/"):
        return None
    slug = path.split("/watch/", 1)[1].split("/", 1)[0].strip()
    if not slug:
        return None
    return _canonical_series_url(slug)


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    blocks = soup.select("div.page-item-detail.video")
    if blocks:
        for block in blocks:
            if len(items) >= limit:
                break
            link = block.select_one(
                "a.geo-restricted[href*='/watch/'], a[href*='/watch/'][title]"
            ) or block.select_one("a[href*='/watch/']")
            if not link:
                continue
            url = _normalize_watch_href(link.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = _clean_title(link.get("title") or link.get_text(strip=True)) or "Unknown Video"
            img = block.select_one("img")
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                }
            )
        return items[:limit]

    for link in soup.select("a[href*='/watch/']"):
        if len(items) >= limit:
            break
        url = _normalize_watch_href(link.get("href") or "")
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
    path = (parsed.path or "").strip("/")
    page_num = max(1, int(page) if page else 1)

    m = _PATH_PAGE_SUFFIX_RE.match(path)
    if m:
        path = m.group(1)

    if page_num <= 1:
        new_path = f"/{path}/" if path else "/"
    elif not path:
        new_path = f"/page/{page_num}/"
    else:
        new_path = f"/{path}/page/{page_num}/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
            "",
            parsed.query,
            "",
        )
    )


def _streams_from_api_payload(payload: dict[str, Any]) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    sources = data.get("sources") if isinstance(data.get("sources"), list) else []

    for source in sources:
        if not isinstance(source, dict):
            continue
        src = _first_non_empty(source.get("src"), source.get("url"))
        if not src:
            continue
        label = _first_non_empty(source.get("label"), source.get("quality")) or "Auto"
        src_type = str(source.get("type") or "").lower()
        fmt = "hls" if ".m3u8" in src.lower() or "mpegurl" in src_type else "mp4"
        quality = "adaptive" if label.lower() == "auto" else label
        streams.append({"quality": quality, "url": src, "format": fmt})
        if hls_url is None:
            hls_url = src

    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url if hls_url and ".m3u8" in hls_url.lower() else None,
        "default": default,
        "has_video": bool(streams),
    }


async def _fetch_player_streams(page_html: str, page_url: str) -> dict[str, Any]:
    iframe_match = _PLAYER_IFRAME_RE.search(page_html)
    if not iframe_match:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    iframe_url = iframe_match.group(1).strip()
    if iframe_url.startswith("//"):
        iframe_url = f"https:{iframe_url}"
    elif iframe_url.startswith("/"):
        iframe_url = urljoin(BASE_SITE, iframe_url)

    player_html = await fetch_page(iframe_url, referer=page_url)
    token_match = _SECURE_TOKEN_RE.search(player_html)
    if not token_match:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    config = _decode_secure_token(token_match.group(1))
    if not config:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    api_url = urljoin(_normalize_plugin_uri(config.get("uri")), "api.php")
    payload = await _post_json_with_curl_cffi(
        api_url,
        referer=iframe_url,
        data={
            "action": "zarat_get_data_player_ajax",
            "a": str(config.get("en") or ""),
            "b": str(config.get("iv") or ""),
        },
    )
    if not payload.get("status"):
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    return _streams_from_api_payload(payload)


def _resolve_episode_url(html: str, url: str) -> str:
    resolved = _resolve_watch_url(url)
    if resolved and _EPISODE_PAGE_RE.match(resolved + "/"):
        return resolved

    soup = BeautifulSoup(html, "lxml")
    for link in soup.select("a[href*='/episode-']"):
        href = link.get("href") or ""
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            href = urljoin(BASE_SITE, href)
        if _EPISODE_PAGE_RE.match(href if href.endswith("/") else href + "/"):
            return href.rstrip("/")
    m = _SERIES_PAGE_RE.match((url or "").strip().split("#", 1)[0] + "/")
    if m:
        return _canonical_episode_url(m.group("slug"), 1)
    raise ValueError(f"Unsupported Hentai Haven URL: {url}")


def parse_video_page(
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = url.rstrip("/")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one('[itemprop="name"]')["content"]
            if soup.select_one('[itemprop="name"]') and soup.select_one('[itemprop="name"]').get("content")
            else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        (soup.select_one('[itemprop="thumbnailUrl"]') or {}).get("content")
        if soup.select_one('[itemprop="thumbnailUrl"]')
        else None,
        _best_image_url(soup.select_one("img.img-responsive, img.tns-lazy-img, img")),
    )
    if thumbnail and str(thumbnail).startswith("//"):
        thumbnail = f"https:{thumbnail}"

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    upload_date = _first_non_empty(
        (soup.select_one('[itemprop="uploadDate"]') or {}).get("content")
        if soup.select_one('[itemprop="uploadDate"]')
        else None
    )

    tags: list[str] = []
    for a in soup.select('a[href*="/tag/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != _normalize_watch_href(page_url + "/")]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": _first_non_empty(
            (soup.select_one('[itemprop="author"]') or {}).get("content")
            if soup.select_one('[itemprop="author"]')
            else None,
            "hhaven",
        ),
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
    initial_html = await fetch_page(url, referer=BASE_SITE)
    episode_url = _resolve_episode_url(initial_html, url)
    html = initial_html
    if episode_url.rstrip("/") != (url or "").strip().split("#", 1)[0].rstrip("/"):
        html = await fetch_page(episode_url, referer=url or BASE_SITE)

    video_data = await _fetch_player_streams(html, episode_url)
    return parse_video_page(html, episode_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
