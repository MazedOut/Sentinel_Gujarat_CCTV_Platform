"""
Security utilities — password hashing, JWT, RBAC.

RULES:
  - Passwords are hashed with bcrypt via passlib (never stored plain)
  - JWTs are signed with HS256 using JWT_SECRET from environment
  - Role checking is done on the backend (not just frontend hiding)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
import bcrypt

from backend.app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing — using bcrypt directly (passlib 1.7.4 + bcrypt 5.0 incompatible)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: int,
    username: str,
    role: str,
    expires_minutes: Optional[int] = None,
) -> str:
    """Create a signed JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT access token.
    Returns the payload dict or None if invalid/expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# RBAC role constants
# ---------------------------------------------------------------------------

class Role:
    ADMIN           = "ADMIN"
    POLICE_OFFICER  = "POLICE_OFFICER"
    DEPARTMENT_USER = "DEPARTMENT_USER"

    ALL = [ADMIN, POLICE_OFFICER, DEPARTMENT_USER]


# Permission matrix
_PERMISSIONS = {
    # ADMIN has everything
    Role.ADMIN: {
        "cameras:read", "cameras:write", "cameras:sync",
        "alerts:read", "alerts:write", "alerts:acknowledge",
        "watchlist:read", "watchlist:write",
        "detections:read",
        "vehicles:read",
        "audit:read",
        "users:read", "users:write",
        "routes:read", "routes:infer",
        "streams:read",
    },
    Role.POLICE_OFFICER: {
        "cameras:read",
        "alerts:read", "alerts:acknowledge",
        "watchlist:read",
        "detections:read",
        "vehicles:read",
        "routes:read", "routes:infer",
        "streams:read",
    },
    Role.DEPARTMENT_USER: {
        "cameras:read",
        "alerts:read",
        "detections:read",
        "vehicles:read",
    },
}


def has_permission(role: str, permission: str) -> bool:
    """Check if a role has the given permission."""
    return permission in _PERMISSIONS.get(role, set())


def require_permission(role: str, permission: str) -> None:
    """Raise PermissionError if role lacks the permission."""
    if not has_permission(role, permission):
        raise PermissionError(
            f"Role '{role}' does not have permission '{permission}'"
        )
