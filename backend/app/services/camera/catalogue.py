"""
sentinel-gujarat/backend/app/services/camera/catalogue.py
----------------------------------------------------------
Camera catalogue service.

OFFICIAL SENTINEL INFRASTRUCTURE (Integrator's Guide):
  Catalogue  : https://cctv.corp8.cloud/cameras.json
  RTSP       : rtsp://103.250.160.189:8554/stream/<camera_id>   [public, no auth]
  WebRTC/WHEP: http://103.250.160.189:8889/stream/<camera_id>/whep [public, no auth]
  HLS        : https://cctv.corp8.cloud/<camera_id>/index.m3u8 [password-protected]

IMPORTANT RULES:
  1. cameras.json is the source of truth for camera discovery.
  2. cameras.json is behind the browser password (same as the HLS grid).
  3. If cameras.json returns a login page (HTML), we detect this, log clearly,
     and fall back to a provisional cam01-cam30 range so that RTSP testing
     can proceed without the web password.
  4. RTSP and WebRTC are NOT behind the web password — they work directly.
  5. Never hard-code the final camera list. Use SENTINEL_CATALOGUE_COOKIE
     in .env to pass the complete Cookie header value to authenticate cameras.json.
     (No Authorization header or token is used).
  6. Do NOT use this module to scrape or automate the browser login.
"""

from __future__ import annotations

import os
import httpx
from typing import List, Optional

from backend.app.core.config import settings
from backend.app.core.logging_config import get_logger
from backend.app.models.camera import CameraEntry, camera_from_catalogue_dict

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Catalogue Authentication:
# Sentinel cameras.json uses HTTP Cookie request header for authentication.
# There is NO Authorization header and NO Bearer token.
# SENTINEL_CATALOGUE_COOKIE provides the complete Cookie header value.
# Kept strictly server-side — NEVER logged, committed, or exposed to frontend.
# ---------------------------------------------------------------------------

def _get_catalogue_cookie() -> str:
    """
    Returns the complete Cookie header value for authenticating catalogue requests.
    Strictly server-side. Never logged or exposed to clients.
    """
    cookie = getattr(settings, "sentinel_catalogue_cookie", "") or os.environ.get("SENTINEL_CATALOGUE_COOKIE", "")
    cookie_str = cookie.strip() if cookie else ""
    if cookie_str and "=" not in cookie_str:
        return f"sentinel={cookie_str}"
    return cookie_str


# ---------------------------------------------------------------------------
# Sync version — used by scripts and CLI tools
# ---------------------------------------------------------------------------

def fetch_catalogue(
    timeout_sec: float = 15.0,
    url: Optional[str] = None,
) -> List[CameraEntry]:
    """
    Fetches the Sentinel camera catalogue synchronously from cameras.json.

    If the endpoint returns a login-wall HTML page (not JSON), this function:
      - Logs the situation clearly
      - Returns a provisional list of cam01..cam30 entries with correct RTSP URLs
        so that RTSP testing can proceed without the web password.

    Args:
        timeout_sec: HTTP timeout in seconds.
        url: Override catalogue URL (useful for testing).

    Returns:
        List of CameraEntry objects. Never raises — returns empty list on error.
    """
    catalogue_url = url or settings.sentinel_catalogue_url
    logger.info("[INFO] Loading Sentinel camera catalogue...")
    logger.info("[INFO] Catalogue URL: %s", catalogue_url)

    headers = {}
    cookie = _get_catalogue_cookie()
    if cookie:
        headers["Cookie"] = cookie
        logger.debug("[INFO] Authenticating catalogue request with Cookie header.")

    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            response = client.get(catalogue_url, headers=headers)
    except httpx.ConnectError as exc:
        logger.warning(
            "Cannot connect to catalogue at %s: %s. Falling back to provisional cam01-cam30 RTSP list.",
            catalogue_url, exc,
        )
        return _provisional_camera_list()
    except httpx.TimeoutException:
        logger.warning(
            "Timeout fetching catalogue from %s after %.1fs. Falling back to provisional cam01-cam30 RTSP list.",
            catalogue_url, timeout_sec,
        )
        return _provisional_camera_list()
    except Exception as exc:
        logger.warning("Unexpected error fetching catalogue: %s. Falling back to provisional camera list.", exc)
        return _provisional_camera_list()

    # Check Content-Type — if it's HTML we hit the login wall
    content_type = response.headers.get("content-type", "")
    body_preview = response.text[:200].strip()

    if response.status_code in (401, 403):
        logger.warning(
            "cameras.json returned HTTP %d (authentication required). "
            "The catalogue is behind the browser password. "
            "Falling back to provisional cam01-cam30 RTSP list.",
            response.status_code,
        )
        return _provisional_camera_list()

    if "text/html" in content_type or body_preview.lower().startswith("<!doctype") or body_preview.lower().startswith("<html"):
        logger.warning(
            "cameras.json returned an HTML login page (HTTP %d). "
            "The catalogue CDN requires the browser access password. "
            "RTSP and WebRTC do NOT require this password — they connect directly.\n"
            "To authenticate the catalogue fetch, set SENTINEL_CATALOGUE_COOKIE "
            "in your .env (see .env.example). "
            "Falling back to provisional cam01-cam30 RTSP list.",
            response.status_code,
        )
        return _provisional_camera_list()

    if response.status_code >= 400:
        logger.warning(
            "HTTP %d from catalogue endpoint %s. Response: %s. Falling back to provisional camera list.",
            response.status_code, catalogue_url, body_preview,
        )
        return _provisional_camera_list()

    # Try to parse as JSON
    try:
        data = response.json()
    except Exception as exc:
        logger.warning(
            "cameras.json did not return valid JSON (HTTP %d). Falling back to provisional camera list. Error: %s",
            response.status_code, exc,
        )
        return _provisional_camera_list()

    cameras = _parse_catalogue_response(data)
    logger.info("[INFO] Cameras discovered: %d", len(cameras))
    return cameras


# ---------------------------------------------------------------------------
# Async version — for FastAPI endpoints in later milestones
# ---------------------------------------------------------------------------

async def fetch_catalogue_async(
    timeout_sec: float = 15.0,
    url: Optional[str] = None,
) -> List[CameraEntry]:
    """Async version of fetch_catalogue. Same behaviour."""
    catalogue_url = url or settings.sentinel_catalogue_url
    logger.info("[INFO] Loading Sentinel camera catalogue (async)...")
    logger.info("[INFO] Catalogue URL: %s", catalogue_url)

    headers = {}
    cookie = _get_catalogue_cookie()
    if cookie:
        headers["Cookie"] = cookie
        logger.debug("[INFO] Authenticating catalogue request with Cookie header (async).")

    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            response = await client.get(catalogue_url, headers=headers)
    except httpx.ConnectError as exc:
        logger.warning("Cannot connect to catalogue at %s: %s. Falling back to provisional cameras.", catalogue_url, exc)
        return _provisional_camera_list()
    except httpx.TimeoutException:
        logger.warning("Timeout fetching catalogue from %s. Falling back to provisional cameras.", catalogue_url)
        return _provisional_camera_list()
    except Exception as exc:
        logger.warning("Unexpected error fetching catalogue: %s. Falling back to provisional cameras.", exc)
        return _provisional_camera_list()

    content_type = response.headers.get("content-type", "")
    body_preview = response.text[:200].strip()

    if response.status_code in (401, 403) or "text/html" in content_type or body_preview.lower().startswith("<!doctype"):
        logger.warning(
            "cameras.json returned a login page or auth error (HTTP %d). "
            "Falling back to provisional cam01-cam30 RTSP list.",
            response.status_code,
        )
        return _provisional_camera_list()

    if response.status_code >= 400:
        logger.warning("HTTP %d from catalogue endpoint %s. Falling back to provisional cameras.", response.status_code, catalogue_url)
        return _provisional_camera_list()

    try:
        data = response.json()
    except Exception as exc:
        logger.warning("cameras.json did not return valid JSON: %s. Falling back to provisional cameras.", exc)
        return _provisional_camera_list()

    cameras = _parse_catalogue_response(data)
    logger.info("[INFO] Cameras discovered: %d", len(cameras))
    return cameras


# ---------------------------------------------------------------------------
# Provisional camera list — used when cameras.json is inaccessible
# ---------------------------------------------------------------------------

def _provisional_camera_list(
    first: int = 1,
    last: int = 30,
) -> List[CameraEntry]:
    """
    Returns a provisional list of CameraEntry objects for cam01..cam30.

    Per the official Integrator's Guide, the current expected camera IDs are
    cam01 through cam30, but this can change. This list is a fallback only.

    All entries are constructed with:
      - Correct RTSP URL: rtsp://103.250.160.189:8554/stream/<camera_id>
      - Correct HLS URL : https://cctv.corp8.cloud/<camera_id>/index.m3u8
      - Correct WebRTC  : http://103.250.160.189:8889/stream/<camera_id>/whep
      - live_status = None (unknown — cannot verify without catalogue)
    """
    cameras = []
    for n in range(first, last + 1):
        cam_id = f"cam{n:02d}"
        entry = CameraEntry(
            camera_id=cam_id,
            location=f"Camera {cam_id} (provisional — catalogue unavailable)",
            codec=None,          # unknown until stream connects
            resolution=None,     # unknown until stream connects
            live_status=None,    # unknown
            rtsp_url=settings.rtsp_url_for(cam_id),
            webrtc_url=settings.webrtc_url_for(cam_id),
            hls_url=settings.hls_url_for(cam_id),
        )
        cameras.append(entry)

    logger.info(
        "Provisional camera list: %d cameras (cam%02d..cam%02d). "
        "RTSP URLs are direct and do NOT require the web password. "
        "Provide SENTINEL_CATALOGUE_COOKIE in .env to fetch the real catalogue.",
        len(cameras), first, last,
    )
    return cameras


# ---------------------------------------------------------------------------
# JSON parser — handles all common Sentinel catalogue response formats
# ---------------------------------------------------------------------------

def _parse_catalogue_response(data) -> List[CameraEntry]:
    """
    Parses the raw JSON from cameras.json.

    The Sentinel catalogue may return:
      - A JSON list:   [ {...}, {...}, ... ]
      - A JSON object: { "cameras": [ {...}, ... ] }
      - A JSON object: { "data": [ {...}, ... ] }
      - A JSON object: { "feeds": [ {...}, ... ] }

    All formats are handled. If the format is unrecognised, the raw structure
    is logged so you can tell us the actual structure to update the parser.
    """
    cameras_raw: list = []

    if isinstance(data, list):
        cameras_raw = data

    elif isinstance(data, dict):
        # Try common wrapper keys
        for key in ("cameras", "data", "feeds", "streams", "results", "items"):
            if key in data and isinstance(data[key], list):
                cameras_raw = data[key]
                logger.debug("Catalogue wrapper key: '%s'", key)
                break

        if not cameras_raw:
            # Maybe the dict IS a single camera
            if "camera_id" in data or "id" in data or "cameraId" in data:
                cameras_raw = [data]
            else:
                logger.error(
                    "Unrecognised cameras.json structure. "
                    "Expected a list or dict with 'cameras'/'data'/'feeds' key. "
                    "Actual top-level keys: %s. "
                    "Please report this so we can update the parser.",
                    list(data.keys())[:20],
                )
                return []
    else:
        logger.error(
            "cameras.json response is neither a list nor a dict. Got: %s",
            type(data).__name__,
        )
        return []

    cameras: List[CameraEntry] = []
    skipped = 0

    for i, raw in enumerate(cameras_raw):
        if not isinstance(raw, dict):
            logger.warning("Skipping non-dict camera entry at index %d: %s", i, raw)
            skipped += 1
            continue
        try:
            cam = _build_camera_entry(raw)
            cameras.append(cam)
        except Exception as exc:
            logger.warning(
                "Could not parse camera at index %d — %s. Raw: %s",
                i, exc, str(raw)[:200],
            )
            skipped += 1

    logger.info(
        "Camera catalogue loaded: %d cameras (skipped %d malformed entries).",
        len(cameras), skipped,
    )

    if not cameras:
        logger.warning(
            "Catalogue returned 0 valid cameras. "
            "Check that the Sentinel sandbox is running and populated."
        )

    return cameras


def _build_camera_entry(raw: dict) -> CameraEntry:
    """
    Builds a CameraEntry from a raw cameras.json record.

    Constructs RTSP/WebRTC/HLS URLs using the official host/port from settings
    when the catalogue doesn't provide them, so the entry is always usable.
    """
    from backend.app.models.camera import camera_from_catalogue_dict

    cam = camera_from_catalogue_dict(raw)

    # If the catalogue didn't provide stream URLs, construct them from
    # the official infrastructure (per Integrator's Guide)
    if cam.camera_id:
        if not cam.rtsp_url:
            cam = cam.model_copy(update={
                "rtsp_url": settings.rtsp_url_for(cam.camera_id)
            })
        if not cam.webrtc_url:
            cam = cam.model_copy(update={
                "webrtc_url": settings.webrtc_url_for(cam.camera_id)
            })
        if not cam.hls_url:
            cam = cam.model_copy(update={
                "hls_url": settings.hls_url_for(cam.camera_id)
            })

    return cam
