"""
Frame Intelligence Scoring Engine — Score 0 to 100
Drives ALL downstream decisions: compression, storage, alerts.
"""

from collections import deque
from datetime import datetime


class FrameScorer:
    # Zone definitions (percentage of frame width)
    # Override per camera via config
    ZONES = {
        'entry': {'x_range': (0.0, 0.2), 'weight': 1.2},
        'restricted': {'x_range': (0.3, 0.7), 'weight': 1.5},
        'exit': {'x_range': (0.8, 1.0), 'weight': 1.2},
        'normal': {'x_range': (0.2, 0.8), 'weight': 1.0}
    }

    def __init__(self):
        self.score_history = deque(maxlen=1000)
        self.prev_positions = {}  # track_id → (x, y) for speed calc

    def calculate_score(self, detections, diff_score, frame_shape,
                        anomaly_flag=False, time_of_day=None):
        """
        Score a frame from 0-100 based on multiple factors.
        """
        h, w = frame_shape[:2]
        score = 0.0

        # Auto-detect time of day
        if time_of_day is None:
            hour = datetime.now().hour
            time_of_day = 'night' if (hour >= 22 or hour < 6) else 'day'

        # --- Factor 1: Person count (max +35) ---
        # First person = +12, each additional = +8, capped at 35
        persons = [d for d in detections if d['is_person']]
        if len(persons) == 0:
            person_score = 0
        elif len(persons) == 1:
            person_score = 12
        else:
            person_score = min(12 + (len(persons) - 1) * 8, 35)
        score += person_score

        # --- Factor 2: Zone weighting (max +20) ---
        zone_score = 0
        for det in persons:
            cx = det['center'][0] / w  # Normalize to 0-1
            for zone_name, zone_def in self.ZONES.items():
                xr = zone_def['x_range']
                if xr[0] <= cx <= xr[1]:
                    zone_score = max(zone_score, 10 * zone_def['weight'])
                    break
        score += min(zone_score, 20)

        # --- Factor 3: Detection confidence (max +15) ---
        if detections:
            max_conf = max(d['confidence'] for d in detections)
            score += max_conf * 15

        # --- Factor 4: Motion intensity from SNN (max +10) ---
        score += min(diff_score * 60, 10)

        # --- Factor 5: Night bonus (max +5) ---
        if time_of_day == 'night':
            score += 5

        # --- Factor 6: Anomaly flag (max +28) ---
        if anomaly_flag:
            score += 28

        # Clamp to 0-100
        final_score = min(max(round(score, 1), 0), 100)
        self.score_history.append(final_score)

        # Categorize per spec: >60 EVENT, 30-60 NORMAL, <30 IDLE
        if final_score > 60:
            category = "EVENT"
        elif final_score > 30:
            category = "NORMAL"
        else:
            category = "IDLE"

        return final_score, category

    def get_avg_score(self):
        if not self.score_history:
            return 0
        return round(sum(self.score_history) / len(self.score_history), 1)

    def get_score_distribution(self):
        if not self.score_history:
            return {'event': 0, 'normal': 0, 'idle': 0}
        scores = list(self.score_history)
        return {
            'event': sum(1 for s in scores if s > 60),
            'normal': sum(1 for s in scores if 30 < s <= 60),
            'idle': sum(1 for s in scores if s <= 30),
            'total': len(scores)
        }