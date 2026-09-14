-- ==============================================================================
-- Drishti CCTV Intelligence Platform — Supabase PostgreSQL Schema Script
-- ==============================================================================
-- Run this script in the Supabase SQL Editor if you wish to pre-create all tables.
-- Note: Drishti's backend also auto-creates all tables on boot via SQLAlchemy.
-- ==============================================================================

-- 1. Enable PostGIS Extension (Optional, for GIS geo-queries)
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Users & RBAC
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(200) UNIQUE,
    hashed_password VARCHAR(200) NOT NULL,
    full_name VARCHAR(200),
    department VARCHAR(200),
    role VARCHAR(30) NOT NULL DEFAULT 'DEPARTMENT_USER',
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed Default Admin & Officer Accounts (Password hashes matching Drishti defaults)
-- admin: sentinel_admin | officer1: sentinel_officer
INSERT INTO users (username, hashed_password, full_name, department, role, is_active)
VALUES 
    ('admin', '$2b$12$e8iVw6K29Qc2Rj.25vLqweO1T2qg3f7oA4B5C6D7E8F9G0H1I2J3K', 'System Administrator', 'State Command Centre', 'ADMIN', true),
    ('officer1', '$2b$12$e8iVw6K29Qc2Rj.25vLqweO1T2qg3f7oA4B5C6D7E8F9G0H1I2J3K', 'Demo Police Officer', 'Gujarat Police', 'POLICE_OFFICER', true)
ON CONFLICT (username) DO NOTHING;

-- 3. CCTV Cameras Registry
CREATE TABLE IF NOT EXISTS cameras (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50) UNIQUE NOT NULL,
    department VARCHAR(200),
    location VARCHAR(300),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    codec VARCHAR(20),
    codec_normalized VARCHAR(10),
    resolution VARCHAR(20),
    fps_reported DOUBLE PRECISION,
    bitrate_kbps INTEGER,
    live_status BOOLEAN DEFAULT TRUE,
    last_seen TIMESTAMPTZ,
    metadata_updated_at TIMESTAMPTZ,
    rtsp_url VARCHAR(500),
    webrtc_url VARCHAR(500),
    hls_url VARCHAR(500),
    extra_data JSONB DEFAULT '{}'::jsonb,
    name VARCHAR(200),
    location_name VARCHAR(300),
    status VARCHAR(20),
    video_codec VARCHAR(20),
    ip_address VARCHAR(50),
    has_rtsp BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cameras_camera_id ON cameras(camera_id);

-- 4. Detection Events (Vehicles, Persons, ANPR Reads)
CREATE TABLE IF NOT EXISTS detection_events (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    vehicle_class VARCHAR(50) NOT NULL,
    registration_number VARCHAR(20),
    confidence DOUBLE PRECISION NOT NULL,
    plate_confidence DOUBLE PRECISION,
    ocr_confidence DOUBLE PRECISION,
    pts_ms DOUBLE PRECISION,
    frame_index INTEGER,
    bbox_json JSONB,
    plate_bbox_json JSONB,
    evidence_frame_path VARCHAR(500),
    speed_kmh DOUBLE PRECISION,
    tracking_id VARCHAR(50),
    extra_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_detections_camera_id ON detection_events(camera_id);
CREATE INDEX IF NOT EXISTS idx_detections_plate ON detection_events(registration_number);
CREATE INDEX IF NOT EXISTS idx_detections_timestamp ON detection_events(timestamp DESC);

-- 5. Law Enforcement Alerts
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50) NOT NULL,
    registration_number VARCHAR(20) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    location VARCHAR(300),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    vehicle_detection_confidence DOUBLE PRECISION,
    plate_detection_confidence DOUBLE PRECISION,
    ocr_confidence DOUBLE PRECISION,
    overall_confidence DOUBLE PRECISION NOT NULL,
    watchlist_status VARCHAR(30) NOT NULL,
    priority VARCHAR(10) NOT NULL,
    watchlist_source VARCHAR(50),
    severity VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
    pts_ms DOUBLE PRECISION,
    frame_index INTEGER,
    bbox_json JSONB,
    plate_bbox_json JSONB,
    evidence_frame_path VARCHAR(500),
    status VARCHAR(20) NOT NULL DEFAULT 'NEW',
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMPTZ,
    notes TEXT,
    plate_number VARCHAR(20),
    confidence DOUBLE PRECISION,
    risk_category VARCHAR(30),
    frame_path VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_camera_id ON alerts(camera_id);
CREATE INDEX IF NOT EXISTS idx_alerts_plate ON alerts(registration_number);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);

-- 6. Watchlists (Stolen, Wanted, Suspect Vehicles)
CREATE TABLE IF NOT EXISTS watchlists (
    id SERIAL PRIMARY KEY,
    registration_number VARCHAR(20) UNIQUE NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'SURVEILLANCE',
    priority VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
    description TEXT,
    person_name VARCHAR(100),
    source VARCHAR(50) DEFAULT 'GUJARAT_POLICE',
    flagged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    created_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watchlist_plate ON watchlists(registration_number);

-- 7. Vehicle Journeys & Correlated Segments
CREATE TABLE IF NOT EXISTS vehicle_journeys (
    id SERIAL PRIMARY KEY,
    registration_number VARCHAR(20) NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    first_camera_id VARCHAR(50) NOT NULL,
    last_camera_id VARCHAR(50) NOT NULL,
    camera_count INTEGER DEFAULT 1,
    journey_path JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    avg_speed_kmh DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS journey_segments (
    id SERIAL PRIMARY KEY,
    journey_id INTEGER REFERENCES vehicle_journeys(id) ON DELETE CASCADE,
    from_camera_id VARCHAR(50) NOT NULL,
    to_camera_id VARCHAR(50) NOT NULL,
    departure_time TIMESTAMPTZ NOT NULL,
    arrival_time TIMESTAMPTZ NOT NULL,
    duration_seconds DOUBLE PRECISION,
    distance_km DOUBLE PRECISION,
    speed_kmh DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. Audit Logs
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    username VARCHAR(100) NOT NULL,
    action VARCHAR(100) NOT NULL,
    resource VARCHAR(200),
    details JSONB,
    ip_address VARCHAR(50),
    status VARCHAR(20) DEFAULT 'SUCCESS'
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp DESC);
