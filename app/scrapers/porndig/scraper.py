from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.porndig.com/"
SITE_HOST = "porndig.com"
SITE_ALIASES = frozenset(
    {
        "porndig.com",
        "www.porndig.com",
        "videos.porndig.com",
        "video-cdn.porndig.com",
        "image-cdn.porndig.com",
        "m3u8.porndig.com",
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
    "Cookie": "dsclcnst=2; discl_s_t=1",
}

_VIDEO_HREF_RE = re.compile(
    r"porndig\.com/videos/(?P<id>\d+)/(?P<slug>[^/?#]+)\.html",
    re.IGNORECASE,
)
_PLAYER_URL_RE = re.compile(
    r"videos\.porndig\.com/player/index/(?P<a>\d+)/(?P<b>\d+)/(?P<c>\d+)",
    re.IGNORECASE,
)
_PLAYER_ARGS_RE = re.compile(r"player_args\.push\((\{.*\})\);\s*</script>", re.DOTALL)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".porndig.com")


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
    for suffix in (" - PornDig", " | PornDig", " - porndig.com", " | porndig.com"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    return u


def _extract_video_id(url: str) -> Optional[str]:
    m = _VIDEO_HREF_RE.search(url or "")
    return m.group("id") if m else None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if "porndig.com" not in href.lower():
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    return f"https://www.porndig.com/videos/{m.group('id')}/{m.group('slug')}.html"


def _parse_iso_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    raw = str(text).strip()
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw, flags=re.I)
    if not m:
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw):
            return raw
        return raw
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    if h:
        return f"{h}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _parse_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "VideoObject":
            return data
    return {}


def _extract_player_url(html: str) -> Optional[str]:
    m = _PLAYER_URL_RE.search(html or "")
    if not m:
        return None
    return (
        f"https://videos.porndig.com/player/index/"
        f"{m.group('a')}/{m.group('b')}/{m.group('c')}"
    )


def _streams_from_player_args(payload: dict[str, Any]) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    for item in payload.get("src") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").lower()
        if item_type == "application/x-mpegurl":
            src = _normalize_media_url(str(item.get("src") or ""))
            if src and src not in seen:
                seen.add(src)
                streams.append({"url": src, "quality": "adaptive", "format": "hls"})
                hls_url = hls_url or src
            continue
        if item_type == "multi-progressive":
            for variant in item.get("srcSet") or []:
                if not isinstance(variant, dict):
                    continue
                src = _normalize_media_url(str(variant.get("src") or ""))
                if not src or src in seen:
                    continue
                seen.add(src)
                label = str(variant.get("label") or "default").strip()
                quality = label if label.lower().endswith("p") else label
                streams.append({"url": src, "quality": quality, "format": "mp4"})

    def _score(s: dict[str, str]) -> tuple[int, int]:
        fmt = (s.get("format") or "").lower()
        qtxt = s.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, qnum)

    streams.sort(key=_score, reverse=True)
    default = streams[0]["url"] if streams else hls_url
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _streams_from_player_html(html: str) -> dict[str, Any]:
    m = _PLAYER_ARGS_RE.search(html or "")
    if not m:
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    if not isinstance(payload, dict):
        return {"streams": [], "hls": None, "default": None, "has_video": False}
    return _streams_from_player_args(payload)


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
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if text:
        return text

    from app.core.pool import fetch_html as pool_fetch_html

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return await pool_fetch_html(url, headers=headers)


def parse_video_page(html: str, url: str, *, player_html: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_json_ld(soup)

    title = _clean_title(
        _first_non_empty(
            ld.get("name"),
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(ld.get("thumbnailUrl"), _meta(soup, prop="og:image"))
    if thumbnail:
        thumbnail = _normalize_media_url(str(thumbnail))

    duration = _parse_iso_duration(ld.get("duration"))
    if not duration:
        dur_el = soup.select_one(".video_stats")
        if dur_el:
            m = re.search(r"Length:\s*(\d{1,2}:\d{2}(?::\d{2})?)", dur_el.get_text(" ", strip=True))
            if m:
                duration = m.group(1)

    upload_date = _first_non_empty(ld.get("uploadDate"), ld.get("datePublished"))
    if not upload_date:
        for stat in soup.select(".video_stats"):
            m = re.search(r"Uploaded on:\s*(.+)$", stat.get_text(" ", strip=True))
            if m:
                upload_date = m.group(1).strip()
                break

    tags: list[str] = []
    keywords = ld.get("keywords")
    if isinstance(keywords, str):
        tags.extend([t.strip() for t in re.split(r"[,|]", keywords) if t.strip()])
    elif isinstance(keywords, list):
        tags.extend([str(t).strip() for t in keywords if str(t).strip()])
    for chip in soup.select(".video_item_chip a"):
        txt = chip.get_text(strip=True)
        if txt and txt not in tags:
            tags.append(txt)

    uploader = None
    actor = ld.get("actor")
    if isinstance(actor, dict):
        uploader = actor.get("name")
    elif isinstance(actor, list) and actor:
        first = actor[0]
        if isinstance(first, dict):
            uploader = first.get("name")
    if not uploader:
        star = soup.select_one(".video_item_chip a[href*='/pornstars/']")
        if star:
            uploader = star.get_text(strip=True)

    preview_url = None
    preview_el = soup.select_one("img.js_video_preview[data-vid], img[data-vid]")
    if preview_el and preview_el.get("data-vid"):
        preview_url = _normalize_media_url(str(preview_el.get("data-vid")))

    video = _streams_from_player_html(player_html or "")
    if not video.get("has_video"):
        player_url = _extract_player_url(html)
        if player_url:
            video["streams"].append(
                {"url": player_url, "quality": "embed", "format": "embed"}
            )
            video["default"] = player_url
            video["has_video"] = True

    return {
        "url": url,
        "title": title,
        "description": ld.get("description") or _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "category": None,
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=canon)
    player_html = None
    player_url = _extract_player_url(html)
    if player_url:
        player_html = await fetch_page(player_url, referer=canon)
    data = parse_video_page(html, canon, player_html=player_html)
    if player_html and data.get("video", {}).get("has_video"):
        return data
    if player_url and not data.get("video", {}).get("streams"):
        data["video"] = {
            "streams": [{"url": player_url, "quality": "embed", "format": "embed"}],
            "hls": None,
            "default": player_url,
            "has_video": True,
        }
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if path.rstrip("/") in ("", "/"):
        path = "/video/"

    clean_path = path.rstrip("/")

    if re.search(r"/videos/page/\d+$", clean_path):
        clean_path = re.sub(r"/page/\d+$", "", clean_path)

    if clean_path.rstrip("/") in ("/video", "/videos"):
        if page_num <= 1:
            new_path = "/video/"
        else:
            new_path = f"/videos/page/{page_num}/"
        return urlunparse((parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", "", ""))

    if page_num <= 1:
        new_path = clean_path + "/"
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", urlencode(query_items), "")
        )

    query_items["page"] = str(page_num)
    new_path = clean_path + "/"
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or SITE_HOST, new_path, "", urlencode(query_items), "")
    )


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    link = box.select_one("h2 a[href], a.video_item_thumbnail[href]")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    title = _clean_title(
        _first_non_empty(
            link.get_text(" ", strip=True),
            link.get("title"),
            link.get("alt"),
        )
    ) or "Unknown Video"

    img = box.select_one("img.js_video_preview, img[data-src], img[src]")
    thumb = None
    if img:
        thumb = _first_non_empty(img.get("src"), img.get("data-src"))
        if thumb:
            thumb = _normalize_media_url(thumb)
            if "18plus.svg" in thumb:
                thumb = _normalize_media_url(img.get("data-src") or "")

    preview = None
    if img and img.get("data-vid"):
        preview = _normalize_media_url(str(img.get("data-vid")))

    duration = None
    dur_el = box.select_one(".bubble_duration span")
    if dur_el:
        duration = dur_el.get_text(strip=True)
    elif box.get("data-post_duration"):
        try:
            secs = int(str(box.get("data-post_duration")))
            duration = _parse_iso_duration(f"PT{secs}S")
        except ValueError:
            pass

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": None,
        "uploader_name": None,
        "preview_url": preview,
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

    for box in soup.select(".video_item_wrapper"):
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        for a in soup.select("a[href*='/videos/']"):
            if len(items) >= limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href or href in seen:
                continue
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": _clean_title(a.get("title") or a.get_text(strip=True)) or "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:limit]
