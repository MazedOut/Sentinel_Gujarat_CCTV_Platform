# Sentinel Gujarat — MCP Context Handoff Document

> **Purpose:** This document is the authoritative context handoff for any MCP controller, AI agent, or new developer taking over work on the Sentinel Gujarat platform. It covers everything from architecture to integration rules to internal design decisions, with enough detail to pick up any task without additional briefing.

---

## 1. Project Identity

| Field | Value |
|---|---|
| **Project name** | Sentinel Gujarat CCTV Intelligence Platform |
| **Hackathon** | Gujarat Police Innovation Challenge 2026 |
| **Challenge** | Sentinel Gujarat CCTV Integration Challenge |
| **Team type** | Student hackathon team |
| **Current milestone** | All milestones implemented (M1→M8 PoC complete) |
| **Tech stack** | Python 3.13, FastAPI, YOLOv8/YOLO26, PaddleOCR, PostgreSQL/PostGIS, Vanilla JS frontend |

---

## 2. Problem Statement

Gujarat Police operates a distributed CCTV network managed through the **Sentinel Gujarat Portal**. The challenge is to build an **interoperability intelligence layer** that:

1. **Consumes** live RTSP/HLS/WebRTC camera feeds from the Sentinel sandbox
2. **Detects** vehicles in real-time using AI (YOLO object detection)
3. **Reads** license plates (ANPR — Automatic Number Plate Recognition) using OCR
4. **Matches** detected plates against a watchlist (VAHAN/SARTHI/eGujCop databases in production, synthetic demo data for PoC)
5. **Correlates** multi-camera sightings to reconstruct vehicle journeys across Gujarat
6. **Alerts** police officers in real-time via WebSocket when watchlisted vehicles are detected
7. **Provides** a GIS-enabled dashboard for officers and administrators

---

## 3. Official Sentinel Infrastructure (Integrator's Guide)

These are the **official, immutable** endpoints for the Sentinel sandbox. Never hardcode camera URLs — always fetch from the catalogue first.

| Service | URL / Details |
|---|---|
| **Camera Catalogue** | `https://cctv.corp8.cloud/cameras.json` — requires Cookie auth |
| **RTSP streams** | `rtsp://103.250.160.189:8554/stream/<camera_id>` — no auth in sandbox |
| **WebRTC/WHEP** | `http://103.250.160.189:8889/stream/<camera_id>/whep` — no auth in sandbox |
| **HLS streams** | `https://cctv.corp8.cloud/<camera_id>/index.m3u8` — password-protected |
| **HLS enc key** | `https://cctv.corp8.cloud/<camera_id>/enc.key` — AES-128 decryption key |

### Non-negotiable integration rules (from Integrator's Guide):
- **RTSP must use TCP** — `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`
- **Use PTS, not wall clock** — `CAP_PROP_POS_MSEC` is the authoritative timestamp
- **Never trust `CAP_PROP_FPS`** — stored for display only, never used for timing
- **Always start from `/cameras.json`** — never hardcode RTSP URLs
- **Reconnect with exponential backoff** — 2s → 4s → 8s → 16s → 30s max
- **Consume only** — no pushing/uploading back to Sentinel gateway

---

## 4. Repository Structure

```
sentinel-gujarat/
├── backend/                         ← Python FastAPI backend
│   ├── __init__.py
│   └── app/
│       ├── __init__.py
│       ├── main.py                  ← FastAPI app — all routes + WebSocket
│       ├── core/
│       │   ├── config.py            ← All settings via pydantic-settings (reads .env)
│       │   ├── security.py          ← JWT auth, RBAC, password hashing
│       │   └── logging_config.py    ← Structured logging setup
│       ├── db/
│       │   ├── session.py           ← SQLAlchemy session factory + get_db dependency
│       │   └── base.py              ← SQLAlchemy Base declarative
│       ├── models/                  ← Pydantic schemas + SQLAlchemy ORM models
│       │   ├── camera.py            ← CameraInfo Pydantic model
│       │   ├── detection.py         ← FrameDetections, DetectionResult, BoundingBox
│       │   ├── anpr.py              ← ANPRResult, PlateDetection, OCRReading
│       │   └── db/                  ← SQLAlchemy ORM models for PostgreSQL
│       │       ├── camera.py        ← Camera ORM
│       │       ├── detection_event.py ← DetectionEvent ORM
│       │       ├── vehicle_journey.py ← VehicleJourney ORM
│       │       ├── alert.py         ← Alert ORM
│       │       ├── watchlist.py     ← WatchlistEntry ORM
│       │       ├── user.py          ← User ORM
│       │       └── audit_log.py     ← AuditLog ORM
│       └── services/
│           ├── camera/
│           │   └── catalogue.py     ← Fetches + parses cameras.json from Sentinel CDN
│           ├── streaming/
│           │   ├── stream_manager.py ← RTSP/TCP ingestion, PTS timing, backoff
│           │   └── hls_proxy.py     ← Server-side HLS proxy (rewrites playlists, proxies keys/segments)
│           ├── detection/
│           │   ├── vehicle_detector.py ← YOLO26 vehicle detection (ByteTrack multi-object tracking)
│           │   └── visualiser.py    ← Debug frame drawing (bboxes, labels)
│           ├── anpr/
│           │   ├── anpr_pipeline.py ← Full ANPR orchestrator (plate detect → OCR → score)
│           │   ├── ocr_engine.py    ← PaddleOCR wrapper + Indian plate normalisation
│           │   └── confidence_scorer.py ← Multi-factor confidence scoring
│           ├── alerting/
│           │   ├── alert_engine.py  ← Alert creation, storage, retrieval, acknowledgement
│           │   ├── watchlist_service.py ← SyntheticWatchlistProvider (PoC demo data)
│           │   └── watchlist_matcher.py ← Plate → watchlist matching logic
│           ├── tracking/
│           │   └── journey_correlator.py ← Multi-camera vehicle journey reconstruction
│           ├── routing/
│           │   └── routing_service.py ← Inferred route between two cameras via Google Maps
│           └── audit/
│               └── audit_service.py ← Immutable audit log for all sensitive operations
├── frontend/                        ← Vanilla HTML/JS/CSS dashboard (served at /ui)
│   ├── index.html                   ← Single-page app shell
│   ├── app.js                       ← Dashboard logic (cameras, alerts, map, HLS playback)
│   ├── style.css                    ← Dark-mode police dashboard styles
│   └── hls.min.js                   ← hls.js for in-browser HLS video playback
├── ai/
│   └── models/                      ← YOLO model weight files (.pt)
│       ├── yolo26n.pt               ← Primary vehicle detector (YOLO26 nano)
│       ├── plate_detector.pt        ← Custom plate detection model
│       ├── yolov8n.pt               ← Fallback / alternative
│       ├── yolov9c.pt               ← Fallback / alternative
│       ├── yolov10n.pt              ← Fallback / alternative
│       └── yolo11n.pt               ← Fallback / alternative
├── scripts/                         ← CLI scripts for testing and diagnostics
│   ├── discover_cameras.py          ← Prints full camera catalogue table
│   ├── test_rtsp.py                 ← Interactive RTSP stream test
│   ├── test_detection.py            ← YOLO detection on live stream
│   ├── test_anpr.py                 ← End-to-end ANPR pipeline test
│   ├── test_yolo_smoke.py           ← YOLO GPU smoke test (no camera needed)
│   ├── start_stream.py              ← Start stream ingestion manually
│   └── sentinel_m1_diagnostic.py   ← Full M1/M2 diagnostic suite (26KB)
├── docs/
│   └── context_handoff.md           ← This file
├── backend/alembic/                 ← Alembic DB migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── .env                             ← Local secrets (never committed)
├── .env.example                     ← Template (safe to commit)
├── .gitignore
├── alembic.ini                      ← Alembic config (points to backend/alembic)
├── docker-compose.yml               ← PostgreSQL/PostGIS container
├── requirements.txt                 ← Python dependencies
└── README.md                        ← User-facing documentation with Mermaid diagrams
```

---

## 5. Architecture

### 5.1 High-Level System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         SENTINEL GUJARAT PLATFORM                            │
│                                                                              │
│  ┌─────────────────────┐     ┌──────────────────────────────────────────┐   │
│  │  Sentinel Sandbox   │     │           FastAPI Backend                │   │
│  │  (External Infra)   │     │          backend/app/main.py             │   │
│  │                     │     │                                          │   │
│  │  cameras.json  ─────┼────▶│  /cameras      CameraService            │   │
│  │  RTSP :8554    ─────┼────▶│  StreamManager → VehicleDetector        │   │
│  │  HLS CDN       ─────┼────▶│  HLSProxy → /api/hls/{cam}/index.m3u8  │   │
│  │  WebRTC :8889  ─────┼────▶│  /cameras/{id}/whep (pass-through)      │   │
│  └─────────────────────┘     │                                          │   │
│                              │  ANPRPipeline → AlertEngine             │   │
│                              │  JourneyCorrelator → RoutingService     │   │
│                              │  AuditService  WatchlistService         │   │
│                              │                                          │   │
│                              │  WebSocket /ws/alerts  ─────────────────┼───▶│  Browser
│                              │  REST API /docs         ─────────────────┼───▶│  Dashboard
│                              └──────────────────────────────────────────┘   │  /ui
│                                            │                                │
│                              ┌─────────────▼──────────┐                    │
│                              │  PostgreSQL + PostGIS   │                    │
│                              │  (docker-compose.yml)   │                    │
│                              └────────────────────────┘                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 AI Processing Pipeline (per camera frame)

```
RTSP Frame (BGR numpy array + PTS)
         │
         ▼
  VehicleDetector.detect()          ← YOLO26 + ByteTrack multi-object tracking
  [Runs on every Nth frame]         ← frame_skip=3 → ~10 detections/sec at 30fps
         │
         ├─── No vehicles detected → discard frame
         │
         └─── For each DetectionResult (vehicle bbox, track_id, confidence):
                      │
                      ▼
              ANPRPipeline.process()
                1. Locate plate region:
                   a) YOLO plate detector (if plate_detector.pt loaded)
                   b) Heuristic: bottom 35% × central 70% of vehicle bbox
                2. PaddleOCR → raw text
                3. Normalise → GJ-01-AB-1234 format
                4. ConfidenceScorer → final_confidence (0.0–1.0)
                5. Track cache: skip re-OCR if same track_id already has plate
                      │
                      ▼
              WatchlistMatcher.match()
                - Compare normalised_plate vs watchlist entries
                - Returns CLEAR / SURVEILLANCE / BOLO / RESTRICTED
                      │
                      ▼
              AlertEngine.evaluate()
                - final_confidence ≥ 0.85 → HIGH severity → auto-alert
                - final_confidence ≥ 0.65 → MEDIUM → queue for review
                - below → store detection event, no alert
                      │
                      ▼
              JourneyCorrelator.record_sighting()
                - Appends (camera_id, location, timestamp, plate) to vehicle journey
                - CONFIRMED_OBSERVATION segments
                      │
                      ▼
              WebSocket broadcast → /ws/alerts
              AuditService.log() → immutable audit trail
```

### 5.3 HLS Proxy Flow

```
Browser requests /api/hls/{camera_id}/index.m3u8
         │
         ▼
hls_proxy.get_playlist()
  - Fetches https://cctv.corp8.cloud/{camera_id}/index.m3u8
  - Injects server-side Cookie (SENTINEL_CATALOGUE_COOKIE / SENTINEL_HLS_COOKIE)
  - Rewrites .ts segment URLs → /api/hls/{camera_id}/{segment}.ts
  - Rewrites enc.key URL → /api/hls/{camera_id}/enc.key
  - Returns rewritten .m3u8 to browser (zero client-side credentials exposed)

Browser requests /api/hls/{camera_id}/{segment}.ts
  - hls_proxy.stream_segment() → proxies raw MPEG-TS bytes via StreamingResponse

Browser requests /api/hls/{camera_id}/enc.key
  - hls_proxy.get_key() → proxies AES-128 key bytes
```

---

## 6. API Reference

All endpoints are on `http://localhost:8000` by default. Interactive docs at `/docs`.

### Auth
| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | None | Create user account |
| `POST` | `/auth/login` | None (form) | OAuth2 password flow → JWT |
| `GET` | `/auth/me` | JWT | Current user info |

**Default accounts (in-memory):**
- `admin` / `sentinel_admin` — Role: `ADMIN`
- `officer1` / `sentinel_officer` — Role: `POLICE_OFFICER`

### Cameras
| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/cameras` | Optional | List all cameras (from catalogue cache) |
| `GET` | `/cameras/{camera_id}` | Required | Single camera detail + stream URLs |
| `POST` | `/cameras/sync` | ADMIN/OFFICER | Force refresh catalogue from Sentinel |
| `GET` | `/api/hls/{cam}/index.m3u8` | None | Proxied HLS playlist |
| `GET` | `/api/hls/{cam}/{segment}` | None | Proxied MPEG-TS segment |
| `GET` | `/api/hls/{cam}/enc.key` | None | Proxied AES-128 decryption key |
| `GET` | `/api/config/maps` | None | Google Maps API key config |

### Alerts & Vehicles
| Method | Path | Auth | Roles | Description |
|---|---|---|---|---|
| `GET` | `/alerts` | Required | All | List alerts (filterable by severity/status) |
| `GET` | `/alerts/{id}` | Required | All | Single alert detail |
| `POST` | `/alerts/{id}/acknowledge` | Required | ADMIN/OFFICER | Acknowledge an alert |
| `GET` | `/vehicles` | Required | All | Search all tracked vehicles |
| `GET` | `/vehicles/{plate}/history` | Required | All | Full journey for one plate |
| `WS` | `/ws/alerts` | None | N/A | Real-time alert stream |

### Watchlist & Routes
| Method | Path | Auth | Roles | Description |
|---|---|---|---|---|
| `GET` | `/watchlist` | Required | All | List watchlist entries |
| `POST` | `/watchlist` | Required | ADMIN/OFFICER | Add entry |
| `DELETE` | `/watchlist/{plate}` | Required | ADMIN | Remove entry |
| `POST` | `/routes/infer` | Required | All | Infer road route between two cameras |
| `GET` | `/audit-logs` | Required | ADMIN only | Immutable audit log |

### RBAC Permissions

| Permission | ADMIN | POLICE_OFFICER | DEPARTMENT_USER | ANALYST | VIEWER |
|---|---|---|---|---|---|
| `alerts:read` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `alerts:acknowledge` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `vehicles:read` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `watchlist:read` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `watchlist:write` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `routes:infer` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `audit:read` | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## 7. Environment Variables (`.env`)

Copy `.env.example` to `.env` and set:

```env
# ── Sentinel Infrastructure ──────────────────────────────────
SENTINEL_CATALOGUE_URL=https://cctv.corp8.cloud/cameras.json
SENTINEL_CATALOGUE_COOKIE=sentinel=<your_session_cookie_from_browser>

SENTINEL_RTSP_HOST=103.250.160.189
SENTINEL_RTSP_PORT=8554
SENTINEL_RTSP_EMAIL=<your_sentinel_email>
SENTINEL_RTSP_PASSWORD=<your_sentinel_password>

SENTINEL_WEBRTC_HOST=103.250.160.189
SENTINEL_WEBRTC_PORT=8889

SENTINEL_HLS_HOST=cctv.corp8.cloud
SENTINEL_HLS_COOKIE=<optional_if_different_from_catalogue_cookie>

# ── Database (PostgreSQL / PostGIS) ──────────────────────────
POSTGRES_USER=sentinel
POSTGRES_PASSWORD=sentinelpassword
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=sentinel_registry
# Or use DATABASE_URL directly:
# DATABASE_URL=postgresql://sentinel:sentinelpassword@localhost:5432/sentinel_registry

# ── AI / Detection ───────────────────────────────────────────
YOLO_MODEL=ai/models/yolo26n.pt
PLATE_DETECTOR_MODEL=ai/models/plate_detector.pt
YOLO_CONFIDENCE_THRESHOLD=0.35
YOLO_FRAME_SKIP=3

# ── ANPR ─────────────────────────────────────────────────────
ANPR_CONFIDENCE_THRESHOLD=0.50
ANPR_SAVE_EVIDENCE_FRAMES=false
ANPR_EVIDENCE_DIR=evidence

# ── Alerts ───────────────────────────────────────────────────
ALERT_HIGH_CONFIDENCE=0.85
ALERT_MEDIUM_CONFIDENCE=0.65

# ── Security ─────────────────────────────────────────────────
JWT_SECRET=change_me_to_a_long_random_string_in_production
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# ── Google Maps ──────────────────────────────────────────────
GOOGLE_MAPS_API_KEY=<your_google_maps_api_key>

# ── Logging ──────────────────────────────────────────────────
LOG_LEVEL=INFO

# ── CORS ─────────────────────────────────────────────────────
CORS_ORIGINS=http://localhost:3000,http://localhost:8080
```

---

## 8. Service Deep-Dives

### 8.1 CatalogueService (`services/camera/catalogue.py`)
- Fetches `cameras.json` from `SENTINEL_CATALOGUE_URL`
- Uses `SENTINEL_CATALOGUE_COOKIE` as the HTTP `Cookie` header for auth
- Detects when the CDN returns an HTML login page instead of JSON (common with session expiry)
- Parses camera objects into `CameraInfo` Pydantic models
- Exposed via `fetch_catalogue()` — cached in memory for 5 minutes in `main.py`

### 8.2 StreamManager (`services/streaming/stream_manager.py`)
- Opens RTSP streams via OpenCV VideoCapture with forced TCP transport
- Reads PTS via `CAP_PROP_POS_MSEC` — not wall clock
- Emits `FrameData` objects: `{frame, camera_id, pts_ms, frame_index, width, height}`
- Reconnects on failure with exponential backoff
- Detects scene discontinuity (PTS backward jump at loop points)
- Thread-safe: runs frame loop in background thread, calls user callback per frame
- Handles pre-IDR decoder join warnings without crashing

### 8.3 VehicleDetector (`services/detection/vehicle_detector.py`)
- Wraps YOLO26 (Ultralytics) with ByteTrack multi-object tracking
- Frame skipping: processes every Nth frame (configurable, default=3)
- Auto-detects CUDA (RTX 4060) — falls back to CPU
- Warm-up: runs 2 dummy inferences on load to trigger JIT compilation
- COCO class filter: only car(2), motorcycle(3), bus(5), truck(7)
- Returns `FrameDetections` with list of `DetectionResult` (bbox + track_id + confidence)

### 8.4 ANPRPipeline (`services/anpr/anpr_pipeline.py`)
- **Stage 1 — Plate location:**
  - If `plate_detector.pt` loaded: YOLO plate detector on vehicle crop
  - Fallback: heuristic — bottom 35% × central 70% of vehicle bbox
- **Stage 2 — OCR:** PaddleOCR (via `ocr_engine.py`)
  - Preprocesses plate crop (grayscale, threshold, resize)
  - Normalises Indian plate format: `GJ01AB1234`
  - Validates against regex patterns for Indian RTO format
- **Stage 3 — Confidence scoring:**
  - Plate detection confidence (YOLO: real conf, heuristic: 0.60 fixed)
  - OCR confidence (from PaddleOCR)
  - Format validity bonus/penalty
  - Length validity check
  - Final score: weighted combination
- **Track caching:** Once a track_id has a high-confidence plate (≥0.65), reuses it without re-running OCR — major GPU savings for stationary/slow vehicles

### 8.5 AlertEngine (`services/alerting/alert_engine.py`)
- In-memory alert store (deque with maxlen=1000)
- Alert creation: `create_alert(detection, watchlist_status, camera_info)`
- Severity tiers: `HIGH` (≥0.85), `MEDIUM` (≥0.65)
- Alert status: `OPEN` → `ACKNOWLEDGED`
- WebSocket callback: registered from `main.py`, broadcast on new alert
- `get_all_alerts(limit)`, `get_alert(id)`, `acknowledge_alert(id, username)`

### 8.6 WatchlistService (`services/alerting/watchlist_service.py`)
- `SyntheticWatchlistProvider` — in-memory demo data for PoC
- 15+ synthetic entries with realistic Gujarat plates
- Statuses: `SURVEILLANCE`, `BOLO` (Be On Look Out), `RESTRICTED`, `CLEAR`
- Priority: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
- Ready for drop-in replacement with real VAHAN/SARTHI/eGujCop API once authorized

### 8.7 JourneyCorrelator (`services/tracking/journey_correlator.py`)
- Maintains per-plate sighting history across cameras
- Sighting: `{camera_id, location, lat, lon, pts_ms, timestamp, confidence}`
- `get_journey(plate)` → ordered sightings list
- Segment types: `CONFIRMED_OBSERVATION` (real camera detection) vs `INFERRED_POSSIBLE_ROUTE` (routing between cameras)

### 8.8 RoutingService (`services/routing/routing_service.py`)
- Infers road routes between two camera GIS coordinates
- Uses Google Maps Directions API (if `GOOGLE_MAPS_API_KEY` set)
- Fallback: straight-line distance estimate
- Returns `RouteResult` with segments, distance, duration
- **Labeled as INFERRED POSSIBLE ROUTE** — never claimed as confirmed vehicle movement

### 8.9 AuditService (`services/audit/audit_service.py`)
- Immutable audit log for all sensitive operations (login, camera view, alert ack, watchlist changes)
- Actions: `LOGIN`, `LOGIN_FAILED`, `LIST_CAMERAS`, `VIEW_CAMERA`, `SYNC_CAMERAS`, `LIST_ALERTS`, `VIEW_ALERT`, `ACK_ALERT`, `VIEW_JOURNEY`, `SEARCH_VEHICLE`, `ADD_WATCHLIST`, `REMOVE_WATCHLIST`, `VIEW_WATCHLIST`, `INFER_ROUTE`, `CREATE_USER`, `VIEW_AUDIT`
- Records: `action`, `username`, `user_id`, `role`, `resource_type`, `resource_id`, `ip_address`, `outcome`, `description`, `timestamp`

### 8.10 HLS Proxy (`services/streaming/hls_proxy.py`)
- Server-side proxy for HLS streams — exposes them to browser without client-side credentials
- `get_playlist(camera_id)`: fetches + rewrites `.m3u8` manifest
- `stream_segment(camera_id, segment_name)`: async streaming of MPEG-TS segments
- `get_key(camera_id)`: proxies AES-128 encryption key
- Uses `httpx.AsyncClient` with Cookie injection

---

## 9. Database Schema (PostgreSQL + PostGIS)

Tables managed via SQLAlchemy ORM + Alembic migrations:

| Table | Key Columns | Purpose |
|---|---|---|
| `cameras` | `id`, `camera_id`, `location`, `lat`, `lon`, `codec`, `resolution`, `live_status` | Persistent camera registry |
| `detection_events` | `id`, `camera_id`, `pts_ms`, `normalised_plate`, `vehicle_class`, `confidence`, `bbox_json` | Every detection stored |
| `vehicle_journeys` | `id`, `normalised_plate`, `sightings_json` | Aggregated journey per plate |
| `alerts` | `id`, `camera_id`, `plate`, `severity`, `status`, `acknowledged_by`, `acknowledged_at` | Alert lifecycle |
| `watchlist_entries` | `id`, `registration_number`, `status`, `priority`, `person_name` | Watchlist (demo in PoC) |
| `users` | `id`, `username`, `hashed_password`, `role`, `department`, `is_active` | User accounts (ORM ready, currently in-memory) |
| `audit_logs` | `id`, `action`, `username`, `role`, `resource_type`, `resource_id`, `ip_address`, `timestamp` | Immutable audit trail |

**Note:** The current PoC runs most data **in-memory** for simplicity (users, alerts, watchlist, journeys). SQLAlchemy ORM models are defined and Alembic migrations are configured for when PostgreSQL is used in production. The database is **optional** — the API works without it via `get_db_optional`.

---

## 10. Running the Platform

### Prerequisites

```powershell
# 1. Clone and enter project
cd sentinel-gujarat

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install PyTorch with CUDA (RTX 4060 / CUDA 12.x)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# 5. Configure environment
copy .env.example .env
# Edit .env with your Sentinel credentials
```

### Start Services

```powershell
# Start PostgreSQL (optional — API works without it)
docker-compose up -d

# Start FastAPI backend
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# Visit dashboard
# http://localhost:8000/ui
# API docs: http://localhost:8000/docs
```

### Key Scripts

```powershell
# Discover all cameras from Sentinel catalogue
python -m scripts.discover_cameras

# Test RTSP stream ingestion
python -m scripts.test_rtsp

# Test vehicle detection on live stream
python -m scripts.test_detection

# Test end-to-end ANPR
python -m scripts.test_anpr

# Full M1/M2 diagnostic suite
python scripts/sentinel_m1_diagnostic.py
```

---

## 11. Design Decisions & Gotchas

### Why YOLO26 instead of YOLOv8?
YOLO26 (custom high-efficiency variant) is used as the primary detector. Multiple `.pt` weights are kept in `ai/models/` for benchmarking. Switch via `YOLO_MODEL` env var.

### Why in-memory users/alerts for PoC?
PostgreSQL is fully configured but optional. Running without a DB allows judges to demo the platform without Docker. Production would use full PostgreSQL persistence.

### Why server-side HLS proxy?
The Sentinel HLS CDN requires a session cookie. Exposing this cookie to the browser would allow any user to download the raw stream. The proxy keeps credentials server-side — the browser never sees them.

### Why PTS instead of wall clock?
Per Sentinel Integrator's Guide. PTS is embedded in the video stream and represents the capture time, not the receive time. Wall clock introduces variable network delay errors.

### Track caching in ANPR
Once a vehicle's track_id has a reliable plate reading (confidence ≥ 0.65), that reading is cached and reused for all subsequent frames of the same vehicle. This eliminates repeated expensive OCR calls for the same car stopped at a junction.

### Synthetic watchlist
The `SyntheticWatchlistProvider` returns demo Gujarat plates with realistic statuses. It is clearly segregated from real law enforcement data and labeled as demo/PoC throughout all API responses.

### Frame skip default = 3
At 30fps RTSP streams, frame_skip=3 gives ~10 detections/second — more than sufficient for vehicle tracking. Running YOLO on every frame would waste GPU compute and provide no meaningful accuracy improvement for tracking.

---

## 12. Milestone Status

| Milestone | Status | Deliverable |
|---|---|---|
| **M1** | ✅ Complete | Camera catalogue consumption + RTSP/TCP ingestion |
| **M2** | ✅ Complete | Vehicle detection (YOLO26 + ByteTrack, GPU-accelerated) |
| **M3** | ✅ Complete | ANPR (PaddleOCR + custom plate detector, Indian format normalisation) |
| **M4** | ✅ Complete | PostgreSQL/PostGIS camera registry (ORM + Alembic) |
| **M5** | ✅ Complete | Watchlist matching + alert engine + RBAC |
| **M6** | ✅ Complete | Multi-camera journey correlation + inferred routing |
| **M7** | ✅ Complete | FastAPI backend + JWT auth + audit log |
| **M8** | ✅ Complete | Dashboard UI (GIS map, HLS live video, alert panel, vehicle search) |

---

## 13. Security Notes

- **`.env` is never committed** — protected by `.gitignore`
- **Credentials are redacted** before logging (see `redact_credentials()` in `config.py`)
- **JWT tokens** expire in 60 minutes by default
- **RBAC** enforced at endpoint level via `require_role()` dependency
- **Audit log** records all sensitive operations with IP address
- **Watchlist** labeled `SYNTHETIC DEMO DATA` in all API responses — no real government databases accessed
- **HLS proxy** ensures RTSP/HLS credentials never reach the browser

---

## 14. Next Steps / Open Tasks

- [ ] Connect PostgreSQL fully — migrate `_users` dict and alert store to ORM
- [ ] Replace `SyntheticWatchlistProvider` with real VAHAN/SARTHI API integration (requires government authorization)
- [ ] Add Alembic migration runner to startup lifespan
- [ ] WebRTC WHEP relay for ultra-low-latency live view
- [ ] Multi-camera simultaneous ingestion (currently one stream at a time in scripts)
- [ ] Production deployment: Nginx + Gunicorn + SSL termination
- [ ] Rate limiting on all endpoints
- [ ] HTTPS enforcement
