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
    registration_number = Column(String(100), index=True, nullable=False)
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
    watchlist_status = Column(String(60), nullable=False)   # STOLEN | WANTED | MISSING | ACCIDENT_COLLISION etc
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
    plate_number = Column(String(100), nullable=True)  # alias for registration_number
    confidence = Column(Float, nullable=True)          # alias for overall_confidence
    risk_category = Column(String(60), nullable=True)  # alias for watchlist_status
    frame_path = Column(String(500), nullable=True)    # alias for evidence_frame_path

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "camera_id": self.camera_id,
            "registration_number": self.registration_number,
            "timestamp": self.timestamp.isoformat() if self.timestamp else (self.created_at.isoformat() if self.created_at else None),
            "location": self.location,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "vehicle_detection_confidence": self.vehicle_detection_confidence,
            "plate_detection_confidence": self.plate_detection_confidence,
            "ocr_confidence": self.ocr_confidence,
            "overall_confidence": self.overall_confidence if self.overall_confidence is not None else (self.confidence or 0.9),
            "watchlist_status": self.watchlist_status or self.risk_category or "SURVEILLANCE",
            "priority": self.priority or self.severity or "MEDIUM",
            "severity": self.severity or "MEDIUM",
            "watchlist_source": self.watchlist_source or "GUJARAT_POLICE_AI_SURVEILLANCE",
            "pts_ms": self.pts_ms,
            "frame_index": self.frame_index,
            "bbox_json": self.bbox_json,
            "plate_bbox_json": self.plate_bbox_json,
            "evidence_frame_path": self.evidence_frame_path or self.frame_path,
            "status": self.status or "NEW",
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Alert(id={self.id}, plate='{self.registration_number}', severity='{self.severity}', status='{self.status}')>"

