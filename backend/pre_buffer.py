"""
Pre-Event Recorder — 30 Second Rewind
Like a dashcam — saves BEFORE the crash, not after.
"""

import os
import cv2
from collections import deque
from datetime import datetime


class PreEventBuffer:
    def __init__(self, buffer_seconds=30, fps=15,
                 output_dir="storage/prebuffer"):
        self.buffer_size = buffer_seconds * fps
        self.buffer = deque(maxlen=self.buffer_size)
        self.fps = fps
        self.buffer_seconds = buffer_seconds
        self.output_dir = output_dir
        self.saved_count = 0
        os.makedirs(output_dir, exist_ok=True)

    def add_frame(self, frame, frame_number):
        """Add frame to circular buffer (always running)"""
        self.buffer.append({
            'frame': frame.copy(),
            'frame_number': frame_number,
            'timestamp': datetime.now().isoformat()
        })

    def save_pre_event(self, event_type="UNKNOWN", frame_shape=None):
        """
        Save the entire buffer as a video clip.
        Called when an event is detected — saves the 30s BEFORE the event.
        """
        if not self.buffer:
            return None

        self.saved_count += 1
        filename = f"prebuffer_{self.saved_count}_{event_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi"
        filepath = os.path.join(self.output_dir, filename)

        # Get frame dimensions from first frame
        sample = self.buffer[0]['frame']
        h, w = sample.shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        writer = cv2.VideoWriter(filepath, fourcc, self.fps, (w, h))

        for entry in self.buffer:
            writer.write(entry['frame'])

        writer.release()

        return {
            'filepath': filepath,
            'frames_saved': len(self.buffer),
            'duration_seconds': len(self.buffer) / self.fps,
            'event_type': event_type,
            'start_frame': self.buffer[0]['frame_number'],
            'end_frame': self.buffer[-1]['frame_number'],
            'timestamp': datetime.now().isoformat()
        }

    def get_buffer_status(self):
        return {
            'capacity': self.buffer_size,
            'filled': len(self.buffer),
            'percent': round(len(self.buffer) / self.buffer_size * 100, 1)
        }