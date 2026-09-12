"""
Multi-camera vehicle journey correlation service.

Correlates detections of the same vehicle across multiple cameras
to build a chronological journey.

Correlation method (PoC):
    - Match by normalised registration number
    - Order by event timestamp
    - Deduplicate rapid consecutive sightings at same camera

IMPORTANT LABELS:
    CONFIRMED_OBSERVATION = camera actually saw the vehicle
    INFERRED_ROUTE        = road route estimated between observations
    
These are ALWAYS kept distinct in data and UI.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)

# In-memory journey store (keyed by normalised plate)
_journeys: dict[str, dict] = {}
_segments: list[dict] = []

# Deduplicate sightings at same camera within this window
_DEDUP_WINDOW_SECONDS = 30


class JourneyCorrelator:
    """
    Maintains vehicle journey state across cameras.
    
    Usage:
        correlator = JourneyCorrelator()
        journey = correlator.add_sighting(
            plate="GJ01AB1234",
            camera_id="cam01",
            event_time=...,
            camera_lat=23.02, camera_lon=72.57,
        )
    """

    def __init__(self, db_session=None):
        self.db = db_session

    def add_sighting(
        self,
        plate: str,
        camera_id: str,
        event_time: datetime,
        location: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        pts_ms: Optional[float] = None,
        overall_confidence: Optional[float] = None,
        detection_event_id: Optional[int] = None,
    ) -> dict:
        """
        Record a confirmed camera sighting.
        
        Returns the updated journey dict.
        """
        plate = plate.upper().strip()

        # Dedup: if same camera saw same plate recently, skip
        if self._is_duplicate(plate, camera_id, event_time):
            logger.debug(
                "Dedup: plate=%s cam=%s within %ds window",
                plate, camera_id, _DEDUP_WINDOW_SECONDS,
            )
            return self._get_journey(plate)

        # Create/update journey
        journey = self._get_or_create_journey(plate)

        segment = {
            "id": str(uuid.uuid4()),
            "journey_plate": plate,
            "camera_id": camera_id,
            "event_time": event_time.isoformat() if isinstance(event_time, datetime) else event_time,
            "pts_ms": pts_ms,
            "location": location,
            "latitude": latitude,
            "longitude": longitude,
            "overall_confidence": overall_confidence,
            "observation_type": "CONFIRMED_OBSERVATION",
            "inferred_route": None,  # filled in by routing service
        }

        # Append to global segment list
        _segments.append(segment)

        # Update journey
        journey["sighting_count"] += 1
        cameras = set(journey.get("cameras_seen", []))
        cameras.add(camera_id)
        journey["cameras_seen"] = list(cameras)
        journey["camera_count"] = len(cameras)

        all_times = [
            s["event_time"] for s in _segments
            if s["journey_plate"] == plate
        ]
        if all_times:
            journey["first_seen"] = min(all_times)
            journey["last_seen"] = max(all_times)

        journey["segments"] = sorted(
            [s for s in _segments if s["journey_plate"] == plate],
            key=lambda x: x["event_time"],
        )

        logger.info(
            "JOURNEY UPDATE: plate=%s cam=%s sightings=%d cameras=%d",
            plate, camera_id, journey["sighting_count"], journey["camera_count"],
        )

        # Persist to DB if available
        self._persist(journey, segment, detection_event_id)

        return journey

    def get_journey(self, plate: str) -> Optional[dict]:
        """Get the complete journey for a plate."""
        return _journeys.get(plate.upper().strip())

    def get_all_journeys(self, limit: int = 100) -> list[dict]:
        """Get all vehicle journeys, most recent first."""
        journeys = list(_journeys.values())
        journeys.sort(key=lambda j: j.get("last_seen", ""), reverse=True)
        return journeys[:limit]

    def _get_journey(self, plate: str) -> dict:
        return _journeys.get(plate, {})

    def _get_or_create_journey(self, plate: str) -> dict:
        if plate not in _journeys:
            _journeys[plate] = {
                "id": str(uuid.uuid4()),
                "normalised_plate": plate,
                "first_seen": None,
                "last_seen": None,
                "sighting_count": 0,
                "camera_count": 0,
                "cameras_seen": [],
                "segments": [],
                "watchlist_matched": False,
                "watchlist_status": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        return _journeys[plate]

    def _is_duplicate(self, plate: str, camera_id: str, event_time: datetime) -> bool:
        """Check if this is a duplicate sighting within the dedup window."""
        cutoff = event_time - timedelta(seconds=_DEDUP_WINDOW_SECONDS)
        cutoff_str = cutoff.isoformat()

        for seg in _segments:
            if (
                seg["journey_plate"] == plate
                and seg["camera_id"] == camera_id
                and seg["event_time"] >= cutoff_str
            ):
                return True
        return False

    def _persist(self, journey: dict, segment: dict, detection_event_id: Optional[int]) -> None:
        """Persist to DB if session available."""
        if not self.db:
            return
        try:
            from backend.app.models.db.vehicle_journey import VehicleJourney, JourneySegment

            # Upsert journey
            plate = journey["normalised_plate"]
            db_journey = (
                self.db.query(VehicleJourney)
                .filter(VehicleJourney.normalised_plate == plate)
                .first()
            )
            if not db_journey:
                db_journey = VehicleJourney(normalised_plate=plate)
                self.db.add(db_journey)

            db_journey.sighting_count = journey["sighting_count"]
            db_journey.camera_count = journey["camera_count"]

            if journey.get("first_seen"):
                try:
                    db_journey.first_seen = datetime.fromisoformat(journey["first_seen"])
                except Exception:
                    pass

            if journey.get("last_seen"):
                try:
                    db_journey.last_seen = datetime.fromisoformat(journey["last_seen"])
                except Exception:
                    pass

            self.db.flush()

            # Add segment
            seg_time = segment["event_time"]
            if isinstance(seg_time, str):
                try:
                    seg_time = datetime.fromisoformat(seg_time)
                except Exception:
                    seg_time = datetime.now(timezone.utc)

            db_seg = JourneySegment(
                journey_id=db_journey.id,
                detection_event_id=detection_event_id,
                camera_id=segment["camera_id"],
                event_time=seg_time,
                pts_ms=segment.get("pts_ms"),
                location=segment.get("location"),
                latitude=segment.get("latitude"),
                longitude=segment.get("longitude"),
                overall_confidence=segment.get("overall_confidence"),
                normalised_plate=plate,
                observation_type="CONFIRMED_OBSERVATION",
            )
            self.db.add(db_seg)
            self.db.commit()

        except Exception as exc:
            logger.error("Failed to persist journey to DB: %s", exc)
            try:
                self.db.rollback()
            except Exception:
                pass


# Singleton correlator (no DB)
_correlator = JourneyCorrelator()


def get_correlator(db_session=None) -> JourneyCorrelator:
    """Get a correlator instance."""
    if db_session:
        return JourneyCorrelator(db_session=db_session)
    return _correlator
