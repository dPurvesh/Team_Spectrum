"""
EdgeVid LowBand — Multi-Camera Pipeline + FastAPI Server
Connects every component. Runs the full pipeline.
"Every second your camera records, ours decides."
"""

import os
import cv2
import time
import json
import asyncio
import base64
import threading
import numpy as np
from datetime import datetime
from contextlib import asynccontextmanager

# Suppress OpenCV warnings (DirectShow detection errors, OpenH264 warnings)
os.environ["OPENCV_VIDEOIO_DEBUG"] = "0"
os.environ["OPENCV_VIDEOIO_PRIORITY_MSMF"] = "0"  # Prefer DirectShow over MSMF
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"  # Suppress OpenCV logs

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from snn_gate import SNNSpikeGate
from detector import PersonDetector
from scorer import FrameScorer
from anomaly_detector import AnomalyDetector
from compressor import DualCompressor
from pre_buffer import PreEventBuffer
from database import ForensicDatabase

# ============================================================
# SHARED COMPONENTS (thread-safe or use locks)
# ============================================================
detector = PersonDetector(confidence=0.3)
detector_lock = threading.Lock()
database = ForensicDatabase()
compressor = DualCompressor(storage_dir="storage")

# Ensure directories
os.makedirs("storage/events", exist_ok=True)
os.makedirs("storage/idle", exist_ok=True)
os.makedirs("storage/clips", exist_ok=True)
os.makedirs("storage/prebuffer", exist_ok=True)

# WebSocket clients
ws_clients = set()


# ============================================================
# THREADED CAMERA READER — Prevents cap.read() from blocking
# ============================================================
class CameraReader:
    """Read camera frames in a dedicated thread so cap.read() never blocks the pipeline.
    Supports both local cameras (int index) and network streams (URL string)."""
    
    def __init__(self, source=0):
        self.source = source
        self.is_network = isinstance(source, str)
        
        if self.is_network:
            # Network camera (DroidCam, IP Webcam, RTSP, etc.)
            print(f"📡 Connecting to network camera: {source}")
            self.cap = cv2.VideoCapture(source)
            # Set buffer size small to reduce latency
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # Timeout for network streams
            self.cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
            self.cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
        else:
            # Local camera
            self.cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                print(f"⚠️ DSHOW failed for source {source}, trying default backend...")
                self.cap = cv2.VideoCapture(source)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self._frame = None
        self._ret = False
        self._lock = threading.Lock()
        self._running = True
        self._connected = self.cap.isOpened()
        self._last_frame_time = time.time()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):
        reconnect_delay = 1.0
        while self._running:
            if not self.cap.isOpened() and self.is_network:
                # Try to reconnect for network cameras
                print(f"🔄 Reconnecting to {self.source}...")
                time.sleep(reconnect_delay)
                self.cap = cv2.VideoCapture(self.source)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                reconnect_delay = min(reconnect_delay * 2, 10.0)  # Exponential backoff
                continue
            
            ret, frame = self.cap.read()
            with self._lock:
                self._ret = ret
                if ret:
                    self._frame = frame
                    self._last_frame_time = time.time()
                    self._connected = True
                    reconnect_delay = 1.0  # Reset backoff on success
            
            if not ret:
                time.sleep(0.01)

    def read(self):
        with self._lock:
            if self._frame is not None:
                return self._ret, self._frame.copy()
            return False, None

    def isOpened(self):
        return self.cap.isOpened() and self._connected

    def is_stale(self, timeout=5.0):
        """Check if no frames received for timeout seconds (network camera health)."""
        with self._lock:
            return (time.time() - self._last_frame_time) > timeout

    def release(self):
        self._running = False
        time.sleep(0.1)
        self.cap.release()


# ============================================================
# CAMERA DETECTION — Probe for available cameras
# ============================================================
# Cache for camera detection results
_camera_cache = {'cameras': [], 'timestamp': 0}
_CACHE_TTL = 30  # seconds

def _is_bad_camera(cap):
    """Returns True if camera should be excluded:
    - IR/Windows Hello cameras (near-grayscale output)
    - Broken/virtual cameras outputting random noise (high inter-frame diff)
    """
    try:
        # Flush the buffer (reduced from 5 to 2 for speed)
        for _ in range(2):
            cap.read()
        ret1, frame1 = cap.read()
        if not ret1 or frame1 is None:
            return True

        # --- IR check: all channels nearly identical (grayscale output) ---
        # Real IR cameras have diff < 2-3. Normal cameras in low light can have
        # diff around 3-8, so we use threshold of 3 to avoid false positives.
        b, g, r = cv2.split(frame1)
        diff_rg = float(np.mean(np.abs(r.astype(np.int16) - g.astype(np.int16))))
        diff_rb = float(np.mean(np.abs(r.astype(np.int16) - b.astype(np.int16))))
        
        # Also check standard deviation - real color images have variance
        std_check = float(np.std(frame1))
        
        # Only filter if BOTH color diffs are tiny AND std is low (true IR)
        if diff_rg < 3 and diff_rb < 3 and std_check < 15:
            print(f"  → IR/grayscale camera detected (diff_rg={diff_rg:.1f}, diff_rb={diff_rb:.1f}, std={std_check:.1f})")
            return True

        # --- Noise check: compare two consecutive frames 50ms apart (reduced from 80ms) ---
        # Threshold of 35 allows for auto-exposure adjustments and minor scene changes
        # True noise/broken cameras typically have diff > 50
        import time as _time
        _time.sleep(0.05)
        ret2, frame2 = cap.read()
        if not ret2 or frame2 is None:
            return True
        inter_frame_diff = float(np.mean(np.abs(frame1.astype(np.int16) - frame2.astype(np.int16))))
        if inter_frame_diff > 40:
            print(f"  → Noisy/broken camera detected (inter-frame diff={inter_frame_diff:.1f})")
            return True

        return False
    except Exception:
        return False


def _probe_single_camera(index):
    """Probe a single camera index with timeout. Returns camera info or None."""
    try:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return None
        
        # Set timeout properties for faster failure
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 2000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000)
        
        ret, _ = cap.read()
        if not ret:
            cap.release()
            return None
            
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Skip IR or noisy/broken cameras
        if _is_bad_camera(cap):
            print(f"⚠️ Camera index {index} is IR/noisy/broken — skipped")
            cap.release()
            return None
            
        cap.release()
        return {
            'index': index,
            'cam_id': f'cam_{index}',
            'name': 'Built-in Camera' if index == 0 else f'External Camera {index}',
            'resolution': f'{w}x{h}',
        }
    except Exception as e:
        print(f"⚠️ Camera index {index} probe failed: {e}")
        return None


def detect_available_cameras(max_check=5, use_cache=True):
    """Probe camera indices 0-4 to find all unique connected cameras.
    Filters out Windows virtual duplicates AND IR/Windows Hello cameras.
    Uses caching to avoid repeated slow probes."""
    global _camera_cache
    
    # Return cached result if still valid
    if use_cache and _camera_cache['cameras'] and (time.time() - _camera_cache['timestamp'] < _CACHE_TTL):
        print(f"📷 Returning cached camera list ({len(_camera_cache['cameras'])} cameras)")
        return _camera_cache['cameras']
    
    # Suppress OpenCV/DirectShow warnings during detection
    import warnings
    warnings.filterwarnings("ignore")
    
    print(f"📷 Probing camera indices 0-{max_check-1}...")
    
    # Step 1: Parallel probe all indices (faster than sequential)
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    candidates = []
    
    with ThreadPoolExecutor(max_workers=max_check) as executor:
        futures = {executor.submit(_probe_single_camera, i): i for i in range(max_check)}
        for future in futures:
            try:
                result = future.result(timeout=5)  # 5 second timeout per camera
                if result:
                    candidates.append(result)
            except FuturesTimeout:
                print(f"⚠️ Camera index {futures[future]} timed out — skipped")
            except Exception as e:
                print(f"⚠️ Camera probe error: {e}")

    if len(candidates) <= 1:
        # Cache and return
        _camera_cache = {'cameras': candidates, 'timestamp': time.time()}
        print(f"📷 Found {len(candidates)} camera(s)")
        return candidates

    # Step 2: Open all candidates simultaneously to detect duplicates.
    # A Windows virtual duplicate will fail when the real camera is already open.
    caps = {}
    unique = []
    for cam in candidates:
        idx = cam['index']
        try:
            c = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if c.isOpened():
                ret, _ = c.read()
                if ret:
                    caps[idx] = c
                    unique.append(cam)
                else:
                    c.release()
                    print(f"⚠️ Camera index {idx} can't read when others are open — duplicate, skipped")
            else:
                print(f"⚠️ Camera index {idx} can't open simultaneously — duplicate, skipped")
        except Exception:
            pass

    # Release all
    for c in caps.values():
        c.release()

    # Cache the result
    _camera_cache = {'cameras': unique, 'timestamp': time.time()}
    print(f"📷 Found {len(unique)} unique camera(s)")
    return unique


# ============================================================
# PER-CAMERA INSTANCE — Each camera has its own AI pipeline
# ============================================================
class CameraInstance:
    """Encapsulates all pipeline state and AI components for one camera."""

    def __init__(self, cam_id, source):
        self.cam_id = cam_id
        self.source = source
        # Per-camera AI components (SNN/scoring/anomaly are stateful per-camera)
        self.spike_gate = SNNSpikeGate(threshold=0.15)
        self.scorer = FrameScorer()
        self.anomaly_detector = AnomalyDetector(loiter_threshold_sec=20, fps=15)
        self.prebuffer = PreEventBuffer(buffer_seconds=30, fps=15)

        # Per-camera pipeline state
        self.state = {
            'running': False,
            'frame_count': 0,
            'current_score': 0,
            'current_category': 'IDLE',
            'current_frame': None,
            'current_detections': [],
            'last_detections': [],       # Persist for crosshair overlay
            'last_det_frame': 0,         # Frame# of last YOLO detection
            'fps': 0,
            'last_alert': None,
            'snn_spike': False,
            'snn_membrane': 0.0,
            'snn_diff': 0.0,
            'session_name': None,
            'session_start': None,
        }

        # Per-camera event clip writer
        self.clip_state = {
            'writer': None,
            'path': None,
            'start_frame': 0,
            'cooldown': 0,
            'frame_shape': None,
            'category': 'IDLE',
            'start_time': None,
        }

        self.thread = None
        self.reader = None

    def start(self, session_name=None):
        """Start this camera's pipeline thread."""
        if self.state['running']:
            return False
        self.state['session_name'] = session_name or f"Demo_{datetime.now().strftime('%H%M%S')}"
        self.state['session_start'] = datetime.now().isoformat()
        self.state['running'] = True
        self.state['current_category'] = 'IDLE'
        self.state['current_score'] = 0
        self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        """Stop this camera's pipeline."""
        if not self.state['running']:
            return False
        self.state['running'] = False
        self.state['current_frame'] = None
        self.state['current_score'] = 0
        self.state['current_category'] = 'IDLE'
        self.state['current_detections'] = []
        self.state['last_detections'] = []
        self.state['fps'] = 0
        self.state['snn_spike'] = False
        self.state['snn_membrane'] = 0.0
        self.state['snn_diff'] = 0.0
        self.spike_gate.prev_frame = None
        self.spike_gate.membrane_potential = 0.0
        self.spike_gate.lif_neuron.reset()
        return True

    def clear(self):
        """Clear per-camera AI state for fresh demo."""
        self.spike_gate.frame_count = 0
        self.spike_gate.spike_count = 0
        self.spike_gate.spike_history.clear()
        self.spike_gate.diff_history.clear()
        self.spike_gate.prev_frame = None
        self.spike_gate.membrane_potential = 0.0
        self.spike_gate.lif_neuron.reset()
        self.scorer.score_history.clear()
        self.anomaly_detector.tracked_objects.clear()
        self.anomaly_detector.alerts.clear()
        self.anomaly_detector.frame_counter = 0
        self.state['frame_count'] = 0
        self.state['current_score'] = 0
        self.state['current_category'] = 'IDLE'
        self.state['last_alert'] = None
        self.state['snn_spike'] = False
        self.state['snn_membrane'] = 0.0
        self.state['last_detections'] = []
        self.state['last_det_frame'] = 0

    def _sanitize_session_name(self, name):
        """Sanitize session name for safe filesystem paths."""
        if not name:
            return None
        # Remove/replace unsafe characters
        import re
        safe = re.sub(r'[\\/:*?"<>|]', '_', name)
        safe = re.sub(r'\s+', '_', safe)
        return safe[:50]  # Limit length

    def _start_event_clip(self, frame, frame_number, category='EVENT'):
        h, w = frame.shape[:2]
        self.clip_state['frame_shape'] = (w, h)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Get sanitized session name
        session = self._sanitize_session_name(self.state.get('session_name'))
        
        # Build filename with session prefix if available
        if session:
            filename = f"{session}_clip_{self.cam_id}_{category}_{frame_number}_{ts}.mp4"
            # Create session folder
            clip_dir = os.path.join("storage", "clips", session)
        else:
            filename = f"clip_{self.cam_id}_{category}_{frame_number}_{ts}.mp4"
            clip_dir = os.path.join("storage", "clips")
        
        os.makedirs(clip_dir, exist_ok=True)
        filepath = os.path.join(clip_dir, filename)
        
        # Use mp4v codec (MPEG-4) - reliable on all platforms, browser playable
        # Avoid avc1/H264 which requires OpenH264 library that may not be installed
        write_fps = 15.0 if category == 'EVENT' else 12.0 if category == 'NORMAL' else 8.0
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(filepath, fourcc, write_fps, (w, h))
        
        if not writer.isOpened():
            # Final fallback to MJPG if mp4v fails
            writer.release()
            filename = filename.replace('.mp4', '.avi')
            filepath = os.path.join("storage", "clips", filename)
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            writer = cv2.VideoWriter(filepath, fourcc, write_fps, (w, h))
        
        self.clip_state['writer'] = writer
        self.clip_state['path'] = filepath
        self.clip_state['start_frame'] = frame_number
        self.clip_state['cooldown'] = 0
        self.clip_state['category'] = category
        self.clip_state['start_time'] = datetime.now().isoformat()
        return filepath

    def _stop_event_clip(self, frame_number):
        if self.clip_state['writer'] is not None:
            self.clip_state['writer'].release()
            duration = (frame_number - self.clip_state['start_frame']) / 15.0
            path = self.clip_state['path']
            cat = self.clip_state.get('category', 'EVENT')
            start_ts = self.clip_state.get('start_time', datetime.now().isoformat())
            meta = {
                'filename': os.path.basename(path),
                'camera': self.cam_id,
                'category': cat,
                'quality': 'HD' if cat == 'EVENT' else 'MEDIUM' if cat == 'NORMAL' else 'LOW',
                'start_time': start_ts,
                'end_time': datetime.now().isoformat(),
                'duration_sec': round(duration, 1),
                'start_frame': self.clip_state['start_frame'],
                'end_frame': frame_number,
                'fps': 15 if cat == 'EVENT' else 12 if cat == 'NORMAL' else 8,
            }
            meta_path = path.replace('.mp4', '.json')
            try:
                with open(meta_path, 'w') as f:
                    json.dump(meta, f)
            except Exception:
                pass
            self.clip_state['writer'] = None
            self.clip_state['path'] = None
            self.clip_state['start_frame'] = 0
            self.clip_state['cooldown'] = 0
            self.clip_state['category'] = 'IDLE'
            self.clip_state['start_time'] = None
            return path, duration
        return None, 0

    def _draw_crosshair_overlay(self, frame, dets):
        """Draw tactical bounding boxes with crosshairs, corner brackets, and labels."""
        display_frame = frame.copy()

        for det in dets:
            x1, y1, x2, y2 = det['box']
            cls = det.get('class_name', '?')
            conf = det.get('confidence', 0)
            is_person = det.get('is_person', False)

            # Colors: bright green for person, amber for other objects
            if is_person:
                color = (0, 255, 100)
                accent = (0, 220, 80)
            else:
                color = (255, 200, 0)
                accent = (220, 180, 0)

            w_box = x2 - x1
            h_box = y2 - y1

            # Main bounding box
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, 2)

            # Inner offset box for double-box tactical look
            offset = 3
            if w_box > 50 and h_box > 50:
                cv2.rectangle(display_frame, (x1 + offset, y1 + offset),
                              (x2 - offset, y2 - offset), accent, 1)

            # Center crosshair
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            cross_size = max(18, min(w_box, h_box) // 5)

            # Crosshair lines
            cv2.line(display_frame, (cx - cross_size, cy), (cx - 4, cy), color, 1)
            cv2.line(display_frame, (cx + 4, cy), (cx + cross_size, cy), color, 1)
            cv2.line(display_frame, (cx, cy - cross_size), (cx, cy - 4), color, 1)
            cv2.line(display_frame, (cx, cy + 4), (cx, cy + cross_size), color, 1)

            # Crosshair center dot
            cv2.circle(display_frame, (cx, cy), 3, color, -1)

            # Crosshair ring
            cv2.circle(display_frame, (cx, cy), cross_size // 2, color, 1)

            # Corner brackets (tactical look)
            blen = max(12, min(30, w_box // 3, h_box // 3))
            thickness = 2
            for (cx1, cy1), (dx, dy) in [
                ((x1, y1), (1, 1)), ((x2, y1), (-1, 1)),
                ((x1, y2), (1, -1)), ((x2, y2), (-1, -1))
            ]:
                cv2.line(display_frame, (cx1, cy1), (cx1 + blen * dx, cy1), color, thickness)
                cv2.line(display_frame, (cx1, cy1), (cx1, cy1 + blen * dy), color, thickness)

            # Label background + text
            label = f"{cls} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(display_frame, (x1, y1 - th - 10), (x1 + tw + 8, y1), color, -1)
            cv2.putText(display_frame, label, (x1 + 4, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

        # Camera ID watermark
        cv2.putText(display_frame, self.cam_id.upper(), (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 212, 255), 2, cv2.LINE_AA)

        return display_frame

    def _run_pipeline(self):
        """Main pipeline loop — runs in a separate thread."""
        print(f"📹 [{self.cam_id}] Opening camera {self.source} with threaded reader...")
        self.reader = CameraReader(self.source)

        if not self.reader.isOpened():
            print(f"❌ [{self.cam_id}] Failed to open camera source: {self.source}")
            self.state['running'] = False
            return

        time.sleep(0.3)
        self.state['running'] = True
        frame_number = 0
        fps_counter = 0
        fps_timer = time.time()
        no_frame_count = 0
        last_yolo_detections = []

        print(f"🚀 [{self.cam_id}] Pipeline Started! (source={self.source})")

        while self.state['running']:
          try:
            ret, frame = self.reader.read()
            if not ret or frame is None:
                no_frame_count += 1
                if no_frame_count > 300:
                    print(f"❌ [{self.cam_id}] Camera feed lost. Stopping.")
                    break
                time.sleep(0.01)
                continue
            no_frame_count = 0

            frame_number += 1
            fps_counter += 1
            start_time = time.time()

            # Resize for speed
            h_orig, w_orig = frame.shape[:2]
            if w_orig > 640:
                scale = 640 / w_orig
                frame = cv2.resize(frame, (640, int(h_orig * scale)))

            # Always encode raw frame first so feed never freezes
            _, raw_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
            self.state['current_frame'] = base64.b64encode(raw_buf).decode('utf-8')
            self.state['frame_count'] = frame_number

            # Pre-buffer (every 3rd frame)
            if frame_number % 3 == 0:
                self.prebuffer.add_frame(frame, frame_number)

            # ---- Step 1: SNN Spike Gate ----
            spike, diff_score, membrane = self.spike_gate.process_frame(frame)
            self.state['snn_spike'] = bool(spike)
            self.state['snn_membrane'] = float(round(float(membrane), 4))
            self.state['snn_diff'] = float(round(float(diff_score), 4))

            if not spike:
                # Periodic YOLO every 8 frames — always detect persons
                if frame_number % 8 == 0:
                    with detector_lock:
                        periodic_dets = detector.detect(frame)
                    person_dets = [d for d in periodic_dets if d['is_person']]
                    if person_dets:
                        spike = True
                        self.state['snn_spike'] = True
                        last_yolo_detections = periodic_dets
                    else:
                        last_yolo_detections = []

            if not spike:
                # Truly idle
                self.state['current_score'] = 0.0
                self.state['current_category'] = 'IDLE'
                self.state['current_detections'] = []

                if frame_number % 5 == 0:
                    compressor.compress_idle(frame, frame_number)

                if self.clip_state['writer'] is not None:
                    self.clip_state['cooldown'] += 1
                    self.clip_state['writer'].write(frame)
                    if self.clip_state['cooldown'] > 45:
                        path, dur = self._stop_event_clip(frame_number)
                        if path:
                            print(f"🎬 [{self.cam_id}] Clip saved: {path} ({dur:.1f}s)")
            else:
                # SPIKE — full processing
                with detector_lock:
                    detections = last_yolo_detections if last_yolo_detections else detector.detect(frame)
                last_yolo_detections = []

                alerts = self.anomaly_detector.update(frame, detections, frame_number)
                anomaly_flag = self.anomaly_detector.has_active_anomaly()
                score, category = self.scorer.calculate_score(
                    detections, diff_score, frame.shape,
                    anomaly_flag=anomaly_flag
                )

                if category == "EVENT":
                    if frame_number % 3 == 0:
                        result = compressor.compress_event(frame, detections, frame_number, score)
                        frame_path = result['filepath'] if result else None
                    else:
                        frame_path = None
                    if frame_number % 10 == 0:
                        database.log_event(
                            frame_number=frame_number, score=score,
                            category=category, detections=detections,
                            event_type="PERSON_DETECTED", severity="HIGH",
                            frame_path=frame_path, compression_type="zstd+roi",
                            camera_id=self.cam_id,
                            session_name=self.state.get('session_name')
                        )
                    if self.clip_state['writer'] is None:
                        self._start_event_clip(frame, frame_number, category='EVENT')
                    elif self.clip_state['category'] != 'EVENT':
                        self.clip_state['category'] = 'EVENT'
                    self.clip_state['cooldown'] = 0
                    self.clip_state['writer'].write(frame)

                elif category == "NORMAL":
                    if frame_number % 5 == 0:
                        compressor.compress_normal(frame, frame_number)
                    if self.clip_state['writer'] is None and len([d for d in detections if d.get('is_person')]) > 0:
                        self._start_event_clip(frame, frame_number, category='NORMAL')
                    if self.clip_state['writer'] is not None:
                        self.clip_state['cooldown'] += 1
                        self.clip_state['writer'].write(frame)
                        if self.clip_state['cooldown'] > 45:
                            path, dur = self._stop_event_clip(frame_number)
                            if path:
                                print(f"🎬 [{self.cam_id}] Clip saved: {path} ({dur:.1f}s)")
                else:
                    if frame_number % 5 == 0:
                        compressor.compress_idle(frame, frame_number)

                for alert in alerts:
                    prebuf_info = self.prebuffer.save_pre_event(alert['type'])
                    event_id = database.log_event(
                        frame_number=frame_number, score=95.0,
                        category="EVENT", detections=detections,
                        event_type=alert['type'], severity="CRITICAL",
                        anomaly_flag=True, duration=alert.get('duration_sec', 0),
                        prebuffer_path=prebuf_info['filepath'] if prebuf_info else None,
                        compression_type="zstd+roi",
                        camera_id=self.cam_id,
                        session_name=self.state.get('session_name')
                    )
                    database.log_alert(event_id, alert['type'], alert['message'])
                    self.state['last_alert'] = alert

                self.state['current_score'] = float(score)
                self.state['current_category'] = str(category)
                self.state['current_detections'] = detections
                # Persist detections for crosshair overlay
                self.state['last_detections'] = detections
                self.state['last_det_frame'] = frame_number

            # ---- Draw crosshair overlay ----
            # Use current detections, or persist last detections for up to 15 frames (~1s)
            dets = self.state.get('current_detections', [])
            if not dets and (frame_number - self.state.get('last_det_frame', 0)) < 15:
                dets = self.state.get('last_detections', [])

            if dets:
                display_frame = self._draw_crosshair_overlay(frame, dets)
                _, buffer = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 55])
                self.state['current_frame'] = base64.b64encode(buffer).decode('utf-8')
            else:
                # Camera ID watermark even without detections
                display_frame = frame.copy()
                cv2.putText(display_frame, self.cam_id.upper(), (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 212, 255), 2, cv2.LINE_AA)
                _, buffer = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                self.state['current_frame'] = base64.b64encode(buffer).decode('utf-8')

            # FPS calculation
            if time.time() - fps_timer >= 1.0:
                self.state['fps'] = fps_counter
                if fps_counter > 0:
                    print(f"📊 [{self.cam_id}] FPS={fps_counter} | F#{frame_number} | Cat={self.state['current_category']} | Score={self.state['current_score']}")
                fps_counter = 0
                fps_timer = time.time()

            if frame_number % 1296000 == 0:
                self.spike_gate.auto_recalibrate()

            if frame_number % 1000 == 0:
                database.log_system_stats(
                    total_frames=frame_number,
                    processed_frames=self.spike_gate.spike_count,
                    compute_savings=self.spike_gate.get_compute_savings(),
                    storage_savings=compressor.get_savings_percent(),
                    spike_rate=self.spike_gate.get_spike_rate(),
                    avg_score=self.scorer.get_avg_score()
                )

            # Adaptive frame rate control
            cat = self.state['current_category']
            target_fps = 15 if cat == 'EVENT' else 12 if cat == 'NORMAL' else 8
            elapsed = time.time() - start_time
            sleep_time = max(0, (1 / target_fps) - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

          except Exception as e:
            print(f"⚠️ [{self.cam_id}] Pipeline error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(0.1)
            continue

        # Cleanup
        if self.clip_state['writer'] is not None:
            self._stop_event_clip(frame_number)
        self.reader.release()
        self.state['running'] = False
        self.state['current_frame'] = None
        self.state['fps'] = 0
        self.state['snn_spike'] = False
        print(f"⏹️ [{self.cam_id}] Pipeline stopped.")

    def get_ws_data(self):
        """Build WebSocket-safe data dict for this camera."""
        cat = str(self.state.get('current_category', 'IDLE'))
        target_fps = 15 if cat == 'EVENT' else 12 if cat == 'NORMAL' else 8

        safe_dets = []
        for d in self.state.get('current_detections', []):
            try:
                box = d.get('box', (0, 0, 0, 0))
                safe_dets.append({
                    'box': [int(b) for b in box],
                    'class': str(d.get('class_name', '?')),
                    'conf': float(d.get('confidence', 0)),
                    'is_person': bool(d.get('is_person', False))
                })
            except Exception:
                pass

        # Include persisted detections if current is empty
        if not safe_dets:
            for d in self.state.get('last_detections', []):
                try:
                    box = d.get('box', (0, 0, 0, 0))
                    safe_dets.append({
                        'box': [int(b) for b in box],
                        'class': str(d.get('class_name', '?')),
                        'conf': float(d.get('confidence', 0)),
                        'is_person': bool(d.get('is_person', False))
                    })
                except Exception:
                    pass

        return {
            "cam_id": self.cam_id,
            "source": self.source,
            "frame": self.state.get('current_frame'),
            "score": _safe_number(self.state.get('current_score', 0)),
            "category": cat,
            "fps": int(self.state.get('fps', 0)),
            "target_fps": int(target_fps),
            "frame_count": int(self.state.get('frame_count', 0)),
            "spike_count": int(self.spike_gate.spike_count),
            "frames_skipped": int(self.spike_gate.frame_count - self.spike_gate.spike_count),
            "compute_savings": float(round(_safe_number(self.spike_gate.get_compute_savings()), 1)),
            "spike_rate": float(round(_safe_number(self.spike_gate.get_spike_rate()), 1)),
            "active_tracks": int(self.anomaly_detector.get_active_tracks()),
            "alerts": int(len(self.anomaly_detector.alerts)),
            "snn_spike": bool(self.state.get('snn_spike', False)),
            "snn_membrane": _safe_number(self.state.get('snn_membrane', 0)),
            "snn_diff": _safe_number(self.state.get('snn_diff', 0)),
            "snn_threshold": float(self.spike_gate.threshold),
            "last_alert": self.state.get('last_alert'),
            "session_name": self.state.get('session_name'),
            "timestamp": datetime.now().isoformat(),
            "detections": safe_dets
        }


# ============================================================
# ACTIVE CAMERAS REGISTRY
# ============================================================
cameras = {}          # cam_id -> CameraInstance
cameras_lock = threading.Lock()


def _safe_number(v):
    """Convert numpy/other numeric types to native Python for JSON serialization."""
    if v is None:
        return 0
    try:
        if isinstance(v, (int, float, bool)):
            return v
        return float(v)
    except (TypeError, ValueError):
        return 0


# ============================================================
# FASTAPI SERVER
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Stop all cameras on shutdown
    with cameras_lock:
        for cam in cameras.values():
            cam.stop()

app = FastAPI(
    title="EdgeVid LowBand API",
    version="2.0.0",
    description="The Camera That Thinks — Multi-Camera Neuromorphic Edge-AI DVR",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve React build as static files
_BUILD_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "edgevid-dashboard", "build")
_BUILD_DIR = os.path.normpath(_BUILD_DIR)
if os.path.isdir(_BUILD_DIR):
    app.mount("/static", StaticFiles(directory=os.path.join(_BUILD_DIR, "static")), name="static")
    app.mount("/build", StaticFiles(directory=_BUILD_DIR), name="build_root")


# ---- REST Endpoints ----

# Catch-all for /api/v1/* requests (browser extensions, etc.)
@app.get("/api/v1/{path:path}")
def api_v1_catchall(path: str):
    """Handle requests to non-existent API v1 endpoints (browser extensions)."""
    return {"error": "API v1 not available", "version": "2.0.0"}


@app.get("/")
def root():
    active = [cid for cid, c in cameras.items() if c.state['running']]
    return {
        "name": "EdgeVid LowBand",
        "version": "2.0.0",
        "active_cameras": active,
        "tagline": "The Camera That Thinks — Multi-Camera"
    }


@app.get("/api/cameras/detect")
def api_detect_cameras(refresh: bool = False):
    """Probe for all available cameras connected to this device.
    Use ?refresh=true to bypass cache and force a fresh scan."""
    available = detect_available_cameras(use_cache=not refresh)
    for cam in available:
        cam_id = f"cam_{cam['index']}"
        cam['cam_id'] = cam_id
        cam['active'] = cam_id in cameras and cameras[cam_id].state['running']
    return {"cameras": available, "count": len(available), "cached": not refresh}


@app.get("/api/cameras")
def api_list_cameras():
    """List all active camera instances."""
    result = []
    for cam_id, cam in cameras.items():
        result.append({
            "cam_id": cam_id,
            "source": cam.source,
            "running": cam.state['running'],
            "fps": cam.state['fps'],
            "category": cam.state['current_category'],
            "score": cam.state['current_score'],
            "frame_count": cam.state['frame_count'],
        })
    return {"cameras": result, "count": len(result)}


@app.get("/api/stats")
def get_stats():
    total_frames = sum(c.state['frame_count'] for c in cameras.values())
    active_count = sum(1 for c in cameras.values() if c.state['running'])
    first_cam = next((c for c in cameras.values() if c.state['running']), None)
    if first_cam:
        return {
            "frame_count": total_frames,
            "current_score": first_cam.state['current_score'],
            "current_category": first_cam.state['current_category'],
            "fps": first_cam.state['fps'],
            "spike_rate": first_cam.spike_gate.get_spike_rate(),
            "compute_savings": first_cam.spike_gate.get_compute_savings(),
            "storage_savings": compressor.get_savings_percent(),
            "avg_score": first_cam.scorer.get_avg_score(),
            "active_tracks": first_cam.anomaly_detector.get_active_tracks(),
            "total_alerts": len(first_cam.anomaly_detector.alerts),
            "active_cameras": active_count,
            "score_distribution": first_cam.scorer.get_score_distribution(),
            "buffer_status": first_cam.prebuffer.get_buffer_status()
        }
    return {
        "frame_count": 0, "current_score": 0, "current_category": "IDLE",
        "fps": 0, "active_cameras": 0,
    }


@app.get("/api/events")
def get_events(category: str = None, limit: int = 50):
    events = database.get_recent_events(limit=limit, category=category)
    return {"events": events, "count": len(events)}


@app.get("/api/alerts")
def get_alerts(unacknowledged_only: bool = False):
    alerts = database.get_recent_alerts(unacknowledged_only=unacknowledged_only)
    return {"alerts": alerts, "count": len(alerts)}


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int):
    database.acknowledge_alert(alert_id)
    return {"status": "acknowledged", "alert_id": alert_id}


# ---- Camera Control Endpoints ----

@app.get("/api/camera/status")
def camera_status():
    result = {}
    any_running = False
    for cam_id, cam in cameras.items():
        result[cam_id] = {
            "running": cam.state['running'],
            "fps": cam.state['fps'],
            "frame_count": cam.state['frame_count'],
        }
        if cam.state['running']:
            any_running = True
    return {
        "camera_on": any_running,
        "cameras": result,
        "count": len(cameras),
    }


@app.post("/api/camera/start")
def camera_start(source: int = 0, session_name: str = None):
    """Start a camera pipeline. Creates a CameraInstance if not exists."""
    cam_id = f"cam_{source}"
    with cameras_lock:
        if cam_id in cameras and cameras[cam_id].state['running']:
            return {"status": "already_running", "cam_id": cam_id}
        cam = CameraInstance(cam_id, source)
        cameras[cam_id] = cam
    # Reset compression stats so every new session starts at 0
    compressor.reset_stats()
    compressor.idle_batch.clear()
    cam.start(session_name)
    return {"status": "started", "cam_id": cam_id, "source": source,
            "session": cam.state['session_name']}


@app.post("/api/cameras/connect")
def connect_network_camera(
    camera_id: str,
    ip_address: str,
    port: str,
    path: str = "/video",
    session_name: str = None
):
    """
    Connect a network camera (DroidCam, IP Webcam, RTSP, etc.).
    Non-blocking: spawns camera reader in daemon thread.
    
    Args:
        camera_id: Unique identifier (e.g., "CAM-02", "phone_cam")
        ip_address: IP address of the camera (e.g., "192.168.1.5")
        port: Port number (e.g., "4747" for DroidCam)
        path: URL path (default "/video", use "/mjpegfeed" for IP Webcam app)
    """
    # Sanitize camera_id
    cam_id = camera_id.lower().replace(" ", "_").replace("-", "_")
    if not cam_id:
        return JSONResponse(
            {"status": "error", "message": "camera_id is required"},
            status_code=400
        )
    
    # Check if already exists
    with cameras_lock:
        if cam_id in cameras:
            if cameras[cam_id].state['running']:
                return {"status": "already_running", "cam_id": cam_id}
            else:
                # Remove old instance
                try:
                    cameras[cam_id].stop()
                except:
                    pass
                del cameras[cam_id]
    
    # Construct stream URL
    # DroidCam: http://{ip}:{port}/video
    # IP Webcam: http://{ip}:{port}/video or /mjpegfeed
    # RTSP: rtsp://{ip}:{port}/stream
    stream_url = f"http://{ip_address}:{port}{path}"
    
    print(f"📡 Connecting network camera: {cam_id} -> {stream_url}")
    
    # Create camera instance with URL source (non-blocking)
    try:
        with cameras_lock:
            cam = CameraInstance(cam_id, stream_url)
            cameras[cam_id] = cam

        # Reset compression stats so every new session starts at 0
        compressor.reset_stats()
        compressor.idle_batch.clear()
        # Start pipeline in background thread
        session = session_name or f"Network_{datetime.now().strftime('%H%M%S')}"
        cam.start(session)
        
        return {
            "status": "started",
            "cam_id": cam_id,
            "stream_url": stream_url,
            "session": cam.state['session_name'],
            "message": f"Network camera {cam_id} connected"
        }
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )


@app.delete("/api/cameras/{cam_id}")
def disconnect_camera(cam_id: str):
    """Disconnect and remove a camera from the registry."""
    if cam_id in cameras:
        cameras[cam_id].stop()
        with cameras_lock:
            del cameras[cam_id]
        return {"status": "disconnected", "cam_id": cam_id}
    return JSONResponse(
        {"status": "error", "message": f"Camera {cam_id} not found"},
        status_code=404
    )


@app.post("/api/camera/stop")
def camera_stop(cam_id: str = None, source: int = None):
    """Stop a specific camera or the first running camera."""
    if cam_id is None and source is not None:
        cam_id = f"cam_{source}"
    if cam_id is None:
        cam_id = next((cid for cid, c in cameras.items() if c.state['running']), None)
    if cam_id and cam_id in cameras:
        cameras[cam_id].stop()
        return {"status": "stopped", "cam_id": cam_id}
    return {"status": "not_found"}


@app.post("/api/camera/stop_all")
def camera_stop_all():
    """Stop all running cameras."""
    stopped = []
    for cam_id, cam in cameras.items():
        if cam.state['running']:
            cam.stop()
            stopped.append(cam_id)
    return {"status": "stopped_all", "cameras": stopped}


@app.get("/api/clips")
def list_clips(response: Response):
    response.headers["Cache-Control"] = "no-store"
    clips_dir = "storage/clips"
    if not os.path.exists(clips_dir):
        return {"clips": []}

    # Collect all (dirpath, filename) pairs recursively so session subdirs work
    all_mp4 = []
    for dirpath, _dirs, filenames in os.walk(clips_dir):
        for fname in filenames:
            if fname.endswith('.mp4'):
                all_mp4.append((dirpath, fname))
    # Sort newest first by filename (timestamp embedded in name)
    all_mp4.sort(key=lambda x: x[1], reverse=True)

    clips = []
    for dirpath, f in all_mp4:
        fpath = os.path.join(dirpath, f)
        size_kb = os.path.getsize(fpath) / 1024
        meta_path = fpath.replace('.mp4', '.json')
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r') as mf:
                    meta = json.load(mf)
            except Exception:
                pass
        parts = f.replace('.mp4', '').split('_')
        timestamp_str = ''
        cat_from_name = 'EVENT'
        cam_from_name = ''
        try:
            if f.startswith('clip_cam_'):
                # clip_cam_0_EVENT_123_20260305_120000.mp4
                cam_from_name = f"cam_{parts[2]}"
                cat_from_name = parts[3]
                timestamp_str = f"{parts[5]}_{parts[6]}"
            elif '_clip_cam_' in f:
                # Session-prefixed: Demo_004058_clip_cam_0_EVENT_123_20260305_120000.mp4
                idx = parts.index('clip') if 'clip' in parts else -1
                if idx >= 0 and len(parts) > idx + 6:
                    cam_from_name = f"cam_{parts[idx+2]}"
                    cat_from_name = parts[idx+3]
                    timestamp_str = f"{parts[-2]}_{parts[-1]}"
            elif f.startswith('clip_'):
                cat_from_name = parts[1]
                timestamp_str = f"{parts[3]}_{parts[4]}"
            else:
                if len(parts) >= 5:
                    timestamp_str = f"{parts[-2]}_{parts[-1]}"
        except (IndexError, ValueError):
            pass
        readable_time = meta.get('start_time', '')
        if not readable_time and timestamp_str:
            try:
                dt = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                readable_time = dt.isoformat()
            except ValueError:
                readable_time = ''
        category = meta.get('category', cat_from_name)
        quality = meta.get('quality', 'HD' if category == 'EVENT' else 'MEDIUM' if category == 'NORMAL' else 'LOW')
        # Store relative path from clips_dir so the download endpoint can find it
        rel_path = os.path.relpath(fpath, clips_dir).replace('\\', '/')
        clips.append({
            "filename": rel_path,      # may include subdir, e.g. "Demo_004058/clip_...mp4"
            "size_kb": round(size_kb, 1),
            "category": category,
            "quality": quality,
            "camera": meta.get('camera', cam_from_name or 'cam_0'),
            "fps": meta.get('fps', 15),
            "duration_sec": meta.get('duration_sec', round(size_kb / 50, 1)),
            "start_time": readable_time,
            "end_time": meta.get('end_time', ''),
        })
    # Sort newest-first by start_time (ISO string, empty string sorts to end)
    clips.sort(key=lambda c: c['start_time'] or '', reverse=True)
    return {"clips": clips, "count": len(clips)}


@app.get("/api/clips/{filename:path}")
def download_clip(filename: str):
    # filename may be a relative path like "Demo_004058/clip_cam_0_...mp4"
    # Sanitize: resolve within clips dir only (prevent path traversal)
    clips_dir = os.path.abspath("storage/clips")
    target = os.path.abspath(os.path.join(clips_dir, filename))
    if not target.startswith(clips_dir):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if os.path.exists(target):
        return FileResponse(target, media_type="video/mp4", filename=os.path.basename(target))
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/prebuffer")
def list_prebuffer(response: Response):
    response.headers["Cache-Control"] = "no-store"
    pb_dir = "storage/prebuffer"
    if not os.path.exists(pb_dir):
        return {"prebuffer": []}
    items = []
    import re as _re
    for f in os.listdir(pb_dir):
        if f.endswith('.avi'):
            fpath = os.path.join(pb_dir, f)
            size_kb = os.path.getsize(fpath) / 1024
            # prebuffer_1_LOITERING_20260305_142300.avi
            # prebuffer_9_SCENE_ANOMALY_20260305_112707.avi
            stem = f.replace('.avi', '')
            event_type = 'UNKNOWN'
            readable_time = ''
            try:
                m = _re.search(r'_(\d{8})_(\d{6})$', stem)
                if m:
                    ts_str = f"{m.group(1)}_{m.group(2)}"
                    dt = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
                    readable_time = dt.isoformat()
                    # Event type is between "prebuffer_N_" and "_YYYYMMDD"
                    first_us = stem.index('_')
                    second_us = stem.index('_', first_us + 1)
                    event_type = stem[second_us + 1:m.start()]
            except (IndexError, ValueError):
                pass
            items.append({
                "filename": f,
                "size_kb": round(size_kb, 1),
                "event_type": event_type,
                "start_time": readable_time,
                "duration_sec": round(size_kb / 40, 1),
            })
    # Sort newest-first by parsed start_time ISO string
    items.sort(key=lambda x: x['start_time'] or '', reverse=True)
    return {"prebuffer": items, "count": len(items)}


@app.get("/api/prebuffer/{filename}")
def download_prebuffer(filename: str):
    safe = os.path.basename(filename)
    filepath = os.path.join("storage", "prebuffer", safe)
    if os.path.exists(filepath):
        return FileResponse(filepath, media_type="video/x-msvideo", filename=safe)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/session/clear")
def clear_session():
    cursor = database.conn.cursor()
    cursor.execute("DELETE FROM events")
    cursor.execute("DELETE FROM alerts")
    cursor.execute("DELETE FROM system_stats")
    database.conn.commit()
    for cam in cameras.values():
        cam.clear()
    compressor.stats = {k: 0 for k in compressor.stats}
    compressor.idle_batch.clear()
    return {"status": "cleared", "timestamp": datetime.now().isoformat()}


@app.delete("/api/clips/clear")
def clear_clips():
    """Delete all recorded clips from storage."""
    clips_dir = "storage/clips"
    deleted = 0
    if os.path.exists(clips_dir):
        for f in os.listdir(clips_dir):
            try:
                os.remove(os.path.join(clips_dir, f))
                deleted += 1
            except Exception:
                pass
    return {"status": "cleared", "deleted": deleted}


@app.delete("/api/prebuffer/clear")
def clear_prebuffer():
    """Delete all pre-buffer recordings from storage."""
    pb_dir = "storage/prebuffer"
    deleted = 0
    if os.path.exists(pb_dir):
        for f in os.listdir(pb_dir):
            try:
                os.remove(os.path.join(pb_dir, f))
                deleted += 1
            except Exception:
                pass
    return {"status": "cleared", "deleted": deleted}


@app.delete("/api/events/clear")
def clear_events():
    """Delete all forensic events from database."""
    cursor = database.conn.cursor()
    cursor.execute("DELETE FROM events")
    database.conn.commit()
    return {"status": "cleared"}


@app.delete("/api/alerts/clear")
def clear_alerts():
    """Delete all alerts from database."""
    cursor = database.conn.cursor()
    cursor.execute("DELETE FROM alerts")
    database.conn.commit()
    return {"status": "cleared"}


@app.delete("/api/all/clear")
def clear_all_data():
    """Delete ALL data: clips, prebuffer, events, alerts - PERMANENT."""
    results = {}
    
    # Clear database
    cursor = database.conn.cursor()
    cursor.execute("DELETE FROM events")
    cursor.execute("DELETE FROM alerts")
    cursor.execute("DELETE FROM system_stats")
    database.conn.commit()
    results["database"] = "cleared"
    
    # Clear clips
    clips_dir = "storage/clips"
    clip_count = 0
    if os.path.exists(clips_dir):
        for f in os.listdir(clips_dir):
            try:
                os.remove(os.path.join(clips_dir, f))
                clip_count += 1
            except Exception:
                pass
    results["clips_deleted"] = clip_count
    
    # Clear prebuffer
    pb_dir = "storage/prebuffer"
    pb_count = 0
    if os.path.exists(pb_dir):
        for f in os.listdir(pb_dir):
            try:
                os.remove(os.path.join(pb_dir, f))
                pb_count += 1
            except Exception:
                pass
    results["prebuffer_deleted"] = pb_count
    
    # Clear events folder
    events_dir = "storage/events"
    ev_count = 0
    if os.path.exists(events_dir):
        for f in os.listdir(events_dir):
            try:
                os.remove(os.path.join(events_dir, f))
                ev_count += 1
            except Exception:
                pass
    results["event_files_deleted"] = ev_count
    
    # Reset camera AI state
    for cam in cameras.values():
        cam.clear()
    
    compressor.stats = {k: 0 for k in compressor.stats}
    compressor.idle_batch.clear()
    
    results["status"] = "all_cleared"
    results["timestamp"] = datetime.now().isoformat()
    return results


@app.get("/api/savings")
def get_savings():
    first_cam = next(iter(cameras.values()), None)
    spike_count = first_cam.spike_gate.spike_count if first_cam else 0
    frame_count = first_cam.spike_gate.frame_count if first_cam else 0
    storage_pct = compressor.get_savings_percent()
    return {
        "storage_savings_percent": storage_pct,
        "monthly_savings_inr": compressor.get_savings_rupees(40000),
        "yearly_savings_inr": compressor.get_savings_rupees(40000) * 12,
        "compression_stats": compressor.stats,
        "frames_processed": spike_count,
        "frames_skipped": frame_count - spike_count
    }


@app.get("/api/compression-proof")
def compression_proof():
    """Scan ALL storage folders on disk — reflects current state including deletions."""
    raw_frame_kb = 900.0  # 640x480x3 bytes

    def scan_dir(path, extensions=None):
        count, kb = 0, 0.0
        if not os.path.isdir(path):
            return count, kb
        for f in os.listdir(path):
            fpath = os.path.join(path, f)
            if not os.path.isfile(fpath):
                continue
            if extensions and not any(f.endswith(e) for e in extensions):
                continue
            count += 1
            kb += os.path.getsize(fpath) / 1024
        return count, kb

    # Compressed frames
    ev_files, ev_kb = scan_dir(os.path.join("storage", "events"), [".zst"])
    idle_files, idle_kb = scan_dir(os.path.join("storage", "idle"), [".7z"])
    norm_files, norm_kb = scan_dir(os.path.join("storage", "compressed"))

    # Clips (hd + compressed subfolders + legacy flat)
    clips_hd_files, clips_hd_kb = scan_dir(os.path.join("storage", "clips", "hd"), [".mp4"])
    clips_comp_files, clips_comp_kb = scan_dir(os.path.join("storage", "clips", "compressed"), [".mp4"])
    clips_flat_files, clips_flat_kb = scan_dir(os.path.join("storage", "clips"), [".mp4"])
    total_clips_files = clips_hd_files + clips_comp_files + clips_flat_files
    total_clips_kb = clips_hd_kb + clips_comp_kb + clips_flat_kb

    # Pre-buffer
    pb_files, pb_kb = scan_dir(os.path.join("storage", "prebuffer"), [".avi", ".mp4"])

    # idle batches count as 100 frames each
    idle_frame_count = idle_files * 100

    categories = {
        "events":    {"files": ev_files,          "size_kb": round(ev_kb, 1),         "estimated_raw_kb": round(ev_files * raw_frame_kb, 1)},
        "idle":      {"files": idle_frame_count,   "size_kb": round(idle_kb, 1),        "estimated_raw_kb": round(idle_frame_count * raw_frame_kb, 1)},
        "normal":    {"files": norm_files,         "size_kb": round(norm_kb, 1),        "estimated_raw_kb": round(norm_files * raw_frame_kb, 1)},
        "clips":     {"files": total_clips_files,  "size_kb": round(total_clips_kb, 1), "estimated_raw_kb": 0},
        "prebuffer": {"files": pb_files,           "size_kb": round(pb_kb, 1),          "estimated_raw_kb": 0},
    }

    for k in ["events", "idle", "normal"]:
        v = categories[k]
        v["ratio"] = round(v["estimated_raw_kb"] / max(v["size_kb"], 1), 1)
        v["savings_pct"] = round((1 - v["size_kb"] / max(v["estimated_raw_kb"], 1)) * 100, 1) if v["estimated_raw_kb"] > 0 else 0

    total_raw = sum(categories[k]["estimated_raw_kb"] for k in ["events", "idle", "normal"])
    total_compressed = sum(categories[k]["size_kb"] for k in ["events", "idle", "normal"])
    total_disk_kb = sum(v["size_kb"] for v in categories.values())
    overall_ratio = round(total_raw / max(total_compressed, 1), 1)
    overall_savings = round((1 - total_compressed / max(total_raw, 1)) * 100, 1) if total_raw > 0 else 0

    return {
        "total_frames_compressed": ev_files + idle_frame_count + norm_files,
        "total_raw_kb": round(total_raw, 1),
        "total_compressed_kb": round(total_compressed, 1),
        "total_disk_kb": round(total_disk_kb, 1),
        "overall_compression_ratio": f"{overall_ratio}x",
        "overall_savings_percent": overall_savings,
        "categories": categories,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/summary")
def get_summary():
    first_cam = next(iter(cameras.values()), None)
    return {
        "event_summary": database.get_event_summary(),
        "total_events": len(database.get_recent_events(limit=99999)),
        "compute_savings": first_cam.spike_gate.get_compute_savings() if first_cam else 0,
        "storage_savings": compressor.get_savings_percent()
    }


@app.get("/api/export")
def export_csv():
    filepath = database.export_to_csv()
    if filepath and os.path.exists(filepath):
        return FileResponse(
            filepath,
            media_type="text/csv",
            filename=f"edgevid_forensic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
    return JSONResponse({"error": "no data to export"}, status_code=404)


# ---- Serve React App (catch-all) ----
@app.get("/app", include_in_schema=False)
@app.get("/app/{path:path}", include_in_schema=False)
def serve_react(path: str = ""):
    index = os.path.join(_BUILD_DIR, "index.html") if os.path.isdir(_BUILD_DIR) else None
    if index and os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"error": "React build not found. Run: npm run build"}, status_code=404)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    f = os.path.join(_BUILD_DIR, "favicon.ico")
    return FileResponse(f) if os.path.exists(f) else JSONResponse({}, status_code=404)

@app.get("/manifest.json", include_in_schema=False)
def manifest():
    f = os.path.join(_BUILD_DIR, "manifest.json")
    return FileResponse(f, media_type="application/json") if os.path.exists(f) else JSONResponse({}, status_code=404)

@app.get("/logo192.png", include_in_schema=False)
def logo192():
    f = os.path.join(_BUILD_DIR, "logo192.png")
    return FileResponse(f, media_type="image/png") if os.path.exists(f) else JSONResponse({}, status_code=404)

@app.get("/logo512.png", include_in_schema=False)
def logo512():
    f = os.path.join(_BUILD_DIR, "logo512.png")
    return FileResponse(f, media_type="image/png") if os.path.exists(f) else JSONResponse({}, status_code=404)


# ---- MJPEG Stream (uses first active camera) ----

def generate_mjpeg():
    while True:
        cam = next((c for c in cameras.values() if c.state['running']), None)
        if cam and cam.state['current_frame']:
            frame_bytes = base64.b64decode(cam.state['current_frame'])
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' +
                   frame_bytes + b'\r\n')
        time.sleep(0.066)


@app.get("/stream")
def video_stream():
    return StreamingResponse(
        generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ---- WebSocket: Real-time Multi-Camera Feed ----

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    print("✅ WebSocket client connected")
    try:
        while True:
            # Check if websocket is still open
            if websocket.client_state.name != "CONNECTED":
                break
                
            cam_data = {}
            for cam_id, cam in cameras.items():
                if cam.state['running'] or cam.state.get('current_frame'):
                    cam_data[cam_id] = cam.get_ws_data()

            # Build payload with multi-cam support
            first_cam_data = next(iter(cam_data.values()), None)

            payload = {
                "cameras": cam_data,
                "active_count": sum(1 for c in cameras.values() if c.state['running']),
                "timestamp": datetime.now().isoformat(),
                "compression": {
                    "original_bytes":   compressor.stats['original_bytes'],
                    "compressed_bytes": compressor.stats['compressed_bytes'],
                    "event_frames":     compressor.stats['event_frames'],
                    "idle_frames":      compressor.stats['idle_frames'],
                    "normal_frames":    compressor.stats['normal_frames'],
                    "batches_archived": compressor.stats['batches_archived'],
                    "savings_pct":      compressor.get_savings_percent()
                },
            }

            # Backward compat: merge lightweight fields from first camera (exclude frame to avoid doubling)
            if first_cam_data:
                for k, v in first_cam_data.items():
                    if k != 'frame':
                        payload[k] = v

            try:
                await websocket.send_text(json.dumps(payload))
            except (RuntimeError, ConnectionResetError):
                # Client disconnected during send
                break
            await asyncio.sleep(0.066)
    except WebSocketDisconnect:
        pass  # Clean disconnect
    except Exception as e:
        # Only log unexpected errors
        if "keepalive" not in str(e).lower() and "closed" not in str(e).lower():
            print(f"⚠️ WebSocket error: {e}")
    finally:
        ws_clients.discard(websocket)
        print("❌ WebSocket client disconnected")


# ============================================================
# RUN SERVER
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🧠 EdgeVid LowBand — The Camera That Thinks")
    print("   Multi-Camera Neuromorphic Edge-AI DVR v2.0")
    print("=" * 60)
    print("Starting server on http://localhost:8000")
    print("Dashboard: http://localhost:8000/app")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
