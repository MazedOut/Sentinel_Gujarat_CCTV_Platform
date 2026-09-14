"""
Drishti — Sample Alert Seeding for Jury Evaluation
==================================================
Seeds compelling, realistic incident alerts (CCTV Collisions, Stolen Vehicle Intercepts,
Wanted Suspects, Wrong-Way Driving, Missing Vehicles) across Gujarat's operational surveillance grid.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from backend.app.db.session import SessionLocal
from backend.app.models.db.alert import Alert
from backend.app.services.alerting.alert_engine import _alert_store
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)

SAMPLE_ALERTS = [
    {
        "camera_id": "cam01",
        "registration_number": "GJ01AB1234, GJ27XY9901",
        "minutes_ago": 18,
        "location": "Ahmedabad — Chimanbhai Patel Bridge (Sabarmati)",
        "latitude": 23.0645,
        "longitude": 72.5794,
        "watchlist_status": "ACCIDENT_COLLISION",
        "priority": "HIGH",
        "severity": "CRITICAL",
        "overall_confidence": 0.96,
        "status": "REVIEWING",
        "acknowledged_by": "officer1",
        "notes": "AI Kinematic Trajectory Consensus: Multi-vehicle high-impact collision detected on Northbound corridor. Emergency medical and traffic division dispatched.",
    },
    {
        "camera_id": "cam04",
        "registration_number": "GJ01DT3341, GJ05EM9012",
        "minutes_ago": 45,
        "location": "Ahmedabad — Paldi Cross Roads (Mahalakshmi 5 Roads)",
        "latitude": 23.0125,
        "longitude": 72.5627,
        "watchlist_status": "ACCIDENT_COLLISION",
        "priority": "HIGH",
        "severity": "CRITICAL",
        "overall_confidence": 0.94,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "High-velocity lateral impact detected at junction. 108 Emergency Medical Services and Ahmedabad Traffic Unit notified.",
    },
    {
        "camera_id": "cam02",
        "registration_number": "GJ01XY7788, GJ06AA1234",
        "minutes_ago": 70,
        "location": "Ahmedabad — Income Tax Underpass & Ashram Road",
        "latitude": 23.0410,
        "longitude": 72.5710,
        "watchlist_status": "ACCIDENT_COLLISION",
        "priority": "HIGH",
        "severity": "CRITICAL",
        "overall_confidence": 0.95,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "AI Motion Kinematics: Two-vehicle side-impact collision at signal crossing with lane blockage. Traffic diversion protocol initiated.",
    },
    {
        "camera_id": "cam02",
        "registration_number": "GJ01AB1234",
        "minutes_ago": 85,
        "location": "Ahmedabad — Janpath, Ashram Road (Usmanpura)",
        "latitude": 23.0452,
        "longitude": 72.5713,
        "watchlist_status": "STOLEN_VEHICLE",
        "priority": "HIGH",
        "severity": "HIGH",
        "overall_confidence": 0.97,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "ANPR match: White Hyundai Creta reported stolen under FIR #492/2026 at Satellite Police Station. Intercept sector notified.",
    },
    {
        "camera_id": "cam25",
        "registration_number": "GJ05CD5678",
        "minutes_ago": 110,
        "location": "Navsari — National Highway 48 Junction",
        "latitude": 20.9467,
        "longitude": 72.9281,
        "watchlist_status": "WANTED_PERSON_VEHICLE",
        "priority": "HIGH",
        "severity": "HIGH",
        "overall_confidence": 0.95,
        "status": "REVIEWING",
        "acknowledged_by": "officer1",
        "notes": "Wanted felony suspect vehicle observed traveling Southbound on NH48 corridor towards Maharashtra border. Highway Patrol intercept activated.",
    },
    {
        "camera_id": "cam11",
        "registration_number": "GJ05BK6789",
        "minutes_ago": 140,
        "location": "Surat — Athwa Gate Circle / Ring Road",
        "latitude": 21.1852,
        "longitude": 72.8095,
        "watchlist_status": "STOLEN_VEHICLE",
        "priority": "HIGH",
        "severity": "HIGH",
        "overall_confidence": 0.96,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "ANPR match: Dark Blue Toyota Fortuner reported stolen under FIR #214/2026 at Umra Police Station. Mobile patrol units alerted.",
    },
    {
        "camera_id": "cam03",
        "registration_number": "GJ06BC8891",
        "minutes_ago": 180,
        "location": "Ahmedabad — ONGC Gujarat Headquarters (Chandkheda)",
        "latitude": 23.1098,
        "longitude": 72.5936,
        "watchlist_status": "WRONG_WAY_TRAJECTORY",
        "priority": "MEDIUM",
        "severity": "MEDIUM",
        "overall_confidence": 0.91,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "Kinematic vector violation: Motorist driving counter-flow into heavy incoming traffic on arterial flyover.",
    },
    {
        "camera_id": "cam10",
        "registration_number": "GJ18EF9012",
        "minutes_ago": 260,
        "location": "Surat — Majura Gate Circle / Ring Road Junction",
        "latitude": 21.1762,
        "longitude": 72.8225,
        "watchlist_status": "MISSING_PERSON_VEHICLE",
        "priority": "MEDIUM",
        "severity": "MEDIUM",
        "overall_confidence": 0.89,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "Silver Maruti Swift associated with missing senior citizen report #1180. Civil patrol squad alerted.",
    },
    {
        "camera_id": "cam14",
        "registration_number": "GJ06DE4321",
        "minutes_ago": 300,
        "location": "Vadodara — Sayajigunj Tower & Station Road",
        "latitude": 22.3115,
        "longitude": 73.1812,
        "watchlist_status": "WRONG_WAY_TRAJECTORY",
        "priority": "MEDIUM",
        "severity": "MEDIUM",
        "overall_confidence": 0.90,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "Kinematic trajectory violation: Commercial pickup van operating against one-way traffic flow during rush hour.",
    },
    {
        "camera_id": "cam05",
        "registration_number": "GJ27GH3456",
        "minutes_ago": 340,
        "location": "Ahmedabad — Visat Three Roads, Gandhinagar Highway",
        "latitude": 23.0963,
        "longitude": 72.5971,
        "watchlist_status": "SPEED_AND_BLACKLIST_VIOLATION",
        "priority": "LOW",
        "severity": "LOW",
        "overall_confidence": 0.88,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "Vehicle clocked at 104 km/h in 60 km/h city expressway zone with multiple unpaid automated e-challans.",
    },
    {
        "camera_id": "cam06",
        "registration_number": "GJ27AA1209",
        "minutes_ago": 390,
        "location": "Ahmedabad — SG Highway / Pakwan Junction",
        "latitude": 23.0381,
        "longitude": 72.5118,
        "watchlist_status": "SPEED_AND_BLACKLIST_VIOLATION",
        "priority": "LOW",
        "severity": "LOW",
        "overall_confidence": 0.89,
        "status": "NEW",
        "acknowledged_by": None,
        "notes": "Vehicle flagged for 14 outstanding automated e-challans and operating 28 km/h above corridor speed limit.",
    }
]


def seed_sample_alerts() -> int:
    """Populates realistic incident alerts into the database if not already present."""
    if SessionLocal is None:
        return 0

    now = datetime.now(timezone.utc)
    seeded_count = 0

    with SessionLocal() as db:
        try:
            # Check existing alerts
            existing_plates = set(
                r[0] for r in db.query(Alert.registration_number).all()
            )

            for sa in SAMPLE_ALERTS:
                reg = sa["registration_number"]
                # If an alert with this exact registration and status already exists, skip
                already_has = db.query(Alert).filter(
                    Alert.registration_number == reg,
                    Alert.watchlist_status == sa["watchlist_status"],
                ).first()

                if already_has:
                    continue

                ts = now - timedelta(minutes=sa["minutes_ago"])
                alert_obj = Alert(
                    camera_id=sa["camera_id"],
                    registration_number=reg,
                    timestamp=ts,
                    location=sa["location"],
                    latitude=sa["latitude"],
                    longitude=sa["longitude"],
                    vehicle_detection_confidence=sa["overall_confidence"],
                    plate_detection_confidence=sa["overall_confidence"],
                    ocr_confidence=sa["overall_confidence"],
                    overall_confidence=sa["overall_confidence"],
                    watchlist_status=sa["watchlist_status"],
                    priority=sa["priority"],
                    severity=sa["severity"],
                    watchlist_source="GUJARAT_POLICE_AI_SURVEILLANCE",
                    status=sa["status"],
                    acknowledged_by=sa["acknowledged_by"],
                    acknowledged_at=(ts + timedelta(minutes=4)) if sa["acknowledged_by"] else None,
                    notes=sa["notes"],
                    plate_number=reg[:20],
                    confidence=sa["overall_confidence"],
                    risk_category=sa["watchlist_status"],
                    created_at=ts,
                )
                db.add(alert_obj)
                seeded_count += 1

            if seeded_count > 0:
                db.commit()
                logger.info("Successfully seeded %d diverse sample alerts for jury evaluation.", seeded_count)

            # Sync into in-memory _alert_store
            all_db_alerts = db.query(Alert).order_by(Alert.timestamp.desc()).all()
            for a in all_db_alerts:
                aid = str(a.id)
                _alert_store[aid] = {
                    "id": aid,
                    "camera_id": a.camera_id,
                    "registration_number": a.registration_number,
                    "timestamp": a.timestamp.isoformat() if a.timestamp else now.isoformat(),
                    "location": a.location,
                    "latitude": a.latitude,
                    "longitude": a.longitude,
                    "overall_confidence": a.overall_confidence or 0.9,
                    "watchlist_status": a.watchlist_status,
                    "priority": a.priority,
                    "severity": a.severity,
                    "status": a.status,
                    "acknowledged_by": a.acknowledged_by,
                    "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                    "notes": a.notes,
                }

        except Exception as e:
            db.rollback()
            logger.error("Failed seeding sample alerts: %s", e)

    return seeded_count
