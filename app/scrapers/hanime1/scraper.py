from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://hanime1.me/"
SITE_HOST = "hanime1.me"
SITE_ALIASES = frozenset({"hanime1.me", "www.hanime1.me"})
CDN_HOST_MARKERS = ("hembed.com", "vdownload.hembed.com")

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US,en;q=0.8",
    "Referer": BASE_SITE,
}

_VIDEO_ID_RE = re.compile(r"^\d+$")
_WATCH_URL_RE = re.compile(
    r"^https?://(?:www\.)?hanime1\.me/(?:watch|download)\?v=(?P<id>\d+)(?:&|$|[?#])",
    re.IGNORECASE,
)
_MP4_CDN_RE = re.compile(
    r"https?://[^\s\"'<>]*hembed\.com/\d+(?:-\d+p)?\.mp4[^\s\"'<>]*",
    re.IGNORECASE,
)


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def can_handle(host: str) -> bool:
    h = _normalize_host(host)
    if h in SITE_ALIASES:
        return True
    return any(marker in h for marker in CDN_HOST_MARKERS)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _is_cloudflare_challenge(html: str) -> bool:
    if not html:
        return True
    low = html.lower()
    if any(
        marker in low
        for marker in (
            "watch?v=",
            "video-item-container",
            'property="og:title"',
            "video-link",
        )
    ):
        return False
    return (
        "just a moment" in low
        or "cf_chl_opt" in low
        or "challenge-platform" in low
        or "enable javascript and cookies" in low
    )


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str | None:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                text = resp.text or ""
                if _is_cloudflare_challenge(text):
                    continue
                return text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer)
    if text:
        return text

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE
    html = await pool_fetch_html(url, headers=headers)
    if _is_cloudflare_challenge(html):
        raise ValueError(f"Blocked by challenge page: {url}")
    return html


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
    t = re.sub(r"\s+", " ", str(title)).strip()
    for suffix in (
        " - Hanime1.me",
        " | Hanime1.me",
        " - H動漫/裏番/線上看 - Hanime1",
        " - H動漫/裏番/線上看",
        " - Hanime1",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    if not raw:
        return None

    m = _WATCH_URL_RE.match(raw if "?" in raw else raw + "&")
    if m:
        return m.group("id")

    parsed = urlparse(raw)
    host = _normalize_host(parsed.netloc or "")
    if host not in SITE_ALIASES and not any(marker in host for marker in CDN_HOST_MARKERS):
        return None

    qs = parse_qs(parsed.query)
    for key in ("v", "id"):
        values = qs.get(key) or []
        if values and _VIDEO_ID_RE.fullmatch(values[0]):
            return values[0]

    cdn_match = re.search(r"hembed\.com/(\d+)(?:-\d+p)?\.mp4", raw, re.I)
    if cdn_match:
        return cdn_match.group(1)

    return None


def _canonical_video_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/watch?v={video_id}"


def _normalize_media_url(url: str | None, *, base: str = BASE_SITE) -> Optional[str]:
    if not url:
        return None
    raw = str(url).strip().replace("\\/", "/")
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return urljoin(base, raw)
    return raw


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("src", "data-src", "data-original"):
        url = _normalize_media_url(img.get(key))
        if url:
            return url
    return None


def _format_duration(seconds: str | None) -> Optional[str]:
    if not seconds or not str(seconds).isdigit():
        return None
    total = int(seconds)
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _quality_from_url(url: str, *, res: str | None = None) -> str:
    if res:
        res = str(res).strip().lower()
        if res.endswith("p") and res[:-1].isdigit():
            return res
    qm = re.search(r"/(\d+)-(\d{3,4})p\.mp4", url, re.I)
    if qm:
        return f"{qm.group(2)}p"
    qm = re.search(r"(\d{3,4})p", url, re.I)
    if qm:
        return f"{qm.group(1)}p"
    return "auto"


def _add_stream(
    streams: list[dict[str, str]],
    seen: set[str],
    url: str,
    *,
    res: str | None = None,
) -> None:
    url = (url or "").replace("\\/", "/").strip()
    if not url.startswith("http") or url in seen:
        return
    if "hembed.com" not in url.lower() and ".mp4" not in url.lower():
        return
    seen.add(url)
    streams.append(
        {
            "url": url,
            "quality": _quality_from_url(url, res=res),
            "format": "hls" if ".m3u8" in url.lower() else "mp4",
        }
    )


def _streams_from_html(html: str, video_id: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "lxml")

    for source in soup.select("video source[src], #player source[src]"):
        src = source.get("src") or ""
        _add_stream(streams, seen, src, res=source.get("res"))

    for url in _MP4_CDN_RE.findall(html or ""):
        _add_stream(streams, seen, url)

    def _score(item: dict[str, str]) -> int:
        digits = "".join(ch for ch in item.get("quality", "") if ch.isdigit())
        return int(digits) if digits else 0

    streams.sort(key=_score, reverse=True)
    default = streams[0]["url"] if streams else None
    hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
    return {
        "streams": streams,
        "hls": hls,
        "default": default or hls,
        "has_video": bool(streams),
    }


def _parse_thumb_block(box: Any) -> Optional[dict[str, Any]]:
    link = box.select_one("a.video-link[href*='watch?v='], a[href*='watch?v=']")
    if not link:
        return None

    href = (link.get("href") or "").strip()
    if href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    video_id = _extract_video_id(href)
    if not video_id:
        return None

    title_el = box.select_one(".title")
    img = box.select_one("img.main-thumb, img")
    dur_el = box.select_one(".duration")
    stats = box.select(".stat-item")
    uploader_el = box.select_one(".subtitle a, .card-mobile-user")

    views = None
    if len(stats) >= 2:
        views = stats[1].get_text(" ", strip=True) or None

    title = _clean_title(
        _first_non_empty(
            box.get("title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            link.get_text(" ", strip=True),
            img.get("alt") if img else None,
        )
    ) or video_id

    return {
        "url": _canonical_video_url(video_id),
        "title": title,
        "thumbnail_url": _best_image_url(img),
        "duration": dur_el.get_text(strip=True) if dur_el else None,
        "views": views,
        "uploader_name": uploader_el.get_text(strip=True) if uploader_el else None,
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for box in soup.select("div.video-item-container"):
        if len(items) >= limit:
            break
        parsed = _parse_thumb_block(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if len(items) < limit:
        for link in soup.select("a[href*='watch?v=']"):
            if len(items) >= limit:
                break
            href = (link.get("href") or "").strip()
            if href.startswith("/"):
                href = urljoin(BASE_SITE, href)
            video_id = _extract_video_id(href)
            if not video_id:
                continue
            url = _canonical_video_url(video_id)
            if url in seen:
                continue
            seen.add(url)
            title = _clean_title(link.get_text(" ", strip=True)) or video_id
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(link.select_one("img")),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                }
            )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    qs = {k: v[-1] for k, v in parse_qs(parsed.query).items() if v}

    if page_num <= 1:
        qs.pop("page", None)
    else:
        qs["page"] = str(page_num)

    path = parsed.path or "/"
    query = urlencode(qs, doseq=False) if qs else ""
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            path,
            "",
            query,
            "",
        )
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    meta_title_el = soup.select_one("h1")
    raw_title = _first_non_empty(
        _meta(soup, prop="og:title"),
        meta_title_el.get_text(" ", strip=True) if meta_title_el else None,
        soup.title.get_text(strip=True) if soup.title else None,
    )
    title = _clean_title(raw_title) or raw_title or video_id or "Unknown Video"

    thumbnail = _normalize_media_url(_meta(soup, prop="og:image"))
    duration = _format_duration(_meta(soup, prop="og:video:duration"))
    if not duration:
        dur_el = soup.select_one(".duration")
        duration = dur_el.get_text(strip=True) if dur_el else None

    views = None
    upload_date = None
    for panel in soup.select(".video-description-panel, .video-details-wrapper"):
        txt = panel.get_text(" ", strip=True)
        if not views:
            vm = re.search(r"觀看次數[：:]\s*([^\s]+)", txt)
            if vm:
                views = vm.group(1)
        if not upload_date:
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", txt)
            if dm:
                upload_date = dm.group(1)

    uploader_el = soup.select_one("#video-artist-name, a#video-artist-name")
    uploader = uploader_el.get_text(strip=True) if uploader_el else None

    tags: list[str] = []
    for a in soup.select(".single-video-tag a, .video-tags-wrapper a"):
        tag = re.sub(r"\s*\(\d+\)\s*$", "", a.get_text(" ", strip=True)).strip()
        if tag and tag not in tags and len(tag) < 80:
            tags.append(tag)

    related = _parse_list_items(soup, limit=24)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or _streams_from_html(html, video_id)
    return {
        "url": page_url,
        "title": title,
        "description": _meta(soup, prop="og:description") or _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": tags[0] if tags else None,
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
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported Hanime1 URL: {url}")

    page_url = _canonical_video_url(video_id)
    html = await fetch_page(page_url, referer=BASE_SITE)
    video_data = _streams_from_html(html, video_id)
    return parse_video_page(html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or BASE_SITE
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
