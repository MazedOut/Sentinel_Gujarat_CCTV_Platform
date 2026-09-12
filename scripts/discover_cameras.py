"""
scripts/discover_cameras.py
----------------------------
MILESTONE 1 — Script 1 of 2

PURPOSE:
    Fetches the Sentinel camera catalogue from GET /api/ingest
    and prints a clean table of all available cameras.

HOW TO RUN (from the sentinel-gujarat/ directory):
    python -m scripts.discover_cameras

    Or with explicit Python path on Windows:
    python scripts\\discover_cameras.py

WHAT TO EXPECT:
    - A numbered table of cameras
    - Camera IDs, locations, codecs, stream URLs
    - Live status indicators

IF IT FAILS:
    - "SENTINEL_BASE_URL is not set" → fill in .env
    - "Cannot connect" → check if sandbox is running, check the URL in .env
    - "0 cameras" → sandbox may be empty or the endpoint path is different

WHAT TO TELL US IF IT FAILS:
    Copy the full output (including error lines) and paste it to us.
    We need the exact error message to help diagnose.
"""

import sys
import os

# Allow running as a standalone script from the sentinel-gujarat/ root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.core.logging_config import configure_logging
from backend.app.core.config import settings
from backend.app.services.camera.catalogue import fetch_catalogue
from backend.app.models.camera import CameraEntry
from typing import List


def main():
    configure_logging()

    print("=" * 70)
    print("  SENTINEL GUJARAT — Camera Catalogue Discovery")
    print("=" * 70)
    print()

    # Show which URL we are hitting (never print auth credentials)
    if not settings.sentinel_base_url:
        print("ERROR: SENTINEL_BASE_URL is not set.")
        print()
        print("Steps to fix:")
        print("  1. Copy .env.example  →  .env")
        print("  2. Open .env")
        print("  3. Set SENTINEL_BASE_URL=<your sandbox URL>")
        print("  4. Save and re-run this script.")
        print()
        print("Contact your hackathon coordinator for the sandbox URL.")
        sys.exit(1)

    print(f"  Catalogue URL : {settings.sentinel_ingest_url}")
    print(f"  RTSP transport: {settings.rtsp_transport.upper()} (forced per Sentinel rules)")
    print()

    # Fetch catalogue
    cameras: List[CameraEntry] = fetch_catalogue()

    if not cameras:
        print("No cameras returned from catalogue.")
        print("Possible causes:")
        print("  - Sandbox not running")
        print("  - Wrong SENTINEL_BASE_URL in .env")
        print("  - Different endpoint path (check SENTINEL_INGEST_ENDPOINT in .env)")
        sys.exit(1)

    # Print table
    print(f"Found {len(cameras)} camera(s):")
    print()
    print(f"  {'#':<4} {'ID':<15} {'Status':<10} {'Location':<30} {'Codec':<8} {'Resolution':<12} {'RTSP':<5} {'HLS':<5}")
    print("  " + "-" * 90)

    rtsp_cameras = []

    for i, cam in enumerate(cameras, start=1):
        status = "LIVE" if cam.live_status else ("OFFLINE" if cam.live_status is False else "UNKNOWN")
        rtsp_flag = "YES" if cam.has_rtsp else "NO"
        hls_flag = "YES" if cam.hls_url else "NO"
        resolution = cam.resolution or "?"
        location = (cam.location or "unknown")[:29]
        codec = cam.codec_normalized

        print(
            f"  {i:<4} {cam.camera_id:<15} {status:<10} {location:<30} "
            f"{codec:<8} {resolution:<12} {rtsp_flag:<5} {hls_flag:<5}"
        )

        if cam.has_rtsp:
            rtsp_cameras.append((i, cam))

    print()
    print(f"  Cameras with RTSP URLs: {len(rtsp_cameras)}")
    print()

    # Show RTSP-capable cameras in detail
    if rtsp_cameras:
        print("  RTSP-capable cameras (usable for AI pipeline):")
        print()
        for idx, cam in rtsp_cameras:
            print(f"  [{idx}] {cam.camera_id}")
            print(f"      Location : {cam.location or 'N/A'}")
            if cam.latitude and cam.longitude:
                print(f"      Coords   : {cam.latitude:.6f}, {cam.longitude:.6f}")
            print(f"      Codec    : {cam.codec_normalized}")
            print(f"      RTSP URL : {cam.rtsp_url}")
            if cam.hls_url:
                print(f"      HLS URL  : {cam.hls_url}")
            if cam.webrtc_url:
                print(f"      WebRTC   : {cam.webrtc_url}")
            print()

    print("=" * 70)
    print("  Next step: Run  python -m scripts.test_rtsp")
    print("  Then pick a camera number when prompted.")
    print("=" * 70)
    print()

    return cameras


if __name__ == "__main__":
    main()
