"""
Alert engine — generates alerts when watchlist matches exceed confidence thresholds.

Pipeline:
    ANPRResult + WatchlistMatch → AlertEngine → Alert (stored in DB)

Severity logic (configurable, from settings):
    HIGH   confidence >= alert_high_confidence   → automatic alert
    MEDIUM confidence >= alert_medium_confidence → manual review queue
    LOW    confidence <  alert_medium_confidence → stored, no alert generated
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from backend.app.core.config import settings
from backend.app.core.logging_config import get_logger
from backend.app.models.anpr import ANPRResult

logger = get_logger(__name__)

# In-memory alert store for when DB is not configured
# Keyed by alert_id (str)
_alert_store: dict[str, dict] = {}
_alert_callbacks: list = []  # WebSocket broadcast callbacks

# Cooldown cache to prevent spamming identical alerts: (plate, camera_id) -> last_alert_datetime
_recent_alerts: dict[tuple[str, str], datetime] = {}
_ALERT_COOLDOWN_SECONDS = 60


def register_alert_callback(callback) -> None:
    """Register a callback to be called when a new alert is generated."""
    _alert_callbacks.append(callback)


def _broadcast_alert(alert: dict) -> None:
    """Notify all registered WebSocket callbacks of a new alert."""
    dead = []
    for cb in _alert_callbacks:
        try:
            cb(alert)
        except Exception:
            dead.append(cb)
    for d in dead:
        _alert_callbacks.remove(d)


class AlertEngine:
    """
    Processes ANPR results and generates alerts for watchlist matches.

    Usage:
        engine = AlertEngine(watchlist_provider)
        alert = engine.process(anpr_result, camera_location=...)
    """

    def __init__(self, watchlist_provider=None, db_session=None):
        """
        Args:
            watchlist_provider: A WatchlistProvider instance (see watchlist module)
            db_session: SQLAlchemy session (optional — uses in-memory store if None)
        """
        self.watchlist = watchlist_provider
        self.db = db_session
        self.total_processed = 0
        self.total_alerts = 0

    def process(
        self,
        anpr_result: ANPRResult,
        camera_location: Optional[str] = None,
        camera_lat: Optional[float] = None,
        camera_lon: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Process one ANPR result.

        1. Check confidence threshold (minimum ANPR_CONFIDENCE_THRESHOLD)
        2. Check plate format validity
        3. Look up plate in watchlist
        4. If matched and confidence is sufficient, generate alert
        5. Return alert dict or None

        Returns alert dict if an alert was generated, None otherwise.
        """
        self.total_processed += 1

        plate = anpr_result.normalised_plate
        if not plate:
            return None

        # Below minimum ANPR threshold — don't even query watchlist
        if anpr_result.final_confidence < settings.anpr_confidence_threshold:
            logger.debug(
                "[%s] Detection below ANPR threshold (%.2f < %.2f): plate=%s",
                anpr_result.camera_id,
                anpr_result.final_confidence,
                settings.anpr_confidence_threshold,
                plate,
            )
            return None

        # Watchlist lookup
        watchlist_entry = None
        if self.watchlist:
            watchlist_entry = self.watchlist.lookup(plate)

        if watchlist_entry is None:
            return None

        # Cooldown deduplication: Avoid duplicate alerts for same vehicle lingering at same camera
        now = datetime.now(timezone.utc)
        cache_key = (plate, anpr_result.camera_id)
        last_time = _recent_alerts.get(cache_key)
        if last_time and (now - last_time).total_seconds() < _ALERT_COOLDOWN_SECONDS:
            logger.debug(
                "[%s] Alert deduplicated for %s (last alerted %ds ago, cooldown=%ds)",
                anpr_result.camera_id,
                plate,
                int((now - last_time).total_seconds()),
                _ALERT_COOLDOWN_SECONDS,
            )
            return None

        # We have a watchlist match — determine severity
        severity = _determine_severity(anpr_result.final_confidence)

        if severity == "LOW":
            logger.info(
                "[%s] Watchlist match for %s (conf=%.2f) → LOW severity, storing without alert",
                anpr_result.camera_id, plate, anpr_result.final_confidence,
            )
            # Still record the detection but don't generate a full alert
            return None

        # Generate alert
        alert = _build_alert(
            anpr_result=anpr_result,
            watchlist_entry=watchlist_entry,
            severity=severity,
            camera_location=camera_location,
            camera_lat=camera_lat,
            camera_lon=camera_lon,
        )

        # Persist
        self._persist_alert(alert)
        _recent_alerts[cache_key] = now

        logger.warning(
            "[ALERT] %s severity | cam=%s | plate=%s | conf=%.2f | status=%s",
            severity,
            anpr_result.camera_id,
            plate,
            anpr_result.final_confidence,
            watchlist_entry.get("status", "UNKNOWN"),
        )

        self.total_alerts += 1
        _broadcast_alert(alert)
        return alert

    def _persist_alert(self, alert: dict) -> None:
        """Persist alert to DB if available, otherwise to in-memory store."""
        if self.db:
            try:
                from backend.app.models.db.alert import Alert as AlertModel
                db_alert = AlertModel(
                    camera_id=alert["camera_id"],
                    registration_number=alert["registration_number"],
                    timestamp=datetime.fromisoformat(alert["timestamp"]),
                    location=alert.get("location"),
                    latitude=alert.get("latitude"),
                    longitude=alert.get("longitude"),
                    vehicle_detection_confidence=alert.get("vehicle_detection_confidence"),
                    plate_detection_confidence=alert.get("plate_detection_confidence"),
                    ocr_confidence=alert.get("ocr_confidence"),
                    overall_confidence=alert["overall_confidence"],
                    watchlist_status=alert["watchlist_status"],
                    priority=alert["priority"],
                    severity=alert["severity"],
                    pts_ms=alert.get("pts_ms"),
                    status="NEW",
                    # backward compat
                    plate_number=alert["registration_number"],
                    confidence=alert["overall_confidence"],
                    risk_category=alert["watchlist_status"],
                )
                self.db.add(db_alert)
                self.db.commit()
                self.db.refresh(db_alert)
                alert["id"] = str(db_alert.id)
                _alert_store[alert["id"]] = alert
            except Exception as exc:
                logger.error("Failed to persist alert to DB: %s", exc)
                _alert_store[alert["id"]] = alert
        else:
            _alert_store[alert["id"]] = alert


def get_all_alerts(limit: int = 100) -> list[dict]:
    """Get recent alerts from in-memory store and database."""
    try:
        from backend.app.db.session import SessionLocal
        if SessionLocal:
            with SessionLocal() as db:
                from backend.app.models.db.alert import Alert as DBAlert
                db_alerts = db.query(DBAlert).order_by(DBAlert.timestamp.desc()).limit(limit).all()
                for dba in db_alerts:
                    aid = str(dba.id)
                    if aid not in _alert_store:
                        _alert_store[aid] = dba.to_dict()
    except Exception:
        pass

    alerts = list(_alert_store.values())
    alerts.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
    return alerts[:limit]


def get_alert(alert_id: str) -> Optional[dict]:
    if alert_id in _alert_store:
        return _alert_store[alert_id]
    try:
        from backend.app.db.session import SessionLocal
        if SessionLocal:
            with SessionLocal() as db:
                from backend.app.models.db.alert import Alert as DBAlert
                dba = db.query(DBAlert).filter(DBAlert.id == alert_id).first()
                if dba:
                    d = dba.to_dict()
                    _alert_store[alert_id] = d
                    return d
    except Exception:
        pass
    return None


def acknowledge_alert(alert_id: str, username: str) -> bool:
    if alert_id in _alert_store:
        _alert_store[alert_id]["status"] = "REVIEWING"
        _alert_store[alert_id]["acknowledged_by"] = username
        _alert_store[alert_id]["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
        return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _determine_severity(confidence: float) -> str:
    if confidence >= settings.alert_high_confidence:
        return "HIGH"
    elif confidence >= settings.alert_medium_confidence:
        return "MEDIUM"
    return "LOW"


def _build_alert(
    anpr_result: ANPRResult,
    watchlist_entry: dict,
    severity: str,
    camera_location: Optional[str] = None,
    camera_lat: Optional[float] = None,
    camera_lon: Optional[float] = None,
) -> dict:
    now = datetime.now(timezone.utc)
    breakdown = anpr_result.confidence_breakdown or {}

    return {
        "id": str(uuid.uuid4()),
        "camera_id": anpr_result.camera_id,
        "registration_number": anpr_result.normalised_plate,
        "timestamp": now.isoformat(),
        "pts_ms": anpr_result.pts_ms,
        "frame_index": anpr_result.frame_index,

        # Location
        "location": camera_location,
        "latitude": camera_lat,
        "longitude": camera_lon,

        # Confidence breakdown
        "vehicle_detection_confidence": breakdown.get("vehicle_detection_confidence"),
        "plate_detection_confidence": breakdown.get("plate_detection_confidence"),
        "ocr_confidence": breakdown.get("ocr_confidence"),
        "overall_confidence": anpr_result.final_confidence,
        "confidence_breakdown": breakdown,

        # Watchlist
        "watchlist_status": watchlist_entry.get("status", "UNKNOWN"),
        "watchlist_description": watchlist_entry.get("description", ""),
        "watchlist_source": watchlist_entry.get("source", "SYNTHETIC_DEMO"),
        "priority": watchlist_entry.get("priority", severity),
        "person_name": watchlist_entry.get("person_name"),

        # Alert
        "severity": severity,
        "status": "NEW",
        "acknowledged_by": None,
        "acknowledged_at": None,

        "created_at": now.isoformat(),
    }
