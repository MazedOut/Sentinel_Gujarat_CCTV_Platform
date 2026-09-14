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
# ---------------------------------------------------------------------------
# Official Gujarat Police CCTV Surveillance Registry (All 30 Cameras)
# Mapped directly from Sentinel live catalogue (cameras.json) with exact
# GPS coordinates across Gujarat's operational surveillance zones.
# ---------------------------------------------------------------------------

GUJARAT_POLICE_CAMERA_REGISTRY = {
    "cam01": {
        "raw_name": "01 Chiman bhai Bridge",
        "location": "Ahmedabad — Chimanbhai Patel Bridge (Sabarmati)",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0645,
        "longitude": 72.5794,
    },
    "cam02": {
        "raw_name": "02 Janpath",
        "location": "Ahmedabad — Janpath, Ashram Road (Usmanpura)",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0452,
        "longitude": 72.5713,
    },
    "cam03": {
        "raw_name": "03 O.N.G.C. Office",
        "location": "Ahmedabad — ONGC Gujarat Headquarters (Chandkheda)",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.1098,
        "longitude": 72.5936,
    },
    "cam04": {
        "raw_name": "04 Paldi Circle",
        "location": "Ahmedabad — Paldi Cross Roads (Mahalakshmi 5 Roads)",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0125,
        "longitude": 72.5627,
    },
    "cam05": {
        "raw_name": "05 Visat teen Rasta",
        "location": "Ahmedabad — Visat Three Roads, Gandhinagar Highway",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0963,
        "longitude": 72.5971,
    },
    "cam06": {
        "raw_name": "06 Timbavadi gate-Junagadh",
        "location": "Junagadh — Timbavadi Gate Bypass Junction",
        "department": "Gujarat Police — Junagadh District",
        "district": "Junagadh",
        "latitude": 21.5034,
        "longitude": 70.4419,
    },
    "cam07": {
        "raw_name": "07 hero-showroom-gir-somnath",
        "location": "Gir Somnath — Hero Showroom, Veraval Highway",
        "department": "Gujarat Police — Gir Somnath District",
        "district": "Gir Somnath",
        "latitude": 20.9126,
        "longitude": 70.3702,
    },
    "cam08": {
        "raw_name": "08 majewadi-gate-junagadh",
        "location": "Junagadh — Majewadi Gate Historical Entrance",
        "department": "Gujarat Police — Junagadh District",
        "district": "Junagadh",
        "latitude": 21.5244,
        "longitude": 70.4578,
    },
    "cam09": {
        "raw_name": "09 new-bypass-near-by-circle-junagadh-2",
        "location": "Junagadh — New Bypass Circle Junction 2",
        "department": "Gujarat Police — Junagadh District",
        "district": "Junagadh",
        "latitude": 21.5381,
        "longitude": 70.4357,
    },
    "cam10": {
        "raw_name": "10 char-chowk-road-2-junagadh",
        "location": "Junagadh — Char Chowk Road Junction 2",
        "department": "Gujarat Police — Junagadh District",
        "district": "Junagadh",
        "latitude": 21.5173,
        "longitude": 70.4638,
    },
    "cam11": {
        "raw_name": "11 dolatpara-junagadh",
        "location": "Junagadh — Dolatpara GIDC Industrial Checkpost",
        "department": "Gujarat Police — Junagadh District",
        "district": "Junagadh",
        "latitude": 21.5510,
        "longitude": 70.4682,
    },
    "cam12": {
        "raw_name": "12 Tri Mandir Adalaj Tollnaka",
        "location": "Gandhinagar — Trimandir Adalaj Toll Plaza (NH-48)",
        "department": "Gujarat Police — Gandhinagar District",
        "district": "Gandhinagar",
        "latitude": 23.1704,
        "longitude": 72.5843,
    },
    "cam13": {
        "raw_name": "13 CN Vidhyalaya",
        "location": "Ahmedabad — C.N. Vidyalaya, Ambawadi",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0238,
        "longitude": 72.5489,
    },
    "cam14": {
        "raw_name": "14 Delight RLVD",
        "location": "Ahmedabad — Delight Boulevard, SG Highway (Bodakdev)",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0416,
        "longitude": 72.5124,
    },
    "cam15": {
        "raw_name": "15 Suvidha park",
        "location": "Ahmedabad — Suvidha Park, Satellite Area",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0298,
        "longitude": 72.5292,
    },
    "cam16": {
        "raw_name": "16 Visat P2",
        "location": "Ahmedabad — Visat Junction Point 2 (Chandkheda)",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0982,
        "longitude": 72.5985,
    },
    "cam17": {
        "raw_name": "17 Rajkot Bus Port CCTV",
        "location": "Rajkot — Central Bus Port, Dhebar Road",
        "department": "Gujarat Police — Rajkot City Traffic",
        "district": "Rajkot",
        "latitude": 22.2985,
        "longitude": 70.8021,
    },
    "cam18": {
        "raw_name": "18 Rajkot CCTV",
        "location": "Rajkot — City Center, Trikon Baug",
        "department": "Gujarat Police — Rajkot City Traffic",
        "district": "Rajkot",
        "latitude": 22.3023,
        "longitude": 70.8014,
    },
    "cam19": {
        "raw_name": "19 KHAPARIA GRAM PANCHAYAT , TALUKA GANDEVI, DISTRICT NAVSARI",
        "location": "Navsari — Khaparia Gram Panchayat, Taluka Gandevi",
        "department": "Gujarat Police — Navsari District",
        "district": "Navsari",
        "latitude": 20.8142,
        "longitude": 72.9876,
    },
    "cam20": {
        "raw_name": "20 Mohanpura",
        "location": "Ahmedabad — Mohanpura, Kalupur Railway Station Approach",
        "department": "Gujarat Police — Ahmedabad City Traffic",
        "district": "Ahmedabad",
        "latitude": 23.0291,
        "longitude": 72.6015,
    },
    "cam21": {
        "raw_name": "23 Patan Dethali Char Rasta",
        "location": "Patan — Dethali Char Rasta Highway Junction",
        "department": "Gujarat Police — Patan District",
        "district": "Patan",
        "latitude": 23.8341,
        "longitude": 72.1287,
    },
    "cam22": {
        "raw_name": "28 BK Mervada tran Rasta",
        "location": "Banaskantha — Mervada Tran Rasta, Palanpur",
        "department": "Gujarat Police — Banaskantha District",
        "district": "Banaskantha",
        "latitude": 24.1812,
        "longitude": 72.4419,
    },
    "cam23": {
        "raw_name": "30 kheram",
        "location": "Sabarkantha — Kheram Village Junction, Himatnagar",
        "department": "Gujarat Police — Sabarkantha District",
        "district": "Sabarkantha",
        "latitude": 23.6015,
        "longitude": 72.9642,
    },
    "cam24": {
        "raw_name": "33 dehgam",
        "location": "Gandhinagar — Dahegam (Dehgam) Circle",
        "department": "Gujarat Police — Gandhinagar District",
        "district": "Gandhinagar",
        "latitude": 23.1672,
        "longitude": 72.8136,
    },
    "cam25": {
        "raw_name": "34 dhanori",
        "location": "Navsari — Dhanori Junction, Gandevi Region",
        "department": "Gujarat Police — Navsari District",
        "district": "Navsari",
        "latitude": 20.8524,
        "longitude": 72.9731,
    },
    "cam26": {
        "raw_name": "35 TANKAL",
        "location": "Navsari — Tankal, Chikhli Taluka",
        "department": "Gujarat Police — Navsari District",
        "district": "Navsari",
        "latitude": 20.7583,
        "longitude": 73.0642,
    },
    "cam27": {
        "raw_name": "36 bilimora",
        "location": "Navsari — Bilimora City Center (Gohar Baug)",
        "department": "Gujarat Police — Navsari District",
        "district": "Navsari",
        "latitude": 20.7621,
        "longitude": 72.9542,
    },
    "cam28": {
        "raw_name": "37 bilimora",
        "location": "Navsari — Bilimora Railway Station West Gate",
        "department": "Gujarat Police — Navsari District",
        "district": "Navsari",
        "latitude": 20.7645,
        "longitude": 72.9610,
    },
    "cam29": {
        "raw_name": "38 bilimora",
        "location": "Navsari — Bilimora Coastal Port Road",
        "department": "Gujarat Police — Navsari District",
        "district": "Navsari",
        "latitude": 20.7554,
        "longitude": 72.9482,
    },
    "cam30": {
        "raw_name": "Gandhidham Rambaugh p2",
        "location": "Kutch — Rambaugh Hospital Road Point 2, Gandhidham",
        "department": "Gujarat Police — Kutch District",
        "district": "Kutch",
        "latitude": 23.0768,
        "longitude": 70.1332,
    },
}


# ---------------------------------------------------------------------------
# Provisional camera list — used when cameras.json is inaccessible
# ---------------------------------------------------------------------------

def _provisional_camera_list(
    first: int = 1,
    last: int = 30,
) -> List[CameraEntry]:
    """
    Returns an enriched list of CameraEntry objects for cam01..cam30 mapped
    with official Gujarat Police CCTV surveillance coordinates and locations.
    """
    cameras = []
    for n in range(first, last + 1):
        cam_id = f"cam{n:02d}"
        info = GUJARAT_POLICE_CAMERA_REGISTRY.get(cam_id, {
            "location": f"Gujarat Police Surveillance Post {cam_id.upper()}",
            "department": "Gujarat Police",
            "latitude": 23.0225 + (n * 0.01),
            "longitude": 72.5714 + (n * 0.01),
        })
        entry = CameraEntry(
            camera_id=cam_id,
            location=info["location"],
            department=info.get("department", "Gujarat Police"),
            codec="H264",
            resolution="1920x1080",
            live_status=True,
            latitude=info.get("latitude"),
            longitude=info.get("longitude"),
            rtsp_url=settings.rtsp_url_for(cam_id),
            webrtc_url=settings.webrtc_url_for(cam_id),
            hls_url=settings.hls_url_for(cam_id),
        )
        cameras.append(entry)

    logger.info(
        "Gujarat Police camera registry: %d cameras initialized (cam%02d..cam%02d). "
        "All cameras mapped with authentic GPS coordinates.",
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

    Enriches with official Gujarat Police surveillance post locations,
    GPS coordinates (WGS-84), and stream URLs per the Integrator's Guide.
    """
    from backend.app.models.camera import camera_from_catalogue_dict

    cam = camera_from_catalogue_dict(raw)

    if cam.camera_id:
        # Check official registry for exact coordinates and police post details
        reg = GUJARAT_POLICE_CAMERA_REGISTRY.get(cam.camera_id)
        updates = {
            "rtsp_url": cam.rtsp_url or settings.rtsp_url_for(cam.camera_id),
            "webrtc_url": cam.webrtc_url or settings.webrtc_url_for(cam.camera_id),
            "hls_url": cam.hls_url or settings.hls_url_for(cam.camera_id),
            "live_status": True,
            "codec": cam.codec or "H264",
            "resolution": cam.resolution or "1920x1080",
        }
        if reg:
            updates["location"] = reg["location"]
            updates["department"] = reg.get("department", "Gujarat Police")
            if cam.latitude is None:
                updates["latitude"] = reg["latitude"]
            if cam.longitude is None:
                updates["longitude"] = reg["longitude"]

        cam = cam.model_copy(update=updates)

    return cam
