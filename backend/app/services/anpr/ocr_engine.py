"""
sentinel-gujarat/backend/app/services/anpr/ocr_engine.py
---------------------------------------------------------
OCR engine wrapper using PaddleOCR 3.x (installed: 3.7.0).

Responsibility:
    Given a cropped image of a license plate, read the text.

PaddleOCR 3.x API differences from 2.x:
    - No `use_angle_cls` parameter → replaced by `use_textline_orientation`
    - No `show_log` parameter → silence via Python logging
    - `.predict()` instead of `.ocr()` method
    - Results structure is different (list of dicts, not nested list)

This module isolates all PaddleOCR API calls so that if we switch OCR
engines later (e.g. to Tesseract), only this file changes.

Design decisions:
    - Singleton pattern: one OCR engine instance per process
      (PaddleOCR model loading is expensive — ~2-3s first init)
    - Minimum confidence threshold: discard unreadable results early
    - Input preprocessing: resize plate crop to standard height for OCR
    - Result parsing: handles PaddleOCR 3.x output format
"""

from __future__ import annotations

import torch  # Must be imported before paddle to ensure DLL compatibility on Windows
import logging
import os
import warnings
from typing import Optional

import cv2
import numpy as np

from backend.app.core.logging_config import get_logger
from backend.app.models.anpr import PlateReading, normalise_plate, is_valid_plate_format

logger = get_logger(__name__)

# Silence PaddleOCR's verbose console output
logging.getLogger("ppocr").setLevel(logging.ERROR)
logging.getLogger("paddle").setLevel(logging.ERROR)
os.environ["GLOG_minloglevel"] = "3"
os.environ["GLOG_v"] = "0"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "False"

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Minimum plate crop height for reliable OCR (pixels)
# Below this, text is too small for PaddleOCR to read accurately
_MIN_PLATE_HEIGHT_PX = 32
_TARGET_PLATE_HEIGHT_PX = 64   # upscale small crops to this height

# Discard any OCR result below this confidence
_MIN_OCR_CONFIDENCE = 0.30


# ──────────────────────────────────────────────────────────────────────────────
# OCREngine
# ──────────────────────────────────────────────────────────────────────────────

class OCREngine:
    """
    Wraps PaddleOCR 3.x for license plate text reading.

    Usage:
        engine = OCREngine()
        engine.load()

        reading = engine.read_plate(plate_crop_bgr)
        if reading:
            print(reading.normalised_text, reading.ocr_confidence)
    """

    def __init__(self, min_confidence: float = _MIN_OCR_CONFIDENCE):
        self.min_confidence = min_confidence
        self._ocr = None
        self._loaded = False

    def load(self) -> None:
        """
        Initialises PaddleOCR. First call downloads models (~10-50 MB).
        Call once at startup, before processing any frames.
        """
        if self._loaded:
            return

        logger.info("Loading PaddleOCR 3.x OCR engine (lang=en)...")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from paddleocr import PaddleOCR

                # PaddleOCR 3.7.0 constructor — verified via introspection
                self._ocr = PaddleOCR(
                    lang="en",
                    use_textline_orientation=False,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    text_det_thresh=0.3,             # lower = more sensitive detection
                    text_det_box_thresh=0.5,
                    text_rec_score_thresh=self.min_confidence,
                )

            self._loaded = True
            logger.info("PaddleOCR loaded successfully.")

        except Exception as exc:
            logger.error("Failed to load PaddleOCR: %s", exc, exc_info=True)
            raise

    def read_plate(self, plate_crop: np.ndarray) -> Optional[PlateReading]:
        """
        Reads text from a plate crop image.

        Args:
            plate_crop: BGR image of the license plate region.
                        Can be any size — will be preprocessed internally.

        Returns:
            PlateReading if text was found with sufficient confidence.
            None if the crop is too small, unreadable, or low-confidence.
        """
        if not self._loaded:
            raise RuntimeError("OCREngine.load() must be called before read_plate().")

        if plate_crop is None or plate_crop.size == 0:
            logger.debug("Empty plate crop — skipping OCR.")
            return None

        h, w = plate_crop.shape[:2]
        if h < _MIN_PLATE_HEIGHT_PX:
            logger.debug("Plate crop too small (%dx%d) for OCR — skipping.", w, h)
            return None

        # Preprocess: upscale small crops, sharpen
        processed = self._preprocess(plate_crop)

        # Run OCR
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                results = self._ocr.predict(processed)
        except Exception as exc:
            logger.warning("PaddleOCR predict() failed: %s", exc)
            return None

        return self._parse_results(results)

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _preprocess(self, crop: np.ndarray) -> np.ndarray:
        """
        Prepares a plate crop for OCR:
        1. Convert to grayscale → back to BGR (PaddleOCR expects BGR)
        2. Upscale to target height if too small
        3. Sharpen edges
        """
        h, w = crop.shape[:2]

        # Upscale if needed
        if h < _TARGET_PLATE_HEIGHT_PX:
            scale = _TARGET_PLATE_HEIGHT_PX / h
            new_w = int(w * scale)
            crop = cv2.resize(crop, (new_w, _TARGET_PLATE_HEIGHT_PX),
                              interpolation=cv2.INTER_CUBIC)

        # Sharpen: unsharp mask
        blurred = cv2.GaussianBlur(crop, (0, 0), 3)
        sharpened = cv2.addWeighted(crop, 1.5, blurred, -0.5, 0)

        return sharpened

    def _parse_results(self, results) -> Optional[PlateReading]:
        """
        Parses PaddleOCR 3.x predict() output into a PlateReading.

        PaddleOCR 3.x returns a list of dicts. Each dict has:
          'rec_texts'   : list of recognised text strings
          'rec_scores'  : list of confidence scores (floats)

        We concatenate all text fragments and take the minimum confidence
        as the overall reading confidence (weakest link).
        """
        if not results:
            return None

        all_texts = []
        all_scores = []

        for item in results:
            if not isinstance(item, dict):
                continue

            texts = item.get("rec_texts", []) or []
            scores = item.get("rec_scores", []) or []

            for text, score in zip(texts, scores):
                if text and score is not None and float(score) >= self.min_confidence:
                    all_texts.append(str(text).strip())
                    all_scores.append(float(score))

        if not all_texts:
            logger.debug("No OCR text above confidence threshold %.2f", self.min_confidence)
            return None

        # Join all fragments — plate text may be split across lines
        raw_text = " ".join(all_texts)
        avg_confidence = sum(all_scores) / len(all_scores)
        normalised = normalise_plate(raw_text)

        logger.debug(
            "OCR read: %r → normalised: %r  conf=%.2f",
            raw_text, normalised, avg_confidence,
        )

        return PlateReading(
            raw_text=raw_text,
            normalised_text=normalised,
            ocr_confidence=avg_confidence,
            is_valid_format=is_valid_plate_format(normalised),
        )
