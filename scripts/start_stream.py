#!/usr/bin/env python3
"""
Start the Drishti AI Inference Pipeline.

Usage:
    python scripts/start_stream.py cam01
    
This script:
1. Connects to the RTSP/HLS stream for the given camera via StreamManager.
2. Runs YOLOv8 vehicle detection with ByteTrack tracking.
3. Crops detected vehicles and runs PaddleOCR ANPR pipeline.
4. Computes multi-factor confidence scores (Plate × OCR × Syntax).
5. Matches against the Law Enforcement Watchlist and triggers Alerts.
6. Updates multi-camera Vehicle Journey correlation and persists events.
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
from backend.app.services.streaming.stream_manager import StreamManager, FrameData
from backend.app.services.detection.vehicle_detector import VehicleDetector
from backend.app.services.anpr.anpr_pipeline import ANPRPipeline
from backend.app.services.alerting.watchlist_service import get_watchlist_provider
from backend.app.services.alerting.alert_engine import AlertEngine
from backend.app.services.tracking.journey_correlator import get_correlator

logger = get_logger(__name__)


def run_pipeline(camera_id: str):
    logger.info("Starting Drishti AI inference pipeline for camera: %s", camera_id)
    
    # 1. Fetch camera details from catalogue
    catalogue = fetch_catalogue()
    cam = next((c for c in catalogue if c.camera_id == camera_id), None)
    if not cam:
        logger.error("Camera %s not found in catalogue", camera_id)
        sys.exit(1)
        
    rtsp_url = cam.rtsp_url
    if not rtsp_url:
        logger.error("No RTSP URL found for camera %s", camera_id)
        sys.exit(1)
        
    logger.info("Target RTSP: %s (TCP transport)", rtsp_url)
    
    # 2. Setup Database and Services
    db = SessionLocal() if SessionLocal else None
    watchlist = get_watchlist_provider(db)
    alert_engine = AlertEngine(watchlist, db)
    correlator = get_correlator(db)
    
    # 3. Setup YOLO Detector and ANPR Engine
    logger.info("Initializing YOLOv8 vehicle detector and PaddleOCR ANPR pipeline...")
    detector = VehicleDetector(
        confidence_threshold=0.35,
        frame_skip=3,
    )
    detector.load()
    
    anpr = ANPRPipeline()
    anpr.load()
    logger.info("AI models initialized and warmed up successfully.")
    
    # 4. Frame processing callback
    def on_frame(fd: FrameData):
        fd_result = detector.detect(fd)
        if fd_result is None or not fd_result.has_vehicles:
            return

        for vehicle in fd_result.detections:
            anpr_result = anpr.process(fd.frame, vehicle)
            if anpr_result and anpr_result.final_confidence >= settings.anpr_confidence_threshold:
                # Check for Watchlist Alert
                alert = alert_engine.process(
                    anpr_result,
                    camera_location=cam.location,
                    camera_lat=cam.latitude,
                    camera_lon=cam.longitude,
                )
                
                # Record sighting in tracking correlator
                correlator.add_sighting(
                    plate=anpr_result.normalised_plate,
                    camera_id=anpr_result.camera_id,
                    event_time=datetime.now(timezone.utc),
                    location=cam.location,
                    latitude=cam.latitude,
                    longitude=cam.longitude,
                    pts_ms=anpr_result.pts_ms,
                    overall_confidence=anpr_result.final_confidence,
                    detection_event_id=alert.get("id") if alert else None,
                )
                
                logger.info(
                    "[%s] Sighting recorded: %s (conf=%.2f, alert=%s)",
                    camera_id,
                    anpr_result.normalised_plate,
                    anpr_result.final_confidence,
                    "YES" if alert else "NO",
                )

    # 5. Connect and run stream ingestion
    stream_mgr = StreamManager(
        camera_id=camera_id,
        rtsp_url=rtsp_url,
        hls_url=cam.hls_url,
    )
    
    try:
        logger.info("Starting live frame ingestion loop for %s...", camera_id)
        stream_mgr.run(on_frame)
    except KeyboardInterrupt:
        logger.info("Pipeline stopped by user (SIGINT)")
    except Exception as e:
        logger.error("Pipeline crashed: %s", e, exc_info=True)
    finally:
        stream_mgr.stop()
        if db:
            db.close()
        logger.info("Pipeline closed cleanly for %s", camera_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start Drishti AI Inference Pipeline")
    parser.add_argument("camera_id", help="Camera ID to process (e.g., cam01)")
    args = parser.parse_args()
    
    run_pipeline(args.camera_id)
