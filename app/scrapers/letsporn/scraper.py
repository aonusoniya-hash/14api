from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://letsporn.com/"
SITE_HOST = "letsporn.com"
SITE_ALIASES = frozenset({"letsporn.com", "www.letsporn.com", "img.letsporn.com"})

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
    r"letsporn\.com/(?P<slug>[a-z0-9][a-z0-9-]*[a-z0-9])-(?P<id>\d+)/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"letsporn\.com/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_id|video_title|video_categories|video_tags|preview_url|license_code|"
    r"video_url|video_url_text|video_alt_url|video_alt_url_text|"
    r"video_alt_url2|video_alt_url2_text|video_alt_url3|video_alt_url3_text)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_GET_FILE_RE = re.compile(
    r"https?://(?:www\.)?letsporn\.com/get_file/[^\s\"'<>]+",
    re.IGNORECASE,
)
_RESERVED_PATH_HEADS = frozenset(
    {
        "categories",
        "channels",
        "pornstars",
        "explore",
        "charts",
        "search",
        "download",
        "embed",
        "login",
        "signup",
        "terms",
        "privacy",
        "dmca",
        "contact",
        "live-sex",
        "most-viewed",
        "new",
        "newest",
        "best",
        "popular",
    }
)
_STREAM_FIELD_PAIRS = (
    ("video_url", "video_url_text"),
    ("video_alt_url", "video_alt_url_text"),
    ("video_alt_url2", "video_alt_url2_text"),
    ("video_alt_url3", "video_alt_url3_text"),
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".letsporn.com")


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
        " - LET'SPORN!",
        " | LET'SPORN!",
        " - LetsPorn",
        " | LetsPorn",
        " - LetsPorn.com",
        " | LetsPorn.com",
        " - letsporn.com",
        " | letsporn.com",
    ):
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
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").strip("/").split("/") if p]


def _extract_video_id(url: str) -> Optional[str]:
    m = _VIDEO_HREF_RE.search(url or "")
    if m:
        return m.group("id")
    m = _EMBED_HREF_RE.search(url or "")
    if m:
        return m.group("id")
    parts = _path_parts(urlparse(url or "").path)
    if len(parts) == 1:
        tail = re.search(r"-(\d+)$", parts[0])
        if tail:
            return tail.group(1)
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)

    parsed = urlparse(href.split("#", 1)[0])
    host = (parsed.netloc or "").lower()
    if "letsporn.com" not in host:
        return None

    parts = _path_parts(parsed.path)
    if len(parts) != 1:
        return None
    if parts[0] in _RESERVED_PATH_HEADS:
        return None

    m = re.fullmatch(r"(?P<slug>.+)-(?P<id>\d+)", parts[0], flags=re.IGNORECASE)
    if not m or len(m.group("slug")) < 8:
        return None

    return f"https://letsporn.com/{parts[0]}/"


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_HREF_RE.search(url or ""))


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = _FLASHVARS_BLOCK_RE.search(html or "")
    if not m:
        return out
    for key, value in _FLASHVARS_PAIR_RE.findall(m.group(1)):
        out[key.lower()] = value.strip()
    return out


def _resolve_kt_url(raw: str) -> str:
    u = (raw or "").strip()
    m = re.match(r"^function/\d+/(https?://.+)$", u)
    if m:
        return m.group(1)
    return _normalize_media_url(u)


def _normalize_quality_label(label: str | None, url: str = "") -> str:
    text = str(label or "").strip()
    if text.isdigit():
        return f"{text}p"
    mq = re.search(r"(\d{3,4})[pP]", text)
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"_(\d{3,4})[pm]\.mp4", url, re.I)
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"-(\d{3,4})\.mp4", url, re.I)
    if mq:
        return f"{mq.group(1)}p"
    if text:
        return text
    return "default"


def _quality_rank(label: str | None) -> int:
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    return int(digits) if digits else 0


def _streams_from_html(html: str, video_url: str) -> dict[str, Any]:
    flash = _parse_flashvars(html)
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for url_key, label_key in _STREAM_FIELD_PAIRS:
        raw = flash.get(url_key)
        if not raw:
            continue
        media = _resolve_kt_url(raw)
        if not media or "/get_file/" not in media:
            continue
        if media in seen:
            continue
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _normalize_quality_label(flash.get(label_key), media),
                "format": "hls" if ".m3u8" in media.lower() else "mp4",
            }
        )

    for media in _GET_FILE_RE.findall((html or "").replace("\\/", "/")):
        media = _normalize_media_url(media)
        if not media or media in seen or "_preview" in media.lower():
            continue
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _normalize_quality_label(None, media),
                "format": "hls" if ".m3u8" in media.lower() else "mp4",
            }
        )

    soup = BeautifulSoup(html, "lxml")
    for source in soup.select("video source[src], video[src]"):
        src = _normalize_media_url(str(source.get("src") or ""))
        if not src or src in seen:
            continue
        seen.add(src)
        streams.append(
            {
                "url": src,
                "quality": _normalize_quality_label(source.get("label"), src),
                "format": "hls" if ".m3u8" in src.lower() else "mp4",
            }
        )

    video_id = _extract_video_id(video_url) or flash.get("video_id")
    if video_id and str(video_id).isdigit():
        embed = f"https://letsporn.com/embed/{video_id}"
        if embed not in seen:
            seen.add(embed)
            streams.append({"url": embed, "quality": "embed", "format": "embed"})

    streams.sort(key=lambda s: _quality_rank(s.get("quality")), reverse=True)
    hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
    mp4 = next((s["url"] for s in streams if s.get("format") == "mp4"), None)
    default = mp4 or hls or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls,
        "default": default,
        "has_video": bool(streams),
    }


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> Optional[str]:
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
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if text:
        return text

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


async def _resolve_get_file_url(get_file_url: str, *, referer: str) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    raw = (get_file_url or "").strip()
    if not raw:
        return None

    headers = {
        "User-Agent": _DEFAULT_HEADERS["User-Agent"],
        "Referer": referer if referer.startswith("http") else BASE_SITE,
        "Accept": "*/*",
    }

    async def _attempt(target: str) -> Optional[str]:
        async with AsyncSession(impersonate="chrome120", headers=headers, timeout=20.0) as client:
            resp = await client.get(target, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location") or resp.headers.get("location")
                if loc and loc.startswith("http") and "letsporn.com/get_file" not in loc.lower():
                    return loc
        return None

    for target in (raw, raw.rstrip("/") + "/"):
        try:
            resolved = await asyncio.wait_for(_attempt(target), timeout=16.0)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")
    ]
    if not get_file_streams:
        return

    video_id = _extract_video_id(referer)

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _resolve_get_file_url(stream["url"], referer=referer)
        return stream, resolved

    pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_streams])
    for stream, resolved in pairs:
        if resolved:
            if video_id and video_id not in resolved and f"/{video_id}/" not in resolved:
                if stream in streams:
                    streams.remove(stream)
                continue
            stream["url"] = resolved
        elif stream in streams:
            streams.remove(stream)

    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    video["default"] = (mp4 or hls or embed or {}).get("url") if (mp4 or hls or embed) else None
    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(streams)


def _parse_video_stats(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str], Optional[str]]:
    duration = None
    views = None
    upload_date = None

    for el in soup.select(".duration, .video-duration, [class*='duration']"):
        text = el.get_text(" ", strip=True)
        if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", text):
            duration = text
            break

    page_text = soup.get_text(" ", strip=True)
    vm = re.search(r"([\d,.]+[KMB]?)\s+views", page_text, re.I)
    if vm:
        views = vm.group(1)

    dm = re.search(r"(\d+\s+(?:hours?|days?|weeks?|months?|years?) ago)", page_text, re.I)
    if dm:
        upload_date = dm.group(1)

    return duration, views, upload_date


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    flash = _parse_flashvars(html)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            flash.get("video_title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), flash.get("preview_url"))
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    duration, views, upload_date = _parse_video_stats(soup)

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]
    if not tags:
        for link in soup.select("a[href*='/categories/']"):
            txt = link.get_text(" ", strip=True)
            if txt and txt not in tags:
                tags.append(txt)

    uploader = _first_non_empty(
        *(
            a.get_text(" ", strip=True)
            for a in soup.select("a[href*='/channels/'], a[href*='/pornstars/']")[:3]
        )
    )

    preview_url = None
    img_preview = soup.select_one("img[data-preview], [data-preview]")
    if img_preview and img_preview.get("data-preview"):
        preview_url = _normalize_media_url(str(img_preview.get("data-preview")))

    video = _streams_from_html(html, url)

    return {
        "url": url,
        "title": title,
        "description": _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": flash.get("video_categories") or (tags[0] if tags else None),
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def _resolve_scrape_url(url: str) -> str:
    if _is_embed_url(url):
        html = await fetch_page(url, referer=BASE_SITE)
        flash = _parse_flashvars(html)
        video_id = flash.get("video_id") or _extract_video_id(url)
        if video_id:
            for match in re.finditer(
                rf"https?://(?:www\.)?letsporn\.com/[a-z0-9-]+-{video_id}/?",
                html or "",
                re.IGNORECASE,
            ):
                canon = _normalize_video_href(match.group(0))
                if canon:
                    return canon
    return _normalize_video_href(url) or url


async def scrape(url: str) -> dict[str, Any]:
    canon = await _resolve_scrape_url(url)
    html = await fetch_page(canon, referer=BASE_SITE)
    data = parse_video_page(html, canon)
    await _resolve_video_streams(data.get("video", {}), referer=canon)
    return data


_LIST_PATH_HEADS = frozenset(
    {
        "categories",
        "channels",
        "pornstars",
        "explore",
        "charts",
        "most-viewed",
        "search",
        "popular",
        "newest",
        "best",
    }
)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)

    parts = _path_parts(parsed.path)
    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
    elif not parts:
        # Root home has no /2/ feed; popular is the closest paginated index.
        new_path = f"/popular/{page_num}/"
    elif parts[0] in _LIST_PATH_HEADS or len(parts) >= 2:
        new_path = "/" + "/".join(parts + [str(page_num)]) + "/"
    else:
        new_path = "/" + "/".join(parts + [str(page_num)]) + "/"

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


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _parse_list_anchor(anchor: Any) -> Optional[dict[str, Any]]:
    href = anchor.get("href") or ""
    canon = _normalize_video_href(href)
    if not canon:
        return None

    img = anchor.find("img") or anchor.select_one("img")
    thumb = _best_image_url(img)

    title = _clean_title(
        _first_non_empty(
            anchor.get("title"),
            img.get("alt") if img else None,
            anchor.get_text(" ", strip=True),
        )
    ) or "Unknown Video"

    duration = None
    parent = anchor.parent
    for _ in range(4):
        if parent is None:
            break
        dur_el = parent.select_one(".duration, .time, [class*='duration']")
        if dur_el:
            text = dur_el.get_text(" ", strip=True)
            if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", text):
                duration = text
                break
        parent = parent.parent

    preview_url = None
    if anchor.get("data-preview"):
        preview_url = _normalize_media_url(str(anchor.get("data-preview")))
    elif img and img.get("data-preview"):
        preview_url = _normalize_media_url(str(img.get("data-preview")))

    uploader = None
    container = anchor.parent
    for _ in range(5):
        if container is None:
            break
        u_el = container.select_one("a[href*='/channels/'], a[href*='/pornstars/']")
        if u_el:
            uploader = u_el.get_text(" ", strip=True)
            break
        container = container.parent

    return {
        "url": canon,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "preview_url": preview_url,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href]"):
        if len(items) >= limit:
            break
        parsed = _parse_list_anchor(anchor)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for match in _VIDEO_HREF_RE.finditer(html):
            href = f"https://letsporn.com/{match.group('slug')}-{match.group('id')}/"
            if href in seen:
                continue
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": match.group("slug").replace("-", " ").title(),
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )
            if len(items) >= limit:
                break

    return items[:limit]
