"""
AuditLog DB model — immutable audit trail of all significant user actions.
"""
from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone

from backend.app.db.base import Base


class AuditLog(Base):
    """
    Append-only audit log. Normal users cannot modify or delete entries.
    Only ADMIN role can view full audit history.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Who
    user_id = Column(Integer, index=True, nullable=True)   # null for system events
    username = Column(String(100), index=True, nullable=True)
    role = Column(String(30), nullable=True)
    ip_address = Column(String(50), nullable=True)

    # What
    action = Column(String(100), nullable=False, index=True)
    # e.g. LOGIN | LOGOUT | VIEW_CAMERA | VIEW_ALERT | ACK_ALERT
    #       SEARCH_VEHICLE | VIEW_WATCHLIST | MODIFY_WATCHLIST
    #       VIEW_AUDIT | SYNC_CAMERAS | START_STREAM | STOP_STREAM

    # Resource
    resource_type = Column(String(50), nullable=True)  # camera | alert | vehicle | watchlist
    resource_id = Column(String(100), nullable=True)   # camera_id, alert id, plate etc

    # Detail
    description = Column(Text, nullable=True)
    extra_data = Column(JSONB, nullable=True)  # was 'metadata' — reserved by SQLAlchemy

    # Result
    outcome = Column(String(20), nullable=True)  # SUCCESS | FAILURE | DENIED

    # When
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def __repr__(self):
        return f"<AuditLog(id={self.id}, action='{self.action}', user='{self.username}')>"
