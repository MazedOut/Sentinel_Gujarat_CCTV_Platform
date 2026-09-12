"""
sentinel-gujarat/backend/app/models/detection.py
-------------------------------------------------
Data models for vehicle detection results.

These are pure data containers — no logic, no I/O.
Every downstream service (ANPR, watchlist, alert, tracking) consumes these.

Design decisions:
  - All timestamps are PTS-based (milliseconds from stream clock).
    NEVER wall-clock time, per Sentinel integration rules.
  - confidence is stored as a raw float [0.0, 1.0].
    We call it "confidence" not "probability" — we have not statistically
    calibrated the model output, so we do not claim it is a probability.
  - BoundingBox uses pixel coordinates in the original frame.
    Callers that need normalised [0..1] coords can divide by width/height.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Vehicle class taxonomy
# ---------------------------------------------------------------------------

class VehicleClass(str, Enum):
    """
    Vehicle classes we care about, mapped from COCO class indices.

    COCO indices used by YOLOv8:
        2  → car
        3  → motorcycle
        5  → bus
        7  → truck

    We intentionally exclude non-vehicle classes from our detections.
    """
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    BUS = "bus"
    TRUCK = "truck"
    UNKNOWN = "unknown"   # fallback for other detected objects


# COCO class ID → our VehicleClass mapping
COCO_TO_VEHICLE_CLASS: dict[int, VehicleClass] = {
    2: VehicleClass.CAR,
    3: VehicleClass.MOTORCYCLE,
    5: VehicleClass.BUS,
    7: VehicleClass.TRUCK,
}

# All COCO class IDs we want to detect (pass to YOLO as filter)
VEHICLE_COCO_CLASS_IDS: list[int] = list(COCO_TO_VEHICLE_CLASS.keys())


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------

@dataclass
class BoundingBox:
    """
    Axis-aligned bounding box in pixel coordinates of the original frame.

    x1, y1 = top-left corner
    x2, y2 = bottom-right corner

    All values are in pixels, relative to the top-left of the frame.
    """
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2.0

    def as_ints(self) -> tuple[int, int, int, int]:
        """Returns (x1, y1, x2, y2) as integers for OpenCV drawing."""
        return int(self.x1), int(self.y1), int(self.x2), int(self.y2)

    def crop(self, frame) -> "np.ndarray":
        """Crops this bounding box region from an OpenCV frame."""
        import numpy as np
        x1, y1, x2, y2 = self.as_ints()
        h, w = frame.shape[:2]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)
        return frame[y1:y2, x1:x2]

    def normalised(self, frame_width: int, frame_height: int) -> "BoundingBox":
        """Returns a copy with coords normalised to [0, 1]."""
        return BoundingBox(
            x1=self.x1 / frame_width,
            y1=self.y1 / frame_height,
            x2=self.x2 / frame_width,
            y2=self.y2 / frame_height,
        )

    def __repr__(self) -> str:
        return f"BBox(x1={self.x1:.0f}, y1={self.y1:.0f}, x2={self.x2:.0f}, y2={self.y2:.0f})"


# ---------------------------------------------------------------------------
# Single detection result
# ---------------------------------------------------------------------------

@dataclass
class DetectionResult:
    """
    One vehicle detected in one frame.

    Produced by VehicleDetector and consumed by:
      - ANPR service (Milestone 3): crops vehicle_bbox → plate detection
      - Tracking service (Milestone 5): correlates across frames
      - Alert engine (Milestone 5): triggers on watchlist matches
      - GIS / investigation (Milestone 6): places on map

    IMPORTANT — timestamp field:
        pts_ms is the presentation timestamp from the stream (CAP_PROP_POS_MSEC).
        It is NOT datetime.now(). This is intentional and required.
    """
    camera_id: str
    pts_ms: float               # Stream PTS — milliseconds (NOT wall clock)
    frame_index: int            # Sequential frame number within this connection
    vehicle_class: VehicleClass
    confidence: float           # Raw model output [0.0, 1.0] — NOT calibrated probability
    bbox: BoundingBox

    # Populated by ByteTrack tracking in Milestone 4/5
    track_id: Optional[int] = None

    # Populated by ANPR in Milestone 3
    plate_text: Optional[str] = None
    plate_confidence: Optional[float] = None
    plate_bbox: Optional[BoundingBox] = None

    def __repr__(self) -> str:
        plate = f"  plate={self.plate_text!r}" if self.plate_text else ""
        track = f"  track={self.track_id}" if self.track_id is not None else ""
        return (
            f"Detection(cam={self.camera_id}, "
            f"pts={self.pts_ms:.0f}ms, "
            f"class={self.vehicle_class.value}, "
            f"conf={self.confidence:.2f}, "
            f"{self.bbox}{track}{plate})"
        )


# ---------------------------------------------------------------------------
# Batch result for one frame
# ---------------------------------------------------------------------------

@dataclass
class FrameDetections:
    """
    All detections from a single frame.
    Produced once per processed frame by VehicleDetector.
    """
    camera_id: str
    pts_ms: float
    frame_index: int
    frame_width: int
    frame_height: int
    detections: list[DetectionResult] = field(default_factory=list)
    inference_time_ms: float = 0.0   # How long YOLO took on this frame

    @property
    def vehicle_count(self) -> int:
        return len(self.detections)

    @property
    def has_vehicles(self) -> bool:
        return len(self.detections) > 0
