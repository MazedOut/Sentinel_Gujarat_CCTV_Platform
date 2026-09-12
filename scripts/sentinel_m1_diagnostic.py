"""
scripts/sentinel_m1_diagnostic.py
-----------------------------------
MILESTONE 1 — Sentinel Gujarat RTSP Diagnostic

PURPOSE:
    1. Fetch the Sentinel camera catalogue from:
           https://cctv.corp8.cloud/cameras.json
    2. Show the raw/parsed catalogue (or fallback list if catalogue is behind auth)
    3. Let you select one camera
    4. Connect via RTSP/TCP to:
           rtsp://103.250.160.189:8554/stream/<camera_id>
    5. Decode real frames and log PTS
    6. Demonstrate reconnect with exponential backoff

IMPORTANT NOTES:
    - cameras.json is behind the browser password (same as the HLS grid).
      If it returns a login page, this script falls back to cam01-cam30.
    - RTSP at 103.250.160.189:8554 is NOT behind that password.
    - Do NOT use the HLS/browser password for RTSP.
    - PTS comes from CAP_PROP_POS_MSEC (stream clock), NOT wall-clock time.

HOW TO RUN (from the sentinel-gujarat/ directory):
    python -m scripts.sentinel_m1_diagnostic

    Or:
    python scripts/sentinel_m1_diagnostic.py
"""

import sys
import os
import time
import signal
import json
import math
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Callable, Iterator

# -----------------------------------------------------------------------
# Allow running as a standalone script from the sentinel-gujarat/ root
# -----------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -----------------------------------------------------------------------
# Import project modules
# -----------------------------------------------------------------------
try:
    import httpx
except ImportError:
    print("[ERROR] httpx is not installed. Run: pip install httpx")
    sys.exit(1)

try:
    import cv2
    import numpy as np
except ImportError:
    print("[ERROR] opencv-python is not installed. Run: pip install opencv-python")
    sys.exit(1)


# =========================================================================
# SENTINEL INFRASTRUCTURE CONSTANTS
# =========================================================================

CATALOGUE_URL = "https://cctv.corp8.cloud/cameras.json"
RTSP_HOST     = "103.250.160.189"
RTSP_PORT     = 8554
WEBRTC_HOST   = "103.250.160.189"
WEBRTC_PORT   = 8889
HLS_HOST      = "cctv.corp8.cloud"

# Provisional camera range (per Integrator's Guide)
# Used when cameras.json returns a login page
PROVISIONAL_FIRST = 1
PROVISIONAL_LAST  = 30

# RTSP settings
RTSP_TRANSPORT        = "tcp"
FRAME_TIMEOUT_SEC     = 10
TEST_DURATION_SEC     = 60
LOG_INTERVAL_SEC      = 1.0

# Backoff schedule: 2 → 4 → 8 → 16 → 30 → 30 → ...
BACKOFF_INITIAL_SEC   = 2.0
BACKOFF_MAX_SEC       = 30.0
BACKOFF_FACTOR        = 2.0
SCENE_DISCONTINUITY_THRESHOLD_MS = 500.0


# =========================================================================
# STEP 1: CAMERA CATALOGUE
# =========================================================================

@dataclass
class CameraRecord:
    camera_id: str
    location: str = ""
    codec: str = ""
    resolution: str = ""
    live_status: Optional[bool] = None
    rtsp_url: str = ""
    webrtc_url: str = ""
    hls_url: str = ""
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.rtsp_url and self.camera_id:
            self.rtsp_url = f"rtsp://{RTSP_HOST}:{RTSP_PORT}/stream/{self.camera_id}"
        if not self.webrtc_url and self.camera_id:
            self.webrtc_url = f"http://{WEBRTC_HOST}:{WEBRTC_PORT}/stream/{self.camera_id}/whep"
        if not self.hls_url and self.camera_id:
            self.hls_url = f"https://{HLS_HOST}/{self.camera_id}/index.m3u8"


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def record_from_dict(raw: dict) -> Optional[CameraRecord]:
    cam_id = _get(raw, "camera_id", "cameraId", "id", "cam_id")
    if not cam_id:
        return None

    live_raw = _get(raw, "live_status", "liveStatus", "live", "is_live", "isLive", "status")
    if isinstance(live_raw, bool):
        live_status = live_raw
    elif isinstance(live_raw, str):
        live_status = live_raw.lower() in ("true", "1", "yes", "online", "live")
    else:
        live_status = None

    rtsp = _get(raw, "rtsp_url", "rtspUrl", "rtsp") or ""
    webrtc = _get(raw, "webrtc_url", "webrtcUrl", "whep_url", "whepUrl", "webrtc") or ""
    hls = _get(raw, "hls_url", "hlsUrl", "hls") or ""
    codec = _get(raw, "codec", "video_codec", "videoCodec") or ""
    resolution = _get(raw, "resolution", "res") or ""
    location = _get(raw, "location", "loc", "name", "label", "description") or ""

    return CameraRecord(
        camera_id=str(cam_id),
        location=str(location),
        codec=str(codec).upper() if codec else "",
        resolution=str(resolution),
        live_status=live_status,
        rtsp_url=str(rtsp) if rtsp else "",
        webrtc_url=str(webrtc) if webrtc else "",
        hls_url=str(hls) if hls else "",
        raw=raw,
    )


def provisional_camera_list() -> List[CameraRecord]:
    """Fallback list when cameras.json is behind auth."""
    return [
        CameraRecord(camera_id=f"cam{n:02d}")
        for n in range(PROVISIONAL_FIRST, PROVISIONAL_LAST + 1)
    ]


def load_catalogue(timeout_sec: float = 15.0) -> List[CameraRecord]:
    """
    Fetches cameras.json and returns parsed CameraRecord list.

    If the endpoint returns HTML (login wall), logs a clear explanation
    and returns the provisional cam01-cam30 list.
    """
    print(f"[INFO] Loading Sentinel camera catalogue...")
    print(f"[INFO] Catalogue URL: {CATALOGUE_URL}")

    # Try optional auth from environment (Cookie header only)
    headers = {}
    cookie = os.environ.get("SENTINEL_CATALOGUE_COOKIE", "").strip()
    if cookie:
        if "=" not in cookie:
            headers["Cookie"] = f"sentinel={cookie}"
        else:
            headers["Cookie"] = cookie

    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            resp = client.get(CATALOGUE_URL, headers=headers)
    except httpx.ConnectError as exc:
        print(f"[ERROR] Cannot connect to catalogue: {exc}")
        print("[WARN]  Falling back to provisional cam01-cam30 list (RTSP still usable)")
        cameras = provisional_camera_list()
        print(f"[INFO] Cameras (provisional): {len(cameras)}")
        return cameras
    except httpx.TimeoutException:
        print(f"[ERROR] Timeout fetching catalogue after {timeout_sec:.0f}s")
        cameras = provisional_camera_list()
        print(f"[INFO] Cameras (provisional): {len(cameras)}")
        return cameras
    except Exception as exc:
        print(f"[ERROR] Unexpected error: {exc}")
        cameras = provisional_camera_list()
        print(f"[INFO] Cameras (provisional): {len(cameras)}")
        return cameras

    ct = resp.headers.get("content-type", "")
    preview = resp.text[:300].strip()

    # Detect login wall
    if resp.status_code in (401, 403) or "text/html" in ct or \
       preview.lower().startswith("<!doctype") or preview.lower().startswith("<html"):
        print(f"[WARN]  cameras.json returned HTTP {resp.status_code} (login page / auth required).")
        print("[WARN]  The catalogue CDN requires the browser access password.")
        print("[WARN]  RTSP at 103.250.160.189:8554 does NOT require this password.")
        print("[WARN]  To authenticate the catalogue, set SENTINEL_CATALOGUE_COOKIE in .env")
        print("[WARN]  Falling back to provisional cam01-cam30 list...")
        print()
        cameras = provisional_camera_list()
        print(f"[INFO] Cameras (provisional): {len(cameras)}")
        return cameras

    if resp.status_code >= 400:
        print(f"[ERROR] HTTP {resp.status_code} from catalogue. Response: {preview[:200]}")
        cameras = provisional_camera_list()
        print(f"[INFO] Cameras (provisional): {len(cameras)}")
        return cameras

    # Parse JSON
    try:
        data = resp.json()
    except Exception as exc:
        print(f"[ERROR] cameras.json did not return valid JSON: {exc}")
        print(f"[ERROR] Content-Type: {ct}")
        print(f"[ERROR] Preview: {preview[:200]}")
        cameras = provisional_camera_list()
        print(f"[INFO] Cameras (provisional): {len(cameras)}")
        return cameras

    # Show raw structure
    print()
    print("[INFO] cameras.json RAW structure:")
    print(f"       Type: {type(data).__name__}")
    if isinstance(data, dict):
        print(f"       Top-level keys: {list(data.keys())[:15]}")
    elif isinstance(data, list) and len(data) > 0:
        print(f"       Array of {len(data)} items. First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]).__name__}")
    print()
    print("[INFO] First record (raw JSON):")
    try:
        first = data if isinstance(data, dict) else (data[0] if data else {})
        print(json.dumps(first if isinstance(first, dict) else {"item": str(first)}, indent=4, default=str))
    except Exception:
        print(f"       {str(data)[:300]}")
    print()

    # Parse records
    raw_list = []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        for key in ("cameras", "data", "feeds", "streams", "results", "items"):
            if key in data and isinstance(data[key], list):
                raw_list = data[key]
                break
        if not raw_list:
            if any(k in data for k in ("camera_id", "id", "cameraId")):
                raw_list = [data]

    cameras = []
    for raw in raw_list:
        if isinstance(raw, dict):
            rec = record_from_dict(raw)
            if rec:
                cameras.append(rec)

    if not cameras:
        print("[WARN]  Parsed 0 valid cameras from JSON response.")
        print("[WARN]  Falling back to provisional cam01-cam30 list.")
        cameras = provisional_camera_list()

    print(f"[INFO] Cameras discovered: {len(cameras)}")
    return cameras


# =========================================================================
# STEP 2: STREAM MANAGER
# =========================================================================

class StreamState(Enum):
    DISCONNECTED = auto()
    CONNECTING   = auto()
    CONNECTED    = auto()
    RECONNECTING = auto()
    STOPPED      = auto()


@dataclass
class FrameData:
    camera_id: str
    frame: "np.ndarray"
    pts_ms: float       # From CAP_PROP_POS_MSEC — NOT wall clock
    frame_index: int
    width: int
    height: int
    codec: str


@dataclass
class StreamInfo:
    camera_id: str
    rtsp_url: str
    state: StreamState = StreamState.DISCONNECTED
    width: int = 0
    height: int = 0
    codec_fourcc: str = "UNKNOWN"
    fps_reported: float = 0.0     # display only — NOT used for timing
    total_frames_decoded: int = 0
    total_reconnects: int = 0
    last_pts_ms: float = 0.0


def _backoff_delays() -> Iterator[float]:
    delay = BACKOFF_INITIAL_SEC
    while True:
        yield delay
        delay = min(delay * BACKOFF_FACTOR, BACKOFF_MAX_SEC)


def _fourcc_to_str(fourcc_int: int) -> str:
    if fourcc_int == 0:
        return "UNKNOWN"
    try:
        chars = [chr((fourcc_int >> (i * 8)) & 0xFF) for i in range(4)]
        result = "".join(c for c in chars if c.isprintable() and c.strip())
        return result.upper() if result else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


class StreamManager:
    def __init__(self, camera_id: str, rtsp_url: str, max_reconnects: Optional[int] = None):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.max_reconnects = max_reconnects
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.info = StreamInfo(camera_id=camera_id, rtsp_url=rtsp_url)

    def start(self, on_frame: Callable[[FrameData], None]) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run, args=(on_frame,),
            name=f"stream-{self.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=6.0)
        self.info.state = StreamState.STOPPED

    def run(self, on_frame: Callable[[FrameData], None]) -> None:
        backoff = _backoff_delays()
        reconnect_count = 0

        while not self._stop_event.is_set():
            if self.max_reconnects is not None and reconnect_count >= self.max_reconnects:
                print(f"[ERROR] [{self.camera_id}] Max reconnects ({self.max_reconnects}) reached. Giving up.")
                break

            cap = self._open_capture()
            if cap is None:
                delay = next(backoff)
                print(f"[WARN]  [{self.camera_id}] Failed to open stream. Reconnecting in {delay:.0f}s...")
                self.info.state = StreamState.RECONNECTING
                reconnect_count += 1
                self.info.total_reconnects = reconnect_count
                self._stop_event.wait(timeout=delay)
                continue

            self.info.state = StreamState.CONNECTED
            print(f"[INFO] Stream connected")
            print(f"[INFO] Resolution: {self.info.width}x{self.info.height}")
            print(f"[INFO] Codec: {self.info.codec_fourcc} (reported FPS: {self.info.fps_reported:.1f} — NOT used for timing)")
            backoff = _backoff_delays()  # reset on success

            disconnected = self._read_frames(cap, on_frame)
            cap.release()

            if self._stop_event.is_set():
                break

            if disconnected:
                delay = next(backoff)
                print(f"[WARN]  Stream disconnected")
                print(f"[INFO] Reconnecting in {delay:.0f} seconds...")
                self.info.state = StreamState.RECONNECTING
                reconnect_count += 1
                self.info.total_reconnects = reconnect_count
                self._stop_event.wait(timeout=delay)

        self.info.state = StreamState.STOPPED

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        self.info.state = StreamState.CONNECTING
        print(f"[INFO] Connecting using RTSP over TCP...")
        print(f"[INFO] RTSP: {self.rtsp_url}")

        # Force RTSP over TCP via environment variable (OpenCV/FFmpeg)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{RTSP_TRANSPORT}"

        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            print(f"[WARN]  cv2.VideoCapture failed to open: {self.rtsp_url}")
            cap.release()
            return None

        self.info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.info.fps_reported = cap.get(cv2.CAP_PROP_FPS)
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        self.info.codec_fourcc = _fourcc_to_str(fourcc_int)

        return cap

    def _read_frames(
        self,
        cap: cv2.VideoCapture,
        on_frame: Callable[[FrameData], None],
    ) -> bool:
        """Returns True if disconnected, False if stopped."""
        frame_index = 0
        last_pts_ms: float = -1.0
        last_frame_wall_time = time.monotonic()
        first_good_frame = False
        codec = self.info.codec_fourcc

        while not self._stop_event.is_set():
            grabbed = cap.grab()

            if not grabbed:
                elapsed = time.monotonic() - last_frame_wall_time
                if elapsed > FRAME_TIMEOUT_SEC:
                    print(f"[WARN]  [{self.camera_id}] No frame for {elapsed:.1f}s — stream dead")
                    return True
                continue

            # PTS from stream clock (NOT wall clock)
            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)

            ret, frame = cap.retrieve()
            if not ret or frame is None:
                if not first_good_frame:
                    # Expected: H.264/H.265 decoder join-time artifact before IDR frame
                    continue
                print(f"[WARN]  [{self.camera_id}] Frame retrieve failed at pts={pts_ms:.0f}ms")
                continue

            first_good_frame = True
            last_frame_wall_time = time.monotonic()

            # Scene discontinuity detection (loop point)
            if last_pts_ms >= 0 and pts_ms < last_pts_ms - SCENE_DISCONTINUITY_THRESHOLD_MS:
                print(
                    f"[INFO] [{self.camera_id}] SCENE_DISCONTINUITY: "
                    f"PTS jumped {last_pts_ms:.0f}ms → {pts_ms:.0f}ms (loop point)"
                )

            # Large PTS gap warning
            if last_pts_ms >= 0:
                pts_delta = pts_ms - last_pts_ms
                if pts_delta > 2000:
                    print(f"[WARN]  [{self.camera_id}] Large PTS gap: {pts_delta:.0f}ms")

            last_pts_ms = pts_ms
            self.info.last_pts_ms = pts_ms
            self.info.total_frames_decoded += 1

            current_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = _fourcc_to_str(current_fourcc) or codec
            h, w = frame.shape[:2]

            fd = FrameData(
                camera_id=self.camera_id,
                frame=frame,
                pts_ms=pts_ms,
                frame_index=frame_index,
                width=w,
                height=h,
                codec=codec,
            )

            try:
                on_frame(fd)
            except Exception as exc:
                print(f"[ERROR] on_frame callback error at frame {frame_index}: {exc}")

            frame_index += 1

        return False


# =========================================================================
# STEP 3: NETWORK DIAGNOSTIC HELPERS
# =========================================================================

def check_tcp_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Test raw TCP connectivity to host:port."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def run_network_diagnostics(camera_id: str):
    """Run basic connectivity checks before attempting RTSP."""
    print()
    print("  --- Network Diagnostics ---")

    rtsp_reachable = check_tcp_port(RTSP_HOST, RTSP_PORT)
    status = "[OK] REACHABLE" if rtsp_reachable else "[FAIL] UNREACHABLE"
    print(f"  TCP {RTSP_HOST}:{RTSP_PORT} (RTSP) : {status}")

    webrtc_reachable = check_tcp_port(WEBRTC_HOST, WEBRTC_PORT)
    status = "[OK] REACHABLE" if webrtc_reachable else "[FAIL] UNREACHABLE"
    print(f"  TCP {WEBRTC_HOST}:{WEBRTC_PORT} (WebRTC): {status}")

    print()
    if not rtsp_reachable:
        print("  [ERROR] RTSP port is unreachable. Possible causes:")
        print("    - No internet / network blocked by firewall")
        print("    - Sentinel sandbox is down")
        print("    - Port 8554 is blocked by your ISP or corporate firewall")
        print("    - Try: telnet 103.250.160.189 8554")
        print(f"    - Try: ffplay -rtsp_transport tcp rtsp://{RTSP_HOST}:{RTSP_PORT}/stream/{camera_id}")

    return rtsp_reachable


# =========================================================================
# MAIN
# =========================================================================

class _Stats:
    def __init__(self):
        self.total_frames = 0
        self.first_pts: Optional[float] = None
        self.last_pts: Optional[float] = None
        self.last_log_time = time.monotonic()


def main():
    print("=" * 70)
    print("  SENTINEL GUJARAT — Milestone 1 RTSP Diagnostic")
    print("  Official Infrastructure:")
    print(f"    Catalogue : {CATALOGUE_URL}")
    print(f"    RTSP host : rtsp://{RTSP_HOST}:{RTSP_PORT}/stream/<camera_id>")
    print(f"    Transport : {RTSP_TRANSPORT.upper()} (mandatory per Integrator's Guide)")
    print("=" * 70)
    print()

    # ---- Step 1: Load catalogue ----
    cameras = load_catalogue()

    if not cameras:
        print("[ERROR] No cameras available. Cannot proceed.")
        sys.exit(1)

    # ---- Step 2: Show cameras ----
    print()
    print(f"  {'#':<4} {'ID':<12} {'Status':<10} {'Location':<35} {'Codec':<8} {'RTSP URL'}")
    print("  " + "-" * 100)

    for i, cam in enumerate(cameras, start=1):
        status = "LIVE" if cam.live_status is True else ("OFFLINE" if cam.live_status is False else "?")
        loc = (cam.location or "")[:34]
        codec = cam.codec or "?"
        print(f"  {i:<4} {cam.camera_id:<12} {status:<10} {loc:<35} {codec:<8} {cam.rtsp_url}")

    print()

    # ---- Step 3: Pick a camera ----
    rtsp_cams = [(i + 1, c) for i, c in enumerate(cameras) if c.rtsp_url]

    if not rtsp_cams:
        print("[ERROR] No cameras have RTSP URLs. Cannot proceed.")
        sys.exit(1)

    if len(rtsp_cams) == 1:
        chosen_idx, chosen_cam = rtsp_cams[0]
        print(f"[INFO] Only one camera — automatically selecting: {chosen_cam.camera_id}")
    else:
        while True:
            try:
                raw = input(f"Enter camera number [1-{len(cameras)}]: ").strip()
                n = int(raw)
                matches = [(i, c) for i, c in rtsp_cams if i == n]
                if not matches:
                    print(f"  No RTSP camera at #{n}. Try again.")
                    continue
                chosen_idx, chosen_cam = matches[0]
                break
            except ValueError:
                print("  Enter a number.")
            except KeyboardInterrupt:
                print("\nAborted.")
                sys.exit(0)

    print()
    print(f"[INFO] Selected camera: {chosen_cam.camera_id}")
    print(f"[INFO] Location       : {chosen_cam.location or 'N/A (provisional)'}")
    print(f"[INFO] Codec (catalogue): {chosen_cam.codec or 'unknown (will detect from stream)'}")
    print(f"[INFO] RTSP           : {chosen_cam.rtsp_url}")
    print(f"[INFO] WebRTC         : {chosen_cam.webrtc_url}")
    print(f"[INFO] HLS (auth req) : {chosen_cam.hls_url}")
    print()

    # ---- Step 4: Network diagnostics ----
    rtsp_reachable = run_network_diagnostics(chosen_cam.camera_id)
    if not rtsp_reachable:
        print()
        print("[ERROR] RTSP port unreachable. Cannot connect.")
        print("        STOP — reporting network issue. Do not proceed.")
        print()
        print("  Diagnosis:")
        print(f"    TCP connection to {RTSP_HOST}:{RTSP_PORT} failed.")
        print("    This is a NETWORK issue, not a code issue.")
        print("    The Sentinel RTSP server may be down or port blocked.")
        sys.exit(1)

    print()
    print(f"[INFO] RTSP port {RTSP_HOST}:{RTSP_PORT} is reachable. Proceeding with stream connection.")
    print()
    print(f"  Will run for {TEST_DURATION_SEC} seconds (or press Ctrl+C to stop early)")
    print()
    print("  FRAME LOG FORMAT:")
    print("    [FRAME] cam=<id>  pts=<stream_pts>ms  size=<W>x<H>  codec=<codec>  frame#=<n>")
    print()
    print("  NOTE: pts is the stream's own presentation timestamp (CAP_PROP_POS_MSEC)")
    print("        NOT wall-clock time. This is correct per Sentinel integration rules.")
    print()

    # ---- Step 5: Connect and stream ----
    stats = _Stats()

    def on_frame(fd: FrameData) -> None:
        stats.total_frames += 1
        if stats.first_pts is None:
            stats.first_pts = fd.pts_ms
        stats.last_pts = fd.pts_ms

        now = time.monotonic()
        if now - stats.last_log_time >= LOG_INTERVAL_SEC:
            print(
                f"  [FRAME] cam={fd.camera_id:<12}  "
                f"pts={fd.pts_ms:>10.0f}ms  "
                f"size={fd.width}x{fd.height}  "
                f"codec={fd.codec:<6}  "
                f"frame#={fd.frame_index}"
            )
            stats.last_log_time = now

    manager = StreamManager(
        camera_id=chosen_cam.camera_id,
        rtsp_url=chosen_cam.rtsp_url,
    )

    start_wall = time.monotonic()

    def _sigint(_sig, _frame):
        print("\n[Ctrl+C] Stopping...")
        manager.stop()

    signal.signal(signal.SIGINT, _sigint)

    manager.start(on_frame)

    try:
        while time.monotonic() - start_wall < TEST_DURATION_SEC:
            if manager.info.state == StreamState.STOPPED:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    manager.stop()

    # ---- Step 6: Summary ----
    elapsed = time.monotonic() - start_wall
    print()
    print("=" * 70)
    print("  MILESTONE 1 DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"  Camera ID         : {chosen_cam.camera_id}")
    print(f"  RTSP URL          : {chosen_cam.rtsp_url}")
    print(f"  Transport         : {RTSP_TRANSPORT.upper()}")
    print(f"  Codec (detected)  : {manager.info.codec_fourcc}")
    print(f"  Resolution        : {manager.info.width}x{manager.info.height}")
    print(f"  FPS (reported)    : {manager.info.fps_reported:.1f} [NOT used for timing]")
    print(f"  Wall time elapsed : {elapsed:.1f}s")
    print(f"  Total frames      : {stats.total_frames}")
    print(f"  Total reconnects  : {manager.info.total_reconnects}")

    if stats.total_frames > 0 and stats.first_pts is not None and stats.last_pts is not None:
        pts_span_sec = (stats.last_pts - stats.first_pts) / 1000.0
        effective_fps = stats.total_frames / max(pts_span_sec, 0.001)
        print(f"  PTS first         : {stats.first_pts:.0f}ms")
        print(f"  PTS last          : {stats.last_pts:.0f}ms")
        print(f"  PTS span          : {pts_span_sec:.1f}s")
        print(f"  Effective FPS     : {effective_fps:.1f} fps (from actual PTS delta)")
        print()
        print("  [OK] SUCCESS: Stream connected and frames decoded with PTS timestamps.")
        print("  STOP CONDITION MET — ready for Milestone 2 (YOLO) approval.")
    else:
        print()
        print("  [FAIL] FAILURE: No frames decoded.")
        print("  Possible causes:")
        print("    - RTSP URL unreachable (network/firewall)")
        print("    - Stream codec unsupported by local FFmpeg build")
        print("    - Sentinel sandbox not running or camera offline")
        print()
        print("  Manual test commands:")
        print(f"    ffplay -rtsp_transport tcp {chosen_cam.rtsp_url}")
        print(f"    ffprobe -rtsp_transport tcp {chosen_cam.rtsp_url}")
        print()
        print("  Paste this full output when reporting the issue.")

    print()
    print("  Next step: Get approval for Milestone 2 (YOLO vehicle detection)")
    print("=" * 70)


if __name__ == "__main__":
    main()
