"""
Complete database schema for Sentinel Gujarat.
All SQLAlchemy ORM models in one place.
"""
from backend.app.models.db.camera import Camera
from backend.app.models.db.user import User
from backend.app.models.db.alert import Alert
from backend.app.models.db.watchlist import Watchlist
from backend.app.models.db.audit_log import AuditLog
from backend.app.models.db.detection_event import DetectionEvent
from backend.app.models.db.vehicle_journey import VehicleJourney, JourneySegment

__all__ = [
    "Camera", "User", "Alert", "Watchlist", "AuditLog",
    "DetectionEvent", "VehicleJourney", "JourneySegment",
]
