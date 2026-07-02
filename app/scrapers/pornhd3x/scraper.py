from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import string
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www9.pornhd3x.tv/"
SITE_HOST = "pornhd3x.tv"
SITE_ALIASES = frozenset(
    {
        "pornhd3x.tv",
        "www.pornhd3x.tv",
        "www9.pornhd3x.tv",
        "pornhd3x.me",
        "www.pornhd3x.me",
        "brazzers3x.com",
        "www.brazzers3x.com",
        "brazzers3x.me",
        "www.brazzers3x.me",
    }
)

# Hardcoded token from fix.js (KqSa.P0(60)); used for get_sources cookie + md5 salt.
_TOKEN = (
    "n1sqcua67bcq9826avrbi6m49vd7shxkn985mhodk06twz87wwxtp3dqiicks2df"
    "yud213k6ygiomq01s94e4tr9v0k887bkyud213k6ygiomq01s94e4tr9v0k887bk"
    "qocxzw39esdyfhvtkpzq9n4e7at4kc6k8sxom08bl4dukp16h09oplu7zov4m5f8"
)
_MD5_SALT = "98126avrbi6m49vd7shxkn985"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_MOVIE_HREF_RE = re.compile(
    r"(?:pornhd3x\.(?:tv|me)|brazzers3x\.(?:com|me))/(?:movies|movie)/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_MOVIE_OBJECT_RE = re.compile(
    r"var\s+movie\s*=\s*\{[^}]*?id:\s*\"(?P<id>[^\"]+)\"[^}]*?\}",
    re.DOTALL | re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h.startswith("www9."):
        h = h[5:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".pornhd3x.tv") or h.endswith(".pornhd3x.me")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
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
        " | PornHD",
        " - PornHD",
        " | PornHD3X",
        " - PornHD3X",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _site_origin(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or "www9.pornhd3x.tv"
    return f"{scheme}://{host}/"


def _normalize_media_url(url: str, *, base: str = BASE_SITE) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(base, u.lstrip("/"))
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u


def _normalize_video_href(href: str, *, base: str = BASE_SITE) -> Optional[str]:
    raw = (href or "").strip()
    if not raw or raw.startswith("#") or raw.startswith("javascript:"):
        return None
    if raw.startswith("/"):
        raw = urljoin(base, raw.lstrip("/"))
    lower = raw.lower()
    if "/movies/" not in lower and "/movie/" not in lower:
        return None
    if not any(x in lower for x in ("pornhd3x.", "brazzers3x.")):
        return None
    parsed = urlparse(raw)
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) < 2 or parts[0] not in ("movies", "movie"):
        return None
    slug = parts[1]
    host = parsed.netloc or urlparse(base).netloc or "www9.pornhd3x.tv"
    return f"{parsed.scheme or 'https'}://{host}/movies/{slug}/"


def _parse_movie_id(html: str) -> Optional[str]:
    match = _MOVIE_OBJECT_RE.search(html or "")
    if match:
        return match.group("id")
    hidden = re.search(
        r'<input[^>]+type=["\']hidden["\'][^>]+value=["\']([A-Z0-9]{10,})["\']',
        html or "",
        re.IGNORECASE,
    )
    if hidden:
        return hidden.group(1)
    return None


def _random_token() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _sources_token(episode_id: str, nonce: str) -> str:
    return hashlib.md5((episode_id + nonce + _MD5_SALT).encode()).hexdigest()


def _cookie_name(episode_id: str) -> str:
    return _TOKEN[13:37] + episode_id + _TOKEN[40:64]


def _normalize_quality_label(label: str | None, url: str | None = None) -> str:
    raw = (label or "").strip().lower()
    if raw in ("4k", "uhd"):
        return "2160p"
    if raw.endswith("p") and raw[:-1].isdigit():
        return raw
    if raw.isdigit():
        return f"{raw}p"
    if url and ".m3u8" in url.lower():
        return "hls"
    return label or "auto"


def _stream_format(file_url: str, source_type: str | None) -> str:
    lower = (file_url or "").lower()
    st = (source_type or "").lower()
    if ".m3u8" in lower or "mpegurl" in st:
        return "hls"
    if ".mp4" in lower or "video/mp4" in st:
        return "mp4"
    return "mp4"


def _parse_playlist_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()
    playlist = data.get("playlist") or []
    if not isinstance(playlist, list):
        return streams

    for item in playlist:
        if not isinstance(item, dict):
            continue
        sources = item.get("sources") or []
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            file_url = _first_non_empty(source.get("file"), source.get("src"))
            if not file_url:
                continue
            file_url = _normalize_media_url(str(file_url))
            if not file_url or file_url in seen:
                continue
            seen.add(file_url)
            label = _normalize_quality_label(
                _first_non_empty(source.get("label"), source.get("quality")),
                file_url,
            )
            streams.append(
                {
                    "url": file_url,
                    "quality": label,
                    "format": _stream_format(file_url, str(source.get("type") or "")),
                }
            )
    return streams


async def _fetch_with_curl_cffi(
    url: str,
    *,
    referer: str | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return 0, ""

    req_headers = dict(_DEFAULT_HEADERS)
    if referer:
        req_headers["Referer"] = referer
    if headers:
        req_headers.update(headers)

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=req_headers, timeout=45.0) as client:
                if cookies:
                    for name, value in cookies.items():
                        client.cookies.set(name, value)
                await client.get(BASE_SITE)
                resp = await client.get(url)
                return resp.status_code, resp.text or ""
        except Exception:
            continue
    return 0, ""


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    status, text = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if status == 200 and text:
        return text

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


async def _fetch_sources_json(episode_id: str, *, referer: str) -> dict[str, Any]:
    origin = _site_origin(referer)
    nonce = _random_token()
    token = _sources_token(episode_id, nonce)
    cookie_name = _cookie_name(episode_id)
    api_url = f"{origin}ajax/get_sources/{episode_id}/{token}?count=1&mobile=false"

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return {}

    headers = {
        **_DEFAULT_HEADERS,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                client.cookies.set(cookie_name, nonce, domain=urlparse(origin).netloc, path="/")
                await client.get(referer)
                resp = await client.get(api_url)
                if resp.status_code != 200:
                    continue
                text = (resp.text or "").strip()
                if not text or text == " ":
                    continue
                data = resp.json()
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


async def _fetch_embed_url(episode_id: str, *, referer: str) -> Optional[str]:
    origin = _site_origin(referer)
    api_url = f"{origin}ajax/load_embed/{episode_id}"
    status, text = await _fetch_with_curl_cffi(
        api_url,
        referer=referer,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    if status != 200 or not text.strip():
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    embed = data.get("embed_url") if isinstance(data, dict) else None
    if embed and str(embed).strip():
        return _normalize_media_url(str(embed), base=origin)
    return None


async def _extract_video_streams(html: str, *, page_url: str) -> dict[str, Any]:
    episode_id = _parse_movie_id(html)
    streams: list[dict[str, Any]] = []

    if episode_id:
        data = await _fetch_sources_json(episode_id, referer=page_url)
        streams = _parse_playlist_sources(data)

        if not streams:
            embed_url = await _fetch_embed_url(episode_id, referer=page_url)
            if embed_url:
                streams.append({"url": embed_url, "quality": "embed", "format": "embed"})

    hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
    default = hls or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls,
        "default": default,
        "has_video": bool(streams),
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    origin = _site_origin(url)

    title_el = soup.select_one(".main-detail h3, .page-detail h3, h1")
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(_meta(soup, prop="og:image"))
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail, base=origin)

    tags: list[str] = []
    for link in soup.select(".main-detail a[href*='/category/'], .mv-info a[href*='/category/']"):
        txt = link.get_text(" ", strip=True)
        if txt and txt not in tags:
            tags.append(txt)

    actor_links = soup.select(".main-detail a[href*='/actor/'], .mv-info a[href*='/actor/']")
    uploader = _first_non_empty(
        *(a.get_text(" ", strip=True) for a in actor_links[:3]),
    )

    preview_el = soup.select_one("[data-preview]")
    preview_url = None
    if preview_el and preview_el.get("data-preview"):
        preview_url = _normalize_media_url(str(preview_el.get("data-preview")), base=origin)

    return {
        "url": url,
        "title": title,
        "description": _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": uploader,
        "category": tags[0] if tags else None,
        "tags": tags,
        "upload_date": None,
        "video": {"streams": [], "hls": None, "default": None, "has_video": False},
        "related_videos": [],
        "preview_url": preview_url,
        "_html": html,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=BASE_SITE)
    result = parse_video_page(html, canon)
    html_cache = result.pop("_html", html)
    result["video"] = await _extract_video_streams(html_cache, page_url=canon)
    return result


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]

    if parts and parts[-1].startswith("page-"):
        parts = parts[:-1]

    host = parsed.netloc or "www9.pornhd3x.tv"
    scheme = parsed.scheme or "https"

    if not parts:
        if page_num <= 1:
            return urlunparse((scheme, host, "/", "", "", ""))
        return urlunparse((scheme, host, f"/premium-porn-hd/page-{page_num}", "", "", ""))

    if parts[0] == "premium-porn-hd":
        parts = parts[1:]

    if not parts:
        if page_num <= 1:
            return urlunparse((scheme, host, "/", "", "", ""))
        return urlunparse((scheme, host, f"/premium-porn-hd/page-{page_num}", "", "", ""))

    if parts[0] == "search" and len(parts) >= 2:
        slug = "/".join(parts[1:])
        if page_num <= 1:
            path = f"/search/{slug}/"
        else:
            path = f"/search/{slug}/page-{page_num}"
        return urlunparse((scheme, host, path, "", "", ""))

    if parts[0] in ("category", "studio") and len(parts) >= 2:
        slug = "/".join(parts[1:])
        if page_num <= 1:
            path = f"/{parts[0]}/{slug}/"
        else:
            path = f"/{parts[0]}/{slug}/page-{page_num}"
        return urlunparse((scheme, host, path, "", "", ""))

    if page_num <= 1:
        return urlunparse((scheme, host, "/" + "/".join(parts) + ("/" if parts else ""), "", "", ""))
    return urlunparse((scheme, host, f"/premium-porn-hd/page-{page_num}", "", "", ""))


def _best_image_url(img: Any, *, base: str = BASE_SITE) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-original", "data-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url, base=base)
    return None


def _parse_list_item(box: Any, *, base: str) -> Optional[dict[str, Any]]:
    link = box.select_one("a[href*='/movies/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "", base=base)
    if not href:
        return None

    img = box.select_one("img.mli-thumb, img.lazy, img")
    thumb = _best_image_url(img, base=base)

    title_el = box.select_one(".mli-info h2, h2")
    title = _clean_title(
        _first_non_empty(
            link.get("title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        )
    ) or "Unknown Video"

    preview_el = box.select_one("[data-preview]")
    preview_url = None
    if preview_el and preview_el.get("data-preview"):
        preview_url = _normalize_media_url(str(preview_el.get("data-preview")), base=base)

    duration_el = box.select_one(".duration")
    duration = duration_el.get_text(" ", strip=True) if duration_el else None

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": None,
        "preview_url": preview_url,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    origin = _site_origin(page_url)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for box in soup.select(".ml-item.item, .item"):
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box, base=origin)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for link in soup.select("a[href*='/movies/']"):
            if len(items) >= limit:
                break
            href = _normalize_video_href(link.get("href") or "", base=origin)
            if not href or href in seen:
                continue
            img = link.find("img")
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": _clean_title(
                        _first_non_empty(
                            link.get("title"),
                            img.get("alt") if img else None,
                            link.get_text(" ", strip=True),
                        )
                    )
                    or "Unknown Video",
                    "thumbnail_url": _best_image_url(img, base=origin),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:limit]
