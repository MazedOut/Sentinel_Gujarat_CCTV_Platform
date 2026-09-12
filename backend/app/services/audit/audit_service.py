"""
Audit logging service.

Records all significant user and system actions.
The log is append-only — normal users cannot edit or delete entries.

IMPORTANT: This is a security-critical service.
Do not add methods to bulk-delete audit records.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import uuid

from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)

# In-memory store when DB unavailable
_audit_log: list[dict] = []


class AuditService:
    """Records user/system actions for compliance and investigation."""

    def __init__(self, db_session=None):
        self.db = db_session

    def log(
        self,
        action: str,
        username: Optional[str] = None,
        user_id: Optional[int] = None,
        role: Optional[str] = None,
        ip_address: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        description: Optional[str] = None,
        outcome: str = "SUCCESS",
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Record an audit event.

        Args:
            action: What happened (e.g. LOGIN, VIEW_ALERT, ACK_ALERT)
            username: Who did it
            user_id: DB ID of the user
            role: User's role at time of action
            ip_address: Client IP
            resource_type: Type of affected resource (camera|alert|vehicle|watchlist)
            resource_id: ID of affected resource
            description: Human-readable description
            outcome: SUCCESS | FAILURE | DENIED
            metadata: Additional structured data

        Returns:
            The audit record dict.
        """
        now = datetime.now(timezone.utc)
        record = {
            "id": str(uuid.uuid4()),
            "timestamp": now.isoformat(),
            "action": action,
            "username": username,
            "user_id": user_id,
            "role": role,
            "ip_address": ip_address,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id else None,
            "description": description,
            "outcome": outcome,
            "metadata": metadata or {},
        }

        logger.info(
            "[AUDIT] action=%s user=%s resource=%s/%s outcome=%s",
            action, username, resource_type, resource_id, outcome,
        )

        # Persist to DB if available
        if self.db:
            self._persist_to_db(record)
        else:
            _audit_log.append(record)

        return record

    def get_logs(
        self,
        limit: int = 100,
        username: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> list[dict]:
        """Get audit log entries (from DB or in-memory)."""
        if self.db:
            return self._query_db(limit, username, action, resource_type)
        
        logs = list(_audit_log)
        if username:
            logs = [l for l in logs if l.get("username") == username]
        if action:
            logs = [l for l in logs if l.get("action") == action]
        if resource_type:
            logs = [l for l in logs if l.get("resource_type") == resource_type]
        logs.sort(key=lambda x: x["timestamp"], reverse=True)
        return logs[:limit]

    def _persist_to_db(self, record: dict) -> None:
        try:
            from backend.app.models.db.audit_log import AuditLog
            entry = AuditLog(
                user_id=record.get("user_id"),
                username=record.get("username"),
                role=record.get("role"),
                ip_address=record.get("ip_address"),
                action=record["action"],
                resource_type=record.get("resource_type"),
                resource_id=record.get("resource_id"),
                description=record.get("description"),
                extra_data=record.get("metadata"),  # stored as extra_data in DB
                outcome=record.get("outcome", "SUCCESS"),
                timestamp=datetime.fromisoformat(record["timestamp"]),
            )
            self.db.add(entry)
            self.db.commit()
        except Exception as exc:
            logger.error("Failed to persist audit log to DB: %s", exc)
            _audit_log.append(record)

    def _query_db(
        self,
        limit: int,
        username: Optional[str],
        action: Optional[str],
        resource_type: Optional[str],
    ) -> list[dict]:
        try:
            from backend.app.models.db.audit_log import AuditLog
            q = self.db.query(AuditLog).order_by(AuditLog.timestamp.desc())
            if username:
                q = q.filter(AuditLog.username == username)
            if action:
                q = q.filter(AuditLog.action == action)
            if resource_type:
                q = q.filter(AuditLog.resource_type == resource_type)
            entries = q.limit(limit).all()
            return [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "action": e.action,
                    "username": e.username,
                    "role": e.role,
                    "ip_address": e.ip_address,
                    "resource_type": e.resource_type,
                    "resource_id": e.resource_id,
                    "description": e.description,
                    "outcome": e.outcome,
                    "extra_data": e.extra_data,
                }
                for e in entries
            ]
        except Exception as exc:
            logger.error("DB audit query failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Common action constants
# ---------------------------------------------------------------------------

class AuditAction:
    # Auth
    LOGIN           = "LOGIN"
    LOGOUT          = "LOGOUT"
    LOGIN_FAILED    = "LOGIN_FAILED"
    TOKEN_REFRESH   = "TOKEN_REFRESH"

    # Cameras
    VIEW_CAMERA     = "VIEW_CAMERA"
    LIST_CAMERAS    = "LIST_CAMERAS"
    SYNC_CAMERAS    = "SYNC_CAMERAS"
    START_STREAM    = "START_STREAM"
    STOP_STREAM     = "STOP_STREAM"

    # Alerts
    VIEW_ALERT      = "VIEW_ALERT"
    LIST_ALERTS     = "LIST_ALERTS"
    ACK_ALERT       = "ACK_ALERT"
    CLOSE_ALERT     = "CLOSE_ALERT"
    FLAG_FALSE_POS  = "FLAG_FALSE_POSITIVE"

    # Vehicles
    SEARCH_VEHICLE  = "SEARCH_VEHICLE"
    VIEW_JOURNEY    = "VIEW_JOURNEY"
    INFER_ROUTE     = "INFER_ROUTE"

    # Watchlist
    VIEW_WATCHLIST  = "VIEW_WATCHLIST"
    ADD_WATCHLIST   = "ADD_WATCHLIST"
    REMOVE_WATCHLIST= "REMOVE_WATCHLIST"

    # Audit
    VIEW_AUDIT      = "VIEW_AUDIT"

    # Users
    CREATE_USER     = "CREATE_USER"
    UPDATE_USER     = "UPDATE_USER"
    DEACTIVATE_USER = "DEACTIVATE_USER"


# Singleton (no DB)
_audit_service = AuditService()


def get_audit_service(db_session=None) -> AuditService:
    if db_session:
        return AuditService(db_session=db_session)
    return _audit_service
