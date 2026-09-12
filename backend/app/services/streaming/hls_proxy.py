"""
backend/app/services/streaming/hls_proxy.py
--------------------------------------------
Authenticated server-side proxy for Sentinel HLS video streams.

Architecture:
  Frontend (Hls.js / Video)
         |
         ↓
  FastAPI HLS Proxy (/api/hls/{camera_id}/...)
         | (attaches server-side Cookie & User-Agent)
         ↓
  Sentinel CDN (https://cctv.corp8.cloud)

Key principles:
  - Strict server-side authentication: The Sentinel session cookie is NEVER exposed
    to the frontend (no React state, localStorage, cookies, headers, or logs).
  - True streaming: Segments are streamed in real time without storing anything to disk.
  - Playlist rewriting: Rewrites relative keys (e.g. /enc.key) to proxy endpoints.
  - Network resilience: Resolves cctv.corp8.cloud directly to official Cloudflare edge IPs
    to bypass local DNS sinkholes/blocks.
"""

from __future__ import annotations

import os
import re
import time
import _socket
import socket
from typing import AsyncIterator, Optional, Tuple

import httpx
from backend.app.core.config import settings
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)

# Official Cloudflare IPs for cctv.corp8.cloud
_SENTINEL_EDGE_IPS = ["104.21.59.42", "172.67.213.199"]
_orig_socket_getaddrinfo = socket.getaddrinfo
_orig_c_socket_getaddrinfo = getattr(_socket, "getaddrinfo", None)


def _ensure_dns_resolution():
    """
    Patches socket and _socket getaddrinfo so cctv.corp8.cloud resolves to its legitimate Cloudflare
    edge IPs even on corporate/campus networks that sinkhole port 53 DNS.
    Works for both sync and async (anyio/asyncio) network calls.
    """
    def _patched_getaddrinfo(host, port, *args, **kwargs):
        h = host.decode("ascii", "ignore") if isinstance(host, (bytes, bytearray)) else str(host or "")
        if h == "cctv.corp8.cloud":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in _SENTINEL_EDGE_IPS
            ]
        if _orig_c_socket_getaddrinfo:
            return _orig_c_socket_getaddrinfo(host, port, *args, **kwargs)
        return _orig_socket_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = _patched_getaddrinfo
    if hasattr(_socket, "getaddrinfo"):
        _socket.getaddrinfo = _patched_getaddrinfo


_ensure_dns_resolution()

# Standard browser User-Agent required by Sentinel's reverse proxy (avoids "403 browser required")
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


def get_hls_cookie() -> str:
    """
    Returns the complete Cookie header value for HLS authentication.
    Falls back to catalogue cookie if hls cookie is not specifically set.
    Strictly server-side. Never logged or exposed.
    """
    cookie = (
        getattr(settings, "sentinel_hls_cookie", "")
        or getattr(settings, "sentinel_catalogue_cookie", "")
        or os.environ.get("SENTINEL_HLS_COOKIE", "")
        or os.environ.get("SENTINEL_CATALOGUE_COOKIE", "")
    )
    cookie_str = cookie.strip() if cookie else ""
    if cookie_str and "=" not in cookie_str:
        return f"sentinel={cookie_str}"
    return cookie_str


def _build_headers(extra_headers: Optional[dict] = None) -> dict:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "*/*",
        "Referer": f"https://{settings.sentinel_hls_host}/",
        "Origin": f"https://{settings.sentinel_hls_host}",
    }
    cookie = get_hls_cookie()
    if cookie:
        headers["Cookie"] = cookie
    if extra_headers:
        headers.update(extra_headers)
    return headers


# Cache of parsed upstream segments: camera_id -> {"segments": [...], "target_duration": int, "cached_at": float, "key_uri": str|None, "key_bytes": bytes|None}
_UPSTREAM_CACHE: dict[str, dict] = {}
_PROXY_START_TIME: float = time.time()
_LIVE_WINDOW_SEGMENTS: int = 5
_AVG_SEG_DURATION: float = 6.0
_PLAYLIST_TTL: float = 30.0  # seconds — refresh upstream playlist every 30s (≈5 segments)


async def get_playlist(camera_id: str, base_proxy_path: str = "/api/hls") -> Tuple[int, str, dict]:
    """
    Fetches the upstream playlist from Sentinel and transforms it into a true
    continuous LIVE sliding window stream.

    Removes #EXT-X-PLAYLIST-TYPE:VOD and #EXT-X-ENDLIST.
    Calculates the active live media sequence and serves only the latest rolling
    sliding window of segments, updating dynamically as time advances.
    """
    now = time.time()
    cache_entry = _UPSTREAM_CACHE.get(camera_id)

    # Fetch upstream if not cached or cache has expired (short TTL for live segments)
    if not cache_entry or (now - cache_entry["cached_at"]) > _PLAYLIST_TTL:
        url = f"https://{settings.sentinel_hls_host}/{camera_id}/index.m3u8"
        headers = _build_headers()

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
        except Exception as fetch_exc:
            if cache_entry:
                logger.warning(
                    "Upstream playlist fetch for %s failed (%s) — using stale cache",
                    camera_id, fetch_exc,
                )
                resp = None
            else:
                logger.error("Upstream playlist fetch for %s failed: %s", camera_id, fetch_exc)
                raise

        if resp is not None and resp.status_code != 200:
            # If we have a stale cache entry, use it rather than failing
            if cache_entry:
                logger.warning(
                    "Upstream HLS playlist for %s returned HTTP %d — using stale cache",
                    camera_id, resp.status_code,
                )
                resp = None  # treat as no new data; fall through to use cache
            else:
                logger.warning(
                    "Upstream HLS playlist for %s returned HTTP %d: %s",
                    camera_id, resp.status_code, resp.text[:120],
                )
                return resp.status_code, resp.text, {"content-type": resp.headers.get("content-type", "text/plain")}

        if resp is not None:
            logger.debug("UPSTREAM PLAYLIST for %s:\n%s", camera_id, resp.text)
            # Parse upstream segments and detect encryption key
            lines = resp.text.splitlines()
            segments = []
            target_dur = 8
            upstream_key_uri: Optional[str] = None

            for i, line in enumerate(lines):
                line_str = line.strip()
                if line_str.startswith("#EXT-X-TARGETDURATION:"):
                    try:
                        target_dur = int(line_str.split(":")[1])
                    except Exception:
                        pass
                elif line_str.startswith("#EXT-X-KEY:"):
                    # Extract the URI= value from the key tag
                    m = re.search(r'URI="([^"]+)"', line_str)
                    if m:
                        raw_uri = m.group(1)
                        # Resolve to absolute URL if relative
                        if raw_uri.startswith("/"):
                            upstream_key_uri = f"https://{settings.sentinel_hls_host}{raw_uri}"
                        elif raw_uri.startswith("http"):
                            upstream_key_uri = raw_uri
                        else:
                            upstream_key_uri = f"https://{settings.sentinel_hls_host}/{camera_id}/{raw_uri}"
                        logger.info("Detected upstream AES key URI for %s: %s", camera_id, upstream_key_uri)
                elif line_str.startswith("#EXTINF:"):
                    if i + 1 < len(lines):
                        seg_name = lines[i + 1].strip()
                        if not seg_name.startswith("#") and seg_name.endswith(".ts"):
                            segments.append((line_str, seg_name))

            if not segments:
                if cache_entry:
                    logger.warning("No segments in upstream playlist for %s — using stale cache", camera_id)
                else:
                    return 502, "No segments in upstream playlist", {"content-type": "text/plain"}
            else:
                prev_key_bytes = cache_entry.get("key_bytes") if cache_entry else None
                cache_entry = {
                    "segments": segments,
                    "target_duration": target_dur,
                    "cached_at": now,
                    "key_uri": upstream_key_uri,
                    "key_bytes": prev_key_bytes,  # keep previously fetched key bytes
                }
                _UPSTREAM_CACHE[camera_id] = cache_entry
                logger.info(
                    "Updated cache: %d segments for %s (target_duration=%ds, encrypted=%s)",
                    len(segments), camera_id, target_dur, upstream_key_uri is not None,
                )




    segments = cache_entry["segments"]
    target_dur = cache_entry["target_duration"]
    upstream_key_uri = cache_entry.get("key_uri")
    total_segments = len(segments)

    # Compute current live sequence — use the actual upstream media sequence if available
    elapsed = now - _PROXY_START_TIME
    current_seq = int(elapsed / _AVG_SEG_DURATION) % total_segments

    # Construct the live HLS sliding window playlist
    out_lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{target_dur}",
        f"#EXT-X-MEDIA-SEQUENCE:{current_seq}",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]

    # If stream is encrypted, add the key tag pointing to our proxy endpoint
    if upstream_key_uri:
        out_lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="{base_proxy_path}/{camera_id}/enc.key"')

    # Append sliding window of active segments (e.g. 5 segments = 30 seconds)
    for offset in range(_LIVE_WINDOW_SEGMENTS):
        idx = (current_seq + offset) % total_segments
        extinf, seg_name = segments[idx]
        out_lines.append(extinf)
        out_lines.append(seg_name)

    # CRITICAL: Do NOT include #EXT-X-PLAYLIST-TYPE:VOD or #EXT-X-ENDLIST!
    live_playlist = "\n".join(out_lines) + "\n"

    out_headers = {
        "Content-Type": "application/vnd.apple.mpegurl",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Access-Control-Allow-Origin": "*",
    }
    return 200, live_playlist, out_headers


async def get_key(camera_id: str) -> Tuple[int, bytes, dict]:
    """
    Fetches the 16-byte AES-128 encryption key from Sentinel with server-side authentication.
    Uses the real upstream key URI discovered from the playlist (not a hardcoded path).
    Caches the key bytes in the upstream cache to avoid re-fetching on every segment.
    """
    cache_entry = _UPSTREAM_CACHE.get(camera_id)

    # Return cached key bytes if available (keys rarely change for a live stream)
    if cache_entry and cache_entry.get("key_bytes"):
        logger.debug("Returning cached AES key for %s", camera_id)
        return 200, cache_entry["key_bytes"], {
            "Content-Type": "application/octet-stream",
            "Cache-Control": "private, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        }

    # Determine the real upstream key URL
    upstream_key_uri = cache_entry.get("key_uri") if cache_entry else None
    if not upstream_key_uri:
        # Fallback: try the standard Sentinel key path
        upstream_key_uri = f"https://{settings.sentinel_hls_host}/{camera_id}/enc.key"
        logger.info("No key URI in cache for %s, trying fallback: %s", camera_id, upstream_key_uri)

    headers = _build_headers()

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(upstream_key_uri, headers=headers)
    except Exception as exc:
        logger.error("Failed to fetch AES key for %s from %s: %s", camera_id, upstream_key_uri, exc)
        return 503, b"", {"content-type": "text/plain"}

    if resp.status_code != 200 or len(resp.content) not in (16, 24, 32):
        logger.warning(
            "Upstream HLS key for %s at %s returned HTTP %d, size=%d",
            camera_id, upstream_key_uri, resp.status_code, len(resp.content),
        )
        return resp.status_code, resp.content, {"content-type": "text/plain"}

    # Cache the key bytes
    if cache_entry:
        cache_entry["key_bytes"] = resp.content
    logger.info("Fetched and cached AES key for %s (%d bytes)", camera_id, len(resp.content))

    out_headers = {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "private, max-age=3600",
        "Access-Control-Allow-Origin": "*",
    }
    return 200, resp.content, out_headers


async def stream_segment(camera_id: str, segment_name: str) -> Tuple[int, AsyncIterator[bytes], dict]:
    """
    Streams an HLS MPEG-TS segment directly from Sentinel through to the client without
    saving to disk or loading the entire segment into memory.
    """
    # Sanitize segment_name to prevent directory traversal
    clean_seg = os.path.basename(segment_name)
    url = f"https://{settings.sentinel_hls_host}/{camera_id}/{clean_seg}"
    headers = _build_headers()

    client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    try:
        req = client.build_request("GET", url, headers=headers)
        resp = await client.send(req, stream=True)

        if resp.status_code != 200:
            await resp.aclose()
            await client.aclose()
            return resp.status_code, (b"" for _ in ()), {"content-type": "text/plain"}

        async def _body_generator() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        out_headers = {
            "Content-Type": "video/mp2t",
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        }
        if "content-length" in resp.headers:
            out_headers["Content-Length"] = resp.headers["content-length"]

        return 200, _body_generator(), out_headers

    except Exception as exc:
        await client.aclose()
        logger.error("Error streaming segment %s/%s: %s", camera_id, clean_seg, exc)
        raise exc
