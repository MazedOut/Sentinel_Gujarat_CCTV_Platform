# Architecture Deep Dive

> [!NOTE]
> This document provides an in-depth look at the architecture of the Sentinel Gujarat Platform, detailing the interaction between the FastAPI backend, pure JS frontend, and PostgreSQL integration.

## High-Level Component Architecture

The Sentinel Gujarat platform is designed as a distributed, high-throughput intelligence layer operating above the existing Gujarat Police CCTV network. It strictly separates video ingestion, AI inference, intelligence correlation, and the operator dashboard into distinct layers.

```mermaid
C4Context
    title Sentinel Gujarat Architecture

    Person(operator, "Police Operator", "Monitors dashboard, dispatches units, searches vehicles")
    Person(admin, "System Admin", "Manages users, oversees platform health")

    System_Ext(sentinel_sandbox, "Sentinel Sandbox", "External HLS CDN, RTSP Gateways, Catalogue")

    System_Boundary(c1, "Sentinel Gujarat Platform") {
        Container(frontend, "Operator Dashboard", "Vanilla JS, HTML/CSS", "Provides real-time UI, HLS playback via hls.js, Leaflet GIS mapping")
        
        System_Boundary(backend, "FastAPI Intelligence Engine") {
            Container(api_gateway, "REST & WebSocket API", "FastAPI", "Serves routes, handles auth, streams HLS, broadcasts alerts")
            Container(stream_manager, "Stream Manager", "Python, OpenCV", "Ingests RTSP/TCP, extracts PTS")
            Container(ai_core, "AI Processing Core", "PyTorch, YOLO26, PaddleOCR", "Runs vehicle detection, tracking, and ANPR pipeline")
            Container(intel_engine, "Intelligence Engine", "Python", "Journey correlation, Watchlist matching, Alert generation")
            Container(hls_proxy, "HLS Proxy", "Python, httpx", "Securely proxies m3u8 and ts segments with AES decryption")
        }
        
        ContainerDb(database, "PostgreSQL + PostGIS", "Relational Database", "Stores camera metadata, vehicle sightings, audit logs, user credentials")
    }

    Rel(operator, frontend, "Views and interacts with", "HTTPS/WSS")
    Rel(admin, frontend, "Configures and manages", "HTTPS")
    
    Rel(frontend, api_gateway, "Fetches data, receives live alerts", "REST/JSON, WebSockets")
    
    Rel(stream_manager, sentinel_sandbox, "Pulls RTSP frames", "RTSP/TCP")
    Rel(api_gateway, sentinel_sandbox, "Fetches catalogue (cameras.json)", "HTTPS")
    Rel(hls_proxy, sentinel_sandbox, "Pulls encrypted HLS streams", "HTTPS")
    
    Rel(stream_manager, ai_core, "Passes frames (FrameData)", "In-Memory")
    Rel(ai_core, intel_engine, "Passes detected plates (ANPRResult)", "In-Memory")
    Rel(intel_engine, api_gateway, "Triggers alerts", "In-Memory")
    
    Rel(api_gateway, database, "Reads/Writes data", "SQLAlchemy ORM")
    Rel(intel_engine, database, "Stores sightings, updates journeys", "SQLAlchemy ORM")
```

## Backend Architecture: FastAPI Intelligence Engine

The backend is structured using Domain-Driven Design (DDD) principles. It is built entirely asynchronously using FastAPI and Uvicorn, though computationally heavy AI tasks are intelligently threaded to prevent event loop blocking.

### Core Modules

#### 1. Ingestion Layer (`services/streaming/`)
- **`StreamManager`**: Initiates `cv2.VideoCapture` with forced TCP transport (`rtsp_transport;tcp`). It reads the `CAP_PROP_POS_MSEC` property to extract the Presentation Time Stamp (PTS), which is critical because wall-clock time is unreliable for real-time video analytics. It features an exponential backoff reconnect mechanism (2s -> 30s) if a camera node drops.
- **`HlsProxy`**: A critical security component. The official Sentinel HLS CDN requires a cookie for authentication. Passing this cookie to the frontend exposes it to XSS and allows raw video downloading. The `HlsProxy` sits in the middle, intercepting `/api/hls/...` requests, injecting the server-side cookie, modifying the `.m3u8` manifest to point back to the proxy, and piping the `.ts` and `enc.key` files back to the browser safely.

#### 2. AI Pipeline Layer (`services/detection/` & `services/anpr/`)
- Frames are sampled (e.g., `YOLO_FRAME_SKIP=3`) and passed to the `VehicleDetector`.
- **Thread Safety**: Model inference happens in a separate thread pool (`ThreadPoolExecutor`) so it does not block FastAPI's async event loop.
- Detections are paired with ByteTrack IDs.
- Valid vehicle crops are passed to the `ANPRPipeline` which utilizes `PaddleOCR`.
- **Track Caching**: A major optimization. Once a track ID yields a high-confidence plate (≥0.65), the string is cached. Future frames for that track bypass OCR, vastly reducing GPU load.

#### 3. Intelligence & Correlation Layer (`services/alerting/`, `services/tracking/`, `services/routing/`)
- **`WatchlistMatcher`**: Compares normalized plates against local databases (PoC uses `SyntheticWatchlistProvider`).
- **`AlertEngine`**: Evaluates confidence. High confidence (≥0.85) triggers a `HIGH` priority alert. Medium confidence (≥0.65) triggers a `MEDIUM` alert for manual review. Alerts are broadcast immediately via the `WebSocket Hub`.
- **`JourneyCorrelator`**: When a vehicle is seen across multiple cameras, it builds a spatiotemporal graph.
- **`RoutingService`**: Takes the correlator's output (Lat/Lon pairs) and infers the likely road taken using the Google Maps Directions API, outputting a GeoJSON polyline for the frontend GIS map.

#### 4. Security & Audit Layer (`services/audit/`, `core/security.py`)
- Standard JWT (JSON Web Tokens) with a 60-minute expiry are used for stateless API auth.
- **Immutable Audit Log**: Every sensitive action (logging in, viewing a camera, acknowledging an alert, searching a vehicle) writes a record to the `audit_logs` table. This ensures chain-of-custody compliance for law enforcement.

## Frontend Architecture: Pure Vanilla JS

To maintain zero dependencies and ultra-fast load times, the frontend is a single-page application built with Vanilla JS, HTML, and CSS.

### Components

1. **Dashboard Shell (`index.html` & `style.css`)**: Implements a dark-mode, tactical police interface. It uses CSS Grid for the quad-view and flexbox for the sidebar.
2. **State Management (`app.js`)**:
   - Manages the WebSocket connection to `/ws/alerts`.
   - Maintains a local cache of the camera catalogue.
   - Handles JWT token injection into standard `fetch()` API calls.
3. **Video Playback (`hls.min.js`)**: 
   - Uses the widely supported `hls.js` library.
   - Points to our backend's proxy: `hls.loadSource('/api/hls/' + cameraId + '/index.m3u8')`.
   - `hls.js` natively handles the AES-128 decryption provided the proxy passes the key correctly.
4. **GIS Mapping (Leaflet)**:
   - Renders OpenStreetMap tiles.
   - Plots camera markers based on `lat`/`lon` from `cameras.json`.
   - Draws OSRM/Google Maps inferred routes as vector polylines when tracking a suspect vehicle.

## Database Integration: PostgreSQL & PostGIS

The system uses SQLAlchemy as its ORM, and Alembic for schema migrations.

- **Tables**: `cameras`, `detection_events`, `vehicle_journeys`, `alerts`, `watchlist_entries`, `users`, `audit_logs`.
- **PostGIS**: While the PoC uses standard Float columns for Lat/Lon, the production architecture incorporates PostGIS extensions to perform high-speed spatial queries (e.g., "Find all sightings within 5km of coordinate X in the last hour").
- **Graceful Degradation**: The API backend is designed to run in-memory if the `DATABASE_URL` is missing or fails to connect, utilizing Python `deque` and dictionaries. This allows for rapid deployment in constrained environments or during hackathon demonstrations without requiring full Docker stack initialization.
