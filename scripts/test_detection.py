"""
scripts/test_detection.py
--------------------------
MILESTONE 2 — Vehicle Detection Diagnostic

PURPOSE:
    Connects to ONE Sentinel camera via RTSP/TCP, runs YOLOv8 vehicle
    detection on every Nth frame, and saves annotated diagnostic images.

    This verifies the full pipeline:
        /api/ingest  →  RTSP/TCP  →  Frame  →  YOLOv8  →  DetectionResult

HOW TO RUN (from sentinel-gujarat/ directory):
    python -m scripts.test_detection

OPTIONS (when prompted):
    - Pick a camera by number (same as test_rtsp.py)
    - OR: press Enter to use a LOCAL video file for offline testing
      (useful if Sentinel sandbox is not yet accessible)

WHAT IT SAVES:
    detection_samples/
        frame_0001_cam-001_2det.jpg   ← annotated frame (saved every 5s)
        frame_0002_cam-001_1det.jpg
        ...

WHAT GOOD OUTPUT LOOKS LIKE:
    [DETECT] cam=CAM-001  pts=1234ms  vehicles=2  inference=4.2ms
    [DETECT]   car        conf=0.91  bbox=(123, 456, 389, 678)
    [DETECT]   truck      conf=0.85  bbox=(700, 200, 1100, 600)
    Saved: detection_samples/frame_0001_cam-001_2det.jpg

WHAT TO TELL US IF IT FAILS:
    Paste the full output including any error lines.
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
from backend.app.models.camera import CameraEntry

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEST_DURATION_SEC = 90          # Run for 90 seconds then auto-stop
SAVE_INTERVAL_SEC = 5.0        # Save an annotated frame every 5 seconds
OUTPUT_DIR = Path("detection_samples")  # Where annotated frames are saved

YOLO_MODEL = "yolo26n.pt"      # yolov8n=fastest, yolov8s=better accuracy
CONFIDENCE_THRESHOLD = 0.40
FRAME_SKIP = 3                 # Process every 3rd frame (~10/s on 30fps stream)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class _State:
    def __init__(self):
        self.detector: VehicleDetector = None
        self.total_detections = 0
        self.frames_with_vehicles = 0
        self.frames_processed = 0
        self.last_save_time = time.monotonic()
        self.save_counter = 0
        self.lock = threading.Lock()


state = _State()


def on_frame(fd: FrameData) -> None:
    """Called for every decoded frame from StreamManager."""
    result = state.detector.detect(fd)

    if result is None:
        return  # frame was skipped

    with state.lock:
        state.frames_processed += 1

        if result.has_vehicles:
            state.frames_with_vehicles += 1
            state.total_detections += result.vehicle_count

            # Print detection summary
            print(
                f"  [DETECT] cam={fd.camera_id:<12}  "
                f"pts={fd.pts_ms:>10.0f}ms  "
                f"vehicles={result.vehicle_count}  "
                f"inference={result.inference_time_ms:.1f}ms"
            )
            for det in result.detections:
                x1, y1, x2, y2 = det.bbox.as_ints()
                print(
                    f"           {det.vehicle_class.value:<12}  "
                    f"conf={det.confidence:.2f}  "
                    f"bbox=({x1},{y1},{x2},{y2})"
                )

        # Save annotated frame periodically
        now = time.monotonic()
        if now - state.last_save_time >= SAVE_INTERVAL_SEC:
            state.last_save_time = now
            state.save_counter += 1
            _save_frame(fd, result)


def _save_frame(fd: FrameData, result) -> None:
    """Saves an annotated frame to disk for review."""
    import cv2

    OUTPUT_DIR.mkdir(exist_ok=True)
    annotated = Visualiser.draw(fd.frame, result)

    n_det = result.vehicle_count
    fname = OUTPUT_DIR / f"frame_{state.save_counter:04d}_{fd.camera_id}_{n_det}det.jpg"
    cv2.imwrite(str(fname), annotated)
    print(f"  [SAVED] {fname}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    configure_logging()

    print("=" * 70)
    print("  SENTINEL GUJARAT — Vehicle Detection Diagnostic (Milestone 2)")
    print("=" * 70)
    print()

    # Step 1: Check SENTINEL_BASE_URL
    use_local = False
    local_video_path = None

    if not settings.sentinel_base_url:
        print("  NOTE: SENTINEL_BASE_URL is not set in .env")
        print("  Options:")
        print("    1) Enter a local video file path for offline testing")
        print("    2) Set SENTINEL_BASE_URL in .env and re-run")
        print()
        local_video_path = input("  Enter local video path (or press Enter to exit): ").strip()
        if not local_video_path:
            print("  Exiting. Set SENTINEL_BASE_URL in .env and re-run.")
            sys.exit(0)
        if not Path(local_video_path).exists():
            print(f"  File not found: {local_video_path}")
            sys.exit(1)
        use_local = True

    # Step 2: Get camera / stream source
    if use_local:
        camera_id = "LOCAL"
        rtsp_url = local_video_path
        print(f"  Using local video: {local_video_path}")
    else:
        print("  Fetching camera catalogue...")
        cameras = fetch_catalogue()
        if not cameras:
            print("  No cameras available. Check SENTINEL_BASE_URL in .env.")
            sys.exit(1)

        rtsp_cameras = [(i + 1, c) for i, c in enumerate(cameras) if c.has_rtsp]
        if not rtsp_cameras:
            print("  No cameras with RTSP URLs in catalogue.")
            sys.exit(1)

        print(f"\n  Available RTSP cameras ({len(rtsp_cameras)}):")
        for idx, cam in rtsp_cameras:
            print(f"    [{idx}] {cam.camera_id} | {cam.location or 'unknown'} | {cam.codec_normalized}")

        if len(rtsp_cameras) == 1:
            idx, chosen = rtsp_cameras[0]
            print(f"\n  Auto-selecting only camera: {chosen.camera_id}")
        else:
            arg_choice = sys.argv[1].strip() if len(sys.argv) > 1 else None
            while True:
                try:
                    if arg_choice:
                        raw = arg_choice
                        arg_choice = None
                    else:
                        raw = input(f"\n  Enter camera number [1-{len(cameras)}] (default 1): ").strip()
                    if not raw:
                        idx, chosen = rtsp_cameras[0]
                        break
                    num = int(raw)
                    matches = [(i, c) for i, c in rtsp_cameras if i == num]
                    if not matches:
                        print(f"  No RTSP camera at position {num}.")
                        continue
                    idx, chosen = matches[0]
                    break
                except (ValueError, EOFError):
                    idx, chosen = rtsp_cameras[0]
                    break
                except KeyboardInterrupt:
                    print("\n  Aborted.")
                    sys.exit(0)

        camera_id = chosen.camera_id
        rtsp_url = settings.rtsp_url_for(chosen.camera_id, authenticated=True)
        print(f"\n  Selected: {camera_id} — {chosen.location or 'unknown location'} (Stream: {redact_credentials(rtsp_url)})")

    print()

    # Step 3: Load YOLOv8
    print(f"  Loading YOLOv8 model: {YOLO_MODEL}")
    print("  (First run downloads weights ~6 MB — wait a moment)")
    print()

    state.detector = VehicleDetector(
        model_name=YOLO_MODEL,
        confidence_threshold=CONFIDENCE_THRESHOLD,
        frame_skip=FRAME_SKIP,
    )

    try:
        state.detector.load()
    except RuntimeError as exc:
        print(f"\n  ERROR loading detector: {exc}")
        print("  Run: pip install ultralytics torch torchvision --index-url https://download.pytorch.org/whl/cu128")
        sys.exit(1)

    print(f"  Detector ready: {state.detector.summary()}")
    print()
    print(f"  Running for {TEST_DURATION_SEC}s  |  saving frames to ./{OUTPUT_DIR}/")
    print(f"  Frame skip: every {FRAME_SKIP}rd frame  |  confidence threshold: {CONFIDENCE_THRESHOLD}")
    print()
    print("  Press Ctrl+C to stop early.")
    print()

    # Step 4: Start stream
    manager = StreamManager(camera_id=camera_id, rtsp_url=rtsp_url)
    start_wall = time.monotonic()

    def _sigint(sig, frame):
        print("\n  [Ctrl+C] Stopping...")
        manager.stop()

    signal.signal(signal.SIGINT, _sigint)
    manager.start(on_frame)

    # Wait
    try:
        while time.monotonic() - start_wall < TEST_DURATION_SEC:
            if manager.info.state == StreamState.STOPPED:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    manager.stop()

    # Step 5: Summary
    elapsed = time.monotonic() - start_wall
    print()
    print("=" * 70)
    print("  DETECTION SUMMARY")
    print("=" * 70)
    print(f"  Camera            : {camera_id}")
    print(f"  RTSP URL          : {rtsp_url}")
    print(f"  Model             : {YOLO_MODEL}")
    print(f"  Device            : {state.detector.device}")
    print(f"  Wall time         : {elapsed:.1f}s")
    print(f"  Frames processed  : {state.detector.total_frames_processed}")
    print(f"  Frames skipped    : {state.detector.total_frames_skipped}")
    print(f"  Total reconnects  : {manager.info.total_reconnects}")
    print(f"  Frames w/ vehicles: {state.frames_with_vehicles}")
    print(f"  Total detections  : {state.detector.total_detections}")
    print(f"  Saved frames      : {state.save_counter}  (in ./{OUTPUT_DIR}/)")
    print()

    if state.detector.total_detections > 0:
        print("  SUCCESS: Vehicle detections produced.")
        print(f"  Check ./{OUTPUT_DIR}/ for annotated frame images.")
        print()
        print("  Next milestone: ANPR (license plate reading) on detected vehicles.")
    else:
        print("  WARNING: No vehicles detected.")
        print("  Possible causes:")
        print("    - Stream contains no vehicles (night, empty road)")
        print("    - Confidence threshold too high — try lowering to 0.25")
        print("    - Model needs a larger variant — try yolov8s.pt")
        print("    - Stream resolution too low for detection")
        print("  Please send us the saved frames and this output for diagnosis.")

    print("=" * 70)


if __name__ == "__main__":
    main()
