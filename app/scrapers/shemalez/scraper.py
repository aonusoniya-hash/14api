from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from typing import Any, Optional, cast
from urllib.parse import parse_qs, quote, urlparse

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BASE_SITE = "https://shemalez.com/"
SITE_HOST = "shemalez.com"
SITE_ALIASES = frozenset({ "shemalez.com", "www.shemalez.com" })

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "X-Requested-With": "XMLHttpRequest",
}

_LIST_LIFETIME = 14400
_INFO_LIFETIME = 86400
_FILE_LIFETIME = 8640000

_VIDEO_HREF_RE = re.compile(
    r"(?:shemalez\.com)/(?:videos?|embed)/(?P<id>\d+)(?:/(?P<slug>[^/?#]+))?/?",
    re.IGNORECASE,
)

_BASE64_TRANSLATION = str.maketrans(
    {
        "\u0405": "S",
        "\u0406": "I",
        "\u0408": "J",
        "\u0410": "A",
        "\u0412": "B",
        "\u0415": "E",
        "\u041a": "K",
        "\u041c": "M",
        "\u041d": "H",
        "\u041e": "O",
        "\u0420": "P",
        "\u0421": "C",
        "\u0425": "X",
        ",": "/",
        ".": "+",
        "~": "=",
    }
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h.endswith(".shemalez.com")


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


def _normalize_site_host(host: str) -> str:
    h = (host or SITE_HOST).lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES or h.endswith(".shemalez.com"):
        return h
    return SITE_HOST


def _video_slug(video_id: str | int) -> str:
    vid = int(video_id)
    return f"{int(1e6 * (vid // 1e6))}/{1000 * (vid // 1000)}"


def decode_base64_url(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return base64.b64decode(raw.translate(_BASE64_TRANSLATION)).decode()


def _normalize_media_url(url: str, *, host: str = SITE_HOST) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return f"https://{host}{u}"
    return u


def _extract_video_ref(url: str) -> tuple[Optional[str], str, Optional[str]]:
    m = _VIDEO_HREF_RE.search(url or "")
    if not m:
        return None, SITE_HOST, None
    parsed = urlparse(url)
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    return m.group("id"), host, m.group("slug")


def _canonical_video_url(video_id: str, slug: str | None, *, host: str = SITE_HOST) -> str:
    slug_part = (slug or "video").strip("/") or "video"
    return f"https://{host}/videos/{video_id}/{slug_part}/"


def _quality_from_format(fmt: str | None) -> str:
    low = (fmt or "").lower().lstrip("_").replace(".mp4", "").replace(".m3u8", "")
    if not low:
        return "default"
    if low in {"lq", "low"}:
        return "360p"
    if low in {"hq", "high"}:
        return "720p"
    qm = re.search(r"(\d{3,4})p?", low)
    if qm:
        return f"{qm.group(1)}p"
    return low


def _build_params_suffix(
    *,
    section: str = "",
    object_id: str = "",
    page: int = 1,
    type_: str = "all",
    duration: str = "",
    date: str = "",
) -> str:
    return f"{section}.{object_id}.{page}.{type_}.{duration}.{date}"


def _parse_list_context(base_url: str) -> dict[str, Any]:
    raw = (base_url or "").strip() or BASE_SITE
    parsed = urlparse(raw if raw.startswith("http") else f"https://shemalez.com{raw if raw.startswith('/') else '/' + raw}")
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    parts = [p for p in (parsed.path or "/").strip("/").split("/") if p]
    query = parse_qs(parsed.query)

    page = 1
    sort = "latest-updates"
    section = ""
    object_id = ""
    search: Optional[str] = None

    if parts and parts[-1].isdigit():
        page = max(1, int(parts[-1]))
        parts = parts[:-1]

    if not parts or parts[0] in ("", "videos"):
        sort = "most-popular" if parts == ["videos"] else "latest-updates"
    elif parts[0] == "latest-updates":
        sort = "latest-updates"
    elif parts[0] == "most-popular":
        sort = "most-popular"
    elif parts[0] == "longest":
        sort = "longest"
    elif parts[0] == "top-rated":
        sort = "top-rated"
    elif parts[0] == "most-viewed":
        sort = "most-viewed"
    elif parts[0] == "categories" and len(parts) >= 2:
        section = "categories"
        object_id = parts[1]
        sort = "latest-updates"
    elif parts[0] == "search":
        search = _first_non_empty(*(query.get("s") or query.get("q") or []))
        sort = "relevance"

    if not search:
        for key in ("s", "q", "query"):
            if key in query and query[key]:
                search = str(query[key][0]).strip()
                sort = "relevance"
                break

    return {
        "host": host,
        "sort": sort,
        "section": section,
        "object_id": object_id,
        "page": page,
        "search": search,
        "referer": raw if raw.startswith("http") else f"https://{host}/{(parsed.path or '/').lstrip('/')}",
    }


def _build_list_api_url(
    *,
    host: str,
    sort: str,
    count: int,
    section: str,
    object_id: str,
    page: int,
    search: Optional[str],
) -> str:
    suffix = _build_params_suffix(section=section, object_id=object_id, page=page)
    if search:
        params = f"{_LIST_LIFETIME}/str/relevance/{count}/search..{page}.all.."
        return f"https://{host}/api/videos2.php?params={quote(params, safe='/')}&s={quote(search)}"
    params = f"{_LIST_LIFETIME}/str/{sort}/{count}/{suffix}"
    return f"https://{host}/api/videos2.php?params={quote(params, safe='/')}"


async def _fetch_with_curl_cffi(
    url: str,
    *,
    referer: str | None = None,
    expect_json: bool = True,
) -> Optional[Any]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    for imp in ("chrome124", "chrome120", "chrome110", "safari15_3"):
        for attempt in range(2):
            try:
                async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        break
                    if expect_json:
                        return resp.json()
                    return resp.text
            except Exception:
                if attempt == 0:
                    continue
                break
    return None


async def fetch_json(url: str, *, referer: str | None = None) -> Any:
    data = await _fetch_with_curl_cffi(url, referer=referer or BASE_SITE)
    if data is not None:
        return data

    from app.core.pool import fetch_html as pool_fetch_html

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    text = await pool_fetch_html(url, headers=headers)
    return json.loads(text)


async def _resolve_get_file_url(get_file_url: str, *, referer: str, host: str) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    target = _normalize_media_url(get_file_url, host=host)
    if not target:
        return None

    headers = {
        "User-Agent": _DEFAULT_HEADERS["User-Agent"],
        "Referer": referer if referer.startswith("http") else BASE_SITE,
        "Accept": "*/*",
    }

    async def _attempt(url: str) -> Optional[str]:
        async with AsyncSession(impersonate="chrome120", headers=headers, timeout=20.0) as client:
            resp = await client.get(url, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                resp_headers = cast(Any, resp).headers
                loc = resp_headers.get("Location") or resp_headers.get("location")
                if loc and loc.startswith("http") and "/get_file/" not in loc.lower():
                    return loc
        return None

    for candidate in (target, target.rstrip("/") + "/"):
        try:
            resolved = await asyncio.wait_for(_attempt(candidate), timeout=16.0)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


def _streams_from_video_files(video_files: list[dict[str, Any]], *, host: str, video_id: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in video_files or []:
        encoded = item.get("video_url")
        if not encoded:
            continue
        media = _normalize_media_url(decode_base64_url(str(encoded)), host=host)
        if not media or media in seen:
            continue
        seen.add(media)
        streams.append(
            {
                "url": media,
                "quality": _quality_from_format(str(item.get("format") or "")),
                "format": "hls" if ".m3u8" in media.lower() else "mp4",
            }
        )

    embed = f"https://{host}/embed/{video_id}"
    if embed not in seen:
        seen.add(embed)
        streams.append({"url": embed, "quality": "embed", "format": "embed"})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        qm = re.search(r"(\d{3,4})", qtxt)
        qnum = int(qm.group(1)) if qm else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, qnum)

    streams.sort(key=_score, reverse=True)
    hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
    default = next((s["url"] for s in streams if s.get("format") == "mp4"), None)
    if not default:
        default = hls or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls,
        "default": default,
        "has_video": bool(streams),
    }


async def _resolve_video_streams(video: dict[str, Any], *, referer: str, host: str, video_id: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_streams = [
        s for s in streams if s.get("format") == "mp4" and "/get_file/" in (s.get("url") or "").lower()
    ]
    if not get_file_streams:
        return

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _resolve_get_file_url(stream["url"], referer=referer, host=host)
        return stream, resolved

    pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_streams])
    for stream, resolved in pairs:
        if resolved:
            stream["url"] = resolved
        elif stream in streams:
            streams.remove(stream)

    remote_mp4 = [s for s in streams if s.get("format") == "mp4" and "/get_file/" not in (s.get("url") or "").lower()]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if remote_mp4:
        video["default"] = remote_mp4[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(remote_mp4) or bool(hls) or bool(embed)


def _tags_from_video_info(video: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    categories = video.get("categories")
    if isinstance(categories, dict):
        for item in categories.values():
            if isinstance(item, dict) and item.get("title"):
                tags.append(str(item["title"]).strip())
    models = video.get("models")
    if isinstance(models, dict):
        for item in models.values():
            if isinstance(item, dict) and item.get("title"):
                tags.append(str(item["title"]).strip())
    return tags


def _preview_url_for_video(video_id: str | int, preview: Any = None, *, host: str = SITE_HOST) -> Optional[str]:
    vid = str(video_id or "").strip()
    if not vid.isdigit():
        return None

    pv_raw = str(preview or "").strip()
    if pv_raw and vid in pv_raw:
        url = _normalize_media_url(pv_raw, host=host)
        if not url:
            return None
        if url.startswith("http"):
            return url
        if url.startswith("//"):
            return f"https:{url}"
        if "." in url.split("/")[0]:
            return f"https://{url.lstrip('/')}"

    bucket = 1000 * (int(vid) // 1000)
    return f"https://vp2.txxx.com/c12/videos/{bucket}/{vid}/{vid}_tr.mp4"



def _list_item_from_video_row(row: dict[str, Any], *, host: str) -> dict[str, Any]:
    video_id = str(row.get("video_id") or row.get("id") or "").strip()
    slug = _first_non_empty(row.get("dir"), "video") or "video"
    stats = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    thumb = _first_non_empty(row.get("thumb"), row.get("thumbsrc"), row.get("scr"))
    views = _first_non_empty(
        stats.get("viewed") if stats else None,
        row.get("video_viewed"),
        row.get("views"),
        row.get("viewed"),
    )
    uploader = _first_non_empty(
        user.get("username") if user else None,
        row.get("username"),
        row.get("user_name"),
    )

    return {
        "url": _canonical_video_url(video_id, slug, host=host) if video_id else BASE_SITE,
        "title": _first_non_empty(row.get("title")) or "Unknown Video",
        "thumbnail_url": _normalize_media_url(thumb, host=host) if thumb else None,
        "duration": _first_non_empty(row.get("duration")),
        "views": views,
        "uploader_name": uploader,
        "preview_url": _preview_url_for_video(video_id, row.get("pv"), host=host),
    }


def parse_video_info(data: dict[str, Any], *, host: str, video_id: str) -> dict[str, Any]:
    video: dict[str, Any] = {}
    raw_video = data.get("video")
    if isinstance(raw_video, dict):
        video = raw_video

    stats: dict[str, Any] = {}
    raw_stats = video.get("statistics")
    if isinstance(raw_stats, dict):
        stats = raw_stats

    user: dict[str, Any] = {}
    raw_user = video.get("user")
    if isinstance(raw_user, dict):
        user = raw_user

    slug = _first_non_empty(video.get("dir"))
    canon = _canonical_video_url(video_id, slug, host=host)

    categories = video.get("categories")
    category = None
    if isinstance(categories, dict) and categories:
        first = next(iter(categories.values()), None)
        if isinstance(first, dict):
            category = first.get("title")

    return {
        "url": canon,
        "title": _first_non_empty(video.get("title")) or "Unknown Video",
        "description": _first_non_empty(video.get("description")),
        "thumbnail_url": _normalize_media_url(
            _first_non_empty(video.get("thumbsrc"), video.get("thumb")) or "",
            host=host,
        )
        or None,
        "duration": _first_non_empty(video.get("duration")),
        "views": _first_non_empty(stats.get("viewed")),
        "uploader_name": _first_non_empty(user.get("username")),
        "category": category,
        "tags": _tags_from_video_info(video),
        "upload_date": _first_non_empty(video.get("post_date")),
        "video": {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
        },
        "related_videos": [],
        "preview_url": _preview_url_for_video(video_id, video.get("pv"), host=host),
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id, host, slug = _extract_video_ref(url)
    if not video_id:
        raise ValueError("Could not extract ShemaleZ video id from URL")

    canon = _canonical_video_url(video_id, slug, host=host)
    slug_path = _video_slug(video_id)
    info_url = f"https://{host}/api/json/video/{_INFO_LIFETIME}/{slug_path}/{video_id}.json"
    file_url = f"https://{host}/api/videofile.php?video_id={video_id}&lifetime={_FILE_LIFETIME}"

    info_data = await fetch_json(info_url, referer=canon)
    if isinstance(info_data, dict) and info_data.get("error"):
        raise ValueError(str(info_data.get("code") or "video_info_error"))

    result = parse_video_info(info_data if isinstance(info_data, dict) else {}, host=host, video_id=video_id)

    file_data = await fetch_json(file_url, referer=canon)
    files: list[dict[str, Any]]
    if isinstance(file_data, list):
        files = file_data
    elif isinstance(file_data, dict) and file_data.get("error"):
        files = []
    else:
        files = []

    video = _streams_from_video_files(files, host=host, video_id=video_id)
    result["video"] = video
    await _resolve_video_streams(result["video"], referer=canon, host=host, video_id=video_id)
    return result


def _extract_list_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        if data.get("error"):
            return []
        for key in ("videos", "items", "list", "results"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    ctx = _parse_list_context(base_url)
    host = ctx["host"]
    page_num = max(1, int(page) if page else 1)
    if page_num == 1 and ctx["page"] > 1:
        page_num = ctx["page"]
    safe_limit = min(max(1, int(limit) if limit else 60), 120)

    api_url = _build_list_api_url(
        host=host,
        sort=ctx["sort"],
        count=safe_limit,
        section=ctx["section"],
        object_id=ctx["object_id"],
        page=page_num,
        search=ctx["search"],
    )

    try:
        data = await fetch_json(api_url, referer=ctx["referer"])
    except Exception:
        return []

    rows = _extract_list_rows(data)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if len(items) >= safe_limit:
            break
        item = _list_item_from_video_row(row, host=host)
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)
    return items[:safe_limit]

