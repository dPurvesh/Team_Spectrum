"""
Anomaly Detector — Catches Stillness, Not Motion
Runs PARALLEL on every frame. Detects what motion-based systems miss.
"Real crime doesn't move — it loiters."
"""

import cv2
import numpy as np
from datetime import datetime
from collections import defaultdict


class AnomalyDetector:
    def __init__(self, loiter_threshold_sec=120, fps=15,
                 position_tolerance=50):
        self.loiter_threshold_frames = loiter_threshold_sec * fps
        self.position_tolerance = position_tolerance
        self.fps = fps

        # Background subtractor for scene anomaly detection
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=50, detectShadows=True
        )

        # Tracking state
        self.tracked_objects = {}  # id → {center, first_seen, last_seen, ...}
        self.next_id = 0
        self.alerts = []
        self.active_anomalies = {}
        self.warmup_frames = fps * 3  # 3 seconds warmup for MOG2
        self.frame_counter = 0

    def update(self, frame, detections, frame_number):
        """
        Update tracking and check for anomalies.
        Returns: list of new alerts
        """
        current_alerts = []
        self.frame_counter += 1

        # Update background model
        fg_mask = self.bg_subtractor.apply(frame)

        # Track person positions
        person_dets = [d for d in detections if d['is_person']]
        matched_ids = set()

        for det in person_dets:
            center = det['center']
            best_match = None
            best_dist = float('inf')

            # Match to existing tracks
            for tid, track in self.tracked_objects.items():
                if tid in matched_ids:
                    continue
                dist = np.sqrt(
                    (center[0] - track['center'][0]) ** 2 +
                    (center[1] - track['center'][1]) ** 2
                )
                if dist < self.position_tolerance and dist < best_dist:
                    best_match = tid
                    best_dist = dist

            if best_match is not None:
                # Update existing track
                track = self.tracked_objects[best_match]
                track['center'] = center
                track['last_seen'] = frame_number
                track['box'] = det['box']
                duration_frames = track['last_seen'] - track['first_seen']
                duration_sec = duration_frames / self.fps
                track['duration_sec'] = duration_sec
                matched_ids.add(best_match)

                # --- LOITERING CHECK ---
                if duration_frames >= self.loiter_threshold_frames:
                    if not track.get('alerted', False):
                        alert = {
                            'type': 'LOITERING',
                            'track_id': best_match,
                            'position': center,
                            'box': det['box'],
                            'duration_sec': round(duration_sec, 1),
                            'frame': frame_number,
                            'timestamp': datetime.now().isoformat(),
                            'severity': 'CRITICAL',
                            'message': f"Person loitering for {round(duration_sec)}s at position {center}"
                        }
                        current_alerts.append(alert)
                        self.alerts.append(alert)
                        track['alerted'] = True
            else:
                # New person — start tracking
                self.tracked_objects[self.next_id] = {
                    'center': center,
                    'first_seen': frame_number,
                    'last_seen': frame_number,
                    'box': det['box'],
                    'duration_sec': 0,
                    'alerted': False
                }
                self.next_id += 1

        # Clean stale tracks (not seen for 5 seconds)
        stale = [tid for tid, t in self.tracked_objects.items()
                 if frame_number - t['last_seen'] > self.fps * 5]
        for tid in stale:
            del self.tracked_objects[tid]

        # --- SCENE ANOMALY CHECK (abandoned objects, crowd density) ---
        fg_percent = np.mean(fg_mask > 0) * 100
        if fg_percent > 60 and self.frame_counter > self.warmup_frames:
            alert = {
                'type': 'SCENE_ANOMALY',
                'frame': frame_number,
                'timestamp': datetime.now().isoformat(),
                'severity': 'HIGH',
                'fg_percent': round(fg_percent, 1),
                'message': f"Unusual scene change: {round(fg_percent)}% of frame altered"
            }
            current_alerts.append(alert)
            self.alerts.append(alert)

        return current_alerts

    def has_active_anomaly(self):
        """Check if any anomaly is currently active (for score boosting)"""
        for track in self.tracked_objects.values():
            if track.get('alerted', False):
                return True
        return False

    def get_active_tracks(self):
        return len(self.tracked_objects)

    def get_loitering_tracks(self):
        return [t for t in self.tracked_objects.values()
                if t.get('alerted', False)]