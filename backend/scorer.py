"""
Frame Intelligence Scoring Engine — Score 0 to 100
Drives ALL downstream decisions: compression, storage, alerts.

Improvements v2:
- Configurable per-camera zones
- Non-linear person count scaling (diminishing returns)
- Temporal smoothing (rolling window)
- Proximity/size bonus (larger detections = closer = more important)
- Speed detection bonus (fast movement = suspicious)
- Configurable thresholds
"""

from collections import deque
from datetime import datetime
import math


class FrameScorer:
    # Default zone definitions (percentage of frame width)
    # Can be overridden per camera via constructor
    DEFAULT_ZONES = {
        'entry': {'x_range': (0.0, 0.2), 'weight': 1.2},
        'restricted': {'x_range': (0.3, 0.7), 'weight': 1.5},
        'exit': {'x_range': (0.8, 1.0), 'weight': 1.2},
        'normal': {'x_range': (0.2, 0.8), 'weight': 1.0}
    }

    # Configurable scoring weights (can tune per deployment)
    DEFAULT_CONFIG = {
        # Person count scoring
        'first_person_score': 12,
        'additional_person_base': 6,
        'person_count_max': 35,
        'person_count_decay': 0.85,  # Diminishing returns for crowds
        
        # Zone scoring
        'zone_base_score': 10,
        'zone_max': 20,
        
        # Confidence scoring
        'confidence_weight': 15,
        
        # Motion scoring
        'motion_multiplier': 50,
        'motion_max': 10,
        
        # Time-based scoring
        'night_bonus': 5,
        'night_start_hour': 22,
        'night_end_hour': 6,
        
        # Anomaly scoring
        'anomaly_score': 28,
        
        # Proximity/size scoring (NEW)
        'size_bonus_enabled': True,
        'size_weight': 8,  # Max bonus for large detections
        'size_threshold': 0.15,  # % of frame area to get full bonus
        
        # Speed scoring (NEW)
        'speed_bonus_enabled': True,
        'speed_weight': 5,  # Max bonus for fast movement
        'speed_threshold': 50,  # pixels/frame for max bonus
        
        # Temporal smoothing (NEW)
        'smoothing_enabled': True,
        'smoothing_window': 5,  # Rolling average over N frames
        'smoothing_weight': 0.3,  # How much history affects score
        
        # Category thresholds
        'event_threshold': 60,
        'normal_threshold': 30,
    }

    def __init__(self, zones=None, config=None, camera_id=None):
        """
        Initialize scorer with optional per-camera configuration.
        
        Args:
            zones: Custom zone definitions (dict) or None for defaults
            config: Custom scoring config (dict) or None for defaults
            camera_id: Optional camera identifier for logging
        """
        self.zones = zones or self.DEFAULT_ZONES.copy()
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.camera_id = camera_id
        
        # Score history for distribution tracking
        self.score_history = deque(maxlen=1000)
        
        # Recent scores for temporal smoothing
        self.recent_scores = deque(maxlen=self.config['smoothing_window'])
        
        # Position tracking for speed detection
        self.prev_positions = {}  # detection_id → (x, y, timestamp)
        self.position_frame = 0

    def calculate_score(self, detections, diff_score, frame_shape,
                        anomaly_flag=False, time_of_day=None):
        """
        Score a frame from 0-100 based on multiple factors.
        
        Args:
            detections: List of detection dicts with 'is_person', 'center', 'confidence', 'area'
            diff_score: Motion intensity from SNN (0-1 range)
            frame_shape: (height, width, channels) tuple
            anomaly_flag: True if anomaly detected (loitering, scene change)
            time_of_day: 'day' or 'night', or None for auto-detect
            
        Returns:
            (score, category) tuple where score is 0-100 float and category is 'EVENT'/'NORMAL'/'IDLE'
        """
        h, w = frame_shape[:2]
        frame_area = h * w
        cfg = self.config
        score = 0.0
        
        self.position_frame += 1

        # Auto-detect time of day
        if time_of_day is None:
            hour = datetime.now().hour
            time_of_day = 'night' if (hour >= cfg['night_start_hour'] or hour < cfg['night_end_hour']) else 'day'

        persons = [d for d in detections if d.get('is_person', False)]
        
        # --- Factor 1: Person count with diminishing returns (max +35) ---
        person_score = self._calc_person_count_score(len(persons), cfg)
        score += person_score

        # --- Factor 2: Zone weighting (max +20) ---
        zone_score = self._calc_zone_score(persons, w, cfg)
        score += zone_score

        # --- Factor 3: Detection confidence (max +15) ---
        if detections:
            max_conf = max(d.get('confidence', 0) for d in detections)
            score += max_conf * cfg['confidence_weight']

        # --- Factor 4: Motion intensity from SNN (max +10) ---
        motion_score = min(diff_score * cfg['motion_multiplier'], cfg['motion_max'])
        score += motion_score

        # --- Factor 5: Night bonus (max +5) ---
        if time_of_day == 'night':
            score += cfg['night_bonus']

        # --- Factor 6: Anomaly flag (max +28) ---
        if anomaly_flag:
            score += cfg['anomaly_score']

        # --- Factor 7: Proximity/Size bonus (NEW, max +8) ---
        if cfg['size_bonus_enabled'] and persons:
            size_score = self._calc_size_score(persons, frame_area, cfg)
            score += size_score

        # --- Factor 8: Speed bonus (NEW, max +5) ---
        if cfg['speed_bonus_enabled'] and persons:
            speed_score = self._calc_speed_score(persons, cfg)
            score += speed_score

        # Clamp raw score
        raw_score = min(max(round(score, 1), 0), 100)

        # --- Temporal smoothing (NEW) ---
        if cfg['smoothing_enabled'] and len(self.recent_scores) > 0:
            avg_recent = sum(self.recent_scores) / len(self.recent_scores)
            final_score = (1 - cfg['smoothing_weight']) * raw_score + cfg['smoothing_weight'] * avg_recent
        else:
            final_score = raw_score
        
        # Update history
        self.recent_scores.append(raw_score)
        self.score_history.append(final_score)

        # Categorize
        if final_score > cfg['event_threshold']:
            category = "EVENT"
        elif final_score > cfg['normal_threshold']:
            category = "NORMAL"
        else:
            category = "IDLE"

        return round(final_score, 1), category

    def _calc_person_count_score(self, count, cfg):
        """Non-linear person count scoring with diminishing returns."""
        if count == 0:
            return 0
        if count == 1:
            return cfg['first_person_score']
        
        # Exponential decay for additional persons
        # 1st person: 12, 2nd: +6, 3rd: +5.1, 4th: +4.3, etc.
        score = cfg['first_person_score']
        additional = cfg['additional_person_base']
        for i in range(1, count):
            score += additional * (cfg['person_count_decay'] ** (i - 1))
        
        return min(score, cfg['person_count_max'])

    def _calc_zone_score(self, persons, frame_width, cfg):
        """Calculate zone-based scoring."""
        zone_score = 0
        for det in persons:
            center = det.get('center')
            if not center:
                continue
            cx = center[0] / frame_width  # Normalize to 0-1
            for zone_name, zone_def in self.zones.items():
                xr = zone_def['x_range']
                if xr[0] <= cx <= xr[1]:
                    zone_score = max(zone_score, cfg['zone_base_score'] * zone_def['weight'])
                    break
        return min(zone_score, cfg['zone_max'])

    def _calc_size_score(self, persons, frame_area, cfg):
        """
        Larger detections (closer to camera) score higher.
        Returns bonus points based on largest person detection.
        """
        if not persons:
            return 0
        
        max_area_ratio = 0
        for det in persons:
            area = det.get('area', 0)
            if area > 0:
                ratio = area / frame_area
                max_area_ratio = max(max_area_ratio, ratio)
        
        # Normalize: threshold=15% of frame for full bonus
        normalized = min(max_area_ratio / cfg['size_threshold'], 1.0)
        return normalized * cfg['size_weight']

    def _calc_speed_score(self, persons, cfg):
        """
        Fast-moving objects score higher (suspicious activity).
        Tracks position changes between frames.
        """
        max_speed = 0
        current_positions = {}
        
        for i, det in enumerate(persons):
            center = det.get('center')
            if not center:
                continue
            
            det_id = i  # Simple index-based ID (could use centroid matching)
            cx, cy = center
            
            # Check if we have previous position
            if det_id in self.prev_positions:
                px, py, pframe = self.prev_positions[det_id]
                frames_elapsed = self.position_frame - pframe
                if frames_elapsed > 0 and frames_elapsed < 30:  # Within ~2 seconds
                    distance = math.sqrt((cx - px)**2 + (cy - py)**2)
                    speed = distance / frames_elapsed  # pixels per frame
                    max_speed = max(max_speed, speed)
            
            current_positions[det_id] = (cx, cy, self.position_frame)
        
        # Update tracking
        self.prev_positions = current_positions
        
        # Normalize speed to bonus
        normalized = min(max_speed / cfg['speed_threshold'], 1.0)
        return normalized * cfg['speed_weight']

    def get_avg_score(self):
        """Get average score from history."""
        if not self.score_history:
            return 0
        return round(sum(self.score_history) / len(self.score_history), 1)

    def get_score_distribution(self):
        """Get distribution of scores by category."""
        if not self.score_history:
            return {'event': 0, 'normal': 0, 'idle': 0, 'total': 0}
        scores = list(self.score_history)
        cfg = self.config
        return {
            'event': sum(1 for s in scores if s > cfg['event_threshold']),
            'normal': sum(1 for s in scores if cfg['normal_threshold'] < s <= cfg['event_threshold']),
            'idle': sum(1 for s in scores if s <= cfg['normal_threshold']),
            'total': len(scores)
        }
    
    def update_config(self, **kwargs):
        """Update configuration at runtime."""
        for key, value in kwargs.items():
            if key in self.config:
                self.config[key] = value
    
    def update_zones(self, zones):
        """Update zone definitions at runtime."""
        self.zones = zones
    
    def reset_smoothing(self):
        """Reset temporal smoothing (e.g., on scene change)."""
        self.recent_scores.clear()
        self.prev_positions.clear()