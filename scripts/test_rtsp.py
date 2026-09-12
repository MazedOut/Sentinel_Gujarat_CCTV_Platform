"""
scripts/test_rtsp.py
---------------------
MILESTONE 1 — Script 2 of 2

PURPOSE:
    Connects to ONE Sentinel camera via RTSP/TCP and verifies:
    - Frame decoding works
    - PTS timestamps are read correctly (NOT wall-clock time)
    - Codec and resolution are detected
    - Reconnection with exponential backoff works

HOW TO RUN (from the sentinel-gujarat/ directory):
    python -m scripts.test_rtsp

    The script will:
    1. Fetch the catalogue and list cameras
    2. Ask you to pick a camera by number
    3. Connect and run for 60 seconds (or until Ctrl+C)
    4. Print a frame-by-frame log
    5. Print a summary at the end

WHAT GOOD OUTPUT LOOKS LIKE:
    [FRAME] cam=CAM-001  pts=    1234ms  size=1920x1080  codec=H264  frame#=5
    [FRAME] cam=CAM-001  pts=    1267ms  size=1920x1080  codec=H264  frame#=6

    Every ~33ms (for 30fps streams) you should see a new [FRAME] line.
    PTS should increase by roughly 1000/fps milliseconds per frame.

WHAT FAILURE LOOKS LIKE:
    [WARN]  Stream disconnected. Reconnecting in 2s...
    [WARN]  Stream disconnected. Reconnecting in 4s...
    (keeps increasing up to 30s)

    If you see only reconnection warnings with no [FRAME] lines,
    the RTSP URL is unreachable or blocked by a firewall.

WHAT TO TELL US IF IT FAILS:
    Paste the full output. Include the RTSP URL it was trying.
    We need to see the exact error to help.
"""

import sys
import os
import time
import signal

# Allow running as a standalone script from the sentinel-gujarat/ root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.core.logging_config import configure_logging, get_logger
from backend.app.core.config import settings, redact_credentials
from backend.app.services.camera.catalogue import fetch_catalogue
from backend.app.services.streaming.stream_manager import StreamManager, FrameData, StreamState
from backend.app.models.camera import CameraEntry
from typing import List, Optional

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# State shared between main thread and on_frame callback
# ---------------------------------------------------------------------------

class _Stats:
    def __init__(self):
        self.total_frames = 0
        self.first_pts: Optional[float] = None
        self.last_pts: Optional[float] = None
        self.last_log_time = time.monotonic()
        self.log_interval_sec = 1.0   # print one summary line per second


stats = _Stats()


def on_frame(fd: FrameData) -> None:
    """
    Called for every decoded frame.
    Demonstrates correct PTS usage per Sentinel integration rules.
    """
    stats.total_frames += 1

    if stats.first_pts is None:
        stats.first_pts = fd.pts_ms

    stats.last_pts = fd.pts_ms

    # Print one summary line per second (not every frame — that would flood the console)
    now = time.monotonic()
    if now - stats.last_log_time >= stats.log_interval_sec:
        print(
            f"  [FRAME] cam={fd.camera_id:<12}  "
            f"pts={fd.pts_ms:>10.0f}ms  "
            f"size={fd.width}x{fd.height}  "
            f"codec={fd.codec:<6}  "
            f"frame#={fd.frame_index}"
        )
        stats.last_log_time = now


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    configure_logging()

    TEST_DURATION_SEC = 60  # Run for 60 seconds then stop automatically

    print("=" * 70)
    print("  SENTINEL GUJARAT — RTSP Stream Diagnostic")
    print("=" * 70)
    print()

    if not settings.sentinel_base_url:
        print("ERROR: SENTINEL_BASE_URL is not set in .env")
        print("Run discover_cameras.py first and follow the setup instructions.")
        sys.exit(1)

    print(f"  Catalogue    : {settings.sentinel_ingest_url}")
    print(f"  RTSP transport: {settings.rtsp_transport.upper()} (TCP mandatory per Sentinel rules)")
    print()

    # Step 1: Fetch catalogue
    print("Fetching camera catalogue...")
    cameras: List[CameraEntry] = fetch_catalogue()

    if not cameras:
        print("No cameras available. Check your .env and sandbox connection.")
        sys.exit(1)

    # Step 2: List RTSP-capable cameras
    rtsp_cameras = [(i + 1, c) for i, c in enumerate(cameras) if c.has_rtsp]

    if not rtsp_cameras:
        print("No cameras with RTSP URLs found in the catalogue.")
        print("Cannot proceed with RTSP test.")
        print("Available cameras:")
        for i, cam in enumerate(cameras, 1):
            print(f"  [{i}] {cam.display_summary()}")
        sys.exit(1)

    print(f"Available RTSP cameras ({len(rtsp_cameras)}):")
    print()
    for idx, cam in rtsp_cameras:
        print(f"  [{idx}] {cam.display_summary()}")
        print(f"       RTSP: {redact_credentials(cam.rtsp_url)}")
        print()

    # Step 3: Let user pick
    if len(rtsp_cameras) == 1:
        chosen_idx, chosen_cam = rtsp_cameras[0]
        print(f"Only one camera available — automatically selecting: {chosen_cam.camera_id}")
    else:
        while True:
            try:
                raw = input(f"Enter camera number [1-{len(cameras)}] (default 1): ").strip()
                if not raw:
                    chosen_idx, chosen_cam = rtsp_cameras[0]
                    break
                chosen_num = int(raw)
                matches = [(i, c) for i, c in rtsp_cameras if i == chosen_num]
                if not matches:
                    print(f"  No RTSP camera at position {chosen_num}. Try again.")
                    continue
                chosen_idx, chosen_cam = matches[0]
                break
            except (ValueError, EOFError):
                chosen_idx, chosen_cam = rtsp_cameras[0]
                break
            except KeyboardInterrupt:
                print("\nAborted.")
                sys.exit(0)
    rtsp_stream_url = settings.rtsp_url_for(chosen_cam.camera_id, authenticated=True)
    has_auth = bool(settings.sentinel_rtsp_email and settings.sentinel_rtsp_password)

    print()
    print(f"Selected camera : {chosen_cam.camera_id}")
    print(f"Location        : {chosen_cam.location or 'unknown'}")
    print(f"Codec (reported): {chosen_cam.codec_normalized}")
    print(f"RTSP Stream URL : {redact_credentials(rtsp_stream_url)}")
    print(f"RTSP Auth State : {'Configured (credentials injected & URL-encoded)' if has_auth else 'MISSING (Configure SENTINEL_RTSP_EMAIL and SENTINEL_RTSP_PASSWORD in .env)'}")
    print()
    print(f"Connecting... (will run for {TEST_DURATION_SEC} seconds, or press Ctrl+C to stop)")
    print()
    print("  Columns: [FRAME] cam=<id>  pts=<stream_pts_ms>  size=<WxH>  codec=<codec>  frame#=<n>")
    print()
    print("  IMPORTANT:")
    print("  'pts' is the stream's own presentation timestamp (from CAP_PROP_POS_MSEC).")
    print("  It is NOT wall-clock time. This is correct per Sentinel integration rules.")
    print()

    # Step 4: Create stream manager and start
    manager = StreamManager(
        camera_id=chosen_cam.camera_id,
        rtsp_url=rtsp_stream_url,
    )

    start_wall = time.monotonic()

    # Set up Ctrl+C handler
    def _sigint_handler(sig, frame):
        print("\n[Ctrl+C] Stopping stream...")
        manager.stop()

    signal.signal(signal.SIGINT, _sigint_handler)

    # Start in background thread
    manager.start(on_frame)

    # Wait for test duration
    try:
        while time.monotonic() - start_wall < TEST_DURATION_SEC:
            if manager.info.state == StreamState.STOPPED:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    manager.stop()

    # Step 5: Print summary
    elapsed = time.monotonic() - start_wall
    print()
    print("=" * 70)
    print("  DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"  Camera ID        : {chosen_cam.camera_id}")
    print(f"  RTSP URL         : {chosen_cam.rtsp_url}")
    print(f"  Transport        : {settings.rtsp_transport.upper()}")
    print(f"  Codec (detected) : {manager.info.codec_fourcc}")
    print(f"  Resolution       : {manager.info.width}x{manager.info.height}")
    print(f"  FPS (reported)   : {manager.info.fps_reported:.1f} [NOT used for timing]")
    print(f"  Wall time elapsed: {elapsed:.1f}s")
    print(f"  Total frames     : {stats.total_frames}")
    print(f"  Total reconnects : {manager.info.total_reconnects}")

    if stats.total_frames > 0 and stats.first_pts is not None and stats.last_pts is not None:
        pts_span_sec = (stats.last_pts - stats.first_pts) / 1000.0
        effective_fps = stats.total_frames / max(pts_span_sec, 0.001)
        print(f"  PTS span         : {pts_span_sec:.1f}s")
        print(f"  Effective FPS    : {effective_fps:.1f} fps (from actual PTS delta)")
        print()
        print("  ✅ SUCCESS: Stream connected and frames decoded with PTS timestamps.")
    else:
        print()
        print("  ❌ FAILURE: No frames decoded.")
        print("  Possible causes:")
        print("    - RTSP URL unreachable (network/firewall issue)")
        print("    - Stream codec unsupported by local FFmpeg")
        print("    - Sentinel sandbox not running")
        print("  Please paste this output and report to us for diagnosis.")

    print()
    print("  Next milestone: Once this shows '✅ SUCCESS', we add YOLO vehicle detection.")
    print("=" * 70)


if __name__ == "__main__":
    main()
