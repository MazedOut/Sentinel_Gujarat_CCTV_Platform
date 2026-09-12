"""
sentinel-gujarat/backend/app/services/detection/visualiser.py
--------------------------------------------------------------
Draws detection bounding boxes on frames for diagnostic/demo purposes.

This is a development tool — it is NOT part of the production pipeline.
The production pipeline operates on metadata (DetectionResult objects),
not on rendered frames.

Used by:
  scripts/test_detection.py   — saves annotated frames to disk for review

Usage:
    annotated = Visualiser.draw(frame, frame_detections)
    cv2.imwrite("debug.jpg", annotated)
"""

from __future__ import annotations

import cv2
import numpy as np

from backend.app.models.detection import FrameDetections, VehicleClass


# Colour map per vehicle class (BGR format for OpenCV)
_CLASS_COLOURS: dict[VehicleClass, tuple[int, int, int]] = {
    VehicleClass.CAR:        (0, 200, 0),      # green
    VehicleClass.MOTORCYCLE: (0, 165, 255),    # orange
    VehicleClass.BUS:        (255, 0, 0),      # blue
    VehicleClass.TRUCK:      (0, 0, 220),      # red
    VehicleClass.UNKNOWN:    (128, 128, 128),  # grey
}

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.55
_THICKNESS = 2


class Visualiser:
    """
    Static helper that draws bounding boxes + labels on a copy of the frame.
    Never modifies the original frame.
    """

    @staticmethod
    def draw(frame: np.ndarray, fd: FrameDetections) -> np.ndarray:
        """
        Returns a new frame with bounding boxes and labels drawn.

        Args:
            frame: Original BGR frame from OpenCV.
            fd: FrameDetections from VehicleDetector.

        Returns:
            Annotated copy of the frame.
        """
        out = frame.copy()

        for det in fd.detections:
            colour = _CLASS_COLOURS.get(det.vehicle_class, (200, 200, 200))
            x1, y1, x2, y2 = det.bbox.as_ints()

            # Draw bounding box
            cv2.rectangle(out, (x1, y1), (x2, y2), colour, _THICKNESS)

            # Label: "car 0.87"
            label = f"{det.vehicle_class.value} {det.confidence:.2f}"
            if det.plate_text:
                label += f" | {det.plate_text}"

            # Background chip for label readability
            (tw, th), _ = cv2.getTextSize(label, _FONT, _FONT_SCALE, _THICKNESS)
            cv2.rectangle(out, (x1, y1 - th - 6), (x1 + tw + 4, y1), colour, -1)
            cv2.putText(
                out, label,
                (x1 + 2, y1 - 4),
                _FONT, _FONT_SCALE,
                (255, 255, 255),  # white text
                _THICKNESS - 1,
                cv2.LINE_AA,
            )

        # HUD overlay (top-left corner)
        hud_lines = [
            f"CAM: {fd.camera_id}",
            f"PTS: {fd.pts_ms:.0f} ms",
            f"Vehicles: {fd.vehicle_count}",
            f"Inference: {fd.inference_time_ms:.1f} ms",
        ]
        _draw_hud(out, hud_lines)

        return out


def _draw_hud(frame: np.ndarray, lines: list[str]) -> None:
    """Draws a semi-transparent HUD in the top-left corner."""
    x, y_start, line_h = 10, 30, 22
    for i, line in enumerate(lines):
        y = y_start + i * line_h
        # Dark shadow for readability on any background
        cv2.putText(frame, line, (x + 1, y + 1), _FONT, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(frame, line, (x, y), _FONT, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
