from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


# Cross-database JSON type: renders JSONB on PostgreSQL, JSON on SQLite
JSONType = JSON().with_variant(JSONB, "postgresql")

