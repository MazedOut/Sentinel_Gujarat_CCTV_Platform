"""
sentinel-gujarat/backend/app/services/anpr/anpr_pipeline.py
------------------------------------------------------------
ANPR pipeline orchestrator.

Combines:
  1. Plate detection (YOLO on vehicle crop)
  2. OCR (PaddleOCR on plate crop)
  3. Confidence scoring

Input:  DetectionResult (one vehicle detection with bounding box)
        + the original frame (numpy BGR array)

Output: ANPRResult (plate text + confidence) or None if no plate found

The pipeline can be called per-vehicle per-frame.
It is stateless — no tracking across frames happens here.

Design decision — WHY use YOLO for plate detection instead of a fixed crop:
    Indian license plates vary widely in:
    - Position within the vehicle (front vs rear)
    - Size relative to vehicle
    - Aspect ratio (old vs new plates)
    - Angle (camera perspective)
    A dedicated plate detector gives much more reliable plate crops than
    assuming a fixed region of the vehicle bounding box.

    We use YOLOv8n with a custom plate detection model (if available)
    or fall back to a simple heuristic crop of the lower portion of the
    vehicle bounding box (which works surprisingly well for front-facing
    cameras at Indian road junctions).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from backend.app.core.logging_config import get_logger
from backend.app.models.anpr import ANPRResult, PlateDetection, is_valid_plate_format, normalise_plate
from backend.app.models.detection import BoundingBox, DetectionResult
from backend.app.services.anpr.confidence_scorer import ConfidenceScorer
from backend.app.services.anpr.ocr_engine import OCREngine

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Heuristic plate crop parameters
# ──────────────────────────────────────────────────────────────────────────────

# For the heuristic fallback (no plate detector):
# Take the bottom N% of the vehicle bounding box as the plate region.
# Rationale: front/rear plates are almost always in the lower portion.
_PLATE_REGION_BOTTOM_FRACTION = 0.35   # bottom 35% of vehicle bbox
_PLATE_REGION_CENTER_FRACTION = 0.70   # central 70% width

# Minimum vehicle crop size to attempt ANPR
_MIN_VEHICLE_WIDTH_PX = 60
_MIN_VEHICLE_HEIGHT_PX = 40


# ──────────────────────────────────────────────────────────────────────────────
# ANPRPipeline
# ──────────────────────────────────────────────────────────────────────────────

class ANPRPipeline:
    """
    Runs the full ANPR pipeline on a vehicle detection.

    Usage:
        pipeline = ANPRPipeline()
        pipeline.load()

        # For each vehicle detected in a frame:
        result = pipeline.process(frame, vehicle_detection)
        if result and result.final_confidence > 0.5:
            print(f"Plate: {result.normalised_plate}  conf={result.final_confidence:.2f}")
    """

    def __init__(self, plate_detector_model: Optional[str] = None):
        """
        Args:
            plate_detector_model: Path to a YOLO plate detection model .pt file.
                Defaults to plate_detector.pt if available locally.
        """
        if plate_detector_model is None:
            local_default = Path("ai/models/plate_detector.pt")
            if local_default.exists():
                plate_detector_model = str(local_default)

        self._plate_detector_model = plate_detector_model
        self._plate_yolo = None       # loaded if model path provided
        self._ocr = OCREngine()
        self._scorer = ConfidenceScorer()
        self._loaded = False

        # Intelligent track caching: track_id -> ANPRResult
        self._track_cache: dict[int, ANPRResult] = {}
        # Track attempts for low quality plates: track_id -> count
        self._track_attempts: dict[int, int] = {}

        # Stats
        self.total_processed = 0
        self.total_plates_found = 0
        self.total_valid_format = 0
        self.total_cache_hits = 0

    def load(self) -> None:
        """Load OCR engine (and optional plate YOLO model)."""
        logger.info("Loading ANPR pipeline...")

        # Load OCR engine
        self._ocr.load()

        # Load plate YOLO model if provided
        if self._plate_detector_model:
            try:
                from ultralytics import YOLO
                self._plate_yolo = YOLO(self._plate_detector_model)
                logger.info(
                    "Plate detector loaded: %s", self._plate_detector_model
                )
            except Exception as exc:
                logger.warning(
                    "Could not load plate detector model %s: %s. "
                    "Falling back to heuristic plate region extraction.",
                    self._plate_detector_model,
                    exc,
                )
                self._plate_yolo = None
        else:
            logger.info(
                "No plate detector model provided. "
                "Using heuristic plate region extraction (bottom 35%% of vehicle bbox)."
            )

        self._loaded = True
        logger.info("ANPR pipeline ready.")

    def process(
        self,
        frame: np.ndarray,
        vehicle_det: DetectionResult,
    ) -> Optional[ANPRResult]:
        """
        Runs ANPR on one detected vehicle.

        Args:
            frame: Full BGR frame from the camera stream.
            vehicle_det: Vehicle detection result (has bbox, pts_ms, camera_id).

        Returns:
            ANPRResult if a plate was read with any confidence.
            None if the vehicle crop is too small, or no text was found.
        """
        if not self._loaded:
            raise RuntimeError("ANPRPipeline.load() must be called first.")

        self.total_processed += 1

        # Check track-level cache: if we already have a reliable plate for this vehicle track,
        # reuse it instead of running expensive YOLO plate detector + PaddleOCR on every frame
        tid = vehicle_det.track_id
        if tid is not None:
            if tid in self._track_cache:
                self.total_cache_hits += 1
                cached = self._track_cache[tid]
                return ANPRResult(
                    camera_id=vehicle_det.camera_id,
                    pts_ms=vehicle_det.pts_ms,
                    frame_index=vehicle_det.frame_index,
                    plate_detection_confidence=cached.plate_detection_confidence,
                    plate_bbox_in_frame=cached.plate_bbox_in_frame,
                    raw_text=cached.raw_text,
                    normalised_plate=cached.normalised_plate,
                    ocr_confidence=cached.ocr_confidence,
                    is_valid_format=cached.is_valid_format,
                    final_confidence=cached.final_confidence,
                    confidence_breakdown=cached.confidence_breakdown,
                )

            # Throttle failed attempts on poor-quality distant vehicles to avoid GPU burn
            attempts = self._track_attempts.get(tid, 0)
            if attempts >= 3:
                return None
            self._track_attempts[tid] = attempts + 1

        # 1. Extract vehicle crop from full frame
        vehicle_crop = vehicle_det.bbox.crop(frame)
        if vehicle_crop is None or vehicle_crop.size == 0:
            return None

        vh, vw = vehicle_crop.shape[:2]
        if vw < _MIN_VEHICLE_WIDTH_PX or vh < _MIN_VEHICLE_HEIGHT_PX:
            logger.debug(
                "[%s] Vehicle crop too small (%dx%d) for ANPR - skipping.",
                vehicle_det.camera_id, vw, vh,
            )
            return None

        # 2. Locate the plate region
        plate_detection = self._locate_plate(vehicle_crop, vehicle_det.bbox)
        if plate_detection is None:
            return None

        # 3. Crop the plate region
        plate_crop = plate_detection.bbox_in_frame.crop(frame)
        if plate_crop is None or plate_crop.size == 0:
            return None

        # 4. OCR
        reading = self._ocr.read_plate(plate_crop)
        if reading is None:
            logger.debug(
                "[%s] No text found in plate crop at pts=%.0fms.",
                vehicle_det.camera_id, vehicle_det.pts_ms,
            )
            return None

        # 5. Score confidence
        scored = self._scorer.score(
            plate_detection_confidence=plate_detection.detection_confidence,
            ocr_conf=reading.ocr_confidence,
            is_valid_format=reading.is_valid_format,
        )

        self.total_plates_found += 1
        if reading.is_valid_format:
            self.total_valid_format += 1

        result = ANPRResult(
            camera_id=vehicle_det.camera_id,
            pts_ms=vehicle_det.pts_ms,
            frame_index=vehicle_det.frame_index,
            plate_detection_confidence=plate_detection.detection_confidence,
            plate_bbox_in_frame=plate_detection.bbox_in_frame,
            raw_text=reading.raw_text,
            normalised_plate=reading.normalised_text,
            ocr_confidence=reading.ocr_confidence,
            is_valid_format=reading.is_valid_format,
            final_confidence=scored.final_score,
            confidence_breakdown=scored.as_dict(),
        )

        # Cache high-confidence reading for this track
        if tid is not None and result.final_confidence >= 0.65 and result.is_valid_format:
            self._track_cache[tid] = result

        logger.info(
            "[%s] ANPR: %r -> %r  conf=%.2f  tier=%s  valid_format=%s",
            vehicle_det.camera_id,
            reading.raw_text,
            reading.normalised_text,
            scored.final_score,
            scored.tier,
            reading.is_valid_format,
        )

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Internal — plate location
    # ──────────────────────────────────────────────────────────────────────────

    def _locate_plate(
        self,
        vehicle_crop: np.ndarray,
        vehicle_bbox_in_frame: BoundingBox,
    ) -> Optional[PlateDetection]:
        """
        Locates the plate region using either the YOLO plate detector
        or the heuristic bottom-fraction method.
        """
        if self._plate_yolo is not None:
            return self._locate_plate_yolo(vehicle_crop, vehicle_bbox_in_frame)
        else:
            return self._locate_plate_heuristic(vehicle_crop, vehicle_bbox_in_frame)

    def _locate_plate_yolo(
        self,
        vehicle_crop: np.ndarray,
        vehicle_bbox: BoundingBox,
    ) -> Optional[PlateDetection]:
        """Use a dedicated YOLO plate detection model."""
        try:
            results = self._plate_yolo(vehicle_crop, verbose=False)
            if not results or not results[0].boxes:
                return None

            # Take highest-confidence plate detection
            boxes = results[0].boxes
            idx = boxes.conf.argmax().item()
            conf = float(boxes.conf[idx])
            x1, y1, x2, y2 = boxes.xyxy[idx].cpu().numpy()

            # Translate coords from vehicle crop → full frame
            vx1, vy1 = vehicle_bbox.x1, vehicle_bbox.y1
            bbox_in_frame = BoundingBox(
                x1=vx1 + x1, y1=vy1 + y1,
                x2=vx1 + x2, y2=vy1 + y2,
            )

            return PlateDetection(
                bbox_in_vehicle_crop=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                bbox_in_frame=bbox_in_frame,
                detection_confidence=conf,
            )
        except Exception as exc:
            logger.warning("Plate YOLO detection failed: %s", exc)
            return None

    def _locate_plate_heuristic(
        self,
        vehicle_crop: np.ndarray,
        vehicle_bbox: BoundingBox,
    ) -> Optional[PlateDetection]:
        """
        Heuristic plate region: bottom 35% × central 70% of vehicle bounding box.

        This works reliably for:
        - Front-facing traffic cameras
        - Indian road junction cameras
        - Vehicles close enough that the plate is visible

        It fails for:
        - Overhead cameras (plate on roof)
        - Vehicles too far away (plate too small)
        - Side-angle cameras

        We assign a fixed detection_confidence of 0.60 to indicate we found
        a region (not a detected object), so the final score is appropriately
        penalised compared to a real plate detection.
        """
        vh, vw = vehicle_crop.shape[:2]

        # Bottom fraction, central width
        y1_ratio = 1.0 - _PLATE_REGION_BOTTOM_FRACTION
        x_margin = (1.0 - _PLATE_REGION_CENTER_FRACTION) / 2.0

        # Coords in vehicle crop
        vc_x1 = int(vw * x_margin)
        vc_y1 = int(vh * y1_ratio)
        vc_x2 = int(vw * (1.0 - x_margin))
        vc_y2 = vh

        if vc_x2 <= vc_x1 or vc_y2 <= vc_y1:
            return None

        # Translate to full-frame coords
        vx1, vy1 = vehicle_bbox.x1, vehicle_bbox.y1
        bbox_in_frame = BoundingBox(
            x1=vx1 + vc_x1, y1=vy1 + vc_y1,
            x2=vx1 + vc_x2, y2=vy1 + vc_y2,
        )

        return PlateDetection(
            bbox_in_vehicle_crop=BoundingBox(
                x1=vc_x1, y1=vc_y1, x2=vc_x2, y2=vc_y2
            ),
            bbox_in_frame=bbox_in_frame,
            detection_confidence=0.60,  # heuristic region — not a detected object
        )
