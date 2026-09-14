"""
DetectionEvent DB model — persists each ANPR detection result.
"""
from sqlalchemy import Column, String, Integer, DateTime, Float, Boolean, Text
from datetime import datetime, timezone

from backend.app.db.base import Base, JSONType


class DetectionEvent(Base):
    """
    Represents one complete ANPR detection event.
    
    An event occurs when:
      1. A vehicle is detected by YOLO
      2. A plate is detected within the vehicle crop
      3. OCR reads the plate text
      4. Confidence is scored
    """
    __tablename__ = "detection_events"

    id = Column(Integer, primary_key=True, index=True)

    # Source
    camera_id = Column(String(50), index=True, nullable=False)
    pts_ms = Column(Float, nullable=True)           # stream PTS — NOT wall clock
    frame_index = Column(Integer, nullable=True)
    event_time = Column(DateTime(timezone=True), nullable=False)  # wall-clock when recorded

    # Vehicle detection
    vehicle_class = Column(String(20), nullable=True)  # car|bus|truck|motorcycle|person
    vehicle_confidence = Column(Float, nullable=True)
    vehicle_bbox = Column(JSONType, nullable=True)         # {x1,y1,x2,y2}
    track_id = Column(Integer, nullable=True, index=True)
    dwell_time_seconds = Column(Float, nullable=True)

    # ANPR
    raw_plate_text = Column(String(50), nullable=True)
    normalised_plate = Column(String(20), index=True, nullable=True)
    plate_detection_confidence = Column(Float, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    is_valid_plate_format = Column(Boolean, nullable=True)
    plate_bbox = Column(JSONType, nullable=True)

    # Composite confidence score
    overall_confidence = Column(Float, nullable=True)

    # Watchlist check
    watchlist_matched = Column(Boolean, default=False)
    watchlist_status = Column(String(30), nullable=True)

    # Optional: path to saved evidence frame
    evidence_path = Column(String(500), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "pts_ms": self.pts_ms,
            "frame_index": self.frame_index,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "vehicle_class": self.vehicle_class,
            "vehicle_confidence": self.vehicle_confidence,
            "vehicle_bbox": self.vehicle_bbox,
            "track_id": self.track_id,
            "dwell_time_seconds": self.dwell_time_seconds,
            "raw_plate_text": self.raw_plate_text,
            "normalised_plate": self.normalised_plate,
            "plate_detection_confidence": self.plate_detection_confidence,
            "ocr_confidence": self.ocr_confidence,
            "is_valid_plate_format": self.is_valid_plate_format,
            "plate_bbox": self.plate_bbox,
            "overall_confidence": self.overall_confidence,
            "watchlist_matched": bool(self.watchlist_matched),
            "watchlist_status": self.watchlist_status,
            "evidence_path": self.evidence_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<DetectionEvent(id={self.id}, cam='{self.camera_id}', plate='{self.normalised_plate}', class='{self.vehicle_class}')>"

