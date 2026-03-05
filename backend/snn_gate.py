"""
SNN Spike Gate — Neuromorphic Pre-Filter
Works like a human brain — ignores boring frames, fires only when something changes.
Saves 80% compute by skipping idle frames before YOLO ever runs.
"""

import cv2
import torch
import numpy as np
from collections import deque
from spikingjelly.activation_based import neuron, surrogate


class SNNSpikeGate:
    def __init__(self, threshold=0.15, decay=0.95, history_size=100):
        self.threshold = threshold
        self.decay = decay
        self.prev_frame = None
        self.membrane_potential = 0.0
        self.spike_history = deque(maxlen=history_size)
        self.frame_count = 0
        self.spike_count = 0
        self.diff_history = deque(maxlen=1000)

        # SpikingJelly LIF neuron
        self.lif_neuron = neuron.LIFNode(
            tau=2.0,
            surrogate_function=surrogate.ATan(),
            v_threshold=1.0,
            v_reset=0.0
        )

    def process_frame(self, frame):
        """
        Returns: (should_spike, diff_score, membrane_potential)
        spike=True  → Frame is interesting → send to YOLO
        spike=False → Frame is boring → skip entirely
        """
        self.frame_count += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

        if self.prev_frame is None:
            self.prev_frame = gray
            return True, 0.0, 0.0  # Always process first frame

        # Pixel-level absolute difference (temporal contrast)
        diff = np.abs(gray - self.prev_frame)
        mean_diff = np.mean(diff)
        significant_pixels = np.mean(diff > 0.05)

        # Combined metric
        diff_score = (mean_diff * 0.6) + (significant_pixels * 0.4)
        self.diff_history.append(diff_score)
        self.prev_frame = gray

        # Feed diff_score through SpikingJelly LIF neuron
        inp = torch.tensor([diff_score], dtype=torch.float32)
        spike_out = self.lif_neuron(inp)
        lif_fired = spike_out.item() > 0.5

        # Also track manual membrane as a secondary signal
        self.membrane_potential = (self.membrane_potential * self.decay) + diff_score

        # Spike if EITHER LIF neuron fires OR manual membrane exceeds threshold
        should_spike = lif_fired or self.membrane_potential >= self.threshold

        if should_spike:
            self.membrane_potential = 0.0  # Reset after spike
            self.spike_count += 1

        self.spike_history.append(1 if should_spike else 0)
        return should_spike, diff_score, self.membrane_potential

    def get_spike_rate(self):
        if not self.spike_history:
            return 0.0
        return (sum(self.spike_history) / len(self.spike_history)) * 100

    def get_compute_savings(self):
        if self.frame_count == 0:
            return 0.0
        return (1 - self.spike_count / self.frame_count) * 100

    def auto_recalibrate(self, target_spike_rate=20):
        """Auto-adjust threshold — call every 5000 frames"""
        current_rate = self.get_spike_rate()

        # Blend scene-adaptive base with rate-based correction
        if self.diff_history:
            avg_diff = np.mean(list(self.diff_history))
            scene_base = max(0.05, min(0.5, avg_diff * 1.5))
        else:
            scene_base = self.threshold

        # Rate correction factor
        if current_rate > target_spike_rate + 10:
            correction = 1.1  # Too many spikes → raise threshold
        elif current_rate < target_spike_rate - 10:
            correction = 0.9  # Too few spikes → lower threshold
        else:
            correction = 1.0

        # Blend: 70% scene-adaptive, 30% current with rate correction
        self.threshold = max(0.05, min(0.5, scene_base * 0.7 + self.threshold * correction * 0.3))

    def reset(self):
        self.prev_frame = None
        self.membrane_potential = 0.0
        self.lif_neuron.reset()
