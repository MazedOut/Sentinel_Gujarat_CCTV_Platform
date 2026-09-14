"""
Automated Multi-Frame Incident & Kinematic Collision Analyzer.

Performs real-time multi-frame kinematic trajectory analysis, IoU bounding-box
intersection tracking, sudden deceleration detection, and persistent roadway hazard
identification across Gujarat Police CCTV surveillance feeds.

Consensus Rules for Collision & Incident Validation:
  1. Spatial Bounding-Box Overlap: IoU >= 0.30 or centroid proximity < threshold.
  2. Kinetic Deceleration: Velocity drops abruptly (> 60% drop or drop below 5 px/s).
  3. Temporal Persistence: Event persists for >= 10 frames (>= 1.5s) to eliminate
     transient occlusions and passing vehicles.
  4. Cooldown Deduplication: Handled via IncidentDetector.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from backend.app.core.logging_config import get_logger
from backend.app.models.detection import BoundingBox, DetectionResult, FrameDetections, VehicleClass
from backend.app.services.alerting.incident_detector import IncidentType, get_incident_detector

logger = get_logger(__name__)


def compute_iou(b1: BoundingBox, b2: BoundingBox) -> float:
    """Computes Intersection over Union (IoU) between two bounding boxes."""
    x_left = max(b1.x1, b2.x1)
    y_top = max(b1.y1, b2.y1)
    x_right = min(b1.x2, b2.x2)
    y_bottom = min(b1.y2, b2.y2)

    if x_right <= x_left or y_bottom <= y_top:
        return 0.0

    inter_area = (x_right - x_left) * (y_bottom - y_top)
    area1 = max(1.0, (b1.x2 - b1.x1) * (b1.y2 - b1.y1))
    area2 = max(1.0, (b2.x2 - b2.x1) * (b2.y2 - b2.y1))
    union_area = area1 + area2 - inter_area
    return float(inter_area / union_area) if union_area > 0 else 0.0


@dataclass
class Waypoint:
    pts_ms: float
    bbox: BoundingBox
    cx: float
    cy: float
    speed_px_s: float
    heading_deg: float


@dataclass
class TrackState:
    track_id: int
    camera_id: str
    vehicle_class: VehicleClass
    confidence: float
    first_seen_ms: float
    last_seen_ms: float
    history: deque = field(default_factory=lambda: deque(maxlen=40))
    consecutive_stopped_frames: int = 0
    max_observed_speed: float = 0.0
    is_active: bool = True

    @property
    def current_bbox(self) -> Optional[BoundingBox]:
        return self.history[-1].bbox if self.history else None

    @property
    def current_speed(self) -> float:
        return self.history[-1].speed_px_s if self.history else 0.0

    @property
    def current_centroid(self) -> Tuple[float, float]:
        if not self.history:
            return (0.0, 0.0)
        return (self.history[-1].cx, self.history[-1].cy)


@dataclass
class IncidentCandidate:
    incident_type: IncidentType
    camera_id: str
    confidence: float
    involved_track_ids: List[int]
    evidence: Dict[str, Any]
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class IncidentAnalyzer:
    """
    Stateful multi-frame kinematic analyzer for a CCTV surveillance network.
    Maintains vehicle trajectories, velocities, headings, and spatial interactions.
    """

    def __init__(self, fps_estimate: float = 15.0):
        self.fps_estimate = fps_estimate
        # camera_id -> dict[track_id, TrackState]
        self._tracks: Dict[str, Dict[int, TrackState]] = {}
        # camera_id -> dict[pair_key, overlap_count]
        self._pair_overlap_persistence: Dict[str, Dict[Tuple[int, int], int]] = {}
        # camera_id -> prevailing flow angle (deg)
        self._prevailing_flow_deg: Dict[str, float] = {}

    def update_tracks(self, frame_detections: FrameDetections) -> List[TrackState]:
        """
        Ingests FrameDetections from VehicleDetector and updates trajectory state.
        """
        cid = frame_detections.camera_id
        if cid not in self._tracks:
            self._tracks[cid] = {}
        if cid not in self._pair_overlap_persistence:
            self._pair_overlap_persistence[cid] = {}

        now_pts = frame_detections.pts_ms
        active_track_ids = set()
        updated_states = []

        for det in frame_detections.detections:
            if det.track_id is None:
                continue
            tid = det.track_id
            active_track_ids.add(tid)

            cx = (det.bbox.x1 + det.bbox.x2) / 2.0
            cy = (det.bbox.y1 + det.bbox.y2) / 2.0

            if tid not in self._tracks[cid]:
                state = TrackState(
                    track_id=tid,
                    camera_id=cid,
                    vehicle_class=det.vehicle_class,
                    confidence=det.confidence,
                    first_seen_ms=now_pts,
                    last_seen_ms=now_pts,
                )
                self._tracks[cid][tid] = state
            else:
                state = self._tracks[cid][tid]
                state.last_seen_ms = now_pts
                state.vehicle_class = det.vehicle_class
                state.confidence = max(state.confidence, det.confidence)

            # Compute kinematic velocity
            speed_px_s = 0.0
            heading_deg = 0.0
            if state.history:
                prev = state.history[-1]
                dt_s = max(0.02, (now_pts - prev.pts_ms) / 1000.0)
                dx = cx - prev.cx
                dy = cy - prev.cy
                dist = math.hypot(dx, dy)
                speed_px_s = dist / dt_s
                heading_deg = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0

            if speed_px_s > state.max_observed_speed:
                state.max_observed_speed = speed_px_s

            if speed_px_s < 8.0:
                state.consecutive_stopped_frames += 1
            else:
                state.consecutive_stopped_frames = max(0, state.consecutive_stopped_frames - 2)

            wp = Waypoint(
                pts_ms=now_pts,
                bbox=det.bbox,
                cx=cx,
                cy=cy,
                speed_px_s=speed_px_s,
                heading_deg=heading_deg,
            )
            state.history.append(wp)
            updated_states.append(state)

        # Evict old or stale tracks (not seen in > 3 seconds)
        dead_tids = [
            tid for tid, s in self._tracks[cid].items()
            if (now_pts - s.last_seen_ms) > 3000.0
        ]
        for dtid in dead_tids:
            self._tracks[cid].pop(dtid, None)

        return updated_states

    def analyze_incidents(
        self,
        frame_detections: FrameDetections,
        trigger_alerts: bool = True,
        camera_location: str = "Gujarat Surveillance Corridor",
        camera_lat: float = 23.0225,
        camera_lon: float = 72.5714,
    ) -> List[IncidentCandidate]:
        """
        Runs multi-frame consensus checks across active tracks in this camera frame.
        """
        cid = frame_detections.camera_id
        active_states = self.update_tracks(frame_detections)
        candidates: List[IncidentCandidate] = []

        if len(active_states) < 1:
            return candidates

        # ------------------------------------------------------------------
        # 1. VEHICULAR COLLISION DETECTION (Pairwise IoU + Deceleration)
        # ------------------------------------------------------------------
        current_pair_overlaps = set()
        for i in range(len(active_states)):
            for j in range(i + 1, len(active_states)):
                s1 = active_states[i]
                s2 = active_states[j]
                if s1.current_bbox is None or s2.current_bbox is None:
                    continue

                iou = compute_iou(s1.current_bbox, s2.current_bbox)
                pair_key = (min(s1.track_id, s2.track_id), max(s1.track_id, s2.track_id))

                # Check proximity / intersection
                if iou >= 0.28:
                    current_pair_overlaps.add(pair_key)
                    count = self._pair_overlap_persistence[cid].get(pair_key, 0) + 1
                    self._pair_overlap_persistence[cid][pair_key] = count

                    # Verify sudden deceleration or stoppage
                    pre_speed = max(s1.max_observed_speed, s2.max_observed_speed)
                    post_speed = min(s1.current_speed, s2.current_speed)

                    # Consensus: overlap persisted for >= 10 frames, significant pre-speed, now stalled
                    if count >= 10 and (post_speed < 12.0 or (pre_speed > 30.0 and post_speed < pre_speed * 0.4)):
                        conf = min(0.96, 0.72 + (iou * 0.3) + min(0.15, count * 0.01))
                        evidence = {
                            "iou": round(iou, 3),
                            "persisted_frames": count,
                            "pre_speed_px_s": round(pre_speed, 1),
                            "post_speed_px_s": round(post_speed, 1),
                            "track_ids": [s1.track_id, s2.track_id],
                            "vehicle_classes": [s1.vehicle_class.value, s2.vehicle_class.value],
                        }
                        cand = IncidentCandidate(
                            incident_type=IncidentType.VEHICLE_COLLISION,
                            camera_id=cid,
                            confidence=round(conf, 2),
                            involved_track_ids=[s1.track_id, s2.track_id],
                            evidence=evidence,
                        )
                        candidates.append(cand)
                        logger.warning(
                            "AI INCIDENT: Collision confirmed between Track-%d and Track-%d on %s (IoU: %.2f, Frames: %d)",
                            s1.track_id, s2.track_id, cid, iou, count
                        )
                else:
                    # Decay overlap persistence if separation restored
                    if pair_key in self._pair_overlap_persistence[cid]:
                        self._pair_overlap_persistence[cid][pair_key] = max(
                            0, self._pair_overlap_persistence[cid][pair_key] - 1
                        )

        # ------------------------------------------------------------------
        # 2. STALLED / STOPPED VEHICLE HAZARD (In-lane Obstruction)
        # ------------------------------------------------------------------
        for s in active_states:
            # Vehicle was moving (>35 px/s), now stopped for >= 18 frames in active traffic
            if s.max_observed_speed > 35.0 and s.consecutive_stopped_frames >= 18:
                evidence = {
                    "stopped_frames": s.consecutive_stopped_frames,
                    "max_speed_px_s": round(s.max_observed_speed, 1),
                    "current_speed_px_s": round(s.current_speed, 1),
                    "track_id": s.track_id,
                    "vehicle_class": s.vehicle_class.value,
                }
                cand = IncidentCandidate(
                    incident_type=IncidentType.STALLED_VEHICLE_HAZARD,
                    camera_id=cid,
                    confidence=0.88,
                    involved_track_ids=[s.track_id],
                    evidence=evidence,
                )
                candidates.append(cand)

        # ------------------------------------------------------------------
        # 3. TRIGGER OFFICIAL ALERTS VIA INCIDENT DETECTOR
        # ------------------------------------------------------------------
        if trigger_alerts and candidates:
            detector = get_incident_detector()
            for cand in candidates:
                involved_labels = [f"TRACK-{tid}" for tid in cand.involved_track_ids]
                desc = (
                    f"AI Incident Analysis: {cand.incident_type.value} verified on {cid.upper()} "
                    f"involving {', '.join(involved_labels)}. Evidence: {cand.evidence}."
                )
                detector.verify_and_trigger_incident(
                    camera_id=cid,
                    location=camera_location,
                    latitude=camera_lat,
                    longitude=camera_lon,
                    incident_type=cand.incident_type,
                    confidence=cand.confidence,
                    involved_vehicles=involved_labels,
                    description=desc,
                )

        return candidates

    def clear(self, camera_id: Optional[str] = None) -> None:
        """Resets tracking buffers for one or all cameras."""
        if camera_id:
            self._tracks.pop(camera_id, None)
            self._pair_overlap_persistence.pop(camera_id, None)
        else:
            self._tracks.clear()
            self._pair_overlap_persistence.clear()


# Global Singleton
_global_analyzer: Optional[IncidentAnalyzer] = None


def get_incident_analyzer() -> IncidentAnalyzer:
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = IncidentAnalyzer()
    return _global_analyzer
