from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.teamskeettube.com/"
SITE_HOST = "teamskeettube.com"
SITE_ALIASES = frozenset({"teamskeettube.com", "www.teamskeettube.com"})

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
    r"teamskeettube\.com/video/(?P<slug>(?!category/)[^/?#]+)/?",
    re.IGNORECASE,
)
_PLAYER_Q_RE = re.compile(r"player-x\.php\?q=([^\"'&]+)", re.IGNORECASE)
_EMBED_SRC_RE = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
_XVIDEOS_EMBED_RE = re.compile(
    r"https?://(?:www\.)?xvideos\.com/embedframe/[a-z0-9]+",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".teamskeettube.com")


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
        " | Team Skeet Tube",
        " - Team Skeet Tube",
        " | TeamSkeet Tube",
        " - TeamSkeet Tube",
        " | teamskeettube.com",
        " - teamskeettube.com",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if ":" in t:
        prefix = t.split(":", 1)[0].strip().lower()
        if prefix in {
            "pervz",
            "mylf",
            "shoplyfter",
            "freeuse",
            "sis loves me",
            "family strokes",
            "dad crush",
            "perv mom",
            "bffs",
            "swappz",
            "exxxtra small",
            "teamskeet",
        }:
            t = t.split(":", 1)[1].strip()
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


def _decode_player_q(q: str) -> dict[str, str]:
    raw = (q or "").strip()
    if not raw:
        return {}
    pad = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(pad).decode("utf-8", errors="ignore")
    except Exception:
        return {}
    out: dict[str, str] = {}
    for part in decoded.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key] = urllib.parse.unquote(value)
    return out


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)

    parsed = urlparse(href.split("#", 1)[0])
    if "teamskeettube.com" not in (parsed.netloc or "").lower():
        return None

    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None

    slug = m.group("slug").strip("/")
    if not slug or slug.startswith("category"):
        return None

    return f"https://www.teamskeettube.com/video/{slug}/"


def _extract_embed_urls(html: str) -> list[str]:
    seen: set[str] = set()
    embeds: list[str] = []

    def _add(url: str) -> None:
        media = _normalize_media_url(url)
        if not media or media in seen:
            return
        seen.add(media)
        embeds.append(media)

    for q in _PLAYER_Q_RE.findall(html or ""):
        data = _decode_player_q(q)
        tag = data.get("tag") or ""
        for match in _EMBED_SRC_RE.finditer(tag):
            _add(match.group(1))

    for match in _XVIDEOS_EMBED_RE.findall(html or ""):
        _add(match)

    for iframe in BeautifulSoup(html, "lxml").select("iframe[src]"):
        src = str(iframe.get("src") or "").strip()
        if src and any(x in src.lower() for x in ("embedframe", "embed", "player")):
            if "teamskeettube.com/wp-content/plugins" not in src.lower():
                _add(src)

    return embeds


def _streams_from_html(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    for embed in _extract_embed_urls(html):
        streams.append({"url": embed, "quality": "embed", "format": "embed"})

    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
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


def _parse_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") in ("Article", "VideoObject"):
                        return node
            if data.get("@type") in ("Article", "VideoObject"):
                return data
    return {}


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    ld = _parse_json_ld(soup)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            ld.get("headline"),
            ld.get("name"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(_meta(soup, prop="og:image"))
    if not thumbnail and isinstance(ld.get("image"), dict):
        thumbnail = ld["image"].get("url")
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    description = _first_non_empty(_meta(soup, prop="og:description"), ld.get("description"))

    tags: list[str] = []
    for link in soup.select("a[rel='tag'], .tags a, .post-tags a"):
        txt = link.get_text(" ", strip=True)
        if txt and txt not in tags:
            tags.append(txt)

    uploader = "Team Skeet"
    for link in soup.select("a[href*='/video/category/']"):
        txt = link.get_text(" ", strip=True)
        if txt:
            uploader = txt
            if txt not in tags:
                tags.insert(0, txt)
            break

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": uploader,
        "category": tags[0] if tags else None,
        "tags": tags,
        "upload_date": ld.get("datePublished"),
        "video": _streams_from_html(html),
        "related_videos": [],
        "preview_url": thumbnail,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url) or url
    html = await fetch_page(canon, referer=BASE_SITE)
    return parse_video_page(html, canon)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("paged", None)

    parts = _path_parts(parsed.path)
    if len(parts) >= 2 and parts[-2] == "page" and parts[-1].isdigit():
        parts = parts[:-2]

    if page_num <= 1:
        new_path = "/" + "/".join(parts) + ("/" if parts else "")
    else:
        new_path = "/" + "/".join(parts + ["page", str(page_num)]) + "/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or f"www.{SITE_HOST}",
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

    return {
        "url": canon,
        "title": title,
        "thumbnail_url": thumb,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "preview_url": thumb,
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

    for anchor in soup.select("a[href*='/video/']"):
        if len(items) >= limit:
            break
        parsed = _parse_list_anchor(anchor)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    return items[:limit]
