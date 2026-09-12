"""
Database session management.
Provides get_db() for FastAPI dependency injection.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from backend.app.core.config import settings


import socket


def _is_server_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _make_engine():
    db_url = settings.effective_database_url or ""
    if not db_url:
        return None
    # Quick probe to see if Postgres port is actively listening
    host = settings.postgres_host or "localhost"
    try:
        port = int(settings.postgres_port or 5432)
    except (ValueError, TypeError):
        port = 5432
    if not _is_server_listening(host, port, timeout=0.5):
        return None
    try:
        engine = create_engine(
            db_url,
            pool_pre_ping=True,    # verify connections are alive before use
            pool_size=5,
            max_overflow=10,
            echo=False,
            connect_args={"connect_timeout": 2},
        )
        return engine
    except Exception:
        return None


engine = _make_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a DB session per request.
    Automatically closes the session after the request.
    
    Raises 503 if database is not configured.
    """
    if SessionLocal is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set DATABASE_URL in .env and restart."
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_optional() -> Generator[Session | None, None, None]:
    """
    Like get_db but yields None instead of raising if DB is unavailable.
    Use for endpoints that work with or without DB.
    """
    if SessionLocal is None:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
