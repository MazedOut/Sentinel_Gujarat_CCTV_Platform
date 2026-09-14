"""
sentinel-gujarat/backend/app/services/streaming/stream_manager.py
------------------------------------------------------------------
RTSP stream ingestion service.

This is the core of Milestone 1.  Every rule from the official
Sentinel Sandbox Integration Reference is implemented here:

  RULE: TCP transport mandatory
        → OPENCV_FFMPEG_CAPTURE_OPTIONS forces rtsp_transport=tcp

  RULE: Use PTS not frame arrival time
        → CAP_PROP_POS_MSEC is read immediately after each grab()

  RULE: Do NOT trust CAP_PROP_FPS
        → fps_reported is stored for display only, never used for timing

  RULE: Variable inter-frame intervals
        → PTS delta is computed; large gaps log a warning, not an error

  RULE: Reconnect with exponential backoff (2s → 4s → 8s → 16s → 30s)
        → _backoff_delay() generator implements this exactly

  RULE: Handle decoder join-time warnings (H.264/H.265 IDR issues)
        → cv2 stderr warnings are expected and logged at DEBUG level;
          the loop keeps running until the first real frame is decoded

  RULE: Mixed codecs — H.264 and H.265
        → StreamInfo.codec is read per-connection from CAP_PROP_CODEC_PIXEL_FORMAT
          and the raw fourCC value; the pipeline does not assume one codec

  RULE: Scene discontinuity (loop point)
        → A sudden PTS reset (new PTS < previous PTS) is detected and logged
          as SCENE_DISCONTINUITY, not treated as a fatal error

  RULE: Graceful shutdown
        → stop() sets a threading.Event that the frame loop checks each iteration
"""

from __future__ import annotations

import math
import time
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterator, Optional

import cv2
import numpy as np

from backend.app.core.config import settings, redact_credentials
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Backoff schedule (seconds): 2, 4, 8, 16, 30, 30, 30 ...
_BACKOFF_INITIAL_SEC = 2.0
_BACKOFF_MAX_SEC = 30.0
_BACKOFF_FACTOR = 2.0

# If PTS jumps backwards by more than this, assume scene discontinuity (loop point)
_SCENE_DISCONTINUITY_THRESHOLD_MS = 500.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class StreamState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    STOPPED = auto()


@dataclass
class StreamInfo:
    """
    Properties of the connected stream, populated once the first frame
    is successfully decoded.
    """
    camera_id: str
    rtsp_url: str
    width: int = 0
    height: int = 0
    codec_fourcc: str = "UNKNOWN"
    fps_reported: float = 0.0   # DISPLAY ONLY — do not use for timing
    state: StreamState = StreamState.DISCONNECTED
    total_frames_decoded: int = 0
    total_reconnects: int = 0
    last_pts_ms: float = 0.0
    connect_time: Optional[float] = None    # Unix timestamp when last connected


@dataclass
class FrameData:
    """
    A decoded frame plus its reliable presentation timestamp.

    IMPORTANT: pts_ms comes from CAP_PROP_POS_MSEC (the stream's own clock),
    NOT from datetime.now() (wall clock).  Using wall-clock time would violate
    the Sentinel integration rules and would produce wrong timing calculations.
    """
    camera_id: str
    frame: np.ndarray               # BGR frame as returned by OpenCV
    pts_ms: float                   # Presentation timestamp in milliseconds (from stream)
    frame_index: int                # Sequential index within this connection
    width: int
    height: int
    codec: str


# ---------------------------------------------------------------------------
# Backoff generator
# ---------------------------------------------------------------------------

def _backoff_delays() -> Iterator[float]:
    """
    Generates reconnect wait times: 2, 4, 8, 16, 30, 30, 30 ...
    Never returns — caller decides when to stop iterating.
    """
    delay = _BACKOFF_INITIAL_SEC
    while True:
        yield delay
        delay = min(delay * _BACKOFF_FACTOR, _BACKOFF_MAX_SEC)


# ---------------------------------------------------------------------------
# Main StreamManager class
# ---------------------------------------------------------------------------

class StreamManager:
    """
    Manages a single RTSP camera connection.

    Usage (simple blocking loop):

        manager = StreamManager(camera_id="CAM-001", rtsp_url="rtsp://...")

        def on_frame(fd: FrameData):
            print(f"PTS={fd.pts_ms:.0f}ms  size={fd.width}x{fd.height}")

        manager.run(on_frame)   # blocks until stop() is called or error

    Usage (background thread):

        manager.start(on_frame)  # non-blocking
        # ... do other work ...
        manager.stop()
    """

    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        hls_url: Optional[str] = None,
        rtsp_transport: Optional[str] = None,
        frame_timeout_sec: Optional[int] = None,
        max_reconnects: Optional[int] = None,  # None = retry forever
    ):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.hls_url = hls_url or (settings.hls_url_for(camera_id) if hasattr(settings, "hls_url_for") else None)
        self.rtsp_transport = rtsp_transport or settings.rtsp_transport
        self.frame_timeout_sec = frame_timeout_sec or settings.rtsp_frame_timeout_sec
        self.max_reconnects = max_reconnects

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.info = StreamInfo(camera_id=camera_id, rtsp_url=redact_credentials(rtsp_url))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self, on_frame: Callable[[FrameData], None]) -> None:
        """
        Starts the stream ingestion loop in a background thread.
        Returns immediately.
        """
        if self._thread and self._thread.is_alive():
            logger.warning("[%s] Stream already running.", self.camera_id)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run,
            args=(on_frame,),
            name=f"stream-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("[%s] Stream thread started.", self.camera_id)

    def stop(self) -> None:
        """
        Signals the stream loop to exit cleanly.
        Blocks until the background thread finishes.
        """
        logger.info("[%s] Stop requested.", self.camera_id)
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.info.state = StreamState.STOPPED
        logger.info("[%s] Stream stopped.", self.camera_id)

    def run(self, on_frame: Callable[[FrameData], None]) -> None:
        """
        Main loop.  Connects to RTSP, reads frames, calls on_frame() for each.
        On disconnection, reconnects with exponential backoff.

        This method BLOCKS until stop() is called or max_reconnects is exhausted.
        """
        backoff = _backoff_delays()
        reconnect_count = 0

        while not self._stop_event.is_set():
            # Check reconnect limit
            if self.max_reconnects is not None and reconnect_count >= self.max_reconnects:
                logger.error(
                    "[%s] Reached max reconnect limit (%d). Giving up.",
                    self.camera_id,
                    self.max_reconnects,
                )
                break

            # --- Connect ---
            cap = self._open_capture()
            if cap is None:
                delay = next(backoff)
                logger.warning(
                    "[%s] Failed to open stream. Reconnecting in %.0fs...",
                    self.camera_id,
                    delay,
                )
                self.info.state = StreamState.RECONNECTING
                reconnect_count += 1
                self.info.total_reconnects = reconnect_count
                self._interruptible_sleep(delay)
                continue

            # --- Connected ---
            self.info.state = StreamState.CONNECTED
            self.info.connect_time = time.time()
            logger.info(
                "[%s] Connected to RTSP stream. Codec=%s  Size=%dx%d  FPS(reported)=%.1f "
                "(NOTE: reported FPS is not used for timing — PTS is used instead).",
                self.camera_id,
                self.info.codec_fourcc,
                self.info.width,
                self.info.height,
                self.info.fps_reported,
            )

            # Reset backoff on successful connection
            backoff = _backoff_delays()

            # --- Frame loop ---
            disconnected = self._read_frames(cap, on_frame)
            cap.release()

            if self._stop_event.is_set():
                break

            if disconnected:
                delay = next(backoff)
                logger.warning(
                    "[%s] Stream disconnected. Reconnecting in %.0fs...",
                    self.camera_id,
                    delay,
                )
                self.info.state = StreamState.RECONNECTING
                reconnect_count += 1
                self.info.total_reconnects = reconnect_count
                self._interruptible_sleep(delay)

        self.info.state = StreamState.STOPPED
        logger.info("[%s] Stream manager exiting.", self.camera_id)

    # ------------------------------------------------------------------ #
    # Internal — RTSP connection
    # ------------------------------------------------------------------ #

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """
        Opens an RTSP stream with mandatory TCP transport.

        Sentinel integration rule:
            "Every RTSP client must force TCP.
             Do NOT rely on UDP — UDP packet loss causes corrupted frames
             that look like AI/model failures.
             If 8554 is blocked, use the HLS endpoint."
        """
        self.info.state = StreamState.CONNECTING
        logger.info(
            "[%s] Opening RTSP stream (transport=%s): %s",
            self.camera_id,
            self.rtsp_transport,
            redact_credentials(self.rtsp_url),
        )

        import os
        # Force TCP transport per Sentinel Integrator's Guide §2 & §3
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{self.rtsp_transport}"

        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)

        # Fallback to HLS if port 8554 is blocked per Integrator's Guide §3
        if not cap.isOpened() and self.hls_url:
            logger.info(
                "[%s] Direct RTSP stream connection failed (port 8554 may be blocked). "
                "Attempting HLS endpoint fallback per Integrator's Guide: %s",
                self.camera_id,
                redact_credentials(self.hls_url),
            )
            cap = cv2.VideoCapture(self.hls_url, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            logger.warning(
                "[%s] cv2.VideoCapture failed to open. URL: %s",
                self.camera_id,
                redact_credentials(self.rtsp_url),
            )
            return None

        # --- Read stream metadata ---
        self.info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Reported FPS — stored for display only.
        # Sentinel rule: Do NOT use CAP_PROP_FPS for any timing calculation.
        self.info.fps_reported = cap.get(cv2.CAP_PROP_FPS)

        # Read fourCC codec identifier
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        self.info.codec_fourcc = _fourcc_to_str(fourcc_int)

        return cap

    # ------------------------------------------------------------------ #
    # Internal — frame reading loop
    # ------------------------------------------------------------------ #

    def _read_frames(
        self,
        cap: cv2.VideoCapture,
        on_frame: Callable[[FrameData], None],
    ) -> bool:
        """
        Reads frames from an open capture in a tight loop.

        Returns True if the loop exited due to stream disconnection.
        Returns False if stop() was requested.

        Key PTS rule:
            We read CAP_PROP_POS_MSEC IMMEDIATELY after grab() to get
            the presentation timestamp from the stream, NOT wall-clock time.
        """
        frame_index = 0
        last_pts_ms: float = -1.0
        last_frame_wall_time = time.monotonic()
        codec = self.info.codec_fourcc

        # Warn if we see what looks like decoder join warnings in the first frames
        # (H.264/H.265 streams may emit warnings until the first IDR frame)
        first_good_frame = False

        while not self._stop_event.is_set():

            # grab() fetches the next frame from the network buffer.
            # We read PTS *immediately* after grab() — before retrieve() —
            # because some backends update the timestamp at grab() time.
            grabbed = cap.grab()

            if not grabbed:
                # Check if this is a timeout (stream dead) or a transient gap
                elapsed = time.monotonic() - last_frame_wall_time
                if elapsed > self.frame_timeout_sec:
                    logger.warning(
                        "[%s] No frame received for %.1fs — treating as stream disconnection.",
                        self.camera_id,
                        elapsed,
                    )
                    return True  # signal: disconnected
                # Transient gap — Sentinel rule: variable inter-frame intervals are normal
                logger.debug(
                    "[%s] Frame grab returned False (transient gap, %.1fs elapsed). Retrying.",
                    self.camera_id,
                    elapsed,
                )
                continue

            # --- Read PTS from stream clock (NOT wall clock) ---
            # Sentinel rule: "Do NOT use datetime.now() as video timestamp.
            #                  Use presentation timestamps (PTS)."
            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)

            # Decode the frame
            ret, frame = cap.retrieve()
            if not ret or frame is None:
                # Decoder failure — could be join-time H.264/H.265 IDR issue.
                # Sentinel rule: "Do not kill the pipeline because of join-time warnings."
                if not first_good_frame:
                    logger.debug(
                        "[%s] Decoder could not retrieve frame (likely pre-IDR join artifact). "
                        "Waiting for first IDR frame...",
                        self.camera_id,
                    )
                else:
                    logger.warning(
                        "[%s] Frame retrieve failed at pts=%.0fms (unexpected mid-stream).",
                        self.camera_id,
                        pts_ms,
                    )
                continue

            first_good_frame = True
            last_frame_wall_time = time.monotonic()

            # --- PTS sanity checks ---

            # Scene discontinuity detection (loop point):
            # Sentinel rule: "At the loop point, scene abruptly changes.
            #                  Long-lived tracking state must recover."
            if last_pts_ms >= 0 and pts_ms < last_pts_ms - _SCENE_DISCONTINUITY_THRESHOLD_MS:
                logger.info(
                    "[%s] SCENE_DISCONTINUITY detected: PTS jumped from %.0fms to %.0fms. "
                    "This is a Sentinel loop point, not a camera restart. "
                    "Tracking state should be reset.",
                    self.camera_id,
                    last_pts_ms,
                    pts_ms,
                )

            # Log large inter-frame gaps (not errors — variable FPS is normal)
            if last_pts_ms >= 0:
                pts_delta = pts_ms - last_pts_ms
                if pts_delta > 2000:  # 2 second gap is unusual
                    logger.warning(
                        "[%s] Large PTS gap: %.0fms between frames "
                        "(inter-frame interval, not a disconnection).",
                        self.camera_id,
                        pts_delta,
                    )

            last_pts_ms = pts_ms
            self.info.last_pts_ms = pts_ms
            self.info.total_frames_decoded += 1

            # Re-read codec — may change if stream restarts with different settings
            # (Sentinel has mixed H.264/H.265 cameras)
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
                logger.error(
                    "[%s] Error in on_frame callback at frame %d: %s",
                    self.camera_id,
                    frame_index,
                    exc,
                    exc_info=True,
                )

            frame_index += 1

        return False  # exited because stop() was called

    def _interruptible_sleep(self, seconds: float) -> None:
        """
        Sleep for `seconds` but wake early if stop() is called.
        This avoids the stream manager sitting in time.sleep() after stop().
        """
        self._stop_event.wait(timeout=seconds)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fourcc_to_str(fourcc_int: int) -> str:
    """
    Converts the integer fourCC value from OpenCV to a 4-character string.
    E.g.  1196444237 → 'avc1'  or  875967080 → 'hvc1'
    """
    if fourcc_int == 0:
        return "UNKNOWN"
    try:
        chars = [chr((fourcc_int >> (i * 8)) & 0xFF) for i in range(4)]
        result = "".join(c for c in chars if c.isprintable() and c.strip())
        return result.upper() if result else "UNKNOWN"
    except Exception:
        return "UNKNOWN"
