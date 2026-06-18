import httpx
from fastapi import APIRouter, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse
from urllib.parse import urljoin, quote
import logging
import re
import json

router = APIRouter()
logger = logging.getLogger(__name__)

# Pattern to find URLs in m3u8 files
URL_PATTERN = re.compile(r'(https?://[^\s]+)')

@router.get("/proxy", summary="HLS Proxy")
async def hls_proxy(
    url: str = Query(..., description="Target HLS URL"),
    referer: str = Query(None, description="Referer header to send"),
    origin: str = Query(None, description="Origin header to send"),
    user_agent: str = Query(None, description="User-Agent header to send"),
    request: Request = None
):
    """
    Proxy HLS manifests and segments to bypass CORS/Referer restrictions.
    Rewrites URLs in m3u8 files to point back to this proxy.
    Streams video chunks efficiently without memory buffering.
    Handles BrazzPW-style meta-refreshes and masked MIME types.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    headers = {}
    ua = user_agent if user_agent else request.headers.get("user-agent", "Mozilla/5.0")
    if ua:
        headers["User-Agent"] = ua
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    if not referer and "bgezuw.cn" in url.lower():
        from app.scrapers.baonai.media_crypto import BAONAI_REFERER

        headers["Referer"] = BAONAI_REFERER
        headers["Origin"] = BAONAI_REFERER.rstrip("/")

    is_baonai_h5 = "/api/app/media/h5/m3u8/" in url
    if is_baonai_h5:
        from urllib.parse import parse_qs, urlparse

        from app.scrapers.baonai.media_crypto import BAONAI_REFERER

        parsed_h5 = urlparse(url)
        token = parse_qs(parsed_h5.query).get("token", [""])[0]
        if token:
            headers["Authorization"] = token
        if not headers.get("Referer"):
            headers["Referer"] = BAONAI_REFERER
            headers["Origin"] = BAONAI_REFERER.rstrip("/")
    
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header
        
    try:
        from starlette.background import BackgroundTask
        
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15.0)
        req = client.build_request("GET", url)
        resp = await client.send(req, stream=True)
        
        # 1. Handle Meta-Refresh or session initialization (common in BrazzPW manifests)
        content_type = resp.headers.get("content-type", "").lower()
        is_html = "text/html" in content_type
        
        url_lower = url.lower()
        is_manifest = "mpegurl" in content_type or url_lower.endswith(".m3u8") or ".m3u8" in url_lower

        if (resp.status_code == 403 or is_html) and is_manifest:
            await resp.aread() # We must read the body to check for meta-refresh
            if "#EXTM3U" not in resp.text:
                m = re.search(r'url=([^"\']*)', resp.text, re.I)
                if m:
                    refresh_url = urljoin(url, m.group(1))
                    logger.info(f"Following meta-refresh to: {refresh_url}")
                    await client.get(refresh_url) # Hit to get cookies
                    resp = await client.send(req, stream=True) # Retry original stream
                else:
                    logger.info("Retrying request to handle potential session initialization...")
                    resp = await client.send(req, stream=True)
            
            # Re-evaluate content type after refresh
            content_type = resp.headers.get("content-type", "").lower()
            is_manifest = "mpegurl" in content_type or url_lower.endswith(".m3u8") or ".m3u8" in url_lower

        if resp.status_code >= 400:
            await resp.aread()
            await client.aclose()
            raise HTTPException(status_code=resp.status_code, detail=f"Upstream error: {resp.status_code}")

        is_baonai_cdn = "bgezuw.cn" in url.lower()
        effective_referer = referer
        if not effective_referer and (is_baonai_cdn or is_baonai_h5):
            from app.scrapers.baonai.media_crypto import BAONAI_REFERER

            effective_referer = BAONAI_REFERER

        # Baonai h5 play API: encrypted JSON or XOR m3u8 payload
        if is_baonai_h5:
            raw_bytes = await resp.aread()
            await client.aclose()
            text = raw_bytes.decode("utf-8", errors="replace").strip()
            plain = text
            if text.startswith("{") and '"hash":true' in text:
                from app.scrapers.baonai.scraper import decrypt_response

                try:
                    outer = json.loads(text)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=502, detail="Invalid h5 m3u8 response")
                if isinstance(outer.get("data"), str) and outer.get("hash"):
                    plain = decrypt_response(outer["data"])
                elif isinstance(outer, dict):
                    plain = json.dumps(outer)
            if plain.lstrip().startswith("#EXTM3U"):
                content = plain
            else:
                try:
                    payload = json.loads(plain)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=502, detail="h5 m3u8 did not return a playlist")
                if isinstance(payload, dict):
                    for key in ("url", "videoUrl", "m3u8", "playUrl"):
                        value = payload.get(key)
                        if isinstance(value, str) and value.strip():
                            redirect = value.strip()
                            if not redirect.startswith("http"):
                                from app.scrapers.baonai.scraper import _video_cdn_url

                                redirect = _video_cdn_url(redirect) or redirect
                            params = f"?url={quote(redirect)}"
                            if effective_referer:
                                params += f"&referer={quote(effective_referer)}"
                            proxy_base = f"{str(request.base_url).rstrip('/')}/api/v1/hls/proxy"
                            return Response(
                                content=f"#EXTM3U\n{proxy_base}{params}\n",
                                media_type="application/vnd.apple.mpegurl",
                                headers={"Access-Control-Allow-Origin": "*"},
                            )
                raise HTTPException(status_code=502, detail="h5 m3u8 upstream error")

            base_url = str(request.base_url).rstrip("/")
            proxy_base = f"{base_url}/api/v1/hls/proxy"
            lines = content.split("\n")
            new_lines = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    new_lines.append(line)
                else:
                    target = urljoin(url, line)
                    params = f"?url={quote(target)}"
                    if effective_referer:
                        params += f"&referer={quote(effective_referer)}"
                    new_lines.append(f"{proxy_base}{params}")
            return Response(
                content="\n".join(new_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        # 2. Manifest Rewriting
        if is_manifest:
            raw_bytes = await resp.aread()
            if is_baonai_cdn and raw_bytes and not raw_bytes.lstrip().startswith(b"#EXTM3U"):
                from app.scrapers.baonai.media_crypto import decrypt_xor_payload

                raw_bytes = decrypt_xor_payload(raw_bytes)
            content = raw_bytes.decode("utf-8", errors="replace")
            await client.aclose() # Close immediately as we are done
            
            base_url = str(request.base_url).rstrip("/")
            proxy_base = f"{base_url}/api/v1/hls/proxy"
            
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    if line.startswith("#EXT-X-KEY") and 'URI="' in line:
                        # Find URI attribute and rewrite it
                        match = re.search(r'URI="([^"]+)"', line)
                        if match:
                            target = urljoin(url, match.group(1))
                            params = f"?url={quote(target)}"
                            if effective_referer: params += f"&referer={quote(effective_referer)}"
                            if origin: params += f"&origin={quote(origin)}"
                            if user_agent: params += f"&user_agent={quote(user_agent)}"
                            proxy_url = f"{proxy_base}{params}"
                            line = line.replace(f'URI="{match.group(1)}"', f'URI="{proxy_url}"')
                    new_lines.append(line)
                else:
                    # It's a URI line
                    target = urljoin(url, line)
                    params = f"?url={quote(target)}"
                    if effective_referer: params += f"&referer={quote(effective_referer)}"
                    if origin: params += f"&origin={quote(origin)}"
                    if user_agent: params += f"&user_agent={quote(user_agent)}"
                    new_lines.append(f"{proxy_base}{params}")
            
            return Response(
                content="\n".join(new_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        
        # 3. Stream Segment without buffering
        else:
            if is_baonai_cdn:
                raw_bytes = await resp.aread()
                await client.aclose()
                from app.scrapers.baonai.media_crypto import decrypt_xor_payload

                raw_bytes = decrypt_xor_payload(raw_bytes)
                response_headers = {"Access-Control-Allow-Origin": "*"}
                final_media_type = content_type
                if "brazzpw.com" in url and "image/" in content_type:
                    final_media_type = "video/mp2t"
                if raw_bytes[:3] == b"#EXT" or ".m3u8" in url_lower:
                    final_media_type = "application/vnd.apple.mpegurl"
                return Response(
                    content=raw_bytes,
                    status_code=resp.status_code,
                    media_type=final_media_type,
                    headers=response_headers,
                )

            response_headers = {"Access-Control-Allow-Origin": "*"}
            for h in ["Content-Range", "Content-Length", "Accept-Ranges"]:
                if h.lower() in resp.headers:
                    response_headers[h] = resp.headers[h.lower()]
            
            final_media_type = content_type
            if "brazzpw.com" in url and "image/" in content_type:
                final_media_type = "video/mp2t"

            return StreamingResponse(
                resp.aiter_bytes(),
                status_code=resp.status_code,
                media_type=final_media_type,
                headers=response_headers,
                background=BackgroundTask(client.aclose)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"HLS Proxy error: {e}")
        try:
            await client.aclose()
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))

