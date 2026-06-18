from fastapi import APIRouter, HTTPException, Query, Response, Request
from urllib.parse import quote
import logging

import httpx

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/proxy", summary="Thumbnail Proxy")
async def thumbnail_proxy(
    url: str = Query(..., description="Target Thumbnail URL"),
    referer: str = Query(None, description="Referer header to send"),
    user_agent: str = Query(None, description="User-Agent header to send"),
    request: Request = None
):
    """
    Proxy thumbnail images to bypass network or Referer restrictions.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = any(x in url_lower for x in ["ypncdn.com", "youporn.com"])
    is_pornhub = any(x in url_lower for x in ["phncdn.com", "pornhub.com"])
    is_redtube = any(x in url_lower for x in ["rdtcdn.com", "redtube.com"])
    is_tube8 = any(x in url_lower for x in ["t8cdn.com", "tube8.com"])
    is_hanime = any(x in url_lower for x in ["hanime.tv", "hb00.io", "hanime-cdn.com", "hb01.io", "hb02.io"])

    if not (is_hqporner or is_youporn or is_pornhub or is_redtube or is_tube8 or is_hanime):
        raise HTTPException(status_code=403, detail="Only allowed domains are supported")
        
    if (is_youporn or is_pornhub or is_redtube or is_tube8) and "/plain/" not in url_lower:
        raise HTTPException(status_code=403, detail="Only YouPorn/Pornhub/RedTube/Tube8 dynamic /plain/ previews are allowed via proxy")
    
    # Build request headers
    headers = {}
    ua = user_agent if user_agent else (request.headers.get("user-agent") if request else None)
    if ua:
        headers["User-Agent"] = ua
    else:
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
    if referer:
        headers["Referer"] = referer
    else:
        if is_hqporner:
            headers["Referer"] = "https://hqporner.com/"
        elif is_youporn:
            headers["Referer"] = "https://www.youporn.com/"
        elif is_pornhub:
            headers["Referer"] = "https://www.pornhub.com/"
        elif is_redtube:
            headers["Referer"] = "https://www.redtube.com/"
        elif is_tube8:
            headers["Referer"] = "https://www.tube8.com/"
        elif is_hanime:
            headers["Referer"] = "https://hanime.tv/"

    try:
        # Per-request client: avoids binding a pooled session to the wrong event loop
        # (see pool.py / a2wsgi); httpx matches other scrapers and resolves cleanly in the IDE.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    f"Thumbnail proxy upstream error {resp.status_code} for {url}"
                )
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Upstream returned {resp.status_code}",
                )
            content = resp.content
            content_type = resp.headers.get("content-type", "image/jpeg")

            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=86400",
                    "X-Proxy-Origin": "AppHub-Thumbnail-Proxy",
                },
            )

    except HTTPException:
        raise
    except httpx.TimeoutException as e:
        logger.warning(f"Thumbnail Proxy timeout for {url}: {e}")
        raise HTTPException(status_code=504, detail="Upstream timeout") from e
    except httpx.RequestError as e:
        logger.error(f"Thumbnail Proxy request error for {url}: {e}")
        raise HTTPException(status_code=502, detail="Upstream connection error") from e
    except Exception as e:
        logger.error(f"Thumbnail Proxy unexpected error for {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

def wrap_thumbnail_url(url: str, api_base_url: str) -> str:
    """Helper to wrap specific thumbnails in the proxy URL."""
    if not url:
        return url
        
    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = any(x in url_lower for x in ["ypncdn.com", "youporn.com"])
    is_pornhub = any(x in url_lower for x in ["phncdn.com", "pornhub.com"])
    is_redtube = any(x in url_lower for x in ["rdtcdn.com", "redtube.com"])
    is_tube8 = any(x in url_lower for x in ["t8cdn.com", "tube8.com"])
    is_hanime = any(x in url_lower for x in ["hanime.tv", "hb00.io", "hanime-cdn.com", "hb01.io", "hb02.io"])

    if not (is_hqporner or is_youporn or is_pornhub or is_redtube or is_tube8 or is_hanime):
        return url
        
    if is_youporn or is_pornhub or is_redtube or is_tube8:
        # Only proxy dynamic previews (which contain /plain/ and require IP-bound validto tokens)
        # Leave standard static .jpg thumbnails unproxied to save backend bandwidth
        if "/plain/" not in url_lower:
            return url

    # If already proxied, don't double wrap
    if "/thumbnails/proxy?url=" in url_lower:
        return url
        
    return f"{api_base_url.rstrip('/')}/api/v1/thumbnails/proxy?url={quote(url)}"
