# Sentinel Gujarat — CCTV Intelligence Platform

<div align="center">

**Gujarat Police Innovation Challenge 2026**

*An interoperable AI intelligence layer that converts live CCTV feeds into actionable, searchable security intelligence.*

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![YOLO](https://img.shields.io/badge/YOLO26-GPU_Accelerated-FF6B35?style=flat)](https://ultralytics.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-PostGIS-336791?style=flat&logo=postgresql&logoColor=white)](https://postgis.net)

</div>

---

## What This Is

A complete, working proof-of-concept for the **Sentinel Gujarat CCTV Integration Challenge**. It ingests live RTSP camera feeds from Gujarat Police's Sentinel sandbox, runs real-time vehicle detection and license plate recognition, matches plates against a watchlist, correlates multi-camera sightings into vehicle journeys, and delivers real-time alerts to police officers via a live dashboard.

---

## System Architecture

```mermaid
flowchart TD
    subgraph SENTINEL["Sentinel Sandbox (External)"]
        CAT["cameras.json\ncctv.corp8.cloud"]
        RTSP["RTSP Streams\n103.250.160.189:8554"]
        HLS["HLS CDN\ncctv.corp8.cloud"]
        WEBRTC["WebRTC/WHEP\n103.250.160.189:8889"]
    end

    subgraph BACKEND["FastAPI Backend (backend/app/main.py)"]
        CAT_SVC["CatalogueService\n/cameras"]
        STREAM["StreamManager\nRTSP + TCP + PTS"]
        HLS_PROXY["HLS Proxy\n/api/hls/{cam}"]
        API["REST API\n+ WebSocket /ws/alerts"]
        AUDIT["AuditService\nImmutable log"]
    end

    subgraph AI["AI Pipeline"]
        YOLO["VehicleDetector\nYOLO26 + ByteTrack"]
        ANPR["ANPRPipeline\nPlate Detect + PaddleOCR"]
        SCORE["ConfidenceScorer\nMulti-factor scoring"]
    end

    subgraph INTEL["Intelligence Layer"]
        WATCH["WatchlistMatcher\nSurveillance / BOLO"]
        ALERT["AlertEngine\nHIGH / MEDIUM severity"]
        JOURNEY["JourneyCorrelator\nMulti-camera tracking"]
        ROUTE["RoutingService\nGoogle Maps inferred route"]
    end

    subgraph STORE["Storage"]
        PG[("PostgreSQL\n+ PostGIS")]
        MEM["In-Memory\nAlerts / Journeys"]
    end

    subgraph FRONTEND["Dashboard (/ui)"]
        MAP["GIS Camera Map\nGoogle Maps"]
        VIDEO["HLS Live Video\nhls.js player"]
        ALERTS_UI["Alert Panel\nReal-time WebSocket"]
        SEARCH["Vehicle Search\nJourney playback"]
    end

    CAT -->|"cameras.json (Cookie auth)"| CAT_SVC
    RTSP -->|"TCP stream"| STREAM
    HLS -->|"Proxied"| HLS_PROXY
    STREAM --> YOLO
    YOLO --> ANPR
    ANPR --> SCORE
    SCORE --> WATCH
    WATCH --> ALERT
    ALERT --> JOURNEY
    JOURNEY --> ROUTE
    ALERT -->|"WebSocket broadcast"| API
    API --> FRONTEND
    HLS_PROXY --> VIDEO
    CAT_SVC --> MAP
    ALERT --> ALERTS_UI
    JOURNEY --> SEARCH
    API <--> PG
    ALERT --> MEM
    JOURNEY --> MEM
    API --> AUDIT
```

---

## AI Processing Pipeline

```mermaid
sequenceDiagram
    participant SM as StreamManager
    participant VD as VehicleDetector<br/>(YOLO26 + ByteTrack)
    participant AP as ANPRPipeline<br/>(PaddleOCR)
    participant WM as WatchlistMatcher
    participant AE as AlertEngine
    participant WS as WebSocket<br/>(/ws/alerts)

    SM->>VD: FrameData {frame, pts_ms, camera_id}
    Note over VD: Skip if frame_index % 3 ≠ 0
    VD-->>VD: YOLO26 inference + ByteTrack
    VD->>AP: DetectionResult[] {bbox, track_id, confidence}
    
    loop For each detected vehicle
        AP-->>AP: Locate plate region (YOLO or heuristic)
        AP-->>AP: PaddleOCR → normalise → GJ01AB1234
        AP-->>AP: Score confidence (plate_conf × ocr_conf × format_bonus)
        AP->>WM: ANPRResult {normalised_plate, final_confidence}
        WM-->>WM: Match vs watchlist
        WM->>AE: {plate, status: SURVEILLANCE/BOLO/CLEAR}
        
        alt confidence ≥ 0.85
            AE-->>AE: Create HIGH severity alert
            AE->>WS: Broadcast NEW_ALERT
        else confidence ≥ 0.65
            AE-->>AE: Create MEDIUM severity alert
            AE->>WS: Broadcast NEW_ALERT
        else below threshold
            AE-->>AE: Store detection event only
        end
    end
```

---

## Project Structure

```
sentinel-gujarat/
├── backend/                    ← Python FastAPI backend
│   └── app/
│       ├── main.py             ← All API routes + WebSocket
│       ├── core/               ← config.py · security.py · logging
│       ├── db/                 ← SQLAlchemy session + base
│       ├── models/             ← Pydantic + ORM models
│       └── services/
│           ├── camera/         ← catalogue.py (cameras.json fetcher)
│           ├── streaming/      ← stream_manager.py · hls_proxy.py
│           ├── detection/      ← vehicle_detector.py (YOLO26)
│           ├── anpr/           ← anpr_pipeline.py · ocr_engine.py
│           ├── alerting/       ← alert_engine.py · watchlist_service.py
│           ├── tracking/       ← journey_correlator.py
│           ├── routing/        ← routing_service.py (Google Maps)
│           └── audit/          ← audit_service.py
├── frontend/                   ← Vanilla JS dashboard (served at /ui)
│   ├── index.html · app.js · style.css · hls.min.js
├── ai/
│   └── models/                 ← YOLO .pt weight files
│       ├── yolo26n.pt          ← Primary vehicle detector
│       └── plate_detector.pt   ← Custom plate detection model
├── scripts/                    ← CLI testing tools
│   ├── discover_cameras.py     ← Print camera catalogue
│   ├── test_rtsp.py            ← Test RTSP ingestion
│   ├── test_detection.py       ← Test YOLO on live stream
│   └── test_anpr.py            ← End-to-end ANPR test
├── docs/
│   └── context_handoff.md      ← Full MCP/agent context document
├── .env.example                ← Config template
├── docker-compose.yml          ← PostgreSQL + PostGIS
├── alembic.ini                 ← DB migration config
└── requirements.txt
```

---

## Quick Start

### 1 — Setup

```powershell
# Create & activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install Python dependencies
pip install -r requirements.txt

# Install PyTorch with CUDA (RTX 4060 / CUDA 12.x)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# Configure environment
copy .env.example .env
# Open .env and set SENTINEL_CATALOGUE_COOKIE, SENTINEL_RTSP_EMAIL/PASSWORD
```

### 2 — Start PostgreSQL (optional)

```powershell
docker-compose up -d
```

### 3 — Launch the API

```powershell
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | What |
|---|---|
| `http://localhost:8000/ui` | Live dashboard |
| `http://localhost:8000/docs` | Interactive API docs (Swagger) |
| `http://localhost:8000/health` | Health check |

### 4 — Default Login Credentials

| Username | Password | Role |
|---|---|---|
| `admin` | `sentinel_admin` | Administrator |
| `officer1` | `sentinel_officer` | Police Officer |

---

## API Summary

```mermaid
flowchart LR
    subgraph AUTH["🔐 Auth"]
        A1["POST /auth/login"]
        A2["POST /auth/register"]
        A3["GET  /auth/me"]
    end

    subgraph CAMERAS["📷 Cameras"]
        C1["GET  /cameras"]
        C2["GET  /cameras/{id}"]
        C3["POST /cameras/sync"]
        C4["GET  /api/hls/{cam}/index.m3u8"]
    end

    subgraph ALERTS["🚨 Alerts"]
        AL1["GET  /alerts"]
        AL2["GET  /alerts/{id}"]
        AL3["POST /alerts/{id}/acknowledge"]
        AL4["WS   /ws/alerts"]
    end

    subgraph VEHICLES["🚗 Vehicles"]
        V1["GET  /vehicles"]
        V2["GET  /vehicles/{plate}/history"]
    end

    subgraph WATCHLIST["📋 Watchlist"]
        W1["GET    /watchlist"]
        W2["POST   /watchlist"]
        W3["DELETE /watchlist/{plate}"]
    end

    subgraph INTEL["🗺️ Intelligence"]
        I1["POST /routes/infer"]
        I2["GET  /audit-logs"]
    end

    API(("Sentinel\nAPI\n:8000")) --> AUTH
    API --> CAMERAS
    API --> ALERTS
    API --> VEHICLES
    API --> WATCHLIST
    API --> INTEL
```

---

## Milestone Roadmap

```mermaid
gantt
    title Sentinel Gujarat — Milestone Progress
    dateFormat X
    axisFormat M%s

    section Core Ingestion
    M1 Camera Catalogue + RTSP TCP          :done, m1, 0, 1

    section AI Detection
    M2 Vehicle Detection (YOLO26 + GPU)     :done, m2, 1, 2
    M3 ANPR (PaddleOCR + Indian plates)     :done, m3, 2, 3

    section Data & Intelligence
    M4 PostgreSQL + PostGIS Registry        :done, m4, 3, 4
    M5 Watchlist + Alert Engine + RBAC      :done, m5, 4, 5
    M6 Multi-camera Journey + Routing       :done, m6, 5, 6

    section Platform
    M7 FastAPI + JWT Auth + Audit Log       :done, m7, 6, 7
    M8 Dashboard UI (GIS + HLS + Alerts)   :done, m8, 7, 8
```

---

## Sentinel Integration Rules

| Rule | Implementation |
|---|---|
| Always start from `/cameras.json` | `CatalogueService.fetch_catalogue()` — URLs never hardcoded |
| RTSP must use TCP | `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` in `stream_manager.py` |
| Use PTS, not wall clock | `CAP_PROP_POS_MSEC` read immediately after each `grab()` |
| Never trust `CAP_PROP_FPS` | FPS stored for display only, never used for timing |
| Variable inter-frame intervals | Large PTS gaps → `WARNING`, not error |
| Reconnect with backoff | 2s → 4s → 8s → 16s → 30s max via `_backoff_delay()` |
| Handle decoder join warnings | Pre-IDR H.264/H.265 warnings logged at `DEBUG`, not fatal |
| Mixed H.264/H.265 | Codec detected per-stream from `fourCC` |
| Scene discontinuity (loop point) | PTS backward jump → `SCENE_DISCONTINUITY` log |
| Consume only, don't push | No upload/push to Sentinel gateway |

---

## Security

- Secrets live in `.env` — **never committed** (protected by `.gitignore`)
- All credentials redacted before logging (`redact_credentials()` in `config.py`)
- JWT tokens — 60-minute expiry, HS256
- Full RBAC: ADMIN / POLICE_OFFICER / DEPARTMENT_USER / ANALYST / VIEWER
- Immutable audit log for all sensitive operations
- HLS proxy: browser never sees stream credentials
- Watchlist labeled `SYNTHETIC DEMO DATA` — no real government databases accessed in PoC

---

## Team

**Gujarat Police Innovation Challenge 2026 — Student Hackathon Team**  
Platform: Sentinel Gujarat Portal
