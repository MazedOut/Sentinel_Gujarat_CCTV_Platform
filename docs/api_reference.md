# API Reference

> [!NOTE]
> This document details the REST API endpoints and WebSocket channels exposed by the FastAPI backend, enabling interaction with the Sentinel Gujarat Intelligence layer.

By default, the API is available at `http://localhost:8000`. Interactive Swagger UI documentation is automatically generated and accessible at `/docs`.

---

## 1. Authentication Endpoints

The API is secured using JSON Web Tokens (JWT). Most endpoints require a valid token passed in the `Authorization` header as `Bearer <token>`.

### `POST /auth/login`
Authenticates a user and issues a JWT.
- **Request Format**: `application/x-www-form-urlencoded` (OAuth2 spec)
- **Parameters**: `username`, `password`
- **Response**:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5c...",
  "token_type": "bearer"
}
```

### `GET /auth/me`
Returns the profile and role of the currently authenticated user.
- **Auth**: Required
- **Response**:
```json
{
  "id": 1,
  "username": "officer1",
  "role": "POLICE_OFFICER",
  "department": "Traffic Police"
}
```

---

## 2. Camera Catalogue & Streaming

### `GET /cameras`
Returns the active catalogue of all surveillance nodes. Data is synchronized from the upstream Sentinel sandbox.
- **Auth**: Optional
- **Response**:
```json
[
  {
    "camera_id": "cam01",
    "location": "Ahmedabad — Chimanbhai Patel Bridge",
    "lat": 23.0645,
    "lon": 72.5794,
    "live_status": true
  }
]
```

### HLS Proxy Endpoints
These endpoints proxy the external Sentinel CDN, stripping client requirements for auth cookies.

- **`GET /api/hls/{camera_id}/index.m3u8`**
  Returns the rewritten HLS playlist for a specific camera.
  
- **`GET /api/hls/{camera_id}/{segment_name}.ts`**
  Proxies the raw MPEG-TS video segment.
  
- **`GET /api/hls/{camera_id}/enc.key`**
  Proxies the AES-128 encryption key required to decode the `.ts` segments.

---

## 3. Alerts & Intelligence

### `GET /alerts`
Retrieves a paginated list of alerts.
- **Auth**: Required
- **Query Parameters**: `status` (OPEN, ACKNOWLEDGED), `severity` (HIGH, MEDIUM), `limit`
- **Response**:
```json
[
  {
    "id": "alt_84jd93j",
    "camera_id": "cam02",
    "plate": "GJ01AB1234",
    "severity": "HIGH",
    "status": "OPEN",
    "timestamp": "2026-09-15T14:30:22Z"
  }
]
```

### `POST /alerts/{alert_id}/acknowledge`
Allows an officer to acknowledge and close an active alert.
- **Auth**: Required (Role: ADMIN or POLICE_OFFICER)
- **Response**: `200 OK`

---

## 4. Vehicle Tracking & Routing

### `GET /vehicles/{plate}/history`
Fetches the chronological journey of a specific vehicle across all cameras.
- **Auth**: Required
- **Response**:
```json
{
  "plate": "GJ01AB1234",
  "sightings": [
    {
      "camera_id": "cam01",
      "lat": 23.0645,
      "lon": 72.5794,
      "timestamp": "2026-09-15T14:10:00Z"
    },
    {
      "camera_id": "cam02",
      "lat": 23.0452,
      "lon": 72.5713,
      "timestamp": "2026-09-15T14:15:30Z"
    }
  ]
}
```

### `POST /routes/infer`
Calculates the inferred road corridor between two GPS coordinates using OSRM or Google Maps.
- **Auth**: Required
- **Payload**:
```json
{
  "start": {"lat": 23.0645, "lon": 72.5794},
  "end": {"lat": 23.0452, "lon": 72.5713}
}
```
- **Response**: Returns a GeoJSON polyline representing the road taken.

---

## 5. WebSocket Event Stream

### `WS /ws/alerts`
The tactical WebSocket channel. The frontend connects to this to receive zero-latency push notifications when the AI pipeline flags a vehicle.

- **Connection**: No auth required (for PoC, can be secured via ticket system in prod).
- **Payload Example (Server -> Client)**:
```json
{
  "event_type": "NEW_ALERT",
  "data": {
    "alert_id": "alt_99xx12",
    "camera_id": "cam04",
    "plate": "GJ27CC9999",
    "watchlist_status": "STOLEN_VEHICLE",
    "severity": "HIGH",
    "confidence": 0.92,
    "location": "Paldi Cross Roads"
  }
}
```
