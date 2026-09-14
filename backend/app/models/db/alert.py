"""
Alert DB model — generated when watchlist match exceeds confidence threshold.
"""
from sqlalchemy import Column, String, Integer, DateTime, Float, Text, Boolean
from datetime import datetime, timezone

from backend.app.db.base import Base, JSONType


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)

    # Identity
    camera_id = Column(String(50), index=True, nullable=False)
    registration_number = Column(String(20), index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)  # wall-clock event time

    # Location at time of detection
    location = Column(String(300), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Confidence breakdown
    vehicle_detection_confidence = Column(Float, nullable=True)
    plate_detection_confidence = Column(Float, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    overall_confidence = Column(Float, nullable=False)

    # Watchlist match
    watchlist_status = Column(String(30), nullable=False)   # STOLEN | WANTED | MISSING etc
    priority = Column(String(10), nullable=False)           # HIGH | MEDIUM | LOW
    watchlist_source = Column(String(50), nullable=True)

    # Severity: HIGH | MEDIUM | LOW (may differ from priority)
    severity = Column(String(10), nullable=False, default="MEDIUM")

    # Evidence
    pts_ms = Column(Float, nullable=True)         # stream PTS at detection
    frame_index = Column(Integer, nullable=True)
    bbox_json = Column(JSONType, nullable=True)       # bounding box of vehicle
    plate_bbox_json = Column(JSONType, nullable=True) # bounding box of plate
    evidence_frame_path = Column(String(500), nullable=True)  # saved frame path if any

    # Alert lifecycle
    status = Column(String(20), nullable=False, default="NEW")  # NEW | REVIEWING | CLOSED | FALSE_POSITIVE
    acknowledged_by = Column(String(100), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # Backward compat
    plate_number = Column(String(20), nullable=True)  # alias for registration_number
    confidence = Column(Float, nullable=True)          # alias for overall_confidence
    risk_category = Column(String(30), nullable=True)  # alias for watchlist_status
    frame_path = Column(String(500), nullable=True)    # alias for evidence_frame_path

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<Alert(id={self.id}, plate='{self.registration_number}', severity='{self.severity}', status='{self.status}')>"
