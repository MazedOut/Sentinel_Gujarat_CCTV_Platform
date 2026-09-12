"""
Camera DB model — complete schema per Sentinel integration spec.
"""
from sqlalchemy import Column, String, Float, Boolean, Integer, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone

from backend.app.db.base import Base


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(String(50), unique=True, index=True, nullable=False)
    department = Column(String(200), nullable=True)
    location = Column(String(300), nullable=True)

    # Geographic coordinates (stored as plain floats — PostGIS Point added via migration)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Stream properties
    codec = Column(String(20), nullable=True)
    codec_normalized = Column(String(10), nullable=True)
    resolution = Column(String(20), nullable=True)
    fps_reported = Column(Float, nullable=True)
    bitrate_kbps = Column(Integer, nullable=True)

    # Status
    live_status = Column(Boolean, nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    metadata_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Stream URLs
    rtsp_url = Column(String(500), nullable=True)
    webrtc_url = Column(String(500), nullable=True)
    hls_url = Column(String(500), nullable=True)

    # Extra catalogue fields as JSON blob
    extra_data = Column(JSONB, nullable=True, default=dict)

    # Legacy fields (backward compat)
    name = Column(String(200), nullable=True)
    location_name = Column(String(300), nullable=True)
    status = Column(String(20), nullable=True)
    video_codec = Column(String(20), nullable=True)
    ip_address = Column(String(50), nullable=True)
    has_rtsp = Column(Boolean, default=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    def __repr__(self):
        return f"<Camera(camera_id='{self.camera_id}', location='{self.location}')>"
