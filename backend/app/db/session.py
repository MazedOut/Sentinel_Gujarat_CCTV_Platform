"""
Database session management.
Provides get_db() for FastAPI dependency injection with SQLite fallback.
"""
from typing import Generator, Optional, Tuple
import os
import socket
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from backend.app.core.config import settings
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)


def _is_server_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _make_engine() -> Tuple[Optional[Engine], str]:
    """
    Creates the SQLAlchemy engine.
    Supports PostgreSQL if available; gracefully falls back to local SQLite so
    the platform is never blocked and always maintains persistence.
    """
    db_url = settings.effective_database_url or ""
    
    # 1. If explicit SQLite URL is configured:
    if db_url.startswith("sqlite"):
        try:
            eng = create_engine(
                db_url,
                connect_args={"check_same_thread": False},
                echo=False,
            )
            return eng, "sqlite"
        except Exception as e:
            logger.error(f"Failed to create SQLite engine from {db_url}: {e}")

    # 2. If PostgreSQL is configured, probe host and port:
    if db_url.startswith("postgresql"):
        host = settings.postgres_host or "localhost"
        try:
            port = int(settings.postgres_port or 5432)
        except (ValueError, TypeError):
            port = 5432
            
        if _is_server_listening(host, port, timeout=0.5):
            try:
                eng = create_engine(
                    db_url,
                    pool_pre_ping=True,
                    pool_size=5,
                    max_overflow=10,
                    echo=False,
                    connect_args={"connect_timeout": 2},
                )
                return eng, "postgresql"
            except Exception as e:
                logger.warning(f"PostgreSQL listening but connection failed: {e}. Falling back to SQLite.")

    # 3. Fallback: local SQLite file database for resilient zero-config persistence
    sqlite_fallback_url = "sqlite:///./sentinel_gujarat.db"
    try:
        eng = create_engine(
            sqlite_fallback_url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
        return eng, "sqlite"
    except Exception as e:
        logger.error(f"Failed to create fallback SQLite engine: {e}")
        return None, "none"


engine, db_type = _make_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None


def init_db():
    """Create all tables in the database if they don't already exist."""
    if engine is None:
        return
    try:
        from backend.app.db.base import Base
        import backend.app.models.db  # register all models
        Base.metadata.create_all(bind=engine)
        logger.info(f"Database schema verified/created successfully using {db_type.upper()}.")
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")


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

