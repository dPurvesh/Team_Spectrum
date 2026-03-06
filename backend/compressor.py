"""
Dual-Layer Compression Engine v2
zstd for real-time event frames, py7zr for idle frame batches.
70%+ overall storage reduction with forensic detail preservation.

v2 Improvements:
- Score-aware quality: Higher scores = less compression = more detail
- Better ROI preservation with feathered edges (no hard blur boundaries)
- Configurable quality profiles per category
- Detection padding scales with box size (larger = closer = more important)
- Adaptive blur kernel based on frame score
- Thread-safe stats tracking
"""

import os
import cv2
import numpy as np
import zstandard as zstd
import py7zr
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading


class DualCompressor:
    # Quality profiles - map score ranges to compression settings
    DEFAULT_CONFIG = {
        # EVENT quality settings (score > 60)
        'event_roi_quality_base': 92,      # Base quality for detection regions
        'event_roi_quality_boost': 5,      # Extra quality per 10 score points above 60
        'event_bg_quality': 35,            # Background quality (was 12%)
        'event_blur_kernel_base': 15,      # Base blur kernel (was 25)
        'event_blur_kernel_min': 7,        # Min blur for high scores
        'event_padding_base': 30,          # Base detection padding
        'event_padding_scale': 0.15,       # Extra padding as % of box size
        'event_feather_radius': 15,        # Feathered edge for smooth transitions
        
        # NORMAL quality settings (score 30-60)
        'normal_quality_min': 45,          # Quality at score=30
        'normal_quality_max': 70,          # Quality at score=60
        
        # IDLE quality settings (score < 30)
        'idle_quality_min': 12,            # Quality at score=0
        'idle_quality_max': 25,            # Quality at score=30
        
        # Batch settings
        'batch_size': 100,
        'zstd_level': 3,
        
        # Feature flags
        'feathered_edges': True,           # Smooth ROI/background transition
        'size_aware_padding': True,        # Larger detections get more padding
        'score_aware_quality': True,       # Quality scales with score
    }

    def __init__(self, storage_dir="storage", config=None, camera_id=None):
        """
        Initialize compressor with optional configuration.
        
        Args:
            storage_dir: Base directory for compressed storage
            config: Custom config dict (merged with defaults)
            camera_id: Optional identifier for logging
        """
        self.storage_dir = storage_dir
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.camera_id = camera_id
        
        self.zstd_compressor = zstd.ZstdCompressor(level=self.config['zstd_level'])
        self.batch_size = self.config['batch_size']
        self.idle_batch = []
        self.batch_count = 0
        
        # Thread-safe stats
        self._stats_lock = threading.Lock()
        self.stats = {
            'original_bytes': 0,
            'compressed_bytes': 0,
            'event_frames': 0,
            'idle_frames': 0,
            'normal_frames': 0,
            'batches_archived': 0,
            'avg_event_ratio': 0,
            'avg_normal_ratio': 0,
            'avg_idle_ratio': 0,
        }
        
        # Ensure storage directories exist
        for subdir in ['events', 'idle', 'normal']:
            os.makedirs(os.path.join(storage_dir, subdir), exist_ok=True)

    def compress_frame(self, frame, detections, frame_number, score, category):
        """
        Universal compression entry point - routes to appropriate method.
        
        Args:
            frame: BGR numpy array
            detections: List of detection dicts with 'box', 'is_person', etc.
            frame_number: Frame index for naming
            score: Frame score (0-100) from scorer
            category: 'EVENT', 'NORMAL', or 'IDLE'
            
        Returns:
            Compression result dict
        """
        if category == "EVENT":
            return self.compress_event(frame, detections, frame_number, score)
        elif category == "NORMAL":
            return self.compress_normal(frame, frame_number, score)
        else:
            return self.compress_idle(frame, frame_number, score)

    def compress_event(self, frame, detections, frame_number, score=70):
        """
        EVENT frame: Score-aware ROI compression with feathered edges.
        Higher scores preserve more forensic detail.
        """
        h, w = frame.shape[:2]
        original_size = frame.nbytes
        cfg = self.config

        # Calculate score-aware quality
        score_bonus = max(0, (score - 60) / 10) * cfg['event_roi_quality_boost']
        roi_quality = min(98, int(cfg['event_roi_quality_base'] + score_bonus))
        
        # Blur kernel decreases with higher scores (less blur = more detail)
        blur_reduction = max(0, (score - 60) / 40) * (cfg['event_blur_kernel_base'] - cfg['event_blur_kernel_min'])
        blur_kernel = max(cfg['event_blur_kernel_min'], int(cfg['event_blur_kernel_base'] - blur_reduction))
        # Ensure odd kernel size
        blur_kernel = blur_kernel if blur_kernel % 2 == 1 else blur_kernel + 1

        # Create ROI mask from detections
        mask = np.zeros((h, w), dtype=np.uint8)
        persons = [d for d in detections if d.get('is_person', False)]

        if persons:
            for det in persons:
                box = det.get('box')
                if not box:
                    continue
                x1, y1, x2, y2 = box
                box_w, box_h = x2 - x1, y2 - y1
                
                # Size-aware padding: larger boxes get more padding
                if cfg['size_aware_padding']:
                    size_factor = max(box_w, box_h)
                    pad = int(cfg['event_padding_base'] + size_factor * cfg['event_padding_scale'])
                else:
                    pad = cfg['event_padding_base']
                
                x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
                x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
                mask[int(y1):int(y2), int(x1):int(x2)] = 255

            # Feathered edges for smooth transition (prevents hard blur lines)
            if cfg['feathered_edges'] and cfg['event_feather_radius'] > 0:
                feather = cfg['event_feather_radius']
                mask = cv2.GaussianBlur(mask, (feather * 2 + 1, feather * 2 + 1), 0)

            # Apply background blur
            blurred = cv2.GaussianBlur(frame, (blur_kernel, blur_kernel), 0)
            
            # Blend using mask (smooth feathered transition)
            mask_3ch = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR).astype(np.float32) / 255.0
            composite = (frame.astype(np.float32) * mask_3ch + 
                        blurred.astype(np.float32) * (1 - mask_3ch)).astype(np.uint8)

            _, encoded = cv2.imencode('.jpg', composite,
                                       [cv2.IMWRITE_JPEG_QUALITY, roi_quality])
        else:
            # No persons - use moderate quality
            fallback_quality = 75 if score > 70 else 65
            _, encoded = cv2.imencode('.jpg', frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, fallback_quality])

        # Apply zstd compression
        jpeg_bytes = encoded.tobytes()
        compressed = self.zstd_compressor.compress(jpeg_bytes)

        # Save
        filename = f"event_{frame_number}_{datetime.now().strftime('%H%M%S')}.zst"
        filepath = os.path.join(self.storage_dir, "events", filename)
        with open(filepath, 'wb') as f:
            f.write(compressed)

        # Update stats thread-safely
        with self._stats_lock:
            self.stats['original_bytes'] += original_size
            self.stats['compressed_bytes'] += len(compressed)
            self.stats['event_frames'] += 1
            # Running average of event compression ratio
            ratio = original_size / max(len(compressed), 1)
            n = self.stats['event_frames']
            self.stats['avg_event_ratio'] = ((n - 1) * self.stats['avg_event_ratio'] + ratio) / n

        return {
            'filepath': filepath,
            'original_bytes': original_size,
            'compressed_bytes': len(compressed),
            'ratio': round(original_size / max(len(compressed), 1), 2),
            'quality': roi_quality,
            'blur_kernel': blur_kernel
        }

    def compress_normal(self, frame, frame_number, score=45):
        """
        NORMAL frame: Score-scaled quality (45-70% based on score 30-60).
        """
        original_size = frame.nbytes
        cfg = self.config
        
        # Linear interpolation: score 30 → min quality, score 60 → max quality
        if cfg['score_aware_quality']:
            score_normalized = (score - 30) / 30  # 0 to 1
            score_normalized = max(0, min(1, score_normalized))
            quality = int(cfg['normal_quality_min'] + 
                         score_normalized * (cfg['normal_quality_max'] - cfg['normal_quality_min']))
        else:
            quality = 50  # Legacy behavior
        
        _, encoded = cv2.imencode('.jpg', frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, quality])

        compressed_size = len(encoded)
        
        with self._stats_lock:
            self.stats['original_bytes'] += original_size
            self.stats['compressed_bytes'] += compressed_size
            self.stats['normal_frames'] += 1
            ratio = original_size / max(compressed_size, 1)
            n = self.stats['normal_frames']
            self.stats['avg_normal_ratio'] = ((n - 1) * self.stats['avg_normal_ratio'] + ratio) / n

        return {
            'encoded': encoded,
            'original_bytes': original_size,
            'compressed_bytes': compressed_size,
            'ratio': round(original_size / max(compressed_size, 1), 2),
            'quality': quality
        }

    def compress_idle(self, frame, frame_number, score=15):
        """
        IDLE frame: Low quality, batched with py7zr for maximum compression.
        Quality scales slightly with score (12-25% for score 0-30).
        """
        original_size = frame.nbytes
        cfg = self.config
        
        # Score-aware quality even for idle frames
        if cfg['score_aware_quality']:
            score_normalized = score / 30  # 0 to 1
            score_normalized = max(0, min(1, score_normalized))
            quality = int(cfg['idle_quality_min'] + 
                         score_normalized * (cfg['idle_quality_max'] - cfg['idle_quality_min']))
        else:
            quality = 15  # Legacy behavior
        
        _, encoded = cv2.imencode('.jpg', frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, quality])

        self.idle_batch.append((frame_number, encoded.tobytes()))
        
        with self._stats_lock:
            self.stats['original_bytes'] += original_size
            self.stats['compressed_bytes'] += len(encoded)
            self.stats['idle_frames'] += 1

        # When batch is full, archive with py7zr
        if len(self.idle_batch) >= self.batch_size:
            return self._archive_idle_batch()
        
        return {
            'batched': True,
            'batch_size': len(self.idle_batch),
            'quality': quality
        }

    def _archive_idle_batch(self):
        """Compress batch of idle frames using py7zr (maximum compression)."""
        if not self.idle_batch:
            return None

        self.batch_count += 1
        archive_name = f"idle_batch_{self.batch_count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.7z"
        archive_path = os.path.join(self.storage_dir, "idle", archive_name)

        original_total = sum(len(data) for _, data in self.idle_batch)
        batch_len = len(self.idle_batch)

        try:
            with py7zr.SevenZipFile(archive_path, 'w') as archive:
                for fnum, data in self.idle_batch:
                    archive.writestr(data, f"frame_{fnum}.jpg")

            compressed_size = os.path.getsize(archive_path)
        except Exception as e:
            # Fallback: save individual files if 7z fails
            compressed_size = original_total
            for fnum, data in self.idle_batch:
                fallback_path = os.path.join(self.storage_dir, "idle", f"frame_{fnum}.jpg")
                with open(fallback_path, 'wb') as f:
                    f.write(data)
        
        with self._stats_lock:
            self.stats['batches_archived'] += 1
            ratio = original_total / max(compressed_size, 1)
            n = self.stats['batches_archived']
            self.stats['avg_idle_ratio'] = ((n - 1) * self.stats['avg_idle_ratio'] + ratio) / n

        self.idle_batch = []

        return {
            'archive_path': archive_path,
            'frames': batch_len,
            'original_kb': round(original_total / 1024, 1),
            'compressed_kb': round(compressed_size / 1024, 1),
            'ratio': round(original_total / max(compressed_size, 1), 1)
        }

    def flush_idle_batch(self):
        """Force archive any remaining idle frames (call on shutdown)."""
        if self.idle_batch:
            return self._archive_idle_batch()
        return None

    def get_savings_percent(self):
        """Overall compression savings percentage."""
        with self._stats_lock:
            if self.stats['original_bytes'] == 0:
                return 0
            return round(
                (1 - self.stats['compressed_bytes'] / self.stats['original_bytes']) * 100, 1
            )

    def get_savings_rupees(self, cost_per_month=40000):
        """Calculate ₹ saved based on compression ratio."""
        savings_pct = self.get_savings_percent()
        return round(cost_per_month * savings_pct / 100)

    def get_detailed_stats(self):
        """Get comprehensive compression statistics."""
        with self._stats_lock:
            stats = self.stats.copy()
        
        stats['savings_percent'] = self.get_savings_percent()
        stats['pending_idle_frames'] = len(self.idle_batch)
        
        return stats
    
    def update_config(self, **kwargs):
        """Update configuration at runtime."""
        for key, value in kwargs.items():
            if key in self.config:
                self.config[key] = value
    
    def reset_stats(self):
        """Reset all statistics (e.g., for new session)."""
        with self._stats_lock:
            self.stats = {
                'original_bytes': 0,
                'compressed_bytes': 0,
                'event_frames': 0,
                'idle_frames': 0,
                'normal_frames': 0,
                'batches_archived': 0,
                'avg_event_ratio': 0,
                'avg_normal_ratio': 0,
                'avg_idle_ratio': 0,
            }