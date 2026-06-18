from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from typing import Any, Optional, cast
from urllib.parse import parse_qs, quote, urlparse

BASE_SITE = "https://d2eabzntayzi4t.cloudfront.net/"
SITE_HOST = "d2eabzntayzi4t.cloudfront.net"
SITE_ALIASES = frozenset(
    {
        "d2eabzntayzi4t.cloudfront.net",
        "baonai.tv",
        "www.baonai.tv",
    }
)

HTTP_REQUEST_KEY = "fkf34lKD9344s6F8"
HTTP_RESPONSE_KEY = "vEukA&w15z4VAD3kAY#fkL#rBnU!WDhN"
APP_VERSION = "2.1.6"

_DEFAULT_IMAGE_CDN = "https://lksqimg.bgezuw.cn/"
_DEFAULT_VIDEO_CDN = "https://blksptt.bgezuw.cn/"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": BASE_SITE,
    "Origin": BASE_SITE.rstrip("/"),
}

_VIDEO_ID_RE = re.compile(
    r"(?:baonai\.tv|cloudfront\.net)(?:/[^?#]*)?(?:/video/|/play/|/detail/)(\d+)",
    re.IGNORECASE,
)
_VIDEO_ID_QUERY_RE = re.compile(r"[?&](?:id|videoId|video_id)=(\d+)", re.IGNORECASE)
_VIDEO_ID_PATH_RE = re.compile(r"/(\d{4,})(?:/|$|\?|#)")

_session_dev_id = secrets.token_hex(16)
_session_sid = str(uuid.uuid4())
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}
_cdn_cache: dict[str, str] = {}


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return h == SITE_HOST


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_site_host(host: str) -> str:
    h = (host or SITE_HOST).lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return h
    return SITE_HOST


def _x_user_agent() -> str:
    return (
        "DevType=Apple iPhone mobile;"
        "SysType=h5_pc;"
        f"Ver={APP_VERSION};"
        "BuildID=Chrome 124.0.0.0;"
        "device_brand=Apple;"
        "device_model=iPhone;"
        "system_name=Windows;"
        "system_version=10;"
        f"sid={_session_sid};"
    )


def _url_encode_bytes(value: str) -> list[int]:
    encoded = quote(value, safe="")
    out: list[int] = []
    idx = 0
    while idx < len(encoded):
        if encoded[idx] == "%":
            out.append(int(encoded[idx + 1 : idx + 3], 16))
            idx += 3
        else:
            out.append(ord(encoded[idx]))
            idx += 1
    return out


def _hex_to_bytes(hex_str: str) -> list[int]:
    return [int(hex_str[i : i + 2], 16) for i in range(0, len(hex_str), 2)]


def _js_splice(arr: list[int], start: int, delete_count: int) -> list[int]:
    removed = arr[start : start + delete_count]
    del arr[start : start + delete_count]
    return removed


def _tr(data: bytes | bytearray | list[int]) -> str:
    if isinstance(data, list):
        data = bytes(data)
    return base64.b64encode(data).decode("ascii")


def decrypt_response(data: str) -> str:
    """Decrypt encrypted API payloads (port of the site's dT() routine)."""
    payload = list(base64.b64decode(data))
    prefix = payload[:12]
    del payload[:12]
    mixed = _url_encode_bytes(HTTP_RESPONSE_KEY) + prefix
    split_at = len(mixed) // 2
    encoded = _tr(mixed)
    digest_a = _hex_to_bytes(hashlib.sha256(base64.b64decode(encoded)).hexdigest())
    segment = digest_a[8:24]
    block = segment + mixed[:split_at]
    del mixed[:split_at]
    encoded_b = _tr(block)
    arr_a = _hex_to_bytes(hashlib.sha256(base64.b64decode(encoded_b)).hexdigest())
    combined = mixed + segment
    encoded_c = _tr(combined)
    arr_h = _hex_to_bytes(hashlib.sha256(base64.b64decode(encoded_c)).hexdigest())
    a_work = list(arr_a)
    h_work = list(arr_h)
    key_bytes = _js_splice(a_work, 0, 8) + _js_splice(h_work, 8, 16) + _js_splice(a_work, 16, 24)
    iv_bytes = _js_splice(h_work, 0, 4) + _js_splice(a_work, 4, 8) + _js_splice(h_work, 8, 12)
    ciphertext = base64.b64decode(_tr(payload))
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(
        algorithms.AES(bytes(key_bytes)),
        modes.CBC(bytes(iv_bytes)),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid padding")
    return padded[:-pad_len].decode("utf-8")


def _nonce() -> str:
    chars = "0123456789abcdef"
    out = [chars[secrets.randbelow(16)] for _ in range(36)]
    out[14] = "4"
    out[19] = chars[(int(out[19], 16) & 3) | 8]
    for idx in (8, 13, 18, 23):
        out[idx] = "-"
    return "".join(out)


def _x_api_key(token: str, api_path: str, *, x_user_agent: Optional[str] = None) -> str:
    ts = int(time.time())
    nonce = _nonce()
    ua = _x_user_agent() if x_user_agent is None else x_user_agent
    msg = f"{token}&/api{api_path}&{ua}&{ts}&{nonce}"
    sign = hmac.new(HTTP_REQUEST_KEY.encode(), msg.encode(), hashlib.sha1).hexdigest()
    return f"timestamp={ts};sign={sign};nonce={nonce}"


def _auth_headers(token: str, api_path: str, *, x_user_agent: Optional[str] = None) -> dict[str, str]:
    headers = dict(_DEFAULT_HEADERS)
    headers["Authorization"] = token or ""
    headers["X-User-Agent"] = _x_user_agent() if x_user_agent is None else x_user_agent
    headers["x-api-key"] = _x_api_key(token, api_path, x_user_agent=x_user_agent)
    return headers


def _parse_api_response(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    outer = json.loads(text)
    if isinstance(outer, dict) and outer.get("hash") and isinstance(outer.get("data"), str):
        plain = decrypt_response(outer["data"])
        if plain == "null":
            outer["data"] = None
        else:
            try:
                outer["data"] = json.loads(plain)
            except json.JSONDecodeError:
                outer["data"] = plain
    return outer


async def _fetch_with_curl_cffi(
    method: str,
    api_path: str,
    *,
    token: str = "",
    json_body: Optional[dict[str, Any]] = None,
    x_user_agent: Optional[str] = None,
) -> Any:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    url = f"{BASE_SITE.rstrip('/')}{api_path}"
    headers = _auth_headers(token, api_path, x_user_agent=x_user_agent)
    if method.upper() == "GET":
        headers.pop("Content-Type", None)

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, timeout=45.0) as client:
                if method.upper() == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, json=json_body or {}, headers=headers)  # type: ignore[attr-defined]
                if resp.status_code != 200:
                    continue
                return _parse_api_response(resp.text)
        except Exception:
            continue
    return None


async def _guest_login(force: bool = False) -> str:
    global _session_dev_id
    now = time.time()
    cached = str(_token_cache.get("token") or "")
    if cached and not force and float(_token_cache.get("expires_at") or 0) > now + 60:
        return cached

    for attempt in range(5):
        if attempt:
            _session_dev_id = secrets.token_hex(16)
            await asyncio.sleep(0.4 * attempt)
        payload = await _fetch_with_curl_cffi(
            "POST",
            "/api/app/login/guest",
            json_body={"devID": _session_dev_id, "affCode": "{}", "token": ""},
        )
        if not isinstance(payload, dict) or payload.get("code") != 200:
            continue
        data = payload.get("data")
        token = ""
        if isinstance(data, dict):
            token = str(data.get("token") or "")
        if token:
            _token_cache["token"] = token
            _token_cache["expires_at"] = now + 3600
            return token

    return cached


async def _api_post(path: str, body: dict[str, Any], *, token: Optional[str] = None) -> dict[str, Any]:
    auth = token if token is not None else await _guest_login()
    result = await _fetch_with_curl_cffi("POST", path, token=auth, json_body=body)
    if not isinstance(result, dict):
        return {}
    if result.get("code") in (6004, 6015, 1000) and token is None:
        auth = await _guest_login(force=True)
        result = await _fetch_with_curl_cffi("POST", path, token=auth, json_body=body)
    return result if isinstance(result, dict) else {}


async def _api_get(path: str, *, token: Optional[str] = None) -> dict[str, Any]:
    auth = token if token is not None else await _guest_login()
    result = await _fetch_with_curl_cffi("GET", path, token=auth)
    if not isinstance(result, dict):
        return {}
    if result.get("code") in (6004, 6015, 1000) and token is None:
        auth = await _guest_login(force=True)
        result = await _fetch_with_curl_cffi("GET", path, token=auth)
    return result if isinstance(result, dict) else {}


def _cdn_url(kind: str, default: str) -> str:
    cached = _cdn_cache.get(kind)
    if cached:
        return cached
    return default


def _apply_domain_config(domain_entries: Any) -> None:
    if not isinstance(domain_entries, list):
        return
    for entry in domain_entries:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").upper()
        urls = entry.get("urls")
        if not isinstance(urls, list) or not urls:
            continue
        base = str(urls[0]).strip()
        if not base:
            continue
        if not base.endswith("/"):
            base = f"{base}/"
        if kind == "IMAGE":
            _cdn_cache["image"] = base
        elif kind == "VID":
            _cdn_cache["video"] = base


async def _ensure_cdn_config() -> None:
    if _cdn_cache.get("image") and _cdn_cache.get("video"):
        return
    config = await _api_get("/api/app/ping/config")
    data = config.get("data")
    if isinstance(data, dict):
        _apply_domain_config(data.get("domain"))


def _image_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    value = str(path).strip().replace("\\/", "/")
    if not value:
        return None
    if value.startswith("http"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    base = _cdn_url("image", _DEFAULT_IMAGE_CDN)
    return f"{base}{value.lstrip('/')}"


def _video_cdn_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    value = str(path).strip().replace("\\/", "/")
    if not value:
        return None
    if value.startswith("http"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    base = _cdn_url("video", _DEFAULT_VIDEO_CDN)
    return f"{base}{value.lstrip('/')}"


def _h5_play_url(video_id: str | int, token: str) -> str:
    api_path = f"/api/app/media/h5/m3u8/{video_id}"
    query = _x_api_key(token, api_path, x_user_agent="").replace(";", "&")
    return f"{BASE_SITE.rstrip('/')}{api_path}?token={token}&{query}"


def _canonical_video_url(video_id: str | int, *, host: str = SITE_HOST) -> str:
    return f"https://{host}/video/{video_id}"


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return raw
    for pattern in (_VIDEO_ID_RE, _VIDEO_ID_QUERY_RE):
        match = pattern.search(raw)
        if match:
            return match.group(1)
    parsed = urlparse(raw if raw.startswith("http") else f"https://{SITE_HOST}/{raw.lstrip('/')}")
    for pattern in (_VIDEO_ID_RE, _VIDEO_ID_QUERY_RE):
        match = pattern.search(parsed.geturl())
        if match:
            return match.group(1)
    path_match = _VIDEO_ID_PATH_RE.search(parsed.path or "")
    if path_match:
        return path_match.group(1)
    return None


def _parse_list_context(base_url: str) -> dict[str, Any]:
    raw = (base_url or "").strip() or BASE_SITE
    parsed = urlparse(raw if raw.startswith("http") else f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}")
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    query = parse_qs(parsed.query)

    page = 1
    for key in ("page", "pageNum"):
        if key in query and query[key]:
            try:
                page = max(1, int(str(query[key][0])))
            except ValueError:
                pass
            break

    category_id = 2
    for key in ("category", "categoryId", "id"):
        if key in query and query[key]:
            try:
                category_id = int(str(query[key][0]))
            except ValueError:
                pass
            break

    list_mode = "home"
    if "short" in query or "short" in (parsed.path or ""):
        list_mode = "short"

    return {
        "host": host,
        "page": page,
        "category_id": category_id,
        "list_mode": list_mode,
        "referer": raw if raw.startswith("http") else f"https://{host}/",
    }


def _duration_text(seconds: Any) -> Optional[str]:
    if seconds is None:
        return None
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return str(seconds).strip() or None
    minutes, secs = divmod(max(total, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _list_item_from_media(row: dict[str, Any], *, host: str) -> dict[str, Any]:
    video_id = str(row.get("id") or "").strip()
    raw_publisher = row.get("publisher")
    publisher: dict[str, Any] = raw_publisher if isinstance(raw_publisher, dict) else {}
    return {
        "url": _canonical_video_url(video_id, host=host) if video_id else BASE_SITE,
        "title": _first_non_empty(row.get("title")) or "Unknown Video",
        "thumbnail_url": _image_url(_first_non_empty(row.get("coverImg"), row.get("cover"))),
        "duration": _duration_text(row.get("playTime")),
        "views": _first_non_empty(row.get("watchTimes"), row.get("views")),
        "uploader_name": _first_non_empty(publisher.get("name"), row.get("publisherName")),
        "preview_url": None,
    }


def _streams_from_media(info: dict[str, Any], *, token: str) -> tuple[list[dict[str, Any]], Optional[str], Optional[str]]:
    video_id = str(info.get("id") or "").strip()
    streams: list[dict[str, Any]] = []
    hls_url: Optional[str] = None
    default_url: Optional[str] = None

    if video_id:
        hls_url = _h5_play_url(video_id, token)
        streams.append({"format": "hls", "url": hls_url, "quality": "auto"})

    direct = _video_cdn_url(_first_non_empty(info.get("videoUrl"), info.get("preFileName")))
    if direct:
        streams.append({"format": "hls", "url": direct, "quality": "source"})
        default_url = direct

    if hls_url and not default_url:
        default_url = hls_url

    return streams, hls_url, default_url


def _video_result_from_media(info: dict[str, Any], *, url: str, token: str) -> dict[str, Any]:
    raw_publisher = info.get("publisher")
    publisher: dict[str, Any] = raw_publisher if isinstance(raw_publisher, dict) else {}
    streams, hls_url, default_url = _streams_from_media(info, token=token)
    tags: list[str] = []
    topics = info.get("topics")
    if isinstance(topics, list):
        for topic in topics:
            if isinstance(topic, dict) and topic.get("name"):
                tags.append(str(topic["name"]).strip())

    return {
        "url": url,
        "title": _first_non_empty(info.get("title")) or "Unknown Video",
        "thumbnail_url": _image_url(_first_non_empty(info.get("coverImg"), info.get("cover"))),
        "duration": _duration_text(info.get("playTime")),
        "views": _first_non_empty(info.get("watchTimes"), info.get("views")),
        "uploader_name": _first_non_empty(publisher.get("name"), info.get("publisherName")),
        "tags": tags,
        "video": {
            "streams": streams,
            "hls": hls_url,
            "default": default_url,
            "has_video": bool(streams),
        },
    }


async def scrape(url: str) -> dict[str, Any]:
    await _ensure_cdn_config()
    parsed = urlparse(url if url.startswith("http") else f"{BASE_SITE.rstrip('/')}/{url.lstrip('/')}")
    host = _normalize_site_host(parsed.netloc or SITE_HOST)
    video_id = _extract_video_id(url)
    if not video_id:
        return {
            "url": url,
            "title": "",
            "thumbnail_url": None,
            "duration": None,
            "views": None,
            "uploader_name": None,
            "video": {"streams": [], "hls": None, "default": None, "has_video": False},
        }

    token = await _guest_login()
    response = await _api_post("/api/app/media/play", {"id": int(video_id)}, token=token)
    raw_data = response.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    raw_info = data.get("mediaInfo")
    info = raw_info if isinstance(raw_info, dict) else None
    if info is None:
        return {
            "url": _canonical_video_url(video_id, host=host),
            "title": "",
            "thumbnail_url": None,
            "duration": None,
            "views": None,
            "uploader_name": None,
            "video": {"streams": [], "hls": None, "default": None, "has_video": False},
        }

    canonical = _canonical_video_url(video_id, host=host)
    return _video_result_from_media(info, url=canonical, token=token)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    await _ensure_cdn_config()
    ctx = _parse_list_context(base_url)
    host = cast(str, ctx["host"])
    page_num = max(page, int(ctx["page"]))
    page_size = max(1, min(limit, 100))
    token = await _guest_login()

    if ctx["list_mode"] == "short":
        response = await _api_post(
            "/api/app/media/short/hot",
            {"pageNum": page_num, "pageSize": page_size},
            token=token,
        )
    else:
        response = await _api_post(
            "/api/app/media/home",
            {"id": int(ctx["category_id"]), "pageNum": page_num, "pageSize": page_size},
            token=token,
        )

    data = response.get("data")
    rows: list[dict[str, Any]] = []
    if isinstance(data, dict):
        media_list = data.get("mediaList")
        if isinstance(media_list, list):
            rows = [row for row in media_list if isinstance(row, dict)]

    return [_list_item_from_media(row, host=host) for row in rows[:page_size]]
