"""
sentinel-gujarat/backend/app/models/anpr.py
-------------------------------------------
Data models for ANPR (Automatic Number Plate Recognition) results.

The ANPR pipeline adds plate information to DetectionResult objects.
Data flows like this:

    FrameData (from StreamManager)
        → VehicleDetector → DetectionResult (bounding box, vehicle class)
            → PlateDetector → PlateDetection (plate bounding box within vehicle crop)
                → OCREngine → PlateReading (raw text + confidence)
                    → ConfidenceScorer → ANPRResult (normalised plate + final score)

IMPORTANT on confidence:
    We call these scores "confidence" not "probability".
    They are raw model outputs, not statistically calibrated.
    The scoring formula is documented in confidence_scorer.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import re

from backend.app.models.detection import BoundingBox


# ---------------------------------------------------------------------------
# Gujarat plate format
# ---------------------------------------------------------------------------

# Standard Indian vehicle registration plate format:
#   GJ 01 AB 1234   (state code, RTO number, series letters, number)
# We normalise to uppercase without spaces: GJ01AB1234

_PLATE_PATTERN = re.compile(
    r"^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$",
    re.IGNORECASE,
)

# Characters that OCR commonly confuses on plates
_OCR_SUBSTITUTIONS = {
    "O": "0",  # letter O → digit 0 (in numeric sections)
    "I": "1",  # letter I → digit 1 (in numeric sections)
    "S": "5",  # letter S → digit 5 (in numeric sections)
    "B": "8",  # letter B → digit 8 (in numeric sections)
    " ": "",   # strip spaces
    "-": "",   # strip hyphens
    ".": "",   # strip dots
}


def normalise_plate(raw_text: str) -> str:
    """
    Cleans and normalises raw OCR plate text per Indian HSRP specification.

    Steps:
      1. Strip whitespace, convert to uppercase
      2. Remove common separators (space, hyphen, dot, newline)
      3. Apply positional character correction if candidate length matches (e.g., 9-10 chars)
    """
    if not raw_text:
        return ""
    cleaned = raw_text.upper().strip()
    for char in (" ", "-", ".", "\n", "\r", "_", ":", "|", "/"):
        cleaned = cleaned.replace(char, "")

    letter_to_digit = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"}
    digit_to_letter = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B"}

    # Standard 10-char format: State(2 letters) + District(2 digits) + Series(2 letters) + Number(4 digits)
    if len(cleaned) == 10:
        chars = list(cleaned)
        # Positions 0, 1: Letters (State)
        chars[0] = digit_to_letter.get(chars[0], chars[0])
        chars[1] = digit_to_letter.get(chars[1], chars[1])
        # Positions 2, 3: Digits (District)
        chars[2] = letter_to_digit.get(chars[2], chars[2])
        chars[3] = letter_to_digit.get(chars[3], chars[3])
        # Positions 4, 5: Letters (Series)
        chars[4] = digit_to_letter.get(chars[4], chars[4])
        chars[5] = digit_to_letter.get(chars[5], chars[5])
        # Positions 6-9: Digits (Number)
        for i in range(6, 10):
            chars[i] = letter_to_digit.get(chars[i], chars[i])
        corrected = "".join(chars)
        if _PLATE_PATTERN.match(corrected):
            return corrected

    # 9-char format: State(2 letters) + District(2 digits) + Series(1 letter) + Number(4 digits)
    elif len(cleaned) == 9:
        chars = list(cleaned)
        chars[0] = digit_to_letter.get(chars[0], chars[0])
        chars[1] = digit_to_letter.get(chars[1], chars[1])
        chars[2] = letter_to_digit.get(chars[2], chars[2])
        chars[3] = letter_to_digit.get(chars[3], chars[3])
        chars[4] = digit_to_letter.get(chars[4], chars[4])
        for i in range(5, 9):
            chars[i] = letter_to_digit.get(chars[i], chars[i])
        corrected = "".join(chars)
        if _PLATE_PATTERN.match(corrected):
            return corrected

    return cleaned


def is_valid_plate_format(plate: str) -> bool:
    """
    Returns True if the plate matches the standard Indian format.
    GJ01AB1234 → True
    HELLO → False
    """
    return bool(_PLATE_PATTERN.match(plate))


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PlateDetection:
    """
    A license plate region detected within a vehicle crop.
    Produced by PlateDetector.
    """
    bbox_in_vehicle_crop: BoundingBox   # coords relative to vehicle crop
    bbox_in_frame: BoundingBox          # coords relative to full frame
    detection_confidence: float         # plate detector confidence [0,1]


@dataclass
class PlateReading:
    """
    Raw OCR output for one plate region.
    Produced by OCREngine.
    """
    raw_text: str               # exactly what the OCR read
    normalised_text: str        # after normalise_plate()
    ocr_confidence: float       # OCR engine confidence [0,1]
    is_valid_format: bool       # matches Indian plate regex


@dataclass
class ANPRResult:
    """
    Complete ANPR result for one detected vehicle.

    Links back to the vehicle detection via camera_id + frame_index + pts_ms.
    These three fields uniquely identify the source frame.

    Confidence breakdown:
        plate_detection_confidence  — plate found in vehicle crop
        ocr_confidence              — OCR text reading
        format_bonus                — +0.05 if plate matches GJ##XX#### pattern
        final_confidence            — computed by ConfidenceScorer

    IMPORTANT: final_confidence is a heuristic score, not a calibrated probability.
    """
    camera_id: str
    pts_ms: float           # Stream PTS — NOT wall clock
    frame_index: int

    # Plate detection
    plate_detection_confidence: float
    plate_bbox_in_frame: Optional[BoundingBox]

    # OCR reading
    raw_text: str
    normalised_plate: str
    ocr_confidence: float
    is_valid_format: bool

    # Final scored confidence
    final_confidence: float

    # Computed by ConfidenceScorer — explains the score
    confidence_breakdown: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"ANPR(cam={self.camera_id}, "
            f"pts={self.pts_ms:.0f}ms, "
            f"plate={self.normalised_plate!r}, "
            f"conf={self.final_confidence:.2f}, "
            f"valid_format={self.is_valid_format})"
        )
