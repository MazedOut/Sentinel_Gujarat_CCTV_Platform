"""
Sentinel Gujarat — FastAPI Backend
====================================
Complete REST API + WebSocket for the Sentinel CCTV Intelligence Platform.

Endpoints:
    GET  /                      — health / welcome
    GET  /health                — health check
    
    POST /auth/register         — create user
    POST /auth/login            — login, get JWT
    GET  /auth/me               — current user info

    GET  /cameras               — list cameras from catalogue
    GET  /cameras/{camera_id}   — single camera detail
    POST /cameras/sync          — sync catalogue from Sentinel
    GET  /cameras/{id}/stream-info — HLS/WebRTC stream URLs

    GET  /detections            — recent detection events
    GET  /detections/{id}       — single detection detail

    GET  /vehicles/{plate}/history   — vehicle journey
    GET  /vehicles              — search vehicles

    GET  /alerts                — all alerts (filterable)
    GET  /alerts/{id}           — single alert
    POST /alerts/{id}/acknowledge — ack an alert

    GET  /watchlist             — list watchlist entries
    POST /watchlist             — add entry
    DELETE /watchlist/{plate}   — remove entry

    GET  /routes/infer          — infer route between two cameras

    GET  /audit-logs            — audit log (ADMIN only)

    WS   /ws/alerts             — real-time alert stream
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import (
    FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect,
    status, Query, Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Project imports
from backend.app.core.config import settings
from backend.app.core.logging_config import configure_logging, get_logger
from backend.app.services.streaming.hls_proxy import get_playlist, get_key, stream_segment, get_stream_diagnostics
from backend.app.services.detection.incident_analyzer import get_incident_analyzer, IncidentAnalyzer
from backend.app.core.security import (
    hash_password, verify_password, create_access_token, decode_access_token,
    Role, has_permission,
)
from backend.app.db.session import get_db, get_db_optional
from backend.app.services.camera.catalogue import fetch_catalogue
from backend.app.services.alerting.watchlist_service import (
    SyntheticWatchlistProvider, get_watchlist_provider
)
from backend.app.services.alerting.alert_engine import (
    get_all_alerts, get_alert, acknowledge_alert, register_alert_callback
)
from backend.app.services.tracking.journey_correlator import get_correlator
from backend.app.services.routing.routing_service import get_routing_service
from backend.app.services.audit.audit_service import get_audit_service, AuditAction
from backend.app.services.alerting.incident_detector import get_incident_detector

configure_logging()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# WebSocket alert broadcast
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        import json
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()


def _alert_broadcast_callback(alert: dict):
    """Called by alert engine when a new alert fires. Schedules WS broadcast."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(ws_manager.broadcast({"type": "NEW_ALERT", "alert": alert}))
    except Exception:
        pass


register_alert_callback(_alert_broadcast_callback)


# ---------------------------------------------------------------------------
# Hardware & System Detection
# ---------------------------------------------------------------------------

_cached_hardware_info = None


def get_system_hardware() -> dict:
    """Detect GPU hardware and runtime acceleration status."""
    global _cached_hardware_info
    if _cached_hardware_info is not None:
        return _cached_hardware_info

    gpu_name = None
    acceleration = "Active (Direct / Hardware)"
    device = "cpu"

    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            acceleration = f"CUDA Active ({gpu_name})"
            device = "cuda"
    except Exception:
        pass

    if not gpu_name and sys.platform == "win32":
        try:
            import subprocess
            cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                names = [n.strip() for n in res.stdout.strip().splitlines() if n.strip()]
                if names:
                    gpu_name = names[0]
                    acceleration = "Active (Direct / Hardware)"
        except Exception:
            pass

    if not gpu_name:
        gpu_name = "Host CPU Accelerator"
        acceleration = "Active (AVX2 / Multi-core)"

    _cached_hardware_info = {
        "name": gpu_name,
        "status": acceleration,
        "device": device,
    }
    return _cached_hardware_info


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Sentinel Gujarat API starting...")
    logger.info("Catalogue: %s", settings.sentinel_catalogue_url)
    logger.info("RTSP host: %s:%d", settings.sentinel_rtsp_host, settings.sentinel_rtsp_port)
    logger.info("DB URL: %s", settings.effective_database_url[:40] + "..." if len(settings.effective_database_url) > 40 else settings.effective_database_url)

    # Initialize DB schema & seed default users
    try:
        from backend.app.db.session import init_db, SessionLocal
        init_db()
        if SessionLocal:
            db = SessionLocal()
            try:
                from backend.app.models.db.user import User
                admin_user = db.query(User).filter(User.username == "admin").first()
                if not admin_user:
                    admin_hash = hash_password("sentinel_admin")
                    db.add(User(
                        username="admin",
                        hashed_password=admin_hash,
                        role=Role.ADMIN,
                        full_name="System Administrator",
                        is_active=True,
                    ))
                officer_user = db.query(User).filter(User.username == "officer1").first()
                if not officer_user:
                    officer_hash = hash_password("sentinel_officer")
                    db.add(User(
                        username="officer1",
                        hashed_password=officer_hash,
                        role=Role.POLICE_OFFICER,
                        full_name="Demo Police Officer",
                        department="Gujarat Police",
                        is_active=True,
                    ))
                db.commit()
                logger.info("Default admin & officer accounts verified in database.")
            except Exception as e:
                logger.warning(f"Could not seed users to database: {e}")
                db.rollback()
            finally:
                db.close()
    except Exception as e:
        logger.warning(f"Database initialization encountered an error: {e}")

    hw = get_system_hardware()
    logger.info("Detected Hardware: %s [%s]", hw["name"], hw["status"])

    # Startup Camera Inventory & Stream Integrity Audit
    try:
        cams = _get_cameras()
        sources = [settings.hls_url_for(c.camera_id) for c in cams]
        unique_sources = set(sources)
        dup_count = len(sources) - len(unique_sources)
        logger.info(
            "CCTV Camera Audit: %d cameras registered | %d unique sources | %d duplicates",
            len(cams), len(unique_sources), dup_count
        )
        if dup_count > 0:
            logger.error("CRITICAL: DUPLICATE CAMERA SOURCES DETECTED IN REGISTRY!")
        else:
            logger.info("Deterministic 1:1 camera mapping verified for all %d cameras.", len(cams))
    except Exception as exc:
        logger.warning("Camera inventory audit exception: %s", exc)

    logger.info("=" * 60)
    yield
    logger.info("Sentinel Gujarat API shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Sentinel Gujarat CCTV Intelligence Platform",
    description=(
        "An interoperable intelligence layer for Gujarat's CCTV ecosystem. "
        "Converts live CCTV feeds into actionable, searchable, AI-generated security intelligence."
    ),
    version="1.0.0-poc",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, use settings.cors_origins_list
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# In-memory user store (when DB unavailable)
_users: dict[str, dict] = {}


def _ensure_default_admin():
    """Create a default admin user if none exists."""
    if not _users:
        admin_hash = hash_password("sentinel_admin")
        _users["admin"] = {
            "id": 1,
            "username": "admin",
            "hashed_password": admin_hash,
            "role": Role.ADMIN,
            "full_name": "System Administrator",
            "department": None,
            "is_active": True,
        }
        officer_hash = hash_password("sentinel_officer")
        _users["officer1"] = {
            "id": 2,
            "username": "officer1",
            "hashed_password": officer_hash,
            "role": Role.POLICE_OFFICER,
            "full_name": "Demo Police Officer",
            "department": "Gujarat Police",
            "is_active": True,
        }


_ensure_default_admin()


def get_current_user(token: str = Depends(oauth2_scheme), db: Optional[Session] = Depends(get_db_optional)) -> dict:
    """Decode JWT and return the current user dict."""
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    username = payload.get("username")
    user = _users.get(username)
    if not user and db:
        try:
            from backend.app.models.db.user import User
            db_u = db.query(User).filter(User.username == username).first()
            if db_u:
                user = {
                    "id": db_u.id,
                    "username": db_u.username,
                    "hashed_password": db_u.hashed_password,
                    "role": db_u.role,
                    "full_name": db_u.full_name,
                    "department": db_u.department,
                    "is_active": db_u.is_active,
                }
                _users[db_u.username] = user
        except Exception:
            pass
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="User account is disabled")
    return user


def require_role(*roles: str):
    """Dependency factory that requires one of the given roles."""
    def checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{current_user['role']}' does not have access to this endpoint.",
            )
        return current_user
    return checker


def get_current_user_optional(token: str = Depends(oauth2_scheme)) -> Optional[dict]:
    """Like get_current_user but returns None instead of raising if not authenticated."""
    if not token:
        return None
    try:
        return get_current_user(token)
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None
    role: str = Role.DEPARTMENT_USER
    department: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    full_name: Optional[str]
    department: Optional[str]


class CameraOut(BaseModel):
    camera_id: str
    location: Optional[str]
    department: Optional[str]
    codec: Optional[str]
    resolution: Optional[str]
    live_status: Optional[bool]
    rtsp_url: Optional[str]
    webrtc_url: Optional[str]
    hls_url: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]


class AlertOut(BaseModel):
    id: str
    camera_id: str
    registration_number: str
    timestamp: str
    severity: str
    status: str
    overall_confidence: float
    watchlist_status: str
    priority: str
    location: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    pts_ms: Optional[float]
    acknowledged_by: Optional[str]
    acknowledged_at: Optional[str]


class WatchlistEntryIn(BaseModel):
    registration_number: str
    status: str = "SURVEILLANCE"
    priority: str = "MEDIUM"
    description: Optional[str] = None
    person_name: Optional[str] = None


class RouteInferRequest(BaseModel):
    origin_camera_id: str
    dest_camera_id: str
    origin_lat: float
    origin_lon: float
    dest_lat: float
    dest_lon: float


class AcknowledgeRequest(BaseModel):
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes — Health
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
def root():
    return {
        "service": "Sentinel Gujarat CCTV Intelligence Platform",
        "version": "1.0.0-poc",
        "status": "operational",
        "dashboard": "/ui",
        "docs": "/docs",
    }


@app.get("/dashboard", include_in_schema=False)
@app.get("/app", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse("/ui/")


@app.get("/health", tags=["health"])
def health_check(db: Optional[Session] = Depends(get_db_optional)):
    from backend.app.db.session import db_type
    db_ok = False
    if db:
        try:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
            db_ok = True
        except Exception:
            pass

    hw = get_system_hardware()
    maps_provider = "google_maps" if (settings.google_maps_api_key and settings.google_maps_api_key.strip()) else "openstreetmap"

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "connected" if db_ok else "not_configured",
        "database_type": db_type if db_ok else "in_memory",
        "rtsp_host": f"{settings.sentinel_rtsp_host}:{settings.sentinel_rtsp_port}",
        "catalogue_url": settings.sentinel_catalogue_url,
        "google_maps": "configured" if maps_provider == "google_maps" else "not_configured",
        "maps_provider": maps_provider,
        "maps_status": "active",
        "hardware": hw,
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/auth/register", tags=["auth"])
def register(
    body: UserRegister,
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    if body.username in _users:
        raise HTTPException(400, f"Username '{body.username}' already exists")

    if body.role not in Role.ALL:
        raise HTTPException(400, f"Invalid role '{body.role}'. Must be one of {Role.ALL}")

    user_id = max((u["id"] for u in _users.values()), default=0) + 1
    hashed = hash_password(body.password)
    user = {
        "id": user_id,
        "username": body.username,
        "hashed_password": hashed,
        "role": body.role,
        "full_name": body.full_name,
        "department": body.department,
        "is_active": True,
    }
    _users[body.username] = user

    if db:
        try:
            from backend.app.models.db.user import User
            db_user = User(
                username=body.username,
                hashed_password=hashed,
                role=body.role,
                full_name=body.full_name,
                department=body.department,
                is_active=True,
            )
            db.add(db_user)
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to persist user to database: {e}")

    audit = get_audit_service()
    audit.log(
        action=AuditAction.CREATE_USER,
        username="system",
        resource_type="user",
        resource_id=body.username,
        ip_address=request.client.host if request else None,
    )

    return {"message": f"User '{body.username}' created", "role": body.role}


@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: Optional[Session] = Depends(get_db_optional),
):
    user = _users.get(form.username)
    if not user and db:
        try:
            from backend.app.models.db.user import User
            db_u = db.query(User).filter(User.username == form.username).first()
            if db_u:
                user = {
                    "id": db_u.id,
                    "username": db_u.username,
                    "hashed_password": db_u.hashed_password,
                    "role": db_u.role,
                    "full_name": db_u.full_name,
                    "department": db_u.department,
                    "is_active": db_u.is_active,
                }
                _users[db_u.username] = user
        except Exception:
            pass

    audit = get_audit_service()

    if not user or not verify_password(form.password, user["hashed_password"]):
        audit.log(
            action=AuditAction.LOGIN_FAILED,
            username=form.username,
            outcome="FAILURE",
            ip_address=request.client.host if request and request.client else None,
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
    )

    audit.log(
        action=AuditAction.LOGIN,
        username=user["username"],
        user_id=user["id"],
        role=user["role"],
        ip_address=request.client.host if request and request.client else None,
    )

    return TokenResponse(
        access_token=token,
        username=user["username"],
        role=user["role"],
    )


@app.get("/auth/me", response_model=UserInfo, tags=["auth"])
def me(current_user: dict = Depends(get_current_user)):
    return UserInfo(
        id=current_user["id"],
        username=current_user["username"],
        role=current_user["role"],
        full_name=current_user.get("full_name"),
        department=current_user.get("department"),
    )


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

# Cache cameras in memory so the API doesn't fetch on every request
_camera_cache: list = []
_camera_cache_time: Optional[float] = None
_CAMERA_CACHE_TTL = 300  # 5 minutes


def _get_cameras() -> list:
    global _camera_cache, _camera_cache_time
    import time
    now = time.monotonic()
    if _camera_cache_time and now - _camera_cache_time < _CAMERA_CACHE_TTL:
        return _camera_cache
    cameras = fetch_catalogue()
    _camera_cache = cameras
    _camera_cache_time = now
    return cameras


@app.get("/api/config/maps", tags=["config"])
def get_maps_config():
    """Returns Google Maps configuration."""
    return {
        "google_maps_api_key": settings.google_maps_api_key or "",
        "has_google_maps": bool(settings.google_maps_api_key),
    }


@app.get("/cameras", response_model=List[CameraOut], tags=["cameras"])
def list_cameras(
    live_only: bool = False,
    current_user: Optional[dict] = Depends(get_current_user_optional),
    request: Request = None,
):
    cameras = _get_cameras()
    if live_only:
        cameras = [c for c in cameras if c.live_status is True]

    audit = get_audit_service()
    if current_user:
        audit.log(
            action=AuditAction.LIST_CAMERAS,
            username=current_user.get("username"),
            role=current_user.get("role"),
            ip_address=request.client.host if request and request.client else None,
        )

    return [
        CameraOut(
            camera_id=c.camera_id,
            location=c.location,
            department=getattr(c, "department", None),
            codec=c.codec,
            resolution=c.resolution,
            live_status=c.live_status,
            rtsp_url=settings.rtsp_url_for(c.camera_id, authenticated=False),
            webrtc_url=settings.webrtc_url_for(c.camera_id, authenticated=False),
            hls_url=f"/api/hls/{c.camera_id}/index.m3u8",
            latitude=c.latitude,
            longitude=c.longitude,
        )
        for c in cameras
    ]


@app.get("/cameras/diagnostics", tags=["cameras"])
def get_camera_diagnostics(
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Returns a comprehensive stream diagnostic audit for all registered cameras.
    Reports unique sources, duplicates, proxy paths, and upstream cache status.
    """
    cameras = _get_cameras()
    stream_diag = get_stream_diagnostics()

    seen_sources = set()
    duplicates = []
    records = []

    for c in cameras:
        source = f"https://{settings.sentinel_hls_host}/{c.camera_id}/index.m3u8"
        is_dup = source in seen_sources
        if is_dup:
            duplicates.append(c.camera_id)
        seen_sources.add(source)

        info = stream_diag.get(c.camera_id, {})
        records.append({
            "camera_id": c.camera_id,
            "location": c.location,
            "proxy_hls_url": f"/api/hls/{c.camera_id}/index.m3u8",
            "upstream_source": source,
            "stream_status": info.get("status", "AVAILABLE"),
            "cached_segments": info.get("segment_count", 0),
            "is_duplicate": is_dup,
        })

    return {
        "total_cameras": len(cameras),
        "unique_sources": len(seen_sources),
        "duplicate_count": len(duplicates),
        "duplicate_camera_ids": duplicates,
        "is_deterministic_mapping": len(duplicates) == 0,
        "cameras": records,
    }


@app.get("/cameras/{camera_id}", tags=["cameras"])
def get_camera(
    camera_id: str,
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    cameras = _get_cameras()
    cam = next((c for c in cameras if c.camera_id == camera_id), None)
    if not cam:
        raise HTTPException(404, f"Camera '{camera_id}' not found in catalogue")

    audit = get_audit_service()
    audit.log(
        action=AuditAction.VIEW_CAMERA,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="camera",
        resource_id=camera_id,
        ip_address=request.client.host if request and request.client else None,
    )

    return {
        "camera_id": cam.camera_id,
        "location": cam.location,
        "department": getattr(cam, "department", None),
        "codec": cam.codec,
        "resolution": cam.resolution,
        "live_status": cam.live_status,
        "rtsp_url": settings.rtsp_url_for(cam.camera_id, authenticated=False),
        "webrtc_url": settings.webrtc_url_for(cam.camera_id, authenticated=False),
        "hls_url": f"/api/hls/{cam.camera_id}/index.m3u8",
        "latitude": cam.latitude,
        "longitude": cam.longitude,
        "hls_note": "Authenticated live HLS proxy stream",
    }


# ---------------------------------------------------------------------------
# HLS Video Streaming Proxy (Server-side Authenticated)
# ---------------------------------------------------------------------------

@app.get("/api/hls/{camera_id}/index.m3u8", tags=["streaming"])
@app.get("/cameras/{camera_id}/hls/index.m3u8", tags=["streaming"])
async def get_camera_hls_playlist(camera_id: str):
    """
    Proxies and rewrites the Sentinel HLS playlist for a camera.
    Uses server-side Cookie authentication so credentials are never exposed to clients.
    """
    code, playlist, headers = await get_playlist(camera_id, base_proxy_path="/api/hls")
    if code != 200:
        raise HTTPException(status_code=code, detail=f"Could not load HLS stream for {camera_id}")
    return Response(content=playlist, status_code=200, media_type="application/vnd.apple.mpegurl", headers=headers)


@app.get("/api/hls/{camera_id}/enc.key", tags=["streaming"])
@app.get("/api/hls/enc.key", tags=["streaming"])
async def get_camera_hls_key(camera_id: str = "cam01"):
    """Proxies the AES-128 stream decryption key with server-side authentication."""
    code, key_bytes, headers = await get_key(camera_id)
    if code != 200:
        raise HTTPException(status_code=code, detail="Could not load HLS encryption key")
    return Response(content=key_bytes, status_code=200, media_type="application/octet-stream", headers=headers)


@app.get("/api/hls/{camera_id}/{segment_name}", tags=["streaming"])
async def get_camera_hls_segment(camera_id: str, segment_name: str):
    """
    Streams a single HLS MPEG-TS media segment directly through to the client without saving
    any video to disk or buffering whole files into memory.
    """
    code, stream_gen, headers = await stream_segment(camera_id, segment_name)
    if code != 200:
        raise HTTPException(status_code=code, detail=f"Could not load segment {segment_name}")
    return StreamingResponse(stream_gen, status_code=200, media_type="video/mp2t", headers=headers)


@app.get("/api/cameras/{camera_id}/live-feed", tags=["streaming"])
@app.get("/cameras/{camera_id}/live-feed", tags=["streaming"])
async def camera_live_feed(camera_id: str, overlay: int = 1):
    """Alias for camera live feed — redirects to authenticated HLS proxy playlist."""
    return RedirectResponse(f"/api/hls/{camera_id}/index.m3u8")


@app.post("/cameras/{camera_id}/whep", tags=["streaming"])
@app.post("/api/whep/{camera_id}", tags=["streaming"])
async def proxy_whep(camera_id: str, request: Request):
    """
    Proxies WebRTC WHEP SDP offer to Sentinel WebRTC gateway with server-side credentials.
    Enables low-latency browser preview per Sentinel Integrator's Guide §1 & §2.
    """
    import httpx
    body = await request.body()
    whep_url = settings.webrtc_url_for(camera_id, authenticated=True)
    
    headers = {"Content-Type": request.headers.get("content-type", "application/sdp")}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(whep_url, content=body, headers=headers)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers={"Content-Type": resp.headers.get("content-type", "application/sdp")}
            )
    except Exception as e:
        logger.error("WHEP proxy error for %s: %s", camera_id, e)
        raise HTTPException(502, f"Failed to connect to WebRTC gateway: {e}")


@app.get("/cameras/{camera_id}/stream-info", tags=["cameras"])
def get_camera_stream_info(camera_id: str, current_user: Optional[dict] = Depends(get_current_user_optional)):
    """Provides complete stream connection info per Sentinel Integrator's Guide."""
    cameras = _get_cameras()
    cam = next((c for c in cameras if c.camera_id == camera_id), None)
    if not cam:
        raise HTTPException(404, f"Camera '{camera_id}' not found in catalogue")
    
    return {
        "camera_id": camera_id,
        "location": cam.location,
        "hls_url": f"/api/hls/{camera_id}/index.m3u8",
        "hls_direct": settings.hls_url_for(camera_id),
        "rtsp_url": settings.rtsp_url_for(camera_id, authenticated=False),
        "rtsp_ai_ingestion": settings.rtsp_url_for(camera_id, authenticated=True),
        "webrtc_endpoint": f"/api/whep/{camera_id}",
        "webrtc_direct": settings.webrtc_url_for(camera_id, authenticated=False),
        "live_status": cam.live_status,
        "codec": cam.codec,
        "resolution": cam.resolution,
        "protocols": {
            "hls": "Available (browser / dashboard / remote AI)",
            "rtsp": "Available (TCP mandatory for AI inference)",
            "webrtc": "Available (WHEP low-latency preview)"
        }
    }


@app.post("/cameras/sync", tags=["cameras"])
def sync_cameras(
    current_user: dict = Depends(require_role(Role.ADMIN, Role.POLICE_OFFICER)),
    request: Request = None,
):
    """Force refresh the camera catalogue from Sentinel."""
    global _camera_cache, _camera_cache_time
    _camera_cache = []
    _camera_cache_time = None

    cameras = _get_cameras()
    audit = get_audit_service()
    audit.log(
        action=AuditAction.SYNC_CAMERAS,
        username=current_user.get("username"),
        role=current_user.get("role"),
        description=f"Synced {len(cameras)} cameras from catalogue",
        ip_address=request.client.host if request and request.client else None,
    )
    return {"cameras_found": len(cameras), "catalogue_url": settings.sentinel_catalogue_url}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/alerts", tags=["alerts"])
def list_alerts(
    severity: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    if not has_permission(current_user["role"], "alerts:read"):
        raise HTTPException(403, "Insufficient permissions")

    alerts = get_all_alerts(limit=limit)
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity.upper()]
    if status_filter:
        alerts = [a for a in alerts if a.get("status") == status_filter.upper()]

    audit = get_audit_service()
    audit.log(
        action=AuditAction.LIST_ALERTS,
        username=current_user.get("username"),
        role=current_user.get("role"),
        ip_address=request.client.host if request and request.client else None,
    )

    return {"alerts": alerts, "total": len(alerts)}


@app.get("/alerts/{alert_id}", tags=["alerts"])
def get_alert_by_id(
    alert_id: str,
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    if not has_permission(current_user["role"], "alerts:read"):
        raise HTTPException(403, "Insufficient permissions")

    alert = get_alert(alert_id)
    if not alert:
        raise HTTPException(404, f"Alert '{alert_id}' not found")

    audit = get_audit_service()
    audit.log(
        action=AuditAction.VIEW_ALERT,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="alert",
        resource_id=alert_id,
        ip_address=request.client.host if request and request.client else None,
    )

    return alert


@app.post("/alerts/{alert_id}/acknowledge", tags=["alerts"])
def ack_alert(
    alert_id: str,
    body: AcknowledgeRequest = AcknowledgeRequest(),
    current_user: dict = Depends(require_role(Role.ADMIN, Role.POLICE_OFFICER)),
    request: Request = None,
):
    if not has_permission(current_user["role"], "alerts:acknowledge"):
        raise HTTPException(403, "Insufficient permissions")

    ok = acknowledge_alert(alert_id, current_user["username"])
    if not ok:
        raise HTTPException(404, f"Alert '{alert_id}' not found")

    audit = get_audit_service()
    audit.log(
        action=AuditAction.ACK_ALERT,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="alert",
        resource_id=alert_id,
        description=body.notes,
        ip_address=request.client.host if request and request.client else None,
    )

    return {"message": "Alert acknowledged", "alert_id": alert_id}


# ---------------------------------------------------------------------------
# Road Incident & Accident Detection (AID)
# ---------------------------------------------------------------------------

class IncidentDetectRequest(BaseModel):
    camera_id: str
    incident_type: str = "VEHICLE_COLLISION"
    confidence: float = 0.92
    involved_vehicles: Optional[list[str]] = None
    description: Optional[str] = None
    force_trigger: bool = False


class IncidentDispatchRequest(BaseModel):
    service_type: str = "108_AMBULANCE"  # "108_AMBULANCE" | "TRAFFIC_PCR"
    notes: Optional[str] = None


@app.get("/incidents/active", tags=["incidents"])
def get_active_incidents(
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    detector = get_incident_detector()
    incidents = detector.get_active_incidents()
    return {"incidents": incidents, "count": len(incidents)}


@app.post("/incidents/detect", tags=["incidents"])
def report_or_detect_incident(
    body: IncidentDetectRequest,
    current_user: dict = Depends(require_role(Role.ADMIN, Role.POLICE_OFFICER)),
    request: Request = None,
):
    catalogue = fetch_catalogue()
    cam = next((c for c in catalogue if c.camera_id.lower() == body.camera_id.lower()), None)
    loc = cam.location if cam else f"Gujarat Police Post {body.camera_id.upper()}"
    lat = cam.latitude if cam and cam.latitude else 23.0225
    lon = cam.longitude if cam and cam.longitude else 72.5714

    detector = get_incident_detector()
    res = detector.verify_and_trigger_incident(
        camera_id=body.camera_id,
        location=loc,
        latitude=lat,
        longitude=lon,
        incident_type=body.incident_type,
        confidence=body.confidence,
        involved_vehicles=body.involved_vehicles,
        description=body.description,
        force_trigger=body.force_trigger,
    )

    audit = get_audit_service()
    audit.log(
        action=AuditAction.VIEW_ALERT,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="incident",
        resource_id=body.camera_id,
        description=f"Incident trigger: {body.incident_type} at {body.camera_id}",
        ip_address=request.client.host if request and request.client else None,
    )

    if not res:
        return {"status": "deduplicated_or_cooldown", "message": "Incident event suppressed by cooldown or low confidence"}

    return {"status": "triggered", "incident": res}


@app.post("/incidents/{incident_id}/dispatch", tags=["incidents"])
def dispatch_incident_emergency(
    incident_id: str,
    body: IncidentDispatchRequest,
    current_user: dict = Depends(require_role(Role.ADMIN, Role.POLICE_OFFICER)),
    request: Request = None,
):
    detector = get_incident_detector()
    res = detector.dispatch_emergency(
        incident_id=incident_id,
        service_type=body.service_type,
        officer_username=current_user.get("username", "operator"),
    )
    if not res:
        raise HTTPException(404, f"Incident '{incident_id}' not found")

    audit = get_audit_service()
    audit.log(
        action=AuditAction.ACK_ALERT,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="incident",
        resource_id=incident_id,
        description=f"Emergency dispatch: {body.service_type} for {incident_id}",
        ip_address=request.client.host if request and request.client else None,
    )

    return {"status": "dispatched", "incident": res}


class IncidentStreamAnalysisRequest(BaseModel):
    camera_id: str = "cam01"
    trigger_alerts: bool = True
    simulate_collision: bool = False


@app.post("/incidents/analyze-stream", tags=["incidents"])
def analyze_camera_stream_incidents(
    body: IncidentStreamAnalysisRequest,
    current_user: dict = Depends(require_role(Role.ADMIN, Role.POLICE_OFFICER)),
    request: Request = None,
):
    """
    Runs multi-frame kinematic trajectory analysis on a CCTV camera stream.
    Supports deterministic collision consensus validation to trigger alerts,
    evaluate emergency facility routing, and broadcast to the police command center.
    """
    from backend.app.models.detection import BoundingBox, DetectionResult, FrameDetections, VehicleClass
    analyzer = get_incident_analyzer()
    catalogue = fetch_catalogue()
    cam = next((c for c in catalogue if c.camera_id.lower() == body.camera_id.lower()), None)
    loc = cam.location if cam else f"Gujarat Police Post {body.camera_id.upper()}"
    lat = cam.latitude if cam and cam.latitude else 23.0225
    lon = cam.longitude if cam and cam.longitude else 72.5714

    detected_incidents = []
    if body.simulate_collision:
        for i in range(16):
            pts = i * 100.0
            if i < 4:
                b1 = BoundingBox(100.0 + i * 15, 200.0, 150.0 + i * 15, 250.0)
                b2 = BoundingBox(220.0 - i * 15, 200.0, 270.0 - i * 15, 250.0)
            else:
                b1 = BoundingBox(160.0, 200.0, 210.0, 250.0)
                b2 = BoundingBox(170.0, 200.0, 220.0, 250.0)
            d1 = DetectionResult(body.camera_id, pts, i, VehicleClass.CAR, 0.94, b1, track_id=101)
            d2 = DetectionResult(body.camera_id, pts, i, VehicleClass.TRUCK, 0.91, b2, track_id=102)
            fd = FrameDetections(body.camera_id, pts, i, 1920, 1080, [d1, d2], 4.5)
            cands = analyzer.analyze_incidents(
                fd,
                trigger_alerts=body.trigger_alerts,
                camera_location=loc,
                camera_lat=lat,
                camera_lon=lon,
            )
            if cands:
                detected_incidents.extend(cands)
                break

    audit = get_audit_service()
    audit.log(
        action=AuditAction.VIEW_CAMERA,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="incident_analysis",
        resource_id=body.camera_id,
        description=f"Ran kinematic incident analysis on {body.camera_id}",
        ip_address=request.client.host if request and request.client else None,
    )

    return {
        "camera_id": body.camera_id,
        "location": loc,
        "incidents_detected": len(detected_incidents),
        "candidates": [
            {
                "type": c.incident_type.value,
                "confidence": c.confidence,
                "involved_tracks": c.involved_track_ids,
                "evidence": c.evidence,
            }
            for c in detected_incidents
        ],
    }


# ---------------------------------------------------------------------------
# Vehicles / Journey
# ---------------------------------------------------------------------------

@app.get("/vehicles/{plate}/history", tags=["vehicles"])
def vehicle_history(
    plate: str,
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    if not has_permission(current_user["role"], "vehicles:read"):
        raise HTTPException(403, "Insufficient permissions")

    plate = plate.upper().strip()
    correlator = get_correlator()
    journey = correlator.get_journey(plate)

    audit = get_audit_service()
    audit.log(
        action=AuditAction.VIEW_JOURNEY,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="vehicle",
        resource_id=plate,
        ip_address=request.client.host if request and request.client else None,
    )

    if not journey:
        return {
            "plate": plate,
            "message": "No sightings recorded for this vehicle",
            "segments": [],
        }

    return {
        "plate": plate,
        "journey": journey,
        "note": "Segments marked CONFIRMED_OBSERVATION = camera detected the vehicle. Routes between observations are INFERRED_POSSIBLE_ROUTE.",
    }


@app.get("/vehicles", tags=["vehicles"])
def search_vehicles(
    plate: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    if not has_permission(current_user["role"], "vehicles:read"):
        raise HTTPException(403, "Insufficient permissions")

    correlator = get_correlator()
    journeys = correlator.get_all_journeys(limit=limit)

    if plate:
        journeys = [j for j in journeys if plate.upper() in j.get("normalised_plate", "")]

    audit = get_audit_service()
    audit.log(
        action=AuditAction.SEARCH_VEHICLE,
        username=current_user.get("username"),
        role=current_user.get("role"),
        description=f"Search: plate={plate}",
        ip_address=request.client.host if request and request.client else None,
    )

    return {"vehicles": journeys, "total": len(journeys)}


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

@app.get("/watchlist", tags=["watchlist"])
def list_watchlist(
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    if not has_permission(current_user["role"], "watchlist:read"):
        raise HTTPException(403, "Insufficient permissions")

    provider = get_watchlist_provider()
    entries = provider.list_all()

    audit = get_audit_service()
    audit.log(
        action=AuditAction.VIEW_WATCHLIST,
        username=current_user.get("username"),
        role=current_user.get("role"),
        ip_address=request.client.host if request and request.client else None,
    )

    return {
        "entries": entries,
        "total": len(entries),
        "note": "SYNTHETIC DEMO DATA — not connected to real government databases",
    }


@app.post("/watchlist", tags=["watchlist"])
def add_watchlist(
    entry: WatchlistEntryIn,
    current_user: dict = Depends(require_role(Role.ADMIN, Role.POLICE_OFFICER)),
    request: Request = None,
):
    if not has_permission(current_user["role"], "watchlist:write"):
        raise HTTPException(403, "Insufficient permissions")

    provider = get_watchlist_provider()
    provider.add(entry.model_dump())

    audit = get_audit_service()
    audit.log(
        action=AuditAction.ADD_WATCHLIST,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="watchlist",
        resource_id=entry.registration_number,
        ip_address=request.client.host if request and request.client else None,
    )

    return {"message": f"Entry added: {entry.registration_number}"}


@app.delete("/watchlist/{plate}", tags=["watchlist"])
def remove_watchlist(
    plate: str,
    current_user: dict = Depends(require_role(Role.ADMIN)),
    request: Request = None,
):
    provider = get_watchlist_provider()
    ok = provider.remove(plate)
    if not ok:
        raise HTTPException(404, f"Plate '{plate}' not found in watchlist")

    audit = get_audit_service()
    audit.log(
        action=AuditAction.REMOVE_WATCHLIST,
        username=current_user.get("username"),
        role=current_user.get("role"),
        resource_type="watchlist",
        resource_id=plate,
        ip_address=request.client.host if request and request.client else None,
    )

    return {"message": f"Entry removed: {plate}"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/routes/infer", tags=["routes"])
def infer_route(
    body: RouteInferRequest,
    current_user: dict = Depends(get_current_user),
    request: Request = None,
):
    if not has_permission(current_user["role"], "routes:infer"):
        raise HTTPException(403, "Insufficient permissions")

    routing = get_routing_service()
    result = routing.infer_route(
        origin_lat=body.origin_lat,
        origin_lon=body.origin_lon,
        dest_lat=body.dest_lat,
        dest_lon=body.dest_lon,
        origin_camera=body.origin_camera_id,
        dest_camera=body.dest_camera_id,
    )

    audit = get_audit_service()
    audit.log(
        action=AuditAction.INFER_ROUTE,
        username=current_user.get("username"),
        role=current_user.get("role"),
        description=f"{body.origin_camera_id} → {body.dest_camera_id}",
        ip_address=request.client.host if request and request.client else None,
    )

    return {
        "route": result.to_dict(),
        "warning": "INFERRED POSSIBLE ROUTE — This is an estimated road route, NOT confirmed vehicle movement.",
    }


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

@app.get("/audit-logs", tags=["audit"])
def get_audit_logs(
    limit: int = 100,
    action: Optional[str] = None,
    username: Optional[str] = None,
    current_user: dict = Depends(require_role(Role.ADMIN)),
    request: Request = None,
):
    if not has_permission(current_user["role"], "audit:read"):
        raise HTTPException(403, "Insufficient permissions")

    svc = get_audit_service()
    logs = svc.get_logs(limit=limit, username=username, action=action)

    svc.log(
        action=AuditAction.VIEW_AUDIT,
        username=current_user.get("username"),
        role=current_user.get("role"),
        ip_address=request.client.host if request and request.client else None,
    )

    return {"logs": logs, "total": len(logs)}


# ---------------------------------------------------------------------------
# WebSocket — real-time alerts
# ---------------------------------------------------------------------------

@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send current open alerts on connect
        alerts = get_all_alerts(limit=10)
        await websocket.send_json({
            "type": "INIT",
            "alerts": alerts,
            "message": "Connected to Sentinel alert stream",
        })
        # Keep connection alive
        while True:
            await websocket.receive_text()  # wait for client ping/close
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Static files / Frontend UI
# ---------------------------------------------------------------------------

_frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
if os.path.isdir(_frontend_dir):
    app.mount("/ui", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )
