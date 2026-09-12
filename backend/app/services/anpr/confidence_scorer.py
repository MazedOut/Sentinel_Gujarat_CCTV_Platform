"""
Confidence scorer for ANPR detections.

This module computes a composite confidence score from multiple signals:
  1. Vehicle detection confidence (from YOLO)
  2. Plate detection confidence (from plate detector)
  3. OCR confidence (from OCR engine)
  4. Format bonus (plate matches Indian plate pattern)
  5. Temporal consistency bonus (plate seen before)

IMPORTANT: We call this a "confidence score", NOT a "probability".
The model outputs are not statistically calibrated probabilities.
The formula is documented here and configurable — not buried in code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
THRESHOLD_HIGH = 0.75
THRESHOLD_MEDIUM = 0.60

# ---------------------------------------------------------------------------
# Indian plate format validator
# ---------------------------------------------------------------------------

_PLATE_REGEX = re.compile(
    r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$",
    re.IGNORECASE,
)


def is_valid_plate_format(plate: str) -> bool:
    """True if plate matches standard Indian format: GJ01AB1234"""
    if not plate:
        return False
    return bool(_PLATE_REGEX.match(plate.strip()))


# ---------------------------------------------------------------------------
# Score breakdown
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceBreakdown:
    """
    Transparent breakdown of the confidence score.
    Every component is stored so the UI can show WHY the score is what it is.
    """
    vehicle_detection_conf: float = 0.0   # YOLO raw confidence
    plate_detection_conf: float = 0.0     # plate detector confidence
    ocr_conf: float = 0.0                 # OCR engine confidence
    format_bonus: float = 0.0             # +bonus for valid format
    temporal_bonus: float = 0.0           # +bonus for repeated observation
    final_score: float = 0.0              # weighted composite
    formula: str = ""                     # human-readable formula used
    notes: list = field(default_factory=list)

    @property
    def tier(self) -> str:
        if self.final_score >= THRESHOLD_HIGH:
            return "HIGH"
        elif self.final_score >= THRESHOLD_MEDIUM:
            return "MEDIUM"
        return "LOW"

    def as_dict(self) -> dict:
        d = self.to_dict()
        d["tier"] = self.tier
        return d

    def to_dict(self) -> dict:
        return {
            "vehicle_detection_confidence": round(self.vehicle_detection_conf, 3),
            "plate_detection_confidence": round(self.plate_detection_conf, 3),
            "ocr_confidence": round(self.ocr_conf, 3),
            "format_bonus": round(self.format_bonus, 3),
            "temporal_bonus": round(self.temporal_bonus, 3),
            "final_score": round(self.final_score, 4),
            "tier": self.tier,
            "formula": self.formula,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ConfidenceScorer:
    """
    Computes ANPR detection confidence from multiple signals.
    """

    def __init__(
        self,
        vehicle_weight: float = 0.35,
        plate_weight: float = 0.35,
        ocr_weight: float = 0.30,
        format_bonus: float = 0.08,
        max_temporal_bonus: float = 0.15,
        temporal_bonus_per_obs: float = 0.05,
    ):
        self.vehicle_weight = vehicle_weight
        self.plate_weight = plate_weight
        self.ocr_weight = ocr_weight
        self.format_bonus = format_bonus
        self.max_temporal_bonus = max_temporal_bonus
        self.temporal_bonus_per_obs = temporal_bonus_per_obs
        self._plate_history: dict[str, int] = {}

    def score(
        self,
        plate_detection_conf: Optional[float] = None,
        plate_detection_confidence: Optional[float] = None,
        ocr_conf: float = 0.0,
        is_valid_format: Optional[bool] = None,
        vehicle_detection_conf: Optional[float] = None,
        plate_text: Optional[str] = None,
        prior_observations: int = 0,
    ) -> ConfidenceBreakdown:
        p_conf = plate_detection_conf if plate_detection_conf is not None else (plate_detection_confidence or 0.0)
        breakdown = ConfidenceBreakdown()
        breakdown.plate_detection_conf = p_conf
        breakdown.ocr_conf = ocr_conf

        if vehicle_detection_conf is None and is_valid_format is not None:
            # 3-signal formula (plate 35%, ocr 50%, format 15%)
            fmt_val = 1.0 if is_valid_format else 0.0
            final = p_conf * 0.35 + ocr_conf * 0.50 + fmt_val * 0.15
            breakdown.format_bonus = 0.15 if is_valid_format else 0.0
            breakdown.final_score = final
            breakdown.formula = f"0.35*{p_conf:.2f} + 0.50*{ocr_conf:.2f} + 0.15*{fmt_val:.1f} = {final:.3f}"
            return breakdown

        v_conf = vehicle_detection_conf or 0.0
        breakdown.vehicle_detection_conf = v_conf

        base = (
            self.vehicle_weight * v_conf
            + self.plate_weight * p_conf
            + self.ocr_weight * ocr_conf
        )

        fmt_bonus = 0.0
        has_valid_format = is_valid_format if is_valid_format is not None else (is_valid_plate_format(plate_text or ""))
        if has_valid_format:
            fmt_bonus = self.format_bonus
            breakdown.notes.append(f"Valid Indian plate format: +{fmt_bonus:.2f}")
        breakdown.format_bonus = fmt_bonus

        prior_from_history = self._plate_history.get(plate_text or "", 0)
        effective_prior = max(prior_observations, prior_from_history)
        temp_bonus = min(
            effective_prior * self.temporal_bonus_per_obs,
            self.max_temporal_bonus,
        )
        if temp_bonus > 0:
            breakdown.notes.append(f"Prior observations ({effective_prior}): +{temp_bonus:.2f}")
        breakdown.temporal_bonus = temp_bonus

        if plate_text:
            self._plate_history[plate_text] = effective_prior + 1

        final = min(base + fmt_bonus + temp_bonus, 1.0)
        breakdown.final_score = final
        breakdown.formula = (
            f"base={base:.3f} + fmt={fmt_bonus:.3f} + temporal={temp_bonus:.3f} = {final:.3f}"
        )
        return breakdown

    def determine_severity(
        self,
        final_score: float,
        high_threshold: float = 0.85,
        medium_threshold: float = 0.65,
    ) -> str:
        """
        Map a confidence score to an alert severity level.

        HIGH:   score >= high_threshold   → automatic alert, urgent
        MEDIUM: score >= medium_threshold → manual review queue
        LOW:    score <  medium_threshold → store, no alert

        Thresholds are passed in (from settings), NOT hard-coded here.
        """
        if final_score >= high_threshold:
            return "HIGH"
        elif final_score >= medium_threshold:
            return "MEDIUM"
        else:
            return "LOW"
