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
    Supports PostgreSQL (Supabase, local, or managed cloud) if available;
    gracefully falls back to SQLite so the platform is never blocked and always
    maintains persistence.
    """
    db_url = (settings.effective_database_url or "").strip()
    
    # Supabase and Heroku style connection strings often start with postgres://
    # SQLAlchemy 1.4+ strictly requires postgresql://
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]

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

    # 2. If PostgreSQL is configured (Supabase, AWS RDS, local):
    if db_url.startswith("postgresql"):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            host = parsed.hostname or settings.postgres_host or "localhost"
            port = parsed.port or settings.postgres_port or 5432
        except Exception:
            host = settings.postgres_host or "localhost"
            port = settings.postgres_port or 5432

        is_local = host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
        
        # Fast local probe to avoid hanging when local postgres service is stopped
        can_attempt = True
        if is_local:
            can_attempt = _is_server_listening(host, port, timeout=0.5)

        if can_attempt:
            try:
                connect_args = {"connect_timeout": 6}
                # For remote cloud databases like Supabase, ensure SSL is enabled if not already in URL
                if not is_local and "sslmode" not in db_url:
                    connect_args["sslmode"] = "require"

                eng = create_engine(
                    db_url,
                    pool_pre_ping=True,
                    pool_recycle=300,
                    pool_size=5,
                    max_overflow=10,
                    echo=False,
                    connect_args=connect_args,
                )
                # Test connection immediately
                with eng.connect() as conn:
                    pass
                logger.info(f"Connected to PostgreSQL database at {host}:{port}")
                return eng, "postgresql"
            except Exception as e:
                logger.warning(f"PostgreSQL connection to {host}:{port} failed ({e}). Falling back to SQLite.")

    # 3. Fallback: local SQLite file database for resilient zero-config persistence
    # In serverless environments (e.g. Vercel), current directory is read-only, use /tmp
    sqlite_dir = "."
    if os.environ.get("VERCEL") or not os.access(".", os.W_OK):
        import tempfile
        sqlite_dir = tempfile.gettempdir()

    sqlite_path = os.path.join(sqlite_dir, "sentinel_gujarat.db")
    sqlite_fallback_url = f"sqlite:///{sqlite_path}"
    try:
        eng = create_engine(
            sqlite_fallback_url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
        logger.info(f"Using SQLite database at {sqlite_path}")
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

