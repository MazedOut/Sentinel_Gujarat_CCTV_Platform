"""
sentinel-gujarat/backend/app/core/config.py
-------------------------------------------
Centralised configuration loader.

All environment-specific values live in .env (never committed to Git).
Import `settings` from here — do NOT use os.environ directly anywhere else.

Uses pydantic-settings so that:
  - values are type-validated on startup
  - missing required values raise a clear error immediately
  - IDE autocomplete works

OFFICIAL SENTINEL INFRASTRUCTURE (from Integrator's Guide):
  Catalogue  : https://cctv.corp8.cloud/cameras.json
  RTSP       : rtsp://103.250.160.189:8554/stream/<camera_id>    [no auth]
  WebRTC/WHEP: http://103.250.160.189:8889/stream/<camera_id>/whep [no auth]
  HLS        : https://cctv.corp8.cloud/<camera_id>/index.m3u8  [password-protected]
"""

from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    Project-wide settings, read from environment variables / .env file.
    Every field has a sensible default where safe.
    Required fields (no default) will raise an error if not set.
    """

    # Database
    postgres_user: str = "sentinel"
    postgres_password: str = "sentinelpassword"
    postgres_host: str = "localhost"
    postgres_port: str = "5432"
    postgres_db: str = "sentinel_registry"

    @property
    def database_url(self) -> str:
        """Constructs the SQLAlchemy database URL."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently ignore unknown env vars
    )

    # ------------------------------------------------------------------
    # Sentinel Sandbox — Official Infrastructure (Integrator's Guide)
    # ------------------------------------------------------------------

    # Camera catalogue endpoint — returns cameras.json
    # NOTE: This endpoint is behind the browser password.
    # If it returns a login page, the catalogue parser detects this and
    # reports it clearly. RTSP/WebRTC remain usable without this.
    sentinel_catalogue_url: str = Field(
        default="https://cctv.corp8.cloud/cameras.json",
        description="Full URL of the Sentinel camera catalogue (cameras.json).",
    )
    sentinel_catalogue_cookie: str = Field(
        default="",
        description="Complete HTTP Cookie header value to authenticate requests to cameras.json.",
    )
    sentinel_catalogue_token: Optional[str] = Field(
        default="",
        description="[Optional/Unused] Bearer token for Sentinel catalogue if ever needed.",
    )

    # RTSP host & credentials (per Integrator's Guide)
    sentinel_rtsp_host: str = Field(
        default="103.250.160.189",
        description="Host IP for the Sentinel RTSP service.",
    )
    sentinel_rtsp_port: int = Field(
        default=8554,
        description="Port for the Sentinel RTSP service.",
    )
    sentinel_rtsp_email: Optional[str] = Field(
        default="",
        description="Email for authenticated Sentinel RTSP stream ingestion.",
    )
    sentinel_rtsp_password: Optional[str] = Field(
        default="",
        description="Password for authenticated Sentinel RTSP stream ingestion.",
    )

    # WebRTC/WHEP host
    sentinel_webrtc_host: str = Field(
        default="103.250.160.189",
        description="Host IP for the Sentinel WebRTC/WHEP service.",
    )
    sentinel_webrtc_port: int = Field(
        default=8889,
        description="Port for the Sentinel WebRTC service.",
    )

    # HLS CDN host — password-protected
    sentinel_hls_host: str = Field(
        default="cctv.corp8.cloud",
        description="CDN host for HLS streams (password-protected, browser use only).",
    )
    sentinel_hls_cookie: Optional[str] = Field(
        default="",
        description="Optional separate cookie for HLS streams if different from catalogue cookie.",
    )

    # Legacy field — kept for backward compatibility with old scripts
    sentinel_base_url: str = Field(
        default="https://cctv.corp8.cloud",
        description="[Legacy] Base URL of the Sentinel CDN. Use sentinel_catalogue_url instead.",
    )
    sentinel_ingest_endpoint: str = Field(
        default="/cameras.json",
        description="[Legacy] Catalogue endpoint path. Full URL is sentinel_catalogue_url.",
    )

    @property
    def sentinel_ingest_url(self) -> str:
        """Full URL for the camera catalogue (cameras.json)."""
        return self.sentinel_catalogue_url

    def rtsp_url_for(self, camera_id: str, authenticated: bool = True) -> str:
        """Constructs the RTSP stream URL for a given camera ID.
        If authenticated=True (default for backend AI ingestion), dynamically injects
        URL-encoded credentials: rtsp://<email>:<password>@<host>:<port>/stream/<camera_id>.
        The '@' symbol in the email is strictly URL-encoded to '%40'.
        If authenticated=False (for public/frontend APIs), omits credentials entirely.
        """
        import urllib.parse
        if authenticated and self.sentinel_rtsp_email and self.sentinel_rtsp_password:
            encoded_user = urllib.parse.quote(self.sentinel_rtsp_email.strip(), safe="")
            encoded_pass = urllib.parse.quote(self.sentinel_rtsp_password.strip(), safe="")
            return f"rtsp://{encoded_user}:{encoded_pass}@{self.sentinel_rtsp_host}:{self.sentinel_rtsp_port}/stream/{camera_id}"
        return f"rtsp://{self.sentinel_rtsp_host}:{self.sentinel_rtsp_port}/stream/{camera_id}"

    def webrtc_url_for(self, camera_id: str, authenticated: bool = False) -> str:
        """Constructs the WebRTC/WHEP URL for a given camera ID.
        Credentials remain server-side unless explicitly requested for backend relay.
        """
        import urllib.parse
        if authenticated and self.sentinel_rtsp_email and self.sentinel_rtsp_password:
            encoded_user = urllib.parse.quote(self.sentinel_rtsp_email.strip(), safe="")
            encoded_pass = urllib.parse.quote(self.sentinel_rtsp_password.strip(), safe="")
            return f"http://{encoded_user}:{encoded_pass}@{self.sentinel_webrtc_host}:{self.sentinel_webrtc_port}/stream/{camera_id}/whep"
        return f"http://{self.sentinel_webrtc_host}:{self.sentinel_webrtc_port}/stream/{camera_id}/whep"

    def hls_url_for(self, camera_id: str) -> str:
        """Constructs the HLS URL for a given camera ID.
        Per Integrator's Guide: https://<hls_host>/<camera_id>/index.m3u8
        HLS is password-protected — for browser/dashboard use only.
        """
        return f"https://{self.sentinel_hls_host}/{camera_id}/index.m3u8"

    # ------------------------------------------------------------------
    # RTSP
    # ------------------------------------------------------------------
    rtsp_transport: str = Field(
        default="tcp",
        description=(
            "Transport protocol for RTSP. Must be 'tcp' per Sentinel integration rules. "
            "UDP causes packet loss that corrupts frames and looks like model failures."
        ),
    )
    rtsp_frame_timeout_sec: int = Field(
        default=10,
        description="Seconds to wait for a frame before treating the stream as dead.",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    # Can set DATABASE_URL directly, or individual parts will be used
    database_url: str = Field(
        default="",
        description="PostgreSQL connection string (overrides individual postgres_* parts if set).",
    )

    @property
    def effective_database_url(self) -> str:
        """PostgreSQL connection string. Uses DATABASE_URL env var if set, else constructs from parts."""
        if self.database_url:
            return self.database_url
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # ------------------------------------------------------------------
    # AI / Detection
    # ------------------------------------------------------------------
    yolo_model: str = Field(
        default="ai/models/yolo26n.pt",
        description="YOLO model weights. yolo26n=fastest, yolo26s=balanced, yolo26m=accurate.",
    )
    plate_detector_model: str = Field(
        default="ai/models/plate_detector.pt",
        description="Dedicated YOLO license plate detection model weights.",
    )
    yolo_confidence_threshold: float = Field(
        default=0.35,
        description="Minimum vehicle detection confidence [0.0-1.0].",
    )
    yolo_frame_skip: int = Field(
        default=3,
        description="Process every Nth frame. 1=every frame. Higher=faster but less frequent.",
    )

    # ------------------------------------------------------------------
    # ANPR
    # ------------------------------------------------------------------
    anpr_confidence_threshold: float = Field(
        default=0.50,
        description="Minimum ANPR overall confidence to record a detection.",
    )
    anpr_save_evidence_frames: bool = Field(
        default=False,
        description="If True, save evidence frame crops for each detection.",
    )
    anpr_evidence_dir: str = Field(
        default="evidence",
        description="Directory to save evidence frame images.",
    )

    # ------------------------------------------------------------------
    # Alert thresholds (configurable — not hard-coded)
    # ------------------------------------------------------------------
    alert_high_confidence: float = Field(
        default=0.85,
        description="Overall confidence >= this → HIGH severity alert (auto-alert).",
    )
    alert_medium_confidence: float = Field(
        default=0.65,
        description="Overall confidence >= this → MEDIUM severity (manual review queue).",
    )
    # Below alert_medium_confidence → store detection, no alert

    # ------------------------------------------------------------------
    # HLS access (browser/dashboard — optional)
    # If the Sentinel HLS CDN requires auth, set these.
    # NEVER put real credentials in .env.example or source code.
    # ------------------------------------------------------------------
    sentinel_hls_username: str = Field(default="", description="HLS CDN username (if required).")
    sentinel_hls_password: str = Field(default="", description="HLS CDN password (if required).")

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    jwt_secret: str = Field(default="CHANGE_ME_TO_A_RANDOM_SECRET", description="JWT signing secret.")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expire_minutes: int = Field(default=60)

    # ------------------------------------------------------------------
    # Google Maps
    # ------------------------------------------------------------------
    google_maps_api_key: str = Field(default="")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Logging verbosity: DEBUG | INFO | WARNING | ERROR",
    )

    # ------------------------------------------------------------------
    # Frontend / CORS
    # ------------------------------------------------------------------
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8080,http://127.0.0.1:8080",
        description="Comma-separated list of allowed CORS origins.",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# ---------------------------------------------------------------------------
# Single shared instance — import this everywhere instead of re-reading .env
# ---------------------------------------------------------------------------
settings = Settings()


def redact_credentials(url: str) -> str:
    """
    Redacts passwords and secrets from RTSP, HTTP, or database URLs for safe logging.
    Example:
      rtsp://you%40example.com:secret@103.250.160.189:8554/stream/cam01
      -> rtsp://you%40example.com:***@103.250.160.189:8554/stream/cam01
    """
    if not url:
        return ""
    import re
    return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", url)
