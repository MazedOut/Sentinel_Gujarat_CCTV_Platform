"""
VehicleJourney and JourneySegment DB models — multi-camera correlation.

A VehicleJourney links all sightings of the same vehicle plate across cameras.
JourneySegment represents a point-in-time sighting at one camera.

IMPORTANT: 
  - Journey correlation is based on OCR plate matching only (for PoC).
  - All observations are CONFIRMED (camera saw the vehicle).
  - Routes BETWEEN observations are INFERRED (possible road routes).
  - These are clearly distinguished in the API and UI.
"""
from sqlalchemy import Column, String, Integer, DateTime, Float, Boolean, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from backend.app.db.base import Base


class VehicleJourney(Base):
    """
    All sightings of a single vehicle plate, chronologically ordered.
    Created/updated each time a new sighting of the same plate is detected.
    """
    __tablename__ = "vehicle_journeys"

    id = Column(Integer, primary_key=True, index=True)
    normalised_plate = Column(String(20), index=True, nullable=False)

    # Temporal extent
    first_seen = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)

    # How many cameras saw this vehicle
    camera_count = Column(Integer, default=0)
    sighting_count = Column(Integer, default=0)

    # Is this vehicle on the watchlist?
    watchlist_matched = Column(Boolean, default=False)
    watchlist_status = Column(String(30), nullable=True)

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

    # Relationship
    segments = relationship("JourneySegment", back_populates="journey", order_by="JourneySegment.event_time")

    def __repr__(self):
        return f"<VehicleJourney(plate='{self.normalised_plate}', sightings={self.sighting_count})>"


class JourneySegment(Base):
    """
    One CONFIRMED camera sighting in a vehicle journey.
    
    Status: CONFIRMED_OBSERVATION (camera actually saw the vehicle).
    Routes between segments are INFERRED.
    """
    __tablename__ = "journey_segments"

    id = Column(Integer, primary_key=True, index=True)
    journey_id = Column(Integer, ForeignKey("vehicle_journeys.id"), index=True, nullable=False)
    detection_event_id = Column(Integer, ForeignKey("detection_events.id"), nullable=True)

    # Camera that saw the vehicle
    camera_id = Column(String(50), index=True, nullable=False)
    event_time = Column(DateTime(timezone=True), nullable=False, index=True)
    pts_ms = Column(Float, nullable=True)

    # Location (from camera metadata)
    location = Column(String(300), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Evidence quality
    overall_confidence = Column(Float, nullable=True)
    normalised_plate = Column(String(20), nullable=True)

    # CONFIRMED = camera saw the vehicle
    # Do NOT create INFERRED segments — use routes for that
    observation_type = Column(String(30), nullable=False, default="CONFIRMED_OBSERVATION")

    # Inferred route FROM this segment to the next (GeoJSON LineString or encoded polyline)
    inferred_route_geometry = Column(Text, nullable=True)     # GeoJSON string
    inferred_route_distance_m = Column(Float, nullable=True)
    inferred_route_duration_s = Column(Float, nullable=True)
    route_provider = Column(String(30), nullable=True)        # google_maps | openroute | none

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationship
    journey = relationship("VehicleJourney", back_populates="segments")

    def __repr__(self):
        return f"<JourneySegment(cam='{self.camera_id}', type='{self.observation_type}', time='{self.event_time}')>"
