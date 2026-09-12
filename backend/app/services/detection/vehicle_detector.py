"""
sentinel-gujarat/backend/app/services/detection/vehicle_detector.py
---------------------------------------------------------------------
YOLOv8-based vehicle detection service.

What this does:
  1. Loads a YOLOv8 model (default: yolov8n — fastest, good for PoC)
  2. Accepts a frame (numpy BGR array) from StreamManager
  3. Runs inference, filtering only for vehicle classes
  4. Returns a FrameDetections object with DetectionResult entries

What it does NOT do:
  - ANPR (Milestone 3)
  - Watchlist matching (Milestone 5)
  - Any tracking across frames (Milestone 5)

Key design decisions:

  MODEL CHOICE — yolov8n vs yolov8s vs yolov8m:
    yolov8n (nano):  ~3ms/frame on RTX 4060 — fast, less accurate
    yolov8s (small): ~6ms/frame on RTX 4060 — good balance, recommended
    yolov8m (medium):~12ms/frame — better accuracy, still real-time
    Default: yolov8n for PoC; easy to upgrade via config.

  GPU vs CPU:
    Automatically uses CUDA if available (RTX 4060 detected).
    Falls back to CPU transparently if CUDA is unavailable.

  FRAME SKIPPING:
    We do NOT run YOLO on every single frame.
    Reason: RTSP streams can be 25-30fps. Running YOLO at 30fps is
    wasteful for a PoC and unnecessary for vehicle tracking.
    Default: process every Nth frame (configurable, default=3).
    This gives ~10 effective detections/second on a 30fps stream.

  CONFIDENCE THRESHOLD:
    Detections below min_confidence are discarded before returning.
    Default: 0.40. Configurable — don't raise too high or you miss vehicles.
    The threshold is configurable, not hardcoded throughout the codebase.

  VEHICLE CLASSES:
    Only COCO classes 2 (car), 3 (motorcycle), 5 (bus), 7 (truck) are returned.
    Persons, animals, traffic lights etc. are filtered out.

  TIMESTAMP:
    Inference results carry the PTS from the source frame, NOT wall-clock time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np

from backend.app.core.logging_config import get_logger
from backend.app.models.detection import (
    BoundingBox,
    DetectionResult,
    FrameDetections,
    VehicleClass,
    COCO_TO_VEHICLE_CLASS,
    VEHICLE_COCO_CLASS_IDS,
)
from backend.app.services.streaming.stream_manager import FrameData

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Default model configuration
# ---------------------------------------------------------------------------

# Where to store downloaded model weights
# YOLO26 auto-downloads or uses local weights
DEFAULT_MODEL_NAME = "ai/models/yolo26n.pt"   # YOLO26 nano — high-efficiency realtime detector

# Default inference confidence threshold
DEFAULT_CONFIDENCE = 0.35

# Default: process every Nth frame (skip the rest)
# GPU (CUDA): frame_skip=3 → ~10 detections/sec on 30fps stream
# CPU:        frame_skip=5 → ~5 detections/sec (still more than enough for tracking)
# The first frame always has a JIT compilation spike — don't mistake it for real performance.
DEFAULT_FRAME_SKIP = 3


# ---------------------------------------------------------------------------
# VehicleDetector
# ---------------------------------------------------------------------------

class VehicleDetector:
    """
    Wraps a YOLOv8 model for vehicle detection on Sentinel camera frames.

    Usage:

        detector = VehicleDetector()
        detector.load()

        def on_frame(fd: FrameData):
            result = detector.detect(fd)
            if result and result.has_vehicles:
                for det in result.detections:
                    print(det)

    The detector is stateless between frames — no internal tracking.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        confidence_threshold: float = DEFAULT_CONFIDENCE,
        frame_skip: int = DEFAULT_FRAME_SKIP,
        device: Optional[str] = None,   # None = auto-detect GPU/CPU
    ):
        """
        Args:
            model_name: YOLOv8 model file name or path.
                        'yolov8n.pt' → auto-download ~6 MB weights.
                        'yolov8s.pt' → ~22 MB, better accuracy.
            confidence_threshold: Minimum detection confidence [0.0, 1.0].
                        Detections below this are discarded.
            frame_skip: Process every Nth frame. 1 = every frame.
            device: 'cuda', 'cpu', or None (auto). Usually leave as None.
        """
        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.frame_skip = max(1, frame_skip)
        self._device = device
        self._model = None          # loaded lazily in load()
        self._frame_counter = 0     # counts frames seen (for skip logic)

        # Stats for diagnostics
        self.total_frames_processed = 0
        self.total_frames_skipped = 0
        self.total_detections = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """
        Loads (and if necessary downloads) the YOLOv8 model.

        Call this once before processing frames.
        First call will download weights from ultralytics CDN (~6-22 MB).
        Subsequent calls use the local cache.
        """
        try:
            from ultralytics import YOLO
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics or torch is not installed. "
                "Run: pip install ultralytics torch torchvision "
                "--index-url https://download.pytorch.org/whl/cu128"
            ) from exc

        # Device selection
        if self._device is None:
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(
            "Loading YOLOv8 model: %s | device=%s | confidence_threshold=%.2f | frame_skip=%d",
            self.model_name,
            self._device,
            self.confidence_threshold,
            self.frame_skip,
        )

        if self._device == "cuda":
            import torch
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
            logger.info("GPU: %s  (%.1f GB VRAM)", gpu_name, gpu_mem_gb)
        else:
            logger.info("Running on CPU — inference will be slower.")

        t0 = time.monotonic()
        self._model = YOLO(self.model_name)
        self._model.to(self._device)

        # Warm-up: run TWO dummy inferences.
        # The first triggers PyTorch JIT compilation (can take 2-5s on CPU).
        # The second gives the real steady-state inference time.
        # Without warmup, the first real video frame appears to take 4+ seconds.
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        logger.debug("Warming up model (first inference triggers JIT compilation)...")
        self._model(dummy, verbose=False)   # JIT compile
        self._model(dummy, verbose=False)   # actual warmup

        elapsed = (time.monotonic() - t0) * 1000
        logger.info("YOLOv8 model loaded and warmed up in %.0fms (includes JIT compile)", elapsed)

    def detect(self, fd: FrameData) -> Optional[FrameDetections]:
        """
        Runs vehicle detection on one frame.

        Args:
            fd: FrameData from StreamManager. Must contain the BGR frame
                and its PTS timestamp.

        Returns:
            FrameDetections if this frame was processed (not skipped).
            None if the frame was skipped (frame_skip logic).

        Raises:
            RuntimeError if load() has not been called.
        """
        if self._model is None:
            raise RuntimeError("VehicleDetector.load() must be called before detect().")

        self._frame_counter += 1

        # Frame skip logic — skip frames that are not multiples of frame_skip
        if self._frame_counter % self.frame_skip != 0:
            self.total_frames_skipped += 1
            return None

        t0 = time.monotonic()

        # Run YOLO26 inference with ByteTrack multi-object tracking
        # persist=True maintains track IDs across consecutive frames
        # classes= filters to vehicle class IDs (cars, motorcycles, buses, trucks)
        try:
            results = self._model.track(
                fd.frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=VEHICLE_COCO_CLASS_IDS,
                conf=self.confidence_threshold,
                verbose=False,
                device=self._device,
            )
        except Exception as track_err:
            logger.debug("ByteTrack fallback to standard inference: %s", track_err)
            results = self._model(
                fd.frame,
                classes=VEHICLE_COCO_CLASS_IDS,
                conf=self.confidence_threshold,
                verbose=False,
                device=self._device,
            )

        inference_ms = (time.monotonic() - t0) * 1000
        self.total_frames_processed += 1

        # Parse results into our DetectionResult objects
        detections = self._parse_results(results, fd)
        self.total_detections += len(detections)

        if detections:
            logger.debug(
                "[%s] Frame#%d pts=%.0fms -> %d vehicle(s) detected in %.1fms",
                fd.camera_id,
                fd.frame_index,
                fd.pts_ms,
                len(detections),
                inference_ms,
            )

        return FrameDetections(
            camera_id=fd.camera_id,
            pts_ms=fd.pts_ms,
            frame_index=fd.frame_index,
            frame_width=fd.width,
            frame_height=fd.height,
            detections=detections,
            inference_time_ms=inference_ms,
        )

    @property
    def device(self) -> str:
        return self._device or "not loaded"

    def summary(self) -> str:
        return (
            f"VehicleDetector("
            f"model={self.model_name}, "
            f"device={self.device}, "
            f"processed={self.total_frames_processed}, "
            f"skipped={self.total_frames_skipped}, "
            f"detections={self.total_detections})"
        )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _parse_results(
        self, results, fd: FrameData
    ) -> list[DetectionResult]:
        """
        Converts raw ultralytics Results into our DetectionResult list with ByteTrack track IDs.
        """
        detections: list[DetectionResult] = []

        if not results or len(results) == 0:
            return detections

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return detections

        # Move tensors to CPU for numpy conversion
        xyxy = boxes.xyxy.cpu().numpy()     # shape: (N, 4)
        confs = boxes.conf.cpu().numpy()    # shape: (N,)
        cls_ids = boxes.cls.cpu().numpy().astype(int)  # shape: (N,)
        track_ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None

        for i in range(len(xyxy)):
            x1, y1, x2, y2 = xyxy[i]
            conf = float(confs[i])
            coco_cls_id = int(cls_ids[i])
            track_id = int(track_ids[i]) if track_ids is not None else None

            # Map COCO class ID -> our VehicleClass
            vehicle_class = COCO_TO_VEHICLE_CLASS.get(
                coco_cls_id, VehicleClass.UNKNOWN
            )

            det = DetectionResult(
                camera_id=fd.camera_id,
                pts_ms=fd.pts_ms,       # PTS from stream, NOT wall clock
                frame_index=fd.frame_index,
                vehicle_class=vehicle_class,
                confidence=conf,
                bbox=BoundingBox(x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2)),
                track_id=track_id,
            )
            detections.append(det)

        return detections
