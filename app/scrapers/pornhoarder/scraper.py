from __future__ import annotations

import json
import os
import re
from typing import Any, Optional, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://ww2.pornhoarder.tw/"
SITE_HOST = "pornhoarder.tw"
PLAYER_SITE = "https://pornhoarder.net/"
SITE_ALIASES = frozenset(
    {
        "pornhoarder.tw",
        "ww2.pornhoarder.tw",
        "www.pornhoarder.tw",
        "pornhoarder.net",
        "www.pornhoarder.net",
        "pornhoarder.pictures",
        "www.pornhoarder.pictures",
    }
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_VIDEO_HREF_RE = re.compile(
    r"pornhoarder\.(?:tw|net)/video/(?P<slug>[^/]+)/(?P<token>[^/?#]+)/?",
    re.IGNORECASE,
)
_PLAYER_URL_RE = re.compile(
    r"pornhoarder\.net/player\.php\?video=(?P<token>[^&\"'\s<>]+)",
    re.IGNORECASE,
)
_IFRAME_SRC_RE = re.compile(
    r"""<iframe[^>]+src=["']([^"']+)["']""",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".pornhoarder.tw") or h.endswith(".pornhoarder.net")


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
    for suffix in (" | PornHoarder.tv", " - PornHoarder.tv", " | PornHoarder", " - PornHoarder"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    return u


def _normalize_site_host(host: str) -> str:
    h = (host or SITE_HOST).lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".pornhoarder.tw"):
        return h if h.endswith(".pornhoarder.tw") else "ww2.pornhoarder.tw"
    return "ww2.pornhoarder.tw"


def _parse_duration(value: str | None) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    m = _DURATION_RE.fullmatch(raw)
    if not m:
        return raw
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _extract_video_parts(url: str) -> tuple[Optional[str], Optional[str]]:
    m = _VIDEO_HREF_RE.search(url or "")
    if not m:
        return None, None
    return m.group("slug"), m.group("token")


def _canonical_video_url(slug: str, token: str, *, host: str = "ww2.pornhoarder.tw") -> str:
    return f"https://{host}/video/{slug.strip('/')}/{token.strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    parsed = urlparse(href)
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    return _canonical_video_url(m.group("slug"), m.group("token"), host=host)


def _encode_form_body(data: dict[str, str] | list[tuple[str, str]] | None) -> bytes:
    if not data:
        return b""
    if isinstance(data, dict):
        return urlencode(data).encode()
    return urlencode(data, doseq=True).encode()


async def _fetch_with_curl_cffi(
    url: str,
    *,
    referer: str | None = None,
    method: str = "GET",
    data: dict[str, str] | list[tuple[str, str]] | None = None,
) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                session = cast(Any, client)
                if method.upper() == "POST":
                    post_headers = {
                        **headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    }
                    resp = await session.post(url, content=_encode_form_body(data), headers=post_headers)
                else:
                    resp = await session.get(url)
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            continue
    return None


async def _fetch_text(
    url: str,
    *,
    referer: str | None = None,
    method: str = "GET",
    data: dict[str, str] | list[tuple[str, str]] | None = None,
) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer, method=method, data=data)
    if text:
        return text

    from app.core.pool import fetch_html as pool_fetch_html

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    if method.upper() == "POST":
        import httpx

        post_headers = {
            **headers,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        async with httpx.AsyncClient(headers=post_headers, follow_redirects=True, timeout=45.0) as client:
            resp = await client.post(url, content=_encode_form_body(data))
            resp.raise_for_status()
            return resp.text
    return await pool_fetch_html(url, headers=headers)


def _is_search_list_url(base_url: str) -> bool:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = (parsed.path or "").strip("/").lower()
    if path == "search" or path.startswith("search/"):
        return True
    query = parse_qs(parsed.query, keep_blank_values=True)
    return any(key in query for key in ("search", "sort", "servers[]", "author", "date"))


def _search_term_from_path(path: str) -> Optional[str]:
    parts = [p for p in (path or "").strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "search" and parts[1]:
        return parts[1]
    return None


def _search_post_data(base_url: str, page: int) -> list[tuple[str, str]]:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    pairs: list[tuple[str, str]] = []
    for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
        for value in values:
            pairs.append((key, value))

    path_term = _search_term_from_path(parsed.path or "")
    if path_term and not any(k == "search" for k, _ in pairs):
        pairs.insert(0, ("search", path_term))

    if not any(k == "search" for k, _ in pairs):
        pairs.append(("search", ""))
    if not any(k == "sort" for k, _ in pairs):
        pairs.append(("sort", "0"))
    if not any(k == "date" for k, _ in pairs):
        pairs.append(("date", "0"))
    if not any(k == "author" for k, _ in pairs):
        pairs.append(("author", "0"))

    pairs = [(k, v) for k, v in pairs if k.lower() != "page"]
    pairs.append(("page", str(max(1, int(page) if page else 1))))
    return pairs


async def _fetch_search_html(base_url: str, page: int) -> str:
    referer = base_url if base_url.startswith("http") else urljoin(BASE_SITE, base_url.lstrip("/"))
    post_data = _search_post_data(base_url, page)
    return await _fetch_text(
        urljoin(BASE_SITE, "ajax_search.php"),
        referer=referer,
        method="POST",
        data=post_data,
    )


def _parse_video_object_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return {}


def _extract_player_url(html: str, page_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_video_object_ld(soup)
    embed = _first_non_empty(ld.get("embedUrl"))
    if embed and "player.php" in embed:
        return _normalize_media_url(embed)

    iframe = soup.select_one("iframe[src*='player.php']")
    if iframe and iframe.get("src"):
        return _normalize_media_url(str(iframe.get("src")))

    m = _PLAYER_URL_RE.search(html or "")
    if m:
        return f"{PLAYER_SITE}player.php?video={m.group('token')}"
    return None


async def _streams_from_player(player_url: str, *, referer: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    posted = await _fetch_text(player_url, referer=referer, method="POST", data={"play": ""})
    iframe_src = None
    if posted:
        m = _IFRAME_SRC_RE.search(posted)
        if m:
            iframe_src = _normalize_media_url(m.group(1))

    if iframe_src and iframe_src not in seen:
        seen.add(iframe_src)
        streams.append({"url": iframe_src, "quality": "embed", "format": "embed"})

    if player_url not in seen:
        seen.add(player_url)
        streams.append({"url": player_url, "quality": "player", "format": "embed"})

    default = iframe_src or player_url
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _video_info_fields(soup: BeautifulSoup) -> dict[str, Optional[str]]:
    duration = None
    uploader = None
    upload_date = None
    for item in soup.select(".video-info .item"):
        title = (item.get("title") or "").lower()
        text = item.get_text(" ", strip=True)
        if "duration" in title:
            duration = text
        elif "hosted on" in title:
            uploader = text
        elif "found" in title or "ago" in text.lower():
            upload_date = text
    return {
        "duration": duration,
        "uploader_name": uploader,
        "upload_date": upload_date,
    }


def _tags_from_page(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for h in soup.select("h3"):
        if "tags" not in h.get_text(" ", strip=True).lower():
            continue
        section = h.find_parent()
        if not section:
            continue
        for a in section.select("a[href]"):
            text = a.get_text(" ", strip=True)
            if text and text not in tags:
                tags.append(text)
        break
    return tags


def _page_title(soup: BeautifulSoup) -> Optional[str]:
    for h1 in soup.select("h1"):
        text = h1.get_text(" ", strip=True)
        if text and "hoarder" not in text.lower():
            return text
    return _clean_title(soup.title.get_text(strip=True) if soup.title else None)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_video_object_ld(soup)
    info = _video_info_fields(soup)

    thumb = _first_non_empty(ld.get("thumbnailUrl"))
    if isinstance(thumb, list) and thumb:
        thumb = thumb[0]
    thumb = _normalize_media_url(str(thumb or "")) or _meta(soup, prop="og:image")

    title = _clean_title(
        _first_non_empty(
            _page_title(soup),
            ld.get("name"),
            _meta(soup, prop="og:title"),
        )
    ) or "Unknown Video"

    duration = info.get("duration") or _parse_duration(str(ld.get("duration") or ""))

    return {
        "url": url,
        "title": title,
        "description": _first_non_empty(ld.get("description"), _meta(soup, prop="og:description")),
        "thumbnail_url": thumb or None,
        "duration": duration,
        "views": None,
        "uploader_name": info.get("uploader_name"),
        "category": None,
        "tags": _tags_from_page(soup),
        "upload_date": _first_non_empty(ld.get("uploadDate"), info.get("upload_date")),
        "video": {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
        },
        "related_videos": [],
        "preview_url": None,
        "_player_url": _extract_player_url(html, url),
    }


async def scrape(url: str) -> dict[str, Any]:
    slug, token = _extract_video_parts(url)
    if not slug or not token:
        raise ValueError("Could not extract PornHoarder video slug/token from URL")

    parsed = urlparse(url if url.startswith("http") else urljoin(BASE_SITE, url))
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    canon = _canonical_video_url(slug, token, host=host)

    html = await _fetch_text(canon, referer=canon)
    data = parse_video_page(html, canon)

    player_url = data.pop("_player_url", None)
    if not player_url:
        raise ValueError("Could not find PornHoarder player URL")

    data["video"] = await _streams_from_player(player_url, referer=canon)
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)

    if _is_search_list_url(raw):
        pairs = _search_post_data(raw, page_num)
        path = parsed.path or "/search/"
        return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode(pairs, doseq=True), ""))

    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]
    if not parts:
        parts = ["hp"]
    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    query_pairs = list(parse_qsl(parsed.query, keep_blank_values=True))
    query_pairs = [(k, v) for k, v in query_pairs if k.lower() != "page"]
    if page_num > 1:
        query_pairs.append(("page", str(page_num)))

    new_path = "/" + "/".join(parts) + ("/" if parts else "")
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", urlencode(query_pairs), ""))


def _parse_list_item(article: Any) -> Optional[dict[str, Any]]:
    link = article.select_one("a.video-link[href], a[href*='/video/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img = article.select_one(".video-image[data-src], .video-image.primary[data-src], img[data-src]")
    thumb = None
    if img and img.get("data-src"):
        thumb = _normalize_media_url(str(img.get("data-src")))

    title_el = article.select_one(".video-content h1, h1")
    title = _clean_title(
        _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            link.get("title"),
        )
    ) or "Unknown Video"

    duration = None
    dur_el = article.select_one(".video-length")
    if dur_el:
        duration = dur_el.get_text(strip=True)

    uploader = None
    for item in article.select(".video-meta .item"):
        text = item.get_text(" ", strip=True)
        if text and "ago" not in text.lower() and "mb" not in text.lower():
            uploader = text
            break

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "preview_url": None,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = min(max(1, int(limit) if limit else 50), 120)
    try:
        if _is_search_list_url(base_url):
            html = await _fetch_search_html(base_url, page)
        else:
            page_url = _build_list_page_url(base_url, page)
            html = await _fetch_text(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    safe_limit = min(max(1, int(limit) if limit else 50), 120)

    for article in soup.select("article"):
        if len(items) >= safe_limit:
            break
        parsed = _parse_list_item(article)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for link in soup.select("a.video-link[href*='/video/']"):
            if len(items) >= safe_limit:
                break
            href = _normalize_video_href(link.get("href") or "")
            if not href or href in seen:
                continue
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": _clean_title(link.get("title")) or "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:safe_limit]
