from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_SITE = "https://spankbang.party/"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "Cookie": "age_verified=1; sb_theme=dark; cookies_accepted=1",
}
# SpankBang blocks Chrome TLS fingerprints; Safari impersonation succeeds.
_CURL_IMPERSONATIONS = ("safari17_0", "safari15_5", "chrome120", "chrome110")


def can_handle(host: str) -> bool:
    h = host.lower()
    return "spankbang.party" in h or "spankbang.com" in h


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _is_cloudflare_challenge(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    low = html.lower()
    if "just a moment" in low and "cloudflare" in low:
        return True
    if "cf_chl_opt" in low:
        return True
    if "checking your browser" in low:
        return True
    if "enable javascript and cookies" in low:
        return True
    return False


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str:
    from curl_cffi.requests import Session

    headers = dict(_DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    last_error: str | None = None

    def _do_request() -> str:
        nonlocal last_error
        for imp in _CURL_IMPERSONATIONS:
            try:
                with Session(impersonate=imp, headers=headers) as session:
                    resp = session.get(url, timeout=45.0)
                if resp.status_code != 200:
                    last_error = f"{imp}: HTTP {resp.status_code}"
                    continue
                text = resp.text
                if _is_cloudflare_challenge(text):
                    last_error = f"{imp}: challenge page"
                    continue
                return text
            except Exception as exc:
                last_error = f"{imp}: {exc}"
                continue
        raise ValueError(f"Failed to fetch {url} ({last_error or 'unknown error'})")

    return await asyncio.to_thread(_do_request)


async def fetch_html(url: str) -> str:
    return await _fetch_with_curl_cffi(url, referer=BASE_SITE)


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    seen_urls = set()
    
    # 1. Parse <source> tags from video element
    soup = BeautifulSoup(html, "lxml")
    sources = soup.select("video source, source") 
    for source in sources:
        src = source.get("src") or source.get("data-src")
        if src and (src.startswith("http") or src.startswith("//")):
            if src.startswith("//"): src = "https:" + src
            
            # Skip invalid URLs
            if "/t/" in src and "td.mp4" in src: continue
            if "tbv.sb-cd.com" in src: continue
            
            # Extract quality
            quality = source.get("size") or source.get("label") or source.get("data-res")
            if not quality:
                m = re.search(r'[-_](\d+p)\.mp4', src)
                quality = m.group(1).replace('p', '') if m else "unknown"
            
            fmt = "hls" if ".m3u8" in src else "mp4"
            
            if src not in seen_urls:
                streams.append({"quality": str(quality), "url": src, "format": fmt})
                seen_urls.add(src)

    # 2. ALWAYS Check for stream_data object (contains more qualities + 4k)
    m_data = re.search(r'var\s+stream_data\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m_data:
        try:
            # Try json.loads first, then ast.literal_eval for single quotes
            raw_data = m_data.group(1)
            try:
                data = json.loads(raw_data)
            except Exception:
                data = ast.literal_eval(raw_data)

            # print("DEBUG: stream_data keys:", list(data.keys()))
            for q, urls in data.items():
                # print(f"DEBUG: Processing key {q} with value {urls}")
                if not urls: continue
                
                # Filter out metadata keys
                if q in ['cover_image', 'thumbnail', 'stream_raw_id', 'stream_sheet', 'length', 'main']:
                    continue
                    
                # Clean key names (e.g. m3u8_1080p -> 1080p)
                clean_q = q.replace("m3u8_", "")
                if clean_q.endswith("p") and clean_q[:-1].isdigit():
                    clean_q = clean_q[:-1]
                
                url = None
                if isinstance(urls, list) and len(urls) > 0:
                    url = urls[0]
                elif isinstance(urls, str):
                    url = urls

                if url:
                    url = url.replace("\\/", "/")

                if url and url not in seen_urls:
                    fmt = "hls" if ".m3u8" in url else "mp4"
                    streams.append({
                        "quality": clean_q,
                        "url": url,
                        "format": fmt
                    })
                    seen_urls.add(url)

        except Exception as e:
            pass

    # 3. Fallback: Check for simple stream_url variable
    if not streams:
        m = re.search(r'stream_url\s*=\s*["\'](https?://.*?)["\']', html)
        if m:
            video_url = m.group(1)
            streams.append({
                "quality": "default",
                "url": video_url,
                "format": "mp4"
            })

    # Sort streams: High quality first (4k > 1080 > 720 > 480 > 240)
    def quality_rank(s):
        q = s['quality']
        if 'k' in q.lower(): return 10000 
        if q.isdigit(): return int(q)
        return 0
    
    streams.sort(key=quality_rank, reverse=True)

    # Determine default: Prioritize "m3u8" quality (master playlist), then any HLS, then highest MP4
    default_url = None
    if streams:
        # 1. Try to find the master HLS playlist (quality="m3u8")
        master_hls = next((s for s in streams if s.get("quality") == "m3u8"), None)
        if master_hls:
            default_url = master_hls["url"]
        else:
            # 2. Try to find ANY HLS stream
            hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
            if hls_stream:
                default_url = hls_stream["url"]
            else:
                # 3. Fallback to first (highest quality) stream
                default_url = streams[0]["url"]

    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    
    title = None
    t_tag = soup.select_one("h1")
    if t_tag: title = t_tag.get_text(strip=True)
    
    thumbnail = None
    # og:image
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")
    
    duration = "0:00"
    # Try to find duration in meta
    # <meta itemprop="duration" content="PT6M33S" /> is standard but SpankBang varies
    # Or parsing from sidebar
    
    uploader = "SpankBang"
    u_el = soup.select_one(".user a, .user-name")
    if u_el: uploader = u_el.get_text(strip=True)
    
    tags = []
    # SpankBang stores tags in meta keywords
    meta_keywords = soup.find("meta", attrs={"name": "keywords"})
    if meta_keywords and meta_keywords.get("content"):
        keywords = meta_keywords.get("content")
        # Split by comma and clean up
        tags = [t.strip() for t in keywords.split(",") if t.strip()]
    
    # Fallback: try HTML tags
    if not tags:
        for t in soup.select(".categories a, .tags a"):
            txt = t.get_text(strip=True)
            if txt and txt.lower() not in ["tags", "categories"]:
                tags.append(txt)
            
    video_data = _extract_video_streams(html)
    
    return {
        "url": url,
        "title": title or "Unknown",
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": "0", 
        "uploader_name": uploader or "SpankBang",
        "category": "SpankBang",
        "tags": tags,
        "video": video_data,
        "related_videos": [], 
        "preview_url": None
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # Pagination: spankbang.party/upcoming/2
    
    url = base_url
    
    # Spankbang standard: /2 for page 2
    if page > 1:
        url = base_url.rstrip("/")
        if url in ("https://spankbang.party", "https://spankbang.com"):
             url = f"{BASE_SITE.rstrip('/')}/trending_videos"
        elif "/s/" in url:
             # Ensure /s/ URLs keep structure: /s/query/page
             # If url was .../s/amateur, make it .../s/amateur/2
             pass 
        
        # Append page number
        url = f"{url}/{page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []
        
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # Updated Selectors based on browser analysis
    # Strategy: Find all potential video items, then group by parent container.
    # The container with the most items is the Main List.
    container_selector = ".js-video-item, .video-item, .video-list-video, [data-testid='video-item']"
    
    # Target only the main content area to avoid featured items in the header
    main_content = soup.select_one('main[data-testid="main"]')
    if main_content:
        selected_items = main_content.select(container_selector)
    else:
        selected_items = soup.select(container_selector)
    
    for item in selected_items:
        try:
            # Get the main link (usually a.thumb for thumbnail or the first big anchor)
            link = item.select_one('a[href*="/video/"], a')
            if not link: continue
            
            href = link.get("href")
            if not href: continue
             
            if href.startswith("/"): href = BASE_SITE.rstrip("/") + href
            
            # Title: Improved selector to avoid matching hashtags/channels
            title = "Unknown"
            # Title is typically in a p tag with specific classes
            title_tag = item.select_one('p a[href*="/video/"] span, p a[href*="/video/"], .n')
            if title_tag:
                title = title_tag.get_text(strip=True)
            
            # Clean Title: Remove common redundant prefixes
            title = re.sub(r'^(\(None\)|（無）|\(無\))\s*', '', title, flags=re.IGNORECASE).strip()

            # Thumbnail
            img = item.find("img")
            thumb = None
            if img:
                thumb = img.get("data-src") or img.get("src")
                if thumb:
                    if thumb.startswith("//"): thumb = "https:" + thumb
                    # Upgrade resolution: w:300 -> w:1200
                    thumb = thumb.replace("w:300", "w:1200")
                
            # Duration: in data-testid="video-item-length"
            duration = "0:00"
            dur_tag = item.select_one('[data-testid="video-item-length"]')
            if dur_tag: 
                duration = dur_tag.get_text(strip=True)
            
            # Views: Use data-testid="views"
            views = "0"
            views_tag = item.select_one('[data-testid="views"]')
            if views_tag:
                # The view count is usually in the last span or the one with text
                # We can just get all text and strip whitespace, BS4 will handle nested spans
                views = views_tag.get_text(strip=True)
            
            # Uploader: Use data-testid="title" for the user/pornstar link carefully
            # Note: Spankbang uses data-testid="title" for tags/channels too.
            # Usually the user/channel link does NOT have /video/ in href.
            uploader = "Unknown"
            # Try to find a link that is NOT a video link but has uploader classes
            uploader_tag = item.select_one('a[href^="/s/"]:not([href*="video"]), a[href^="/profile/"], span.text-action-tertiary')
            if uploader_tag:
                uploader = uploader_tag.get_text(strip=True)
            
            items.append({
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader
            })
            
        except Exception:
            continue
            
    return items
