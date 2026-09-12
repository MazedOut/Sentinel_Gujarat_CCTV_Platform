"""
User DB model — RBAC with roles: ADMIN, POLICE_OFFICER, DEPARTMENT_USER
"""
from sqlalchemy import Column, String, Integer, DateTime, Boolean
from datetime import datetime, timezone

from backend.app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=True)
    hashed_password = Column(String(200), nullable=False)
    full_name = Column(String(200), nullable=True)
    department = Column(String(200), nullable=True)  # for DEPARTMENT_USER scope

    # Roles: ADMIN | POLICE_OFFICER | DEPARTMENT_USER
    role = Column(String(30), nullable=False, default="DEPARTMENT_USER")

    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
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

    def __repr__(self):
        return f"<User(username='{self.username}', role='{self.role}')>"
