"""
Watchlist DB model — synthetic watchlist for PoC.
IMPORTANT: This is SYNTHETIC/DEMO data only. 
NOT connected to real government databases (VAHAN/SARTHI/eGujCop).
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean
from datetime import datetime, timezone

from backend.app.db.base import Base


class WatchlistEntry(Base):
    """
    SYNTHETIC WATCHLIST — DEMO DATA ONLY
    
    In a real deployment this would connect to:
      - VAHAN (vehicle registration)
      - SARTHI (driving license) 
      - eGujCop (crime records)
    
    The WatchlistProvider interface supports swappable backends.
    """
    __tablename__ = "watchlist_entries"

    id = Column(Integer, primary_key=True, index=True)

    # Registration number — normalized form: GJ01AB1234
    registration_number = Column(String(20), unique=True, index=True, nullable=False)

    # What type of entry
    # STOLEN | WANTED | MISSING | BLACKLISTED | SURVEILLANCE
    status = Column(String(30), nullable=False, default="SURVEILLANCE")

    # Priority: HIGH | MEDIUM | LOW
    priority = Column(String(10), nullable=False, default="MEDIUM")

    # Description / reason for watchlist
    description = Column(Text, nullable=True)

    # Optional associated person
    person_name = Column(String(200), nullable=True)
    person_description = Column(Text, nullable=True)

    # Data source — always mark as SYNTHETIC for PoC
    source = Column(String(50), nullable=False, default="SYNTHETIC_DEMO")
    is_active = Column(Boolean, default=True)

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
        return f"<WatchlistEntry(plate='{self.registration_number}', status='{self.status}', priority='{self.priority}')>"


# Backward compat alias
Watchlist = WatchlistEntry
