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
    vehicle_class = Column(String(20), nullable=True)  # car|bus|truck|motorcycle
    vehicle_confidence = Column(Float, nullable=True)
    vehicle_bbox = Column(JSONType, nullable=True)         # {x1,y1,x2,y2}

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

    def __repr__(self):
        return f"<DetectionEvent(id={self.id}, cam='{self.camera_id}', plate='{self.normalised_plate}')>"
