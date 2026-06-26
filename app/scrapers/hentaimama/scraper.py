from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://hentaimama.io/"
DEFAULT_BROWSE_URL = "https://hentaimama.io/"
SITE_HOST = "hentaimama.io"
SITE_ALIASES = frozenset({"hentaimama.io", "www.hentaimama.io"})
AJAX_URL = "https://hentaimama.io/wp-admin/admin-ajax.php"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
}

_EPISODE_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaimama\.io/episodes/(?P<slug>[^/]+)/?$",
    re.IGNORECASE,
)
_TVSHOW_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?hentaimama\.io/tvshows/(?P<slug>[^/]+)/?$",
    re.IGNORECASE,
)
_POST_ID_BODY_RE = re.compile(r"postid-(\d+)", re.IGNORECASE)
_POST_ID_INPUT_RE = re.compile(r'name=["\']idpost["\']\s+value=["\'](\d+)["\']', re.IGNORECASE)
_SHORTLINK_RE = re.compile(r"/\?p=(\d+)", re.IGNORECASE)
_IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_JW_FILE_RE = re.compile(r'file:\s*["\']([^"\']+)["\']', re.IGNORECASE)
_EPISODE_NUM_RE = re.compile(r"-episode-(\d+)", re.IGNORECASE)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$", re.IGNORECASE)
_PATH_NUMERIC_SUFFIX_RE = re.compile(r"^(.+)/(\d+)$", re.IGNORECASE)
_PAGE_PAGINATION_ROOTS = frozenset(
    {"genre", "tvshows", "recent-episodes", "new-monthly-hentai"}
)
_SINGLE_PAGE_ROOTS = frozenset({"hentai-list"})
_HOMEPAGE_LISTING = "recent-episodes"


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return (
        h in SITE_ALIASES
        or h.endswith(".hentaimama.io")
        or h == "gdvid.info"
        or h.endswith(".gdvid.info")
        or h.endswith(".javprovider.com")
        or h == "javprovider.com"
    )


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
    for suffix in (
        " &ndash; Hentaimama",
        " – Hentaimama",
        " - Hentaimama",
        " | Hentaimama",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    raw = str(text).strip().upper()
    m = re.match(r"^([\d.]+)\s*K$", raw)
    if m:
        try:
            return str(int(float(m.group(1)) * 1000))
        except ValueError:
            pass
    digits = re.sub(r"[^\d]", "", raw)
    return digits or None


def _is_cloudflare_challenge(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    low = html.lower()
    if "sorry, you have been blocked" in low:
        return True
    if "just a moment" in low and "hentaimama" not in low and "player_sist" not in low:
        return True
    if "enable javascript and cookies" in low and "player_sist" not in low:
        return True
    return False


def _canonical_episode_url(slug: str) -> str:
    return f"https://{SITE_HOST}/episodes/{slug.strip('/')}/"


def _canonical_tvshow_url(slug: str) -> str:
    return f"https://{SITE_HOST}/tvshows/{slug.strip('/')}/"


def _normalize_episode_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host != SITE_HOST:
        return None
    path = (parsed.path or "").rstrip("/")
    m = re.match(r"^/episodes/([^/]+)$", path, re.I)
    if m:
        return _canonical_episode_url(m.group(1))
    return None


def _normalize_tvshow_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host != SITE_HOST:
        return None
    path = (parsed.path or "").rstrip("/")
    m = re.match(r"^/tvshows/([^/]+)$", path, re.I)
    if m:
        return _canonical_tvshow_url(m.group(1))
    return None


def _episode_number_from_url(url: str) -> int:
    m = _EPISODE_NUM_RE.search(url or "")
    return int(m.group(1)) if m else 9999


def _extract_post_id(html: str) -> Optional[str]:
    m = _POST_ID_BODY_RE.search(html)
    if m:
        return m.group(1)
    m = _POST_ID_INPUT_RE.search(html)
    if m:
        return m.group(1)
    m = _SHORTLINK_RE.search(html)
    if m:
        return m.group(1)
    return None


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


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    return await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)


async def _fetch_player_iframes(post_id: str, page_url: str) -> list[str]:
    raw = await _fetch_with_curl_cffi(
        AJAX_URL,
        referer=page_url,
        method="POST",
        data={"action": "get_player_contents", "a": post_id},
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    urls: list[str] = []
    for item in payload:
        if not isinstance(item, str):
            continue
        for match in _IFRAME_SRC_RE.finditer(item):
            src = match.group(1).strip().replace("\\/", "/")
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(BASE_SITE, src)
            if src and src not in urls:
                urls.append(src)
    return urls


def _streams_from_player_html(html: str, label: str) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    for match in _JW_FILE_RE.finditer(html):
        url = match.group(1).strip().replace("\\/", "/")
        if not url.startswith("http"):
            continue
        fmt = "hls" if ".m3u8" in url.lower() else "mp4"
        streams.append({"quality": label, "url": url, "format": fmt})
    return streams


async def _fetch_player_streams(html: str, page_url: str) -> dict[str, Any]:
    post_id = _extract_post_id(html)
    if not post_id:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    iframe_urls = await _fetch_player_iframes(post_id, page_url)
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    labels = ("Mirror 1", "Mirror 2", "Mirror 3")
    for idx, iframe_url in enumerate(iframe_urls):
        label = labels[idx] if idx < len(labels) else f"Mirror {idx + 1}"
        if iframe_url not in seen:
            seen.add(iframe_url)
            streams.append({"quality": label, "url": iframe_url, "format": "embed"})
        try:
            player_html = await fetch_page(iframe_url, referer=page_url)
        except Exception:
            continue
        for stream in _streams_from_player_html(player_html, label):
            if stream["url"] in seen:
                continue
            seen.add(stream["url"])
            streams.append(stream)
            if stream["format"] == "hls" and hls_url is None:
                hls_url = stream["url"]

    default = hls_url or next(
        (s["url"] for s in streams if s.get("format") == "mp4"),
        streams[0]["url"] if streams else None,
    )
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _resolve_episode_url(html: str, url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.endswith("/"):
        raw += "/"
    if _EPISODE_PAGE_RE.match(raw):
        return raw

    if _TVSHOW_PAGE_RE.match(raw):
        soup = BeautifulSoup(html, "lxml")
        episodes: list[tuple[int, str]] = []
        for link in soup.select("a[href*='/episodes/']"):
            ep_url = _normalize_episode_href(link.get("href") or "")
            if ep_url:
                episodes.append((_episode_number_from_url(ep_url), ep_url))
        if episodes:
            episodes.sort(key=lambda x: x[0])
            return episodes[0][1]
        raise ValueError(f"No episodes found for series: {url}")

    raise ValueError(f"Unsupported Hentaimama URL: {url}")


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("article.item.episodes, .items .episodes"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href*='/episodes/']")
        if not link:
            continue
        url = _normalize_episode_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        img = block.select_one("img")
        series = block.select_one(".season_m .b, .serie")
        episode = block.select_one(".season_m .c")
        title = _clean_title(
            _first_non_empty(
                img.get("alt") if img else None,
                (
                    f"{series.get_text(strip=True)} {episode.get_text(strip=True)}"
                    if series and episode
                    else None
                ),
            )
        ) or "Unknown Episode"
        date_span = block.select_one(".data span")
        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": None,
                "views": None,
                "uploader_name": None,
                "upload_date": date_span.get_text(strip=True) if date_span else None,
            }
        )

    for block in soup.select("article.item.tvshows, .items .item"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href*='/tvshows/']")
        if not link:
            continue
        url = _normalize_tvshow_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        img = block.select_one("img")
        title_link = block.select_one(".data h3 a, h3 a")
        title = _clean_title(
            _first_non_empty(
                title_link.get_text(strip=True) if title_link else None,
                img.get("alt") if img else None,
            )
        ) or "Unknown Series"
        year_span = block.select_one(".data span")
        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": None,
                "views": None,
                "uploader_name": None,
                "upload_date": year_span.get_text(strip=True) if year_span else None,
            }
        )

    if len(items) < limit:
        for link in soup.select("a.anm_det_pop[href*='/tvshows/'], a.pop_info[href*='/tvshows/']"):
            if len(items) >= limit:
                break
            url = _normalize_tvshow_href(link.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = _clean_title(
                _first_non_empty(link.get("title"), link.get_text(strip=True))
            ) or "Unknown Series"
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

    if len(items) < limit:
        for link in soup.select(
            ".content a[href*='/tvshows/'], .content a[href*='/episodes/'], "
            ".module a[href*='/tvshows/'], .module a[href*='/episodes/']"
        ):
            if len(items) >= limit:
                break
            href = link.get("href") or ""
            label = _first_non_empty(link.get("title"), link.get_text(strip=True))
            if not label:
                continue
            if "/episodes/" in href:
                url = _normalize_episode_href(href)
                if not url or url in seen:
                    continue
                seen.add(url)
                title = _clean_title(label) or "Unknown Episode"
            elif "/tvshows/" in href:
                url = _normalize_tvshow_href(href)
                if not url or url in seen:
                    continue
                seen.add(url)
                title = _clean_title(label) or "Unknown Series"
            else:
                continue
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
    raw = (base_url or "").strip() or DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = (parsed.path or "").strip("/")
    page_num = max(1, int(page) if page else 1)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)

    m_page = _PATH_PAGE_SUFFIX_RE.match(path)
    m_num = _PATH_NUMERIC_SUFFIX_RE.match(path)
    if m_page:
        path = m_page.group(1)
    elif m_num and m_num.group(1).split("/")[0] in _SINGLE_PAGE_ROOTS:
        path = m_num.group(1)

    parts = path.split("/") if path else []
    root = parts[0] if parts else ""

    if not path:
        if query.get("s"):
            new_path = "/"
            if page_num > 1:
                query["page"] = str(page_num)
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
        path = _HOMEPAGE_LISTING
        parts = path.split("/")
        root = parts[0]

    if root in _SINGLE_PAGE_ROOTS:
        new_path = f"/{path}/"
    elif root in _PAGE_PAGINATION_ROOTS:
        base_path = "/" + "/".join(parts) + "/"
        new_path = base_path if page_num <= 1 else f"{base_path}page/{page_num}/"
    else:
        new_path = f"/{path}/" if path else "/"
        if page_num > 1:
            query["page"] = str(page_num)

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


def parse_video_page(
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = url if url.endswith("/") else f"{url}/"

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("img")),
    )
    if thumbnail and str(thumbnail).startswith("//"):
        thumbnail = f"https:{thumbnail}"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    views: Optional[str] = None
    views_el = soup.select_one(".control .views strong, .views strong")
    if views_el:
        views = _normalize_views(views_el.get_text(strip=True))

    tags: list[str] = []
    for a in soup.select(".sgeneros a, .mgen a, a[href*='/genre/']"):
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
        "views": views,
        "uploader_name": "hentaimama",
        "category": None,
        "tags": tags or None,
        "upload_date": None,
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
