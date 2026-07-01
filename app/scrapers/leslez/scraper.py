from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://leslez.com/"
SITE_HOST = "leslez.com"


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = BASE_SITE) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    return await pool_fetch_html(url, headers=headers)


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
    for suffix in (" - LesLez", " | LesLez", " - leslez.com", " | leslez.com"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    t = re.sub(r"\s+\d+\s+(?:day|days|week|weeks|month|months|year|years)\s+ago\s*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+\d+\s+(?:minute|minutes|min)\s*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*$", "", t).strip()
    return t or None


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b", text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"\b(\d[\d\s,\.]*\s*[KMBkmb])\b", text, flags=re.IGNORECASE)
    if not m:
        return None
    return _normalize_numberish(m.group(1))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.endswith("/l.svg") or url.endswith("l.svg"):
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://{SITE_HOST}{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if SITE_HOST not in parsed.netloc.lower():
        return None
    if parsed.query:
        return None
    if not re.match(r"^/videos/\d+/[^/]+/?$", parsed.path or "", flags=re.IGNORECASE):
        return None
    return urlunparse(("https", SITE_HOST, parsed.path.rstrip("/") + "/", "", "", ""))


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8") or "mpegurl" in low or "/media=hls/" in low:
        return "hls"
    if any(host in low for host in ("vcdn", "ahvcdn.com", "ahcdn.com", "icdn")):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    if "/embed/" in low:
        return "embed"
    return None


def _is_non_video_asset_url(url: str) -> bool:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")
    if path.endswith(image_exts):
        return True
    blocked_markers = (
        "/screenshots/",
        "/thumb/",
        "/thumbs/",
        "/thumbnails/",
        "/poster/",
        "/preview.jpg",
        "/preview_",
    )
    return any(marker in low for marker in blocked_markers)


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in re.finditer(r"https?://[^\s\"'<>]+", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip()
        if u and _detect_media_format(u):
            urls.append(u)
    for m in re.finditer(r"/get_file[^\s\"'<>]*", unescaped, flags=re.IGNORECASE):
        urls.append(urljoin(BASE_SITE, m.group(0).strip()))
    return list(dict.fromkeys(urls))


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    if f"{SITE_HOST}/embed/" in low:
        return "leslez"
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if _detect_media_format(url) == "hls":
        return "adaptive"
    res = re.search(r"res=([^&\"']+)", low)
    if res:
        return res.group(1)
    return "source"


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    blocked = (
        "bngdin.com",
        "bongacams",
        "wasp-",
        "usco1621",
        "app.yrotary.com",
        "/api/spots/",
        "adspyglass",
        "traforama",
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "trafficjunky",
        "popads",
        "reklon.net",
        "dynamic_banner",
    )
    return any(marker in s for marker in blocked)


def _extract_streams(soup: BeautifulSoup, html: str, page_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for video in soup.select("video"):
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(page_url, src)
            if not src.startswith("http") or src in seen or _is_non_video_asset_url(src):
                continue
            fmt = _detect_media_format(src)
            if fmt not in ("mp4", "hls"):
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    for src in _extract_inline_urls(html):
        if src in seen or _is_probable_ad_iframe(src) or _is_non_video_asset_url(src):
            continue
        fmt = _detect_media_format(src)
        if fmt not in ("mp4", "hls", "embed"):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    video_id = _extract_video_id(page_url)

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, 0)

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]
    materialized.sort(key=_score, reverse=True)

    has_playable = any(s.get("format") in ("mp4", "hls") for s in materialized)
    if not has_playable:
        materialized.append(_page_embed_stream(page_url))

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        match = next((s for s in materialized if s.get("format") == preferred), None)
        if match:
            default_url = match.get("url")
            break

    return {
        "streams": materialized,
        "hls": None,
        "default": default_url,
        "has_video": bool(materialized),
    }


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    raw = get_file_url.strip()
    ref = referer.strip() if referer.strip().startswith("http") else BASE_SITE
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    parsed = urlparse(raw)
    if parsed.query and ("f=" in parsed.query or "custom=" in parsed.query):
        candidate_urls = [raw, raw.rstrip("/") + "/"]
    else:
        base = raw.split("?", 1)[0].strip().rstrip("/")
        candidate_urls = [f"{base}/", base, raw, raw.rstrip("/") + "/"]

    async def _attempt(url: str, method: str, range_hdr: Optional[str]) -> Optional[str]:
        h = dict(headers)
        if range_hdr:
            h["Range"] = range_hdr
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            if method == "HEAD":
                resp = await client.head(url, headers=h)
            else:
                resp = await client.get(url, headers=h)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return None
            if loc.startswith("/"):
                loc = urljoin(raw, loc)
            if _is_non_video_asset_url(loc) or _is_probable_ad_iframe(loc):
                return None
            fmt = _detect_media_format(loc)
            if fmt in ("mp4", "hls"):
                return loc
        return None

    attempts: list[tuple[str, str, Optional[str]]] = []
    for candidate in candidate_urls:
        attempts.extend(
            [
                (candidate, "HEAD", None),
                (candidate, "GET", "bytes=0-"),
                (candidate, "GET", "bytes=0-0"),
            ]
        )
    for u, method, rng in attempts:
        try:
            resolved = await asyncio.wait_for(_attempt(u, method, rng), timeout=16.0)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


def _page_embed_stream(page_url: str) -> dict[str, str]:
    return {"url": page_url, "quality": "leslez", "format": "embed"}


def _promote_stream_to_embed(stream: dict[str, str]) -> None:
    """LesLez CDN links are HLS manifests with a .mp4 suffix; use embed for WebView playback."""
    stream["format"] = "embed"
    quality = (stream.get("quality") or "").strip()
    if not quality or quality.lower() in {"adaptive", "source", "mp4", "hls"}:
        stream["quality"] = "leslez"


def _extract_media_token(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"[?&]f=([^&\"']+)", url)
    if not m:
        return None
    token = m.group(1).strip().rstrip("/")
    if token.endswith(".mp4"):
        token = token[:-4]
    return token or None


def _url_contains_video_id(url: str, video_id: str) -> bool:
    if not video_id:
        return True
    low = (url or "").lower()
    vid = str(video_id).lower()
    return (
        f"/{vid}/" in low
        or f"/{vid}." in low
        or f"%2f{vid}%2f" in low
        or f"%2f{vid}.mp4" in low
        or f"{vid}.mp4" in low
        or f"/{vid}__" in low
    )


def _url_matches_resolved_stream(source_url: str, resolved_url: str, video_id: Optional[str]) -> bool:
    if _url_contains_video_id(resolved_url, video_id or ""):
        return True
    low = resolved_url.lower()
    if any(host in low for host in ("ahvcdn.com", "ahcdn.com", "vcdn", ".leslez.com")):
        token = _extract_media_token(source_url)
        if token and token.lower() in low:
            return True
        return bool(video_id is None)
    return False


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    video_id = _extract_video_id(referer)

    if get_file_mp4:

        async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
            resolved = await _get_file_to_remote_playable(stream["url"], referer=referer)
            return stream, resolved

        resolved_pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_mp4])
        for stream, resolved in resolved_pairs:
            if resolved:
                if video_id and not _url_matches_resolved_stream(stream["url"], resolved, video_id):
                    streams.remove(stream)
                    continue
                stream["url"] = resolved
                _promote_stream_to_embed(stream)
            else:
                streams.remove(stream)

    embed_streams = [s for s in streams if s.get("format") == "embed"]
    if not embed_streams and referer:
        fallback = _page_embed_stream(referer)
        streams.append(fallback)
        embed_streams = [fallback]

    video["default"] = embed_streams[0]["url"] if embed_streams else None
    video["hls"] = None
    video["has_video"] = bool(embed_streams)


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/videos/(\d+)/", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    duration_el = soup.select_one("[data-duration]")
    duration_text = duration_el.get("data-duration") if duration_el else None

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    text_blob = soup.get_text(" ", strip=True)
    duration = _extract_duration(duration_text) or _extract_duration(text_blob)
    views = _extract_views(text_blob)

    tags: list[str] = []
    for el in soup.select(".video_categories a, a.video_info_lnk.brd, a.video_info_lnk"):
        tag = el.get_text(" ", strip=True)
        if tag and tag.lower() not in {"live sex / watch & chat"}:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": _meta(soup, prop="article:section"),
        "tags": tags,
        "upload_date": _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        ),
        "video": _extract_streams(soup, html, url),
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    data = parse_video_page(html, url)
    await _resolve_video_streams_to_remote_playable(data.get("video", {}), referer=url)
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or SITE_HOST
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/\d+/?$", "", path.rstrip("/"))
    if not clean_path:
        clean_path = "/"
    if "/search/" in clean_path or query_items.get("q"):
        query_items["page"] = str(page)
        return urlunparse((scheme, netloc, clean_path + "/", "", urlencode(query_items), ""))
    paged_path = clean_path.rstrip("/") + f"/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for section in soup.select("section.item, .list-videos .item"):
        if len(items) >= limit:
            break
        link = section.select_one("a[href*='/videos/']")
        if not link:
            continue
        href = _normalize_video_href(link.get("href") or "")
        if not href or href in seen:
            continue

        img = section.select_one("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title_el = section.select_one(".item_title")
        title = (
            (title_el.get_text(" ", strip=True) if title_el else None)
            or link.get("title")
            or (img.get("alt") if img else None)
            or link.get_text(" ", strip=True)
        )
        title = _clean_list_title(title) or "Unknown Video"

        duration_el = section.select_one(".dr")
        duration_text = duration_el.get_text(" ", strip=True) if duration_el else section.get_text(" ", strip=True)
        duration = _extract_duration(duration_text)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": None,
                "uploader_name": None,
            }
        )

    return items[:limit]
