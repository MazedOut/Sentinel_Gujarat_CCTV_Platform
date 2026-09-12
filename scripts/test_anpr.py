"""
scripts/test_anpr.py
---------------------
MILESTONE 3 — ANPR Diagnostic

PURPOSE:
    Full end-to-end test: RTSP/local video → vehicle detection → ANPR

    For every vehicle detected, it:
      1. Runs ANPR on the vehicle crop
      2. Prints the plate reading and confidence score
      3. Saves annotated frames (with plate text overlaid)

HOW TO RUN:
    python -m scripts.test_anpr

    Options when prompted:
      - Enter a local video file path (best for ANPR testing — use a video
        with clear plate visibility)
      - Or leave blank to use Sentinel RTSP (if SENTINEL_BASE_URL is set)

WHAT GOOD OUTPUT LOOKS LIKE:
    [ANPR] cam=CAM-001  pts=5432ms  plate="GJ01AB1234"  conf=0.81  tier=HIGH  format=True
    [ANPR] cam=CAM-001  pts=5499ms  plate="GJ05CD5678"  conf=0.72  tier=MEDIUM  format=True

WHAT TO TELL US IF ANPR FINDS NOTHING:
    1. Do vehicles appear in the [DETECT] lines?
    2. Are the saved vehicle crops recognisable in detection_samples/?
    3. How close are vehicles to the camera in the video?
    ANPR works best when plates are >= 60px wide in the frame.
"""

import sys
import os
import time
import signal
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.core.logging_config import configure_logging, get_logger
from backend.app.core.config import settings, redact_credentials
from backend.app.services.camera.catalogue import fetch_catalogue
from backend.app.services.streaming.stream_manager import StreamManager, FrameData, StreamState
from backend.app.services.detection.vehicle_detector import VehicleDetector
from backend.app.services.detection.visualiser import Visualiser
from backend.app.services.anpr.anpr_pipeline import ANPRPipeline
from backend.app.models.detection import DetectionResult

import cv2
import numpy as np

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

TEST_DURATION_SEC = 120
SAVE_INTERVAL_SEC = 3.0
OUTPUT_DIR = Path("anpr_samples")

YOLO_MODEL = "yolo26n.pt"
CONFIDENCE_THRESHOLD = 0.35    # slightly lower than detection test — catch more vehicles
FRAME_SKIP = 3                 # GPU accelerated: process every 3rd frame

# ──────────────────────────────────────────────────────────────────────────────
# Shared state
# ──────────────────────────────────────────────────────────────────────────────

class _State:
    def __init__(self):
        self.detector = None
        self.anpr = None
        self.last_save = time.monotonic()
        self.save_counter = 0
        self.all_results = []   # list of ANPRResult
        self.lock = threading.Lock()

state = _State()


def on_frame(fd: FrameData) -> None:
    fd_result = state.detector.detect(fd)
    if fd_result is None:
        return   # skipped frame

    if not fd_result.has_vehicles:
        return   # no vehicles this frame

    anpr_results_this_frame = []

    for vehicle in fd_result.detections:
        anpr_result = state.anpr.process(fd.frame, vehicle)
        if anpr_result:
            anpr_results_this_frame.append(anpr_result)
            with state.lock:
                state.all_results.append(anpr_result)

            print(
                f"  [ANPR] cam={fd.camera_id:<10}  "
                f"pts={fd.pts_ms:>8.0f}ms  "
                f"plate={anpr_result.normalised_plate!r:<14}  "
                f"conf={anpr_result.final_confidence:.2f}  "
                f"tier={anpr_result.confidence_breakdown.get('tier','?'):<7}  "
                f"format={anpr_result.is_valid_format}"
            )

    # Save annotated frame periodically
    now = time.monotonic()
    if now - state.last_save >= SAVE_INTERVAL_SEC and anpr_results_this_frame:
        state.last_save = now
        _save_annotated(fd, fd_result, anpr_results_this_frame)


def _save_annotated(fd, fd_result, anpr_results):
    OUTPUT_DIR.mkdir(exist_ok=True)
    annotated = Visualiser.draw(fd.frame, fd_result)

    # Overlay plate text on annotated frame
    for ar in anpr_results:
        if ar.plate_bbox_in_frame:
            x1, y1, x2, y2 = ar.plate_bbox_in_frame.as_ints()
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 255), 2)
            label = f"{ar.normalised_plate} ({ar.final_confidence:.2f})"
            cv2.putText(annotated, label, (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    state.save_counter += 1
    fname = OUTPUT_DIR / f"anpr_{state.save_counter:04d}_{fd.camera_id}.jpg"
    cv2.imwrite(str(fname), annotated)
    print(f"  [SAVED] {fname}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    configure_logging()

    print("=" * 70)
    print("  SENTINEL GUJARAT — ANPR Diagnostic (Milestone 3)")
    print("=" * 70)
    print()

    # Source selection
    use_local = False
    local_path = None

    if not settings.sentinel_base_url:
        print("  SENTINEL_BASE_URL not set. Options:")
        print("  1) Enter a local video file path")
        print("  2) Press Enter to exit")
        print()
        local_path = input("  Local video path: ").strip()
        if not local_path:
            print("  Set SENTINEL_BASE_URL in .env and re-run.")
            sys.exit(0)
        if not Path(local_path).exists():
            print(f"  File not found: {local_path}")
            sys.exit(1)
        use_local = True

    if use_local:
        camera_id = "LOCAL"
        rtsp_url = local_path
    else:
        cameras = fetch_catalogue()
        rtsp_cameras = [(i+1, c) for i, c in enumerate(cameras) if c.has_rtsp]
        if not rtsp_cameras:
            print("No RTSP cameras in catalogue.")
            sys.exit(1)
        print("Available RTSP cameras:")
        for idx, cam in rtsp_cameras:
            print(f"  [{idx}] {cam.camera_id} | {cam.location or 'unknown'}")
        if len(rtsp_cameras) == 1:
            idx, chosen = rtsp_cameras[0]
        else:
            arg_choice = sys.argv[1].strip() if len(sys.argv) > 1 else None
            try:
                raw = arg_choice or input(f"\nCamera number [1-{len(cameras)}] (default 1): ").strip()
                num = int(raw) if raw else 1
            except (ValueError, EOFError):
                num = 1
            chosen = next((c for i, c in rtsp_cameras if i == num), rtsp_cameras[0][1])
        camera_id = chosen.camera_id
        rtsp_url = settings.rtsp_url_for(chosen.camera_id, authenticated=True)
        print(f"\n  Selected: {camera_id} (Stream: {redact_credentials(rtsp_url)})")

    print()

    # Load models
    print("  Loading YOLO26 vehicle detector...")
    state.detector = VehicleDetector(
        model_name=YOLO_MODEL,
        confidence_threshold=CONFIDENCE_THRESHOLD,
        frame_skip=FRAME_SKIP,
    )
    state.detector.load()

    print("  Loading ANPR pipeline (PaddleOCR 3.x)...")
    state.anpr = ANPRPipeline()
    state.anpr.load()

    print()
    print(f"  Running for {TEST_DURATION_SEC}s  |  frame_skip={FRAME_SKIP}")
    print(f"  Saving annotated frames to ./{OUTPUT_DIR}/")
    print()
    print("  [DETECT] lines = vehicle detected")
    print("  [ANPR]   lines = plate read")
    print()
    print("  Ctrl+C to stop early.")
    print()

    manager = StreamManager(camera_id=camera_id, rtsp_url=rtsp_url)
    start = time.monotonic()

    def _stop(sig, frame):
        print("\n  Stopping...")
        manager.stop()
    signal.signal(signal.SIGINT, _stop)

    manager.start(on_frame)

    try:
        while time.monotonic() - start < TEST_DURATION_SEC:
            if manager.info.state == StreamState.STOPPED:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    manager.stop()
    elapsed = time.monotonic() - start

    # Summary
    with state.lock:
        results = list(state.all_results)

    print()
    print("=" * 70)
    print("  ANPR SUMMARY")
    print("=" * 70)
    print(f"  Camera           : {camera_id}")
    print(f"  Wall time        : {elapsed:.1f}s")
    print(f"  Frames processed : {state.detector.total_frames_processed}")
    print(f"  Vehicles detected: {state.detector.total_detections}")
    print(f"  Plates read      : {state.anpr.total_plates_found}")
    print(f"  Valid GJ format  : {state.anpr.total_valid_format}")
    print(f"  Saved frames     : {state.save_counter}")
    print()

    if results:
        print("  Plates found (all readings):")
        # Group by normalised plate
        from collections import Counter
        plate_counts = Counter(r.normalised_plate for r in results)
        for plate, count in plate_counts.most_common(10):
            best = max((r for r in results if r.normalised_plate == plate),
                       key=lambda r: r.final_confidence)
            valid = "valid" if best.is_valid_format else "invalid format"
            print(f"    {plate:<15}  seen {count}x  best_conf={best.final_confidence:.2f}  [{valid}]")
        print()
        print("  SUCCESS: ANPR pipeline is working.")
        print("  Next: Milestone 4 — Watchlist matching + PostgreSQL registry")
    else:
        print("  WARNING: No plates read.")
        print("  Possible causes:")
        print("    - No vehicles in frame (empty road / night)")
        print("    - Plates too small (camera too far away)")
        print("    - Video quality too low for OCR")
        print("    - Increase test duration or use a closer camera angle")
        print()
        print("  Try a dashcam video or close-up traffic footage for testing.")

    print("=" * 70)


if __name__ == "__main__":
    main()
