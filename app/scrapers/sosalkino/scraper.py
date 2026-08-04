from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://wvw.sosalkino.guru/"
SITE_HOST = "wvw.sosalkino.guru"
SITE_ALIASES = frozenset(
    {
        "sosalkino.guru",
        "www.sosalkino.guru",
        "wvw.sosalkino.guru",
        "sosalkino.ooo",
        "www.sosalkino.ooo",
    }
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_SITE,
}

_VIDEO_HREF_RE = re.compile(
    r"sosalkino\.(?:guru|ooo)/videos/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"sosalkino\.(?:guru|ooo)/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_id|video_title|video_categories|video_tags|video_models|preview_url|"
    r"video_url|video_url_text|video_alt_url|video_alt_url_text|"
    r"video_alt_url2|video_alt_url2_text|video_alt_url3|video_alt_url3_text)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_GET_FILE_RE = re.compile(
    r"https?://[^\s\"'<>]*sosalkino\.(?:guru|ooo)[^\s\"'<>]*/get_file/[^\s\"'<>]+",
    re.IGNORECASE,
)
_VIEWS_RE = re.compile(r"([\d\s]+)\s*просмотр", re.IGNORECASE)

_RESERVED_PATH_HEADS = frozenset(
    {
        "categories",
        "category",
        "photos",
        "series",
        "models",
        "studios",
        "community",
        "login",
        "register",
        "search",
        "static",
        "player",
        "embed",
        "get_file",
        "contents",
        "short",
        "latest-updates",
        "top-rated",
        "most-popular",
        "private",
        "premium",
        "help",
        "terms",
        "privacy",
        "dmca",
        "abuse",
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
    return h.endswith(".sosalkino.guru") or h.endswith(".sosalkino.ooo")


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
        " | Sosalkino",
        " - Sosalkino",
        " | sosalkino.guru",
        " - sosalkino.guru",
        " | sosalkino.ooo",
        " - sosalkino.ooo",
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


def _is_sosalkino_host(host: str) -> bool:
    h = (host or "").lower()
    return "sosalkino.guru" in h or "sosalkino.ooo" in h


def _extract_video_id(url: str) -> Optional[str]:
    m = _EMBED_HREF_RE.search(url or "")
    if m:
        return m.group("id")
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
    if not _is_sosalkino_host(parsed.netloc or ""):
        return None

    parts = _path_parts(parsed.path)
    if len(parts) != 2 or parts[0] != "videos":
        return None
    slug = parts[1].strip()
    if not slug or slug in _RESERVED_PATH_HEADS:
        return None

    host = (parsed.netloc or SITE_HOST).lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunparse(("https", host, f"/videos/{slug}/", "", "", ""))


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
    mq = re.search(r"_(\d{3,4})p\.mp4", url, re.I)
    if mq:
        return f"{mq.group(1)}p"
    if text:
        return text
    return "default"


def _quality_rank(label: str | None) -> int:
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    return int(digits) if digits else 0


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0:00"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _normalize_views(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    digits = re.sub(r"\s+", "", raw.strip())
    return digits or None


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
        if "_preview" in media.lower() or media in seen:
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
        media = _normalize_media_url(media.split("&download=", 1)[0])
        if not media or media in seen or "_preview" in media.lower():
            continue
        if "/screenshots/" in media.lower():
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

    video_id = flash.get("video_id") or _extract_video_id(video_url)
    if video_id and str(video_id).isdigit():
        embed = urljoin(BASE_SITE, f"embed/{video_id}/")
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

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer
    raw = get_file_url.strip()

    for imp in ("chrome120", "chrome110"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(raw, allow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location") or resp.headers.get("location")
                    if loc and loc.startswith("http") and "/get_file/" not in loc:
                        return loc
                if resp.status_code == 200 and resp.url and "/get_file/" not in str(resp.url):
                    return str(resp.url)
        except Exception:
            continue
    return None


async def _resolve_video_streams(video: dict[str, Any], *, referer: str) -> None:
    streams = video.get("streams") or []
    if not isinstance(streams, list):
        return

    for stream in list(streams):
        if not isinstance(stream, dict):
            continue
        if stream.get("format") != "mp4":
            continue
        url = stream.get("url") or ""
        if "/get_file/" not in url:
            continue
        resolved = await _resolve_get_file_url(url, referer=referer)
        if resolved:
            stream["url"] = resolved
        elif stream in streams:
            streams.remove(stream)

    mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    video["default"] = (mp4 or hls or embed or {}).get("url") if (mp4 or hls or embed) else None
    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(streams)


def _parse_video_stats(soup: BeautifulSoup, html: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    duration = None
    dur_meta = _meta(soup, prop="og:duration")
    if dur_meta and str(dur_meta).isdigit():
        duration = _format_duration(int(dur_meta))

    if not duration:
        for el in soup.select(".video-info span.duration, .player-holder span.duration, span.duration"):
            text = el.get_text(" ", strip=True)
            if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", text):
                duration = text
                break

    views = None
    for el in soup.select(".info-holder .item-info .text, .video-info .text"):
        text = el.get_text(" ", strip=True)
        vm = _VIEWS_RE.search(text)
        if vm:
            views = _normalize_views(vm.group(1))
            break
    if not views:
        ym = re.search(r'ya:ovs:views_total"\s+content="(\d+)"', html)
        if ym:
            views = ym.group(1)

    upload_date = None
    page_text = soup.get_text(" ", strip=True)
    dm = re.search(
        r"(\d+\s+(?:час|часа|часов|день|дня|дней|недел|месяц|год)\s+назад)",
        page_text,
        re.I,
    )
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

    duration, views, upload_date = _parse_video_stats(soup, html)

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]
    if not tags:
        for link in soup.select("a[href*='/categories/']"):
            txt = link.get_text(" ", strip=True)
            if txt and txt not in tags:
                tags.append(txt)

    models = flash.get("video_models") or ""
    uploader = _first_non_empty(models.split(",")[0].strip() if models else None)

    preview_url = None
    holder = soup.select_one(".img-holder[data-preview], [data-preview]")
    if holder and holder.get("data-preview"):
        preview_url = _normalize_media_url(str(holder.get("data-preview")))

    video = _streams_from_html(html, url)
    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != url]

    return {
        "url": url,
        "title": title,
        "description": _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": flash.get("video_categories") or (tags[0] if tags else None),
        "tags": tags or None,
        "upload_date": upload_date,
        "video": video,
        "related_videos": related,
        "preview_url": preview_url,
    }


async def _resolve_scrape_url(url: str) -> str:
    if _is_embed_url(url):
        html = await fetch_page(url, referer=BASE_SITE)
        flash = _parse_flashvars(html)
        video_id = flash.get("video_id") or _extract_video_id(url)
        if video_id:
            for match in re.finditer(
                rf"https?://[^\s\"'<>]*sosalkino\.(?:guru|ooo)/videos/[^\"'\s<>]+",
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
    for key in ("data-src", "data-webp", "data-original", "data-lazy", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    link = box if box.name == "a" else box.select_one("a.link[href*='/videos/'], a[href*='/videos/']")
    if not link:
        return None

    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img = link.select_one("img.thumb, img") or box.select_one("img.thumb, img")
    thumb = _best_image_url(img)

    title_el = link.select_one("p.title, .title-item .title, .title")
    title = _clean_title(
        _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
            link.get("title"),
        )
    ) or "Unknown Video"

    duration = None
    dur_el = link.select_one("span.duration, .duration") or box.select_one("span.duration, .duration")
    if dur_el:
        text = dur_el.get_text(" ", strip=True)
        if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", text):
            duration = text

    views = None
    for info in link.select(".info-holder .item-info .text, .item-info .text"):
        text = info.get_text(" ", strip=True)
        vm = _VIEWS_RE.search(text)
        if vm:
            views = _normalize_views(vm.group(1))
            break

    preview_url = None
    holder = link.select_one(".img-holder[data-preview], [data-preview]") or box.select_one("[data-preview]")
    if holder and holder.get("data-preview"):
        preview_url = _normalize_media_url(str(holder.get("data-preview")))

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "preview_url": preview_url,
    }


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for box in soup.select("div.item"):
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if len(items) < limit:
        for anchor in soup.select("a.link[href*='/videos/'], a[href*='/videos/']"):
            if len(items) >= limit:
                break
            parsed = _parse_list_item(anchor)
            if not parsed or parsed["url"] in seen:
                continue
            seen.add(parsed["url"])
            items.append(parsed)

    if len(items) < limit:
        for match in _VIDEO_HREF_RE.finditer(html):
            href = _normalize_video_href(match.group(0))
            if not href or href in seen:
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


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, html, limit=limit)
