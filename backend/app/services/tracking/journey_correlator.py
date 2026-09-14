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
        norm = plate.upper().strip()
        journey = _journeys.get(norm)
        if journey and journey.get("segments"):
            return journey

        # Check DB if available
        db = self.db
        if not db:
            from backend.app.db.session import SessionLocal
            if SessionLocal:
                try:
                    db = SessionLocal()
                except Exception:
                    db = None

        if db:
            try:
                from backend.app.models.db.vehicle_journey import VehicleJourney, JourneySegment
                db_j = db.query(VehicleJourney).filter(VehicleJourney.normalised_plate == norm).first()
                if db_j:
                    db_segs = (
                        db.query(JourneySegment)
                        .filter(JourneySegment.journey_id == db_j.id)
                        .order_by(JourneySegment.event_time.asc())
                        .all()
                    )
                    segs = []
                    cams = set()
                    for s in db_segs:
                        cams.add(s.camera_id)
                        segs.append({
                            "id": str(s.id),
                            "journey_plate": norm,
                            "camera_id": s.camera_id,
                            "event_time": s.event_time.isoformat() if s.event_time else "",
                            "pts_ms": s.pts_ms,
                            "location": s.location,
                            "latitude": s.latitude,
                            "longitude": s.longitude,
                            "overall_confidence": s.overall_confidence,
                            "observation_type": s.observation_type or "CONFIRMED_OBSERVATION",
                            "inferred_route": s.inferred_route_geometry,
                        })
                    res = {
                        "id": str(db_j.id),
                        "normalised_plate": norm,
                        "first_seen": db_j.first_seen.isoformat() if db_j.first_seen else (segs[0]["event_time"] if segs else None),
                        "last_seen": db_j.last_seen.isoformat() if db_j.last_seen else (segs[-1]["event_time"] if segs else None),
                        "sighting_count": db_j.sighting_count or len(segs),
                        "camera_count": db_j.camera_count or len(cams),
                        "cameras_seen": list(cams),
                        "segments": segs,
                        "watchlist_matched": bool(db_j.watchlist_matched),
                        "watchlist_status": db_j.watchlist_status,
                        "created_at": db_j.created_at.isoformat() if db_j.created_at else datetime.now(timezone.utc).isoformat(),
                    }
                    _journeys[norm] = res
                    return res
            except Exception as exc:
                logger.error("Failed to query journey from DB: %s", exc)

        return journey

    def get_all_journeys(self, limit: int = 100) -> list[dict]:
        """Get all vehicle journeys, most recent first."""
        # Query DB if available to synchronize
        db = self.db
        if not db:
            from backend.app.db.session import SessionLocal
            if SessionLocal:
                try:
                    db = SessionLocal()
                except Exception:
                    db = None

        if db:
            try:
                from backend.app.models.db.vehicle_journey import VehicleJourney
                db_journeys = db.query(VehicleJourney).order_by(VehicleJourney.last_seen.desc()).limit(limit).all()
                for dj in db_journeys:
                    if dj.normalised_plate not in _journeys:
                        self.get_journey(dj.normalised_plate)
            except Exception as exc:
                logger.debug("DB sync for all journeys failed: %s", exc)

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


def _seed_demo_data(correlator: JourneyCorrelator) -> None:
    """Seed representative demo vehicle journeys matching synthetic watchlist entries."""
    now = datetime.now(timezone.utc)
    
    # 1. GJ01AB1234 — Stolen Vehicle Journey across Ahmedabad corridor
    p1 = "GJ01AB1234"
    if p1 not in _journeys:
        t1 = now - timedelta(minutes=32)
        t2 = now - timedelta(minutes=18)
        t3 = now - timedelta(minutes=5)
        correlator.add_sighting(
            plate=p1,
            camera_id="cam01",
            event_time=t1,
            location="Ahmedabad — SG Highway & Prahlad Nagar Junction",
            latitude=23.0135,
            longitude=72.5082,
            overall_confidence=0.95,
        )
        correlator.add_sighting(
            plate=p1,
            camera_id="cam02",
            event_time=t2,
            location="Ahmedabad — CTM Expressway Toll / NH48 Junction",
            latitude=22.9876,
            longitude=72.6321,
            overall_confidence=0.92,
        )
        correlator.add_sighting(
            plate=p1,
            camera_id="cam03",
            event_time=t3,
            location="Ahmedabad — Narol Industrial Circle & Ring Road",
            latitude=22.9712,
            longitude=72.5984,
            overall_confidence=0.94,
        )

    # 2. GJ05CD5678 — Wanted Vehicle in South Gujarat corridor
    p2 = "GJ05CD5678"
    if p2 not in _journeys:
        t4 = now - timedelta(minutes=45)
        t5 = now - timedelta(minutes=12)
        correlator.add_sighting(
            plate=p2,
            camera_id="cam25",
            event_time=t4,
            location="Navsari — National Highway 48 Junction",
            latitude=20.9467,
            longitude=72.9281,
            overall_confidence=0.91,
        )
        correlator.add_sighting(
            plate=p2,
            camera_id="cam26",
            event_time=t5,
            location="Bilimora — Somnath Temple Circle",
            latitude=20.7612,
            longitude=72.9684,
            overall_confidence=0.93,
        )


# Singleton correlator
_correlator = JourneyCorrelator()
try:
    _seed_demo_data(_correlator)
except Exception as e:
    logger.debug("Demo journey seeding skipped: %s", e)


def get_correlator(db_session=None) -> JourneyCorrelator:
    """Get a correlator instance."""
    if db_session:
        return JourneyCorrelator(db_session=db_session)
    from backend.app.db.session import SessionLocal
    if SessionLocal:
        try:
            return JourneyCorrelator(db_session=SessionLocal())
        except Exception:
            pass
    return _correlator

