"""
Watchlist service — SYNTHETIC/DEMO data provider.

IMPORTANT NOTICE:
  This module contains SYNTHETIC data for PoC demonstration.
  It is NOT connected to any real government database.
  
  Real integrations would implement the WatchlistProvider interface
  against VAHAN, SARTHI, eGujCop etc.

Provider interface:
    WatchlistProvider.lookup(plate: str) -> Optional[dict]
    WatchlistProvider.add(entry: dict) -> None
    WatchlistProvider.list_all() -> list[dict]

Current implementation:
    SyntheticWatchlistProvider — uses an in-memory dict

Future implementations (same interface):
    DatabaseWatchlistProvider  — reads from PostgreSQL watchlist_entries table
    VahanWatchlistProvider     — calls VAHAN API (requires government access)
    EGujCopWatchlistProvider   — calls eGujCop API
"""
from __future__ import annotations

from typing import Optional
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# WatchlistProvider interface
# ---------------------------------------------------------------------------

class WatchlistProvider:
    """Abstract interface for watchlist backends."""

    def lookup(self, registration_number: str) -> Optional[dict]:
        """
        Look up a plate number in the watchlist.
        Returns entry dict if found, None otherwise.
        """
        raise NotImplementedError

    def add(self, entry: dict) -> None:
        """Add an entry to the watchlist."""
        raise NotImplementedError

    def list_all(self, active_only: bool = True) -> list[dict]:
        """Return all watchlist entries."""
        raise NotImplementedError

    def remove(self, registration_number: str) -> bool:
        """Remove an entry. Returns True if found and removed."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Synthetic demo implementation
# ---------------------------------------------------------------------------

class SyntheticWatchlistProvider(WatchlistProvider):
    """
    SYNTHETIC / DEMO DATA ONLY.
    
    Pre-loaded with representative Gujarat plate numbers.
    Used for hackathon demonstration.
    
    Status codes:
        STOLEN      — vehicle reported stolen
        WANTED      — vehicle associated with wanted person
        MISSING     — vehicle reported missing
        BLACKLISTED — vehicle banned from certain areas/events
        SURVEILLANCE — under monitoring
    
    Priority:
        HIGH   — immediate action required
        MEDIUM — priority investigation
        LOW    — routine monitoring
    """

    def __init__(self):
        # Initialize with synthetic seed data
        # These are FAKE plates for demonstration only
        self._entries: dict[str, dict] = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """Seed synthetic watchlist with representative demo entries."""
        seed = [
            {
                "registration_number": "GJ01AB1234",
                "status": "STOLEN",
                "priority": "HIGH",
                "description": "DEMO: Vehicle reported stolen from Ahmedabad (synthetic data)",
                "person_name": "DEMO SUSPECT",
                "source": "SYNTHETIC_DEMO",
                "is_active": True,
            },
            {
                "registration_number": "GJ05CD5678",
                "status": "WANTED",
                "priority": "HIGH",
                "description": "DEMO: Associated with wanted person (synthetic data)",
                "person_name": "DEMO WANTED PERSON",
                "source": "SYNTHETIC_DEMO",
                "is_active": True,
            },
            {
                "registration_number": "GJ18EF9012",
                "status": "MISSING",
                "priority": "MEDIUM",
                "description": "DEMO: Vehicle reported missing from Surat (synthetic data)",
                "person_name": None,
                "source": "SYNTHETIC_DEMO",
                "is_active": True,
            },
            {
                "registration_number": "GJ27GH3456",
                "status": "BLACKLISTED",
                "priority": "MEDIUM",
                "description": "DEMO: Vehicle blacklisted for traffic violations (synthetic data)",
                "person_name": None,
                "source": "SYNTHETIC_DEMO",
                "is_active": True,
            },
            {
                "registration_number": "MH12XY9999",
                "status": "SURVEILLANCE",
                "priority": "LOW",
                "description": "DEMO: Out-of-state vehicle under routine monitoring (synthetic data)",
                "person_name": None,
                "source": "SYNTHETIC_DEMO",
                "is_active": True,
            },
            # Add some cam01-compatible plates that might appear in stream
            {
                "registration_number": "GJ01AA0001",
                "status": "STOLEN",
                "priority": "HIGH",
                "description": "DEMO: Stolen vehicle for cam01 demonstration",
                "person_name": "DEMO PERSON",
                "source": "SYNTHETIC_DEMO",
                "is_active": True,
            },
        ]
        for entry in seed:
            plate = entry["registration_number"]
            self._entries[plate] = entry
            logger.debug("Watchlist seeded: %s (%s)", plate, entry["status"])

        logger.info(
            "SYNTHETIC watchlist loaded with %d entries. "
            "NOT connected to VAHAN/SARTHI/eGujCop — DEMO DATA ONLY.",
            len(self._entries),
        )

    def lookup(self, registration_number: str) -> Optional[dict]:
        """Look up a plate. Returns entry or None."""
        plate = registration_number.upper().strip().replace(" ", "").replace("-", "")
        entry = self._entries.get(plate)
        if entry and entry.get("is_active", True):
            logger.info(
                "WATCHLIST MATCH: %s -> status=%s priority=%s [SYNTHETIC DEMO]",
                plate, entry["status"], entry["priority"],
            )
            return entry
        return None

    def add(self, entry: dict) -> None:
        plate = entry.get("registration_number", "").upper().strip()
        if not plate:
            raise ValueError("registration_number is required")
        entry["registration_number"] = plate
        if "source" not in entry:
            entry["source"] = "SYNTHETIC_DEMO"
        self._entries[plate] = entry
        logger.info("Watchlist entry added: %s", plate)

    def list_all(self, active_only: bool = True) -> list[dict]:
        entries = list(self._entries.values())
        if active_only:
            entries = [e for e in entries if e.get("is_active", True)]
        return entries

    def remove(self, registration_number: str) -> bool:
        plate = registration_number.upper().strip()
        if plate in self._entries:
            self._entries[plate]["is_active"] = False
            return True
        return False


# ---------------------------------------------------------------------------
# Database-backed implementation
# ---------------------------------------------------------------------------

class DatabaseWatchlistProvider(WatchlistProvider):
    """
    Watchlist backed by the PostgreSQL watchlist_entries table.
    Requires active DB connection.
    """

    def __init__(self, db_session):
        self.db = db_session

    def lookup(self, registration_number: str) -> Optional[dict]:
        plate = registration_number.upper().strip().replace(" ", "").replace("-", "")
        try:
            from backend.app.models.db.watchlist import WatchlistEntry
            entry = (
                self.db.query(WatchlistEntry)
                .filter(
                    WatchlistEntry.registration_number == plate,
                    WatchlistEntry.is_active == True,
                )
                .first()
            )
            if entry:
                return {
                    "registration_number": entry.registration_number,
                    "status": entry.status,
                    "priority": entry.priority,
                    "description": entry.description,
                    "person_name": entry.person_name,
                    "source": entry.source,
                    "is_active": entry.is_active,
                }
        except Exception as exc:
            logger.error("DB watchlist lookup failed: %s", exc)
        return None

    def add(self, entry: dict) -> None:
        try:
            from backend.app.models.db.watchlist import WatchlistEntry
            plate = entry.get("registration_number", "").upper().strip()
            db_entry = WatchlistEntry(
                registration_number=plate,
                status=entry.get("status", "SURVEILLANCE"),
                priority=entry.get("priority", "MEDIUM"),
                description=entry.get("description"),
                person_name=entry.get("person_name"),
                source=entry.get("source", "SYNTHETIC_DEMO"),
            )
            self.db.add(db_entry)
            self.db.commit()
        except Exception as exc:
            logger.error("DB watchlist add failed: %s", exc)

    def list_all(self, active_only: bool = True) -> list[dict]:
        try:
            from backend.app.models.db.watchlist import WatchlistEntry
            q = self.db.query(WatchlistEntry)
            if active_only:
                q = q.filter(WatchlistEntry.is_active == True)
            return [
                {
                    "registration_number": e.registration_number,
                    "status": e.status,
                    "priority": e.priority,
                    "description": e.description,
                    "person_name": e.person_name,
                    "source": e.source,
                    "is_active": e.is_active,
                }
                for e in q.all()
            ]
        except Exception as exc:
            logger.error("DB watchlist list failed: %s", exc)
            return []

    def remove(self, registration_number: str) -> bool:
        plate = registration_number.upper().strip()
        try:
            from backend.app.models.db.watchlist import WatchlistEntry
            entry = (
                self.db.query(WatchlistEntry)
                .filter(WatchlistEntry.registration_number == plate)
                .first()
            )
            if entry:
                entry.is_active = False
                self.db.commit()
                return True
        except Exception as exc:
            logger.error("DB watchlist remove failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Factory — return appropriate provider based on DB availability
# ---------------------------------------------------------------------------

_synthetic_provider: Optional[SyntheticWatchlistProvider] = None


def get_watchlist_provider(db_session=None) -> WatchlistProvider:
    """
    Returns the best available watchlist provider:
      - DatabaseWatchlistProvider if db_session is provided and working
      - SyntheticWatchlistProvider as fallback (always works)
    """
    global _synthetic_provider

    if db_session is not None:
        try:
            # Test DB connection
            db_provider = DatabaseWatchlistProvider(db_session)
            entries = db_provider.list_all()
            if entries:
                return db_provider
        except Exception:
            pass

    # Fall back to synthetic
    if _synthetic_provider is None:
        _synthetic_provider = SyntheticWatchlistProvider()
    return _synthetic_provider
