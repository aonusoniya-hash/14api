from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

BASE_SITE = "https://www.underhentai.net/"
DEFAULT_BROWSE_URL = "https://www.underhentai.net/"
SITE_HOST = "www.underhentai.net"
SITE_ALIASES = frozenset({"underhentai.net", "www.underhentai.net"})
STREAM_HOSTS = frozenset(
    {
        "static.underhentai.net",
        "krakenfiles.com",
        "krakencloud.net",
        "luluvdo.com",
        "lulucdn.com",
    }
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": DEFAULT_BROWSE_URL,
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?underhentai\.net/(?P<slug>[^/?#]+)/?$",
    re.IGNORECASE,
)
_WATCH_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?underhentai\.net/watch/?\?(?P<query>[^#]+)$",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(
    r"(?:https?://(?:www\.)?underhentai\.net)?/(?P<slug>[a-z0-9][a-z0-9-]+)/?",
    re.IGNORECASE,
)
_NON_VIDEO_SLUGS = frozenset(
    {
        "cat",
        "dmca",
        "embed",
        "feed",
        "go",
        "index",
        "out",
        "page",
        "pop",
        "random",
        "refer",
        "recommend",
        "recommends",
        "releases",
        "tag",
        "top",
        "uncensored",
        "watch",
        "wp-admin",
        "wp-content",
        "wp-json",
        "wp-login.php",
        "xmlrpc.php",
    }
)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/page/(\d+)$", re.IGNORECASE)
_KRAKEN_EMBED_RE = re.compile(
    r"""https?://krakenfiles\.com/embed-video/[A-Za-z0-9]+""",
    re.IGNORECASE,
)
_LULU_EMBED_RE = re.compile(
    r"""https?://luluvdo\.com/embed/[A-Za-z0-9]+""",
    re.IGNORECASE,
)
_DISQUS_URL_RE = re.compile(r"""var\s+disqus_url\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".underhentai.net"):
        return True
    if h in STREAM_HOSTS:
        return True
    return any(h.endswith("." + cdn) for cdn in STREAM_HOSTS)


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
    t = html.unescape(str(title).strip())
    for suffix in (
        " - UnderHentai",
        " | UnderHentai",
        " – UnderHentai",
        " &#8211; UnderHentai",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
        elif t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str | None) -> Optional[str]:
    if not url:
        return None
    raw = str(url).strip().replace("\\/", "/")
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return urljoin(BASE_SITE, raw)
    return raw


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("src", "data-src", "data-original"):
        url = _normalize_media_url(img.get(key))
        if url:
            return url
    return None


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/{slug.strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href in ("/", "#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host and host not in SITE_ALIASES and not host.endswith(".underhentai.net"):
        return None
    path = (parsed.path or "").strip("/")
    if not path or "/" in path:
        return None
    slug = path.lower()
    if slug in _NON_VIDEO_SLUGS:
        return None
    if parsed.query or parsed.fragment:
        return None
    return _canonical_video_url(path)


def _resolve_video_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0]
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    if _WATCH_PAGE_RE.match(raw):
        return raw
    if not raw.endswith("/"):
        raw += "/"
    match = _VIDEO_PAGE_RE.match(raw.rstrip("/") + "/")
    if not match:
        raise ValueError(f"Unsupported UnderHentai URL: {url}")
    slug = match.group("slug").lower()
    if slug in _NON_VIDEO_SLUGS:
        raise ValueError(f"Unsupported UnderHentai URL: {url}")
    return raw if raw.endswith("/") else f"{raw}/"


def _is_watch_url(url: str) -> bool:
    return bool(_WATCH_PAGE_RE.match((url or "").strip().split("#", 1)[0]))


def _strip_emoji(text: str | None) -> str:
    if not text:
        return ""
    return "".join(ch for ch in str(text) if ord(ch) < 0x1F000).strip()


def _variant_prefix(vtype: str, subs_meta: str) -> str:
    vtype_l = _strip_emoji(vtype).lower()
    subs_l = _strip_emoji(subs_meta).lower()
    if "raw" in vtype_l:
        return "japanese raw"
    if "spanish" in subs_l or "espa" in subs_l or "espanol" in subs_l:
        return "spanish sub"
    if "english" in subs_l:
        return "english sub"
    if "sub" in vtype_l:
        return "sub"
    return (_strip_emoji(vtype) or "sub").lower()


def _stream_quality_label(variant: str, mirror: str) -> str:
    variant = (variant or "").strip().lower()
    mirror = (mirror or "").strip().lower()
    if mirror:
        return f"{variant} {mirror}".strip()
    return variant


def _parse_episode_cards(soup: BeautifulSoup) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for card in soup.select(".ep2-card"):
        vtype_el = card.select_one(".ep2-vtype")
        vtype = _strip_emoji(vtype_el.get_text(" ", strip=True) if vtype_el else "")

        subs_meta = ""
        for item in card.select(".ep2-meta-item"):
            label = item.select_one(".ep2-meta-label")
            value = item.select_one(".ep2-meta-value")
            if not label or not value:
                continue
            if "subs" in label.get_text(strip=True).lower():
                subs_meta = value.get_text(" ", strip=True)

        mega_url: Optional[str] = None
        for a in card.select("a.ep2-dl"):
            if "mega" in a.get_text(strip=True).lower():
                mega_url = _normalize_media_url(a.get("href"))
                break

        stream_el = card.select_one("a.ep2-stream[href]")
        stream_url = _normalize_media_url(stream_el.get("href")) if stream_el else None

        cards.append(
            {
                "vtype": vtype,
                "subs_meta": _strip_emoji(subs_meta),
                "mega_url": mega_url,
                "stream_url": stream_url,
            }
        )
    return cards


def _extract_embed_urls(page_html: str) -> dict[str, str]:
    html = page_html or ""
    kraken = _KRAKEN_EMBED_RE.search(html)
    lulu = _LULU_EMBED_RE.search(html)
    out: dict[str, str] = {}
    if kraken:
        out["krakenfiles"] = kraken.group(0).strip().replace("\\/", "/")
    if lulu:
        out["lulustream"] = lulu.group(0).strip().replace("\\/", "/")
    return out


def _watch_episode_key(watch_url: str) -> Optional[str]:
    parsed = urlparse(watch_url)
    if "/watch/" not in (parsed.path or ""):
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    video_id = _first_non_empty(query.get("id"))
    episode = _first_non_empty(query.get("ep"))
    if video_id is None or episode is None:
        return None
    return f"{video_id}:{episode}"


def _card_episode_key(card: dict[str, Any]) -> Optional[str]:
    stream_url = card.get("stream_url")
    if not stream_url:
        return None
    return _watch_episode_key(stream_url if str(stream_url).startswith("http") else urljoin(BASE_SITE, stream_url))


def _watch_video_id(watch_url: str) -> Optional[str]:
    parsed = urlparse(watch_url)
    if "/watch/" not in (parsed.path or ""):
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return _first_non_empty(query.get("id"))


async def _resolve_parent_url_from_watch(watch_url: str, *, referer: str) -> Optional[str]:
    video_id = _watch_video_id(watch_url)
    if not video_id:
        return None

    from curl_cffi import requests as cr

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer
    lookup_url = f"{BASE_SITE}?p={video_id}"

    def _do_request() -> Optional[str]:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                resp = cr.get(
                    lookup_url,
                    headers=headers,
                    impersonate=imp,
                    timeout=45.0,
                    allow_redirects=True,
                )
                if resp.status_code != 200:
                    continue
                final = resp.url.rstrip("/") + "/"
                if _VIDEO_PAGE_RE.match(final):
                    return final
            except Exception:
                continue
        return None

    return await asyncio.to_thread(_do_request)


def _find_card_for_watch(cards: list[dict[str, Any]], watch_url: str) -> Optional[dict[str, Any]]:
    target = _watch_episode_key(watch_url)
    if not target:
        return None
    for card in cards:
        if _card_episode_key(card) == target:
            return card
    return None


def _streams_from_card_variants(
    cards: list[dict[str, Any]],
    *,
    embeds_by_watch: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for card in cards:
        prefix = _variant_prefix(str(card.get("vtype") or ""), str(card.get("subs_meta") or ""))
        mega_url = card.get("mega_url")
        if mega_url and mega_url not in seen:
            seen.add(mega_url)
            streams.append(
                {"quality": _stream_quality_label(prefix, "mega"), "url": mega_url, "format": "embed"}
            )

        watch_url = card.get("stream_url")
        if not watch_url:
            continue
        embeds = embeds_by_watch.get(str(watch_url), {})
        for mirror_key, mirror_name in (("krakenfiles", "krakenfiles"), ("lulustream", "lulustream")):
            embed_url = embeds.get(mirror_key)
            if not embed_url or embed_url in seen:
                continue
            seen.add(embed_url)
            streams.append(
                {
                    "quality": _stream_quality_label(prefix, mirror_name),
                    "url": embed_url,
                    "format": "embed",
                }
            )

    return streams


async def _collect_embeds_for_cards(
    cards: list[dict[str, Any]],
    *,
    referer: str,
) -> dict[str, dict[str, str]]:
    embeds_by_watch: dict[str, dict[str, str]] = {}
    for card in cards:
        watch_url = card.get("stream_url")
        if not watch_url or watch_url in embeds_by_watch:
            continue
        try:
            watch_html = await fetch_page(watch_url, referer=referer)
        except Exception:
            embeds_by_watch[watch_url] = {}
            continue
        embeds_by_watch[watch_url] = _extract_embed_urls(watch_html)
    return embeds_by_watch


def _video_payload_from_streams(streams: list[dict[str, str]]) -> dict[str, Any]:
    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


async def _streams_from_video_page(soup: BeautifulSoup, *, referer: str) -> dict[str, Any]:
    cards = _parse_episode_cards(soup)
    if not cards:
        return _video_payload_from_streams([])
    embeds_by_watch = await _collect_embeds_for_cards(cards, referer=referer)
    streams = _streams_from_card_variants(cards, embeds_by_watch=embeds_by_watch)
    return _video_payload_from_streams(streams)


async def _streams_from_watch_page(
    watch_html: str,
    watch_url: str,
    *,
    referer: str,
) -> dict[str, Any]:
    parent_url = None
    match = _DISQUS_URL_RE.search(watch_html or "")
    if match:
        parent_url = match.group(1).strip()
    if not parent_url:
        parent_url = await _resolve_parent_url_from_watch(watch_url, referer=referer)

    cards: list[dict[str, Any]] = []
    if parent_url:
        try:
            parent_html = await fetch_page(parent_url, referer=referer)
            cards = _parse_episode_cards(BeautifulSoup(parent_html, "lxml"))
        except Exception:
            cards = []

    card = _find_card_for_watch(cards, watch_url) if cards else None
    if card:
        embeds = _extract_embed_urls(watch_html)
        streams = _streams_from_card_variants([card], embeds_by_watch={watch_url: embeds})
        return _video_payload_from_streams(streams)

    embeds = _extract_embed_urls(watch_html)
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    fallback_prefix = "japanese raw"
    for mirror_key, mirror_name in (("krakenfiles", "krakenfiles"), ("lulustream", "lulustream")):
        embed_url = embeds.get(mirror_key)
        if not embed_url or embed_url in seen:
            continue
        seen.add(embed_url)
        streams.append(
            {
                "quality": _stream_quality_label(fallback_prefix, mirror_name),
                "url": embed_url,
                "format": "embed",
            }
        )
    return _video_payload_from_streams(streams)


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str:
    from curl_cffi import requests as cr

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    def _do_request() -> str:
        for imp in ("chrome120", "chrome110", "safari15_3"):
            try:
                resp = cr.get(url, headers=headers, impersonate=imp, timeout=45.0)
                if resp.status_code != 200:
                    continue
                return resp.text
            except Exception:
                continue
        raise ValueError(f"Failed to fetch: {url}")

    return await asyncio.to_thread(_do_request)


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    return await _fetch_with_curl_cffi(url, referer=referer or DEFAULT_BROWSE_URL)


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("article.data-block"):
        if len(items) >= limit:
            break
        link = block.select_one(".article-header h2 a[href], .article-section a[href]")
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                link.get_text(strip=True),
                img.get("title") if img else None,
                img.get("alt") if img else None,
            )
        ) or "Unknown Video"

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": None,
                "views": None,
                "uploader_name": "underhentai",
            }
        )

    if len(items) < limit:
        for link in soup.select("a[href]"):
            if len(items) >= limit:
                break
            url = _normalize_video_href(link.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            img = link.select_one("img")
            title = _clean_title(
                _first_non_empty(
                    img.get("alt") if img else None,
                    img.get("title") if img else None,
                    link.get("title"),
                )
            )
            if not title:
                continue
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": _best_image_url(img),
                    "duration": None,
                    "views": None,
                    "uploader_name": "underhentai",
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

    m_page = _PATH_PAGE_SUFFIX_RE.match(path)
    if m_page:
        path = m_page.group(1)

    if page_num > 1:
        if query.get("s"):
            query["page"] = str(page_num)
            new_path = "/" if not path else f"/{path}/"
        else:
            new_path = f"/{path}/page/{page_num}/" if path else f"/page/{page_num}/"
    else:
        query.pop("page", None)
        new_path = "/" if not path else f"/{path}/"

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
    page_html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")
    page_url = _resolve_video_url(url)

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _normalize_media_url(
            soup.select_one(".content-head img.img-responsive, .content-box img.img-responsive").get("src")
        )
        if soup.select_one(".content-head img.img-responsive, .content-box img.img-responsive")
        else None,
    )

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )

    tags: list[str] = []
    for a in soup.select('a[href*="/tag/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    category: Optional[str] = None
    for a in soup.select('a[href*="/cat/brand/"]'):
        label = a.get_text(strip=True)
        if label:
            category = label
            break

    upload_date: Optional[str] = None
    for box in soup.select(".content-box.sidebar-light.content-foot, .content-box.content-foot.sidebar-light"):
        label = box.select_one("p")
        if not label or "aired" not in label.get_text(" ", strip=True).lower():
            continue
        value = box.select_one(".label-primary")
        if value:
            upload_date = value.get_text(strip=True)
            break

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {"streams": [], "hls": None, "default": None, "has_video": False}
    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": "underhentai",
        "category": category,
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
    raw_url = (url or "").strip().split("#", 1)[0]
    if _is_watch_url(raw_url):
        watch_url = raw_url if raw_url.startswith("http") else urljoin(BASE_SITE, raw_url.lstrip("/"))
        watch_html = await fetch_page(watch_url, referer=DEFAULT_BROWSE_URL)
        video_data = await _streams_from_watch_page(watch_html, watch_url, referer=DEFAULT_BROWSE_URL)
        soup = BeautifulSoup(watch_html, "lxml")
        title = _clean_title(
            _first_non_empty(
                soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
                _meta(soup, prop="og:title"),
                soup.title.get_text(strip=True) if soup.title else None,
            )
        ) or "Unknown Video"
        return {
            "url": watch_url,
            "title": title,
            "description": _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description")),
            "thumbnail_url": _first_non_empty(
                _meta(soup, prop="og:image"),
                _normalize_media_url(soup.select_one("img.img-responsive").get("src"))
                if soup.select_one("img.img-responsive")
                else None,
            ),
            "duration": None,
            "views": None,
            "uploader_name": "underhentai",
            "category": None,
            "tags": None,
            "upload_date": None,
            "video": {
                k: v
                for k, v in video_data.items()
                if k in ("streams", "hls", "default", "has_video")
            },
            "related_videos": _parse_list_items(soup, limit=20),
        }

    page_url = _resolve_video_url(url)
    page_html = await fetch_page(page_url, referer=DEFAULT_BROWSE_URL)
    soup = BeautifulSoup(page_html, "lxml")
    video_data = await _streams_from_video_page(soup, referer=page_url)
    return parse_video_page(page_html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        page_html = await fetch_page(page_url, referer=normalized_base or DEFAULT_BROWSE_URL)
    except Exception:
        return []
    soup = BeautifulSoup(page_html, "lxml")
    return _parse_list_items(soup, limit=limit)
