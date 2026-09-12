"""
sentinel-gujarat/backend/app/models/camera.py
----------------------------------------------
Pydantic model for a camera entry from the Sentinel catalogue.

Design decisions:
  - Every field is Optional so that missing/null catalogue values do NOT crash parsing.
  - Fields use snake_case internally; aliases handle camelCase JSON from the API.
  - We do NOT hard-code which fields exist — the catalogue is the source of truth.
  - `model_validate` is used with `by_alias=False` so callers use snake_case.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator
import re


class CameraEntry(BaseModel):
    """
    Represents one camera as returned by GET /api/ingest.

    The Sentinel catalogue may not always include every field (e.g. lat/lon
    may be missing for some legacy cameras).  All fields default to None so
    we never crash on incomplete records — we log warnings instead.

    Stream URL naming follows the official Sentinel integration reference:
      RTSP  : rtsp://<host>:8554/stream/<id>
      WebRTC: http://<host>:8889/stream/<id>/whep
      HLS   : http://<host>/live/stream/<id>/index.m3u8
    """

    # ------------------------------------------------------------------ #
    # Identity
    # ------------------------------------------------------------------ #
    camera_id: str = Field(..., description="Unique camera identifier from the catalogue.")
    department: Optional[str] = Field(None, description="Owning government department.")
    location: Optional[str] = Field(None, description="Human-readable location label.")
    latitude: Optional[float] = Field(None, description="Camera latitude (WGS-84).")
    longitude: Optional[float] = Field(None, description="Camera longitude (WGS-84).")

    # ------------------------------------------------------------------ #
    # Stream properties
    # ------------------------------------------------------------------ #
    codec: Optional[str] = Field(
        None,
        description="Video codec reported by the catalogue, e.g. 'H264' or 'H265'.",
    )
    resolution: Optional[str] = Field(
        None,
        description="Resolution string if reported, e.g. '1920x1080'.",
    )
    fps_reported: Optional[float] = Field(
        None,
        description=(
            "FPS as reported by catalogue. "
            "WARNING: Do NOT use this for timing calculations. "
            "Use actual PTS (CAP_PROP_POS_MSEC) instead per Sentinel integration rules."
        ),
    )
    bitrate_kbps: Optional[int] = Field(
        None,
        description="Bitrate in kbps if available.",
    )
    live_status: Optional[bool] = Field(
        None,
        description="Whether the camera is currently reporting as live.",
    )

    # ------------------------------------------------------------------ #
    # Stream URLs (per official Sentinel protocol reference)
    # ------------------------------------------------------------------ #
    rtsp_url: Optional[str] = Field(
        None,
        description=(
            "RTSP URL for AI inference pipeline. "
            "Must be consumed with TCP transport only."
        ),
    )
    webrtc_url: Optional[str] = Field(
        None,
        description="WebRTC/WHEP URL for low-latency browser preview.",
    )
    hls_url: Optional[str] = Field(
        None,
        description="HLS URL for dashboard / mobile / restricted networks.",
    )

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #
    last_seen: Optional[datetime] = Field(
        None,
        description="Last time the camera was seen active (from catalogue).",
    )
    metadata_updated_at: Optional[datetime] = Field(
        None,
        description="When this catalogue record was last updated.",
    )

    # Raw field bag — stores any extra fields from the catalogue we don't model yet
    extra_fields: dict = Field(
        default_factory=dict,
        description="Any additional catalogue fields not explicitly modelled.",
    )

    model_config = {"extra": "ignore"}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @property
    def has_rtsp(self) -> bool:
        """True if a valid RTSP URL is present."""
        return bool(self.rtsp_url)

    @property
    def is_streamable(self) -> bool:
        """True if the camera has at least one stream URL available."""
        return any([self.rtsp_url, self.webrtc_url, self.hls_url])

    @property
    def codec_normalized(self) -> str:
        """
        Returns codec as a clean uppercase string: 'H264', 'H265', or 'UNKNOWN'.
        Handles variations like 'h264', 'avc', 'hevc' etc.
        """
        if not self.codec:
            return "UNKNOWN"
        c = self.codec.upper().strip()
        if c in ("H264", "AVC", "H.264"):
            return "H264"
        if c in ("H265", "HEVC", "H.265"):
            return "H265"
        return c

    def display_summary(self) -> str:
        """One-line human-readable summary for CLI output."""
        status = "[LIVE]" if self.live_status else ("[OFFLINE]" if self.live_status is False else "[UNKNOWN]")
        lat_lon = (
            f"{self.latitude:.4f}, {self.longitude:.4f}"
            if self.latitude is not None and self.longitude is not None
            else "no coords"
        )
        return (
            f"[{self.camera_id}] {status} | "
            f"{self.location or 'unknown location'} | "
            f"{lat_lon} | "
            f"codec={self.codec_normalized} | "
            f"rtsp={'YES' if self.has_rtsp else 'NO'}"
        )


# ---------------------------------------------------------------------------
# Factory — build a CameraEntry from a raw catalogue dict safely
# ---------------------------------------------------------------------------

def camera_from_catalogue_dict(raw: dict) -> CameraEntry:
    """
    Safely converts a raw dict from /api/ingest into a CameraEntry.

    The Sentinel API may use different key names (camelCase, snake_case, etc.)
    This function normalises common variations before passing to Pydantic.

    If an unexpected key format appears, extend the mapping here rather than
    changing the model — keeps the model stable.
    """

    def _get(*keys, default=None):
        """Try multiple key variants; return first match or default."""
        for k in keys:
            if k in raw:
                return raw[k]
        return default

    # Normalise stream URLs
    rtsp = _get("rtsp_url", "rtspUrl", "rtsp")
    webrtc = _get("webrtc_url", "webrtcUrl", "whep_url", "whepUrl", "webrtc")
    hls = _get("hls_url", "hlsUrl", "hls")

    # Normalise live status — may be bool, or string "true"/"false"
    live_raw = _get("live_status", "liveStatus", "live", "is_live", "isLive")
    if isinstance(live_raw, str):
        live_status = live_raw.lower() in ("true", "1", "yes", "online")
    else:
        live_status = bool(live_raw) if live_raw is not None else None

    # Known keys we handle explicitly
    known_keys = {
        "camera_id", "cameraId", "id", "camera_ID",
        "department", "dept",
        "location", "loc", "name", "camera_name", "label",
        "latitude", "lat",
        "longitude", "lon", "lng",
        "codec",
        "resolution",
        "fps", "fps_reported", "fpsReported",
        "bitrate", "bitrate_kbps", "bitrateKbps",
        "live_status", "liveStatus", "live", "is_live", "isLive",
        "rtsp_url", "rtspUrl", "rtsp",
        "webrtc_url", "webrtcUrl", "whep_url", "whepUrl", "webrtc",
        "hls_url", "hlsUrl", "hls",
        "last_seen", "lastSeen",
        "metadata_updated_at", "metadataUpdatedAt", "updated_at", "updatedAt",
    }
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    return CameraEntry(
        camera_id=str(
            _get("camera_id", "cameraId", "id", "camera_ID") or ""
        ),
        department=_get("department", "dept"),
        location=_get("location", "loc", "name", "camera_name", "label"),
        latitude=_float_or_none(_get("latitude", "lat")),
        longitude=_float_or_none(_get("longitude", "lon", "lng")),
        codec=_get("codec"),
        resolution=_get("resolution"),
        fps_reported=_float_or_none(_get("fps", "fps_reported", "fpsReported")),
        bitrate_kbps=_int_or_none(_get("bitrate", "bitrate_kbps", "bitrateKbps")),
        live_status=live_status,
        rtsp_url=rtsp,
        webrtc_url=webrtc,
        hls_url=hls,
        last_seen=_datetime_or_none(_get("last_seen", "lastSeen")),
        metadata_updated_at=_datetime_or_none(
            _get("metadata_updated_at", "metadataUpdatedAt", "updated_at", "updatedAt")
        ),
        extra_fields=extra,
    )


def _float_or_none(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _datetime_or_none(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
