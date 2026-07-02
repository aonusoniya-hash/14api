from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html, pool

BASE_SITE = "https://www.1porn.tv/"
SITE_HOST = "1porn.tv"
SITE_ALIASES = frozenset({"1porn.tv", "www.1porn.tv"})

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
    r"1porn\.tv/videos/(?P<slug>[^/?#]+)/?",
    re.IGNORECASE,
)
_EMBED_HREF_RE = re.compile(
    r"1porn\.tv/embed/(?P<id>\d+)/?",
    re.IGNORECASE,
)
_FLASHVARS_BLOCK_RE = re.compile(r"var\s+flashvars\s*=\s*\{(.+?)\};", re.DOTALL)
_FLASHVARS_PAIR_RE = re.compile(
    r"(video_models|video_tags|video_categories|preview_url|video_title|video_id|video_url)\s*:\s*'([^']*)'",
    re.IGNORECASE,
)
_ISO_DURATION_RE = re.compile(
    r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
    re.IGNORECASE,
)
_QUALITY_IN_URL_RE = re.compile(r"_(\d{3,4})[pm]\.mp4", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".1porn.tv")


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
        " | Free Porn",
        " - Free Porn",
        " | 1Porn.TV",
        " - 1Porn.TV",
        " | 1Porn",
        " - 1Porn",
        " | 1porn.tv",
        " - 1porn.tv",
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


def _parse_iso_duration(value: str | None) -> Optional[str]:
    if not value:
        return None
    m = _ISO_DURATION_RE.fullmatch(str(value).strip())
    if not m:
        return None
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if "1porn.tv" not in href.lower():
        return None
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    slug = m.group("slug").strip("/")
    return f"https://www.1porn.tv/videos/{slug}/"


def _is_embed_url(url: str) -> bool:
    return bool(_EMBED_HREF_RE.search(url or ""))


def _parse_flashvars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = _FLASHVARS_BLOCK_RE.search(html or "")
    if not m:
        return out
    block = m.group(1)
    for key, value in _FLASHVARS_PAIR_RE.findall(block):
        out[key.lower()] = value.strip()
    return out


def _parse_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
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


def _quality_from_url(url: str, label: str = "") -> str:
    label = (label or "").strip()
    if label and label.lower() not in {"default", "auto"}:
        mq = re.search(r"(\d{3,4})[pP]", label)
        if mq:
            return f"{mq.group(1)}p"
        return label
    mq = _QUALITY_IN_URL_RE.search(url or "")
    if mq:
        return f"{mq.group(1)}p"
    mq = re.search(r"(\d{3,4})[pP]", url or "")
    if mq:
        return f"{mq.group(1)}p"
    return "default"


def _stream_key(url: str) -> str:
    parsed = urlparse(_normalize_media_url(url))
    return f"{parsed.netloc}{parsed.path}".lower()


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    seen_urls: set[str] = set()

    soup = BeautifulSoup(html, "lxml")
    for video_el in soup.select("video.video-js, video"):
        for tag in video_el.find_all("source"):
            src = _normalize_media_url(str(tag.get("src") or ""))
            if not src:
                continue
            key = _stream_key(src)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            quality = _quality_from_url(src, str(tag.get("label") or ""))
            fmt = "hls" if ".m3u8" in src else "mp4"
            streams.append({"quality": quality, "url": src, "format": fmt})
            if fmt == "hls":
                hls_url = hls_url or src

    for link in soup.select("a[href*='/get_file/'], .video-links__link[href*='/get_file/']"):
        href = _normalize_media_url(str(link.get("href") or ""))
        if not href:
            continue
        key = _stream_key(href)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        text = link.get_text(" ", strip=True)
        streams.append({"quality": _quality_from_url(href, text), "url": href, "format": "mp4"})

    if not streams:
        for match in re.finditer(
            r"https?://(?:www\.)?1porn\.tv/get_file/[^\"'\s<>]+\.mp4[^\"'\s<>]*",
            html or "",
            re.IGNORECASE,
        ):
            src = _normalize_media_url(match.group(0))
            if not src:
                continue
            key = _stream_key(src)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            streams.append(
                {
                    "quality": _quality_from_url(src),
                    "url": src,
                    "format": "mp4",
                }
            )

    def _qval(s: dict) -> int:
        digits = "".join(filter(str.isdigit, str(s.get("quality", ""))))
        return int(digits) if digits else 0

    streams.sort(key=_qval, reverse=True)
    default_url = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
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
                await client.get(BASE_SITE)
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.text) > 2000:
                    return resp.text
                if resp.status_code == 200:
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


async def _resolve_redirect(url: str) -> str:
    headers = dict(_DEFAULT_HEADERS)
    try:
        session = await pool.get_session()
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            final_url = str(response.url)
            if any(x in final_url for x in (".mp4", ".m3u8", "get_file", "cdn", "ahcdn.com")):
                return final_url
    except Exception:
        pass
    return url


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-webp", "data-original", "data-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        return _normalize_media_url(url)
    return None


def _count_item_value(soup: BeautifulSoup, icon_class: str) -> Optional[str]:
    scope = soup.select_one(".video-holder") or soup
    for item in scope.select(".count-item"):
        if not item.select_one(f".{icon_class}"):
            continue
        text = re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
        return text or None
    return None


def _views_from_json_ld(data: dict[str, Any]) -> Optional[str]:
    stats = data.get("interactionStatistic")
    if not isinstance(stats, list):
        return None
    for item in stats:
        if not isinstance(item, dict):
            continue
        interaction = str(item.get("interactionType") or "")
        if "WatchAction" in interaction:
            count = item.get("userInteractionCount")
            if count is not None:
                return str(count)
    return None


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    flash = _parse_flashvars(html)
    ld = _parse_json_ld(soup)

    title_el = soup.select_one("h1.title, h1.headline__title, h1")
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            ld.get("name"),
            title_el.get_text(" ", strip=True) if title_el else None,
            flash.get("video_title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        ld.get("thumbnailUrl"),
        flash.get("preview_url"),
    )
    if thumbnail:
        thumbnail = _normalize_media_url(str(thumbnail))

    video_el = soup.select_one("video.video-js, video")
    if video_el and video_el.get("poster"):
        thumbnail = thumbnail or _normalize_media_url(str(video_el.get("poster")))

    duration = _first_non_empty(
        _count_item_value(soup, "icon-oclock"),
        _parse_iso_duration(str(ld.get("duration") or "")),
    )
    if not duration:
        duration_el = soup.select_one(".duration, .video-info__duration")
        if duration_el:
            duration = duration_el.get_text(" ", strip=True)

    views = _first_non_empty(
        _count_item_value(soup, "icon-eye"),
        _views_from_json_ld(ld),
    )

    upload_date = _first_non_empty(
        _count_item_value(soup, "icon-calendar"),
        ld.get("uploadDate"),
    )

    raw_tags = flash.get("video_tags") or flash.get("video_categories") or ""
    tags = [t.strip() for t in re.split(r"[,|]", raw_tags) if t.strip()]
    if not tags:
        for link in soup.select(".video-holder a[href*='/categories/'], .video-links__link[href*='/categories/']"):
            txt = link.get_text(" ", strip=True)
            if txt:
                tags.append(txt)

    model_link = soup.select_one(
        ".video-holder a[href*='/pornstars/'], .video-holder a[href*='/models/'], "
        ".video-links__link[href*='/models/']"
    )
    channel_link = soup.select_one(
        ".video-holder a[href*='/channels/'], .video-links__link[href*='/sites/']"
    )
    uploader = _first_non_empty(
        flash.get("video_models"),
        model_link.get_text(" ", strip=True) if model_link else None,
        channel_link.get_text(" ", strip=True) if channel_link else None,
    )

    preview_url = None
    preview_div = soup.select_one(".thumb__img[data-preview], .img[data-preview], img[data-preview]")
    if preview_div and preview_div.get("data-preview"):
        preview_url = _normalize_media_url(str(preview_div.get("data-preview")))

    video = _extract_video_streams(html)
    if not video.get("has_video"):
        embed = ld.get("embedUrl")
        if embed:
            embed_url = _normalize_media_url(str(embed))
            video = {
                "streams": [{"url": embed_url, "quality": "embed", "format": "embed"}],
                "hls": None,
                "default": embed_url,
                "has_video": True,
            }

    return {
        "url": url,
        "title": title,
        "description": _first_non_empty(_meta(soup, prop="og:description"), ld.get("description")),
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
        video_url = _normalize_video_href(flash.get("video_url") or "")
        if video_url:
            return video_url
    return _normalize_video_href(url) or url


async def scrape(url: str) -> dict[str, Any]:
    canon = await _resolve_scrape_url(url)
    html = await fetch_page(canon, referer=BASE_SITE)
    data = parse_video_page(html, canon)

    video_data = data.get("video") or {}
    streams = video_data.get("streams") or []
    if streams:
        resolved_streams = []
        for stream in streams:
            stream = dict(stream)
            if "get_file" in stream.get("url", ""):
                stream["url"] = await _resolve_redirect(stream["url"])
                if ".m3u8" in stream["url"]:
                    stream["format"] = "hls"
                elif ".mp4" in stream["url"]:
                    stream["format"] = "mp4"
            resolved_streams.append(stream)
        video_data["streams"] = resolved_streams
        if video_data.get("default") and "get_file" in str(video_data["default"]):
            video_data["default"] = await _resolve_redirect(str(video_data["default"]))
        data["video"] = video_data

    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
        return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))

    if not parts:
        new_path = f"/latest-updates/{page_num}/"
        return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))

    new_path = "/" + "/".join(parts + [str(page_num)]) + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", new_path, "", "", ""))


def _list_section_candidates(base_url: str) -> list[str]:
    path = (urlparse(_build_list_page_url(base_url, 1)).path or "/").lower().rstrip("/") or "/"

    if path in ("/latest-updates", ""):
        return [
            "list_videos_latest_videos_list_items",
            "list_videos_most_recent_videos_items",
            "custom_list_videos_most_recent_videos_items",
        ]
    if path.startswith("/search"):
        return [
            "custom_list_videos_videos_list_search_result_items",
            "list_videos_common_videos_list_items",
        ]
    if path.startswith("/categories/") or path in ("/most-popular", "/top-rated", "/longest"):
        return [
            "list_videos_common_videos_list_items",
            "custom_list_videos_common_videos_list_items",
        ]
    return [
        "list_videos_most_recent_videos_items",
        "list_videos_common_videos_list_items",
        "list_videos_latest_videos_list_items",
    ]


def _list_root(soup: BeautifulSoup, base_url: str) -> Any:
    for section_id in _list_section_candidates(base_url):
        root = soup.select_one(f"#{section_id}")
        if root is not None and root.select("a[href*='/videos/']"):
            return root

    for root in soup.select("[id$='_items']"):
        if root.select("a[href*='/videos/']"):
            return root

    thumbs = soup.select_one(".thumbs:not(.thumbs--albums)")
    if thumbs and thumbs.select("a[href*='/videos/']"):
        return thumbs
    return None


def _parse_list_item(box: Any) -> Optional[dict[str, Any]]:
    classes = box.get("class") or []
    if "item--adv-thumb" in classes or "avd-video-item" in classes:
        return None

    link = box.select_one("a[href*='/videos/']")
    if not link:
        return None
    href = _normalize_video_href(link.get("href") or "")
    if not href:
        return None

    img_wrap = box.select_one(".thumb__img, .img.thumb__img, .img")
    img = img_wrap.select_one("img") if img_wrap else box.select_one("img")
    thumb = _best_image_url(img)
    preview = None
    if img_wrap and img_wrap.get("data-preview"):
        preview = _normalize_media_url(str(img_wrap.get("data-preview")))

    title_el = box.select_one(".title, .thumb__title, .item-title")
    title = _clean_title(
        _first_non_empty(
            link.get("title"),
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        )
    ) or "Unknown Video"

    duration = None
    for sel in (".duration", ".time", ".thumb__duration"):
        duration_el = box.select_one(sel)
        if duration_el:
            duration = duration_el.get_text(" ", strip=True)
            break

    views = None
    for item in box.select(".thumb-item, .item-info"):
        if item.select_one(".icon-eye"):
            views = re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
            break

    uploader = None
    model_link = box.select_one(".thumb-models__link, a[href*='/models/'], a[href*='/pornstars/']")
    if model_link:
        uploader = model_link.get_text(" ", strip=True)

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "preview_url": preview,
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = _list_root(soup, base_url)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    boxes: list[Any] = []
    if root is not None:
        boxes = root.select(".thumb.item, .item")
        if not boxes:
            boxes = root.select(".item")

    for box in boxes:
        if len(items) >= limit:
            break
        parsed = _parse_list_item(box)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if not items:
        scope = root or soup
        for a in scope.select("a[href*='/videos/']"):
            if len(items) >= limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href or href in seen:
                continue
            img = a.find("img")
            seen.add(href)
            items.append(
                {
                    "url": href,
                    "title": _clean_title(a.get("title") or (img.get("alt") if img else None))
                    or "Unknown Video",
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "preview_url": None,
                }
            )

    return items[:limit]
