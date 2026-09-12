#!/usr/bin/env python3
"""
Start the Sentinel Gujarat AI Inference Pipeline.

Usage:
    python scripts/start_stream.py cam01
    
This script:
1. Connects to the RTSP stream for the given camera.
2. Runs YOLOv8 vehicle detection.
3. Crops detected vehicles and runs PaddleOCR ANPR.
4. Generates confidence scores.
5. Checks the Watchlist and triggers Alerts.
6. Pushes results to the database and WebSocket frontend.
"""
import sys
import os
import argparse
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.app.core.config import settings
from backend.app.core.logging_config import get_logger
from backend.app.db.session import SessionLocal
from backend.app.services.camera.catalogue import fetch_catalogue
from backend.app.services.detection.vehicle_detector import VehicleDetector
from backend.app.services.alerting.watchlist_service import get_watchlist_provider
from backend.app.services.alerting.alert_engine import AlertEngine
from backend.app.services.tracking.journey_correlator import get_correlator
from backend.app.models.anpr import ANPRResult

logger = get_logger(__name__)

async def run_pipeline(camera_id: str):
    logger.info("Starting AI inference pipeline for camera: %s", camera_id)
    
    # 1. Fetch camera details
    catalogue = fetch_catalogue()
    cam = next((c for c in catalogue if c.camera_id == camera_id), None)
    if not cam:
        logger.error("Camera %s not found in catalogue", camera_id)
        sys.exit(1)
        
    rtsp_url = cam.rtsp_url
    if not rtsp_url:
        logger.error("No RTSP URL found for camera %s", camera_id)
        sys.exit(1)
        
    logger.info("Target RTSP: %s (TCP)", rtsp_url)
    
    # 2. Setup DB and Services
    db = SessionLocal() if SessionLocal else None
    
    watchlist = get_watchlist_provider(db)
    alert_engine = AlertEngine(watchlist, db)
    correlator = get_correlator(db)
    
    # Setup the YOLO detector (it will lazy-load PaddleOCR internally)
    detector = VehicleDetector(camera_id=camera_id, rtsp_url=rtsp_url)
    
    # 3. Main processing loop
    try:
        # Start generator
        for event in detector.process_stream():
            
            # Record sighting in tracking correlator
            # (ANPRResult from detector)
            if event.final_confidence >= settings.anpr_confidence_threshold:
                
                # Check for alert
                alert = alert_engine.process(
                    event,
                    camera_location=cam.location,
                    camera_lat=cam.latitude,
                    camera_lon=cam.longitude
                )
                
                # Add to journey history
                correlator.add_sighting(
                    plate=event.normalised_plate,
                    camera_id=event.camera_id,
                    event_time=datetime.now(timezone.utc),
                    location=cam.location,
                    latitude=cam.latitude,
                    longitude=cam.longitude,
                    pts_ms=event.pts_ms,
                    overall_confidence=event.final_confidence,
                    detection_event_id=alert.get("id") if alert else None
                )
                
    except KeyboardInterrupt:
        logger.info("Pipeline stopped by user")
    except Exception as e:
        logger.error("Pipeline crashed: %s", e, exc_info=True)
    finally:
        if db:
            db.close()
            
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start Sentinel AI Inference Pipeline")
    parser.add_argument("camera_id", help="Camera ID to process (e.g., cam01)")
    args = parser.parse_args()
    
    # Run async loop (though process_stream is currently a synchronous generator,
    # we wrap it for future async expansions like websocket pushing).
    asyncio.run(run_pipeline(args.camera_id))
