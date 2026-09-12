from sqlalchemy.orm import Session
from typing import Optional

from backend.app.models.db.watchlist import Watchlist
from backend.app.models.db.alert import Alert
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)

class WatchlistMatcher:
    def __init__(self, db_session: Session):
        self.db = db_session

    def process_plate_detection(self, plate_number: str, camera_id: str, confidence: float, frame_path: str = None) -> Optional[Alert]:
        """
        Check if a detected plate is on the watchlist and create an alert if it is.
        """
        # Normalize the plate number before checking
        normalized_plate = plate_number.replace(" ", "").replace("-", "").upper()
        
        watchlist_entry = self.db.query(Watchlist).filter(Watchlist.plate_number == normalized_plate).first()
        
        if watchlist_entry:
            logger.warning(f"WATCHLIST MATCH: Plate {normalized_plate} detected at {camera_id} with risk {watchlist_entry.risk_category}")
            
            # Create a new alert
            alert = Alert(
                plate_number=normalized_plate,
                camera_id=camera_id,
                confidence=confidence,
                frame_path=frame_path,
                risk_category=watchlist_entry.risk_category,
                status="NEW"
            )
            
            self.db.add(alert)
            self.db.commit()
            self.db.refresh(alert)
            return alert
            
        return None
