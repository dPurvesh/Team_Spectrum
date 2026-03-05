"""
EdgeVid LowBand — Main Pipeline + FastAPI Server
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
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
# INITIALIZE ALL COMPONENTS
# ============================================================
spike_gate = SNNSpikeGate(threshold=0.15)
detector = PersonDetector(confidence=0.3)
scorer = FrameScorer()
anomaly_detector = AnomalyDetector(loiter_threshold_sec=20, fps=15)
compressor = DualCompressor(storage_dir="storage")
prebuffer = PreEventBuffer(buffer_seconds=30, fps=15)
database = ForensicDatabase()

# Ensure directories
os.makedirs("storage/events", exist_ok=True)
os.makedirs("storage/idle", exist_ok=True)
os.makedirs("storage/clips", exist_ok=True)
os.makedirs("storage/prebuffer", exist_ok=True)

# Pipeline state
pipeline_state = {
    'running': False,
    'frame_count': 0,
    'current_score': 0,
    'current_category': 'IDLE',
    'current_frame': None,
    'current_detections': [],
    'fps': 0,
    'last_alert': None,
    'snn_spike': False,
    'snn_membrane': 0.0,
    'snn_diff': 0.0,
    'session_name': None,
    'session_start': None,
}

# Active MP4 event clip writer
clip_state = {
    'writer': None,
    'path': None,
    'start_frame': 0,
    'cooldown': 0,        # frames since last EVENT — keep recording for 45 frames after
    'frame_shape': None,
    'category': 'IDLE',   # Track the highest category during this clip
    'start_time': None,   # Real timestamp when clip started
}

# Metadata file for clip info (category, timestamp, duration, quality)
# Written alongside each .mp4 as .json


# Pipeline thread reference
pipeline_thread = None

# WebSocket clients
ws_clients = set()


# ============================================================
# THREADED CAMERA READER — Prevents cap.read() from blocking
# ============================================================
class CameraReader:
    """Read camera frames in a dedicated thread so cap.read() never blocks the pipeline.
    On Windows, OpenCV MSMF/DSHOW backends can hang on cap.read() — this fixes that."""
    def __init__(self, source=0):
        # Try DirectShow first (faster on Windows), fall back to default
        self.cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            print("⚠️ DSHOW failed, trying default backend...")
            self.cap = cv2.VideoCapture(source)
        # Minimize internal buffer so we always get latest frame
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._frame = None
        self._ret = False
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):
        """Continuously grab frames — runs in its own thread"""
        while self._running:
            ret, frame = self.cap.read()
            with self._lock:
                self._ret = ret
                self._frame = frame
            if not ret:
                time.sleep(0.01)  # brief pause on failure

    def read(self):
        """Get the latest frame (non-blocking)"""
        with self._lock:
            # Return a copy so pipeline can process without lock contention
            if self._frame is not None:
                return self._ret, self._frame.copy()
            return False, None

    def isOpened(self):
        return self.cap.isOpened()

    def release(self):
        self._running = False
        time.sleep(0.1)  # let reader thread exit
        self.cap.release()


def start_event_clip(frame, frame_number, category='EVENT'):
    """Start recording an MP4 event clip"""
    h, w = frame.shape[:2]
    clip_state['frame_shape'] = (w, h)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"clip_{category}_{frame_number}_{ts}.mp4"
    filepath = os.path.join("storage", "clips", filename)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    # Quality-based FPS: EVENT=15fps HD, NORMAL=12fps, IDLE=8fps
    write_fps = 15.0 if category == 'EVENT' else 12.0 if category == 'NORMAL' else 8.0
    clip_state['writer'] = cv2.VideoWriter(filepath, fourcc, write_fps, (w, h))
    clip_state['path'] = filepath
    clip_state['start_frame'] = frame_number
    clip_state['cooldown'] = 0
    clip_state['category'] = category
    clip_state['start_time'] = datetime.now().isoformat()
    return filepath


def stop_event_clip(frame_number):
    """Stop and finalize the event clip — write metadata JSON alongside"""
    if clip_state['writer'] is not None:
        clip_state['writer'].release()
        duration = (frame_number - clip_state['start_frame']) / 15.0
        path = clip_state['path']
        cat = clip_state.get('category', 'EVENT')
        start_ts = clip_state.get('start_time', datetime.now().isoformat())
        # Write metadata JSON
        meta = {
            'filename': os.path.basename(path),
            'category': cat,
            'quality': 'HD' if cat == 'EVENT' else 'MEDIUM' if cat == 'NORMAL' else 'LOW',
            'start_time': start_ts,
            'end_time': datetime.now().isoformat(),
            'duration_sec': round(duration, 1),
            'start_frame': clip_state['start_frame'],
            'end_frame': frame_number,
            'fps': 15 if cat == 'EVENT' else 12 if cat == 'NORMAL' else 8,
        }
        meta_path = path.replace('.mp4', '.json')
        try:
            with open(meta_path, 'w') as f:
                json.dump(meta, f)
        except Exception:
            pass
        clip_state['writer'] = None
        clip_state['path'] = None
        clip_state['start_frame'] = 0
        clip_state['cooldown'] = 0
        clip_state['category'] = 'IDLE'
        clip_state['start_time'] = None
        return path, duration
    return None, 0


# ============================================================
# PIPELINE THREAD — Runs continuously
# ============================================================
def run_pipeline(camera_source=0):
    """Main pipeline loop — runs in a separate thread"""
    print(f"📹 Opening camera {camera_source} with threaded reader...")
    reader = CameraReader(camera_source)

    if not reader.isOpened():
        print(f"❌ Failed to open camera source: {camera_source}")
        pipeline_state['running'] = False
        return

    # Wait briefly for first frame from reader thread
    time.sleep(0.3)

    # Already set to True by camera_start — just confirm
    pipeline_state['running'] = True
    frame_number = 0
    fps_counter = 0
    fps_timer = time.time()
    no_frame_count = 0
    last_yolo_detections = []  # Cache last YOLO result for non-spike frames

    print("🚀 EdgeVid LowBand Pipeline Started!")
    print(f"📹 Camera source: {camera_source} (threaded reader active)")

    while pipeline_state['running']:
      try:
        ret, frame = reader.read()
        if not ret or frame is None:
            no_frame_count += 1
            if no_frame_count > 300:  # ~3 seconds with no frames
                print("❌ Camera feed lost. Stopping pipeline.")
                break
            time.sleep(0.01)
            continue
        no_frame_count = 0

        frame_number += 1
        fps_counter += 1
        start_time = time.time()

        # Resize for speed if frame is large
        h_orig, w_orig = frame.shape[:2]
        if w_orig > 640:
            scale = 640 / w_orig
            frame = cv2.resize(frame, (640, int(h_orig * scale)))

        # ---- ALWAYS encode raw frame first so feed never freezes ----
        _, raw_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
        pipeline_state['current_frame'] = base64.b64encode(raw_buf).decode('utf-8')
        pipeline_state['frame_count'] = frame_number

        # ---- Pre-buffer (only every 3rd frame to reduce memory pressure) ----
        if frame_number % 3 == 0:
            prebuffer.add_frame(frame, frame_number)

        # ---- Step 1: SNN Spike Gate ----
        spike, diff_score, membrane = spike_gate.process_frame(frame)
        pipeline_state['snn_spike'] = bool(spike)
        pipeline_state['snn_membrane'] = float(round(float(membrane), 4))
        pipeline_state['snn_diff'] = float(round(float(diff_score), 4))

        if not spike:
            # NO SPIKE — but still run YOLO every 8 frames as safety net
            # This ensures we ALWAYS detect persons even in "static" scenes
            if frame_number % 8 == 0:
                periodic_dets = detector.detect(frame)
                person_dets = [d for d in periodic_dets if d['is_person']]
                if person_dets:
                    # Person found! Force-spike and process fully
                    spike = True
                    pipeline_state['snn_spike'] = True
                    last_yolo_detections = periodic_dets
                else:
                    last_yolo_detections = []

        if not spike:
            # Truly idle — no motion AND no persons
            pipeline_state['current_score'] = 0.0
            pipeline_state['current_category'] = 'IDLE'
            pipeline_state['current_detections'] = []

            # Compress idle only every 5th frame (disk I/O is slow)
            if frame_number % 5 == 0:
                compressor.compress_idle(frame, frame_number)

            # If event clip is recording, track cooldown
            if clip_state['writer'] is not None:
                clip_state['cooldown'] += 1
                clip_state['writer'].write(frame)
                if clip_state['cooldown'] > 45:
                    path, dur = stop_event_clip(frame_number)
                    if path:
                        print(f"🎬 Event clip saved: {path} ({dur:.1f}s)")
        else:
            # SPIKE — full processing
            # ---- Step 2: YOLOv8-nano ----
            detections = last_yolo_detections if last_yolo_detections else detector.detect(frame)
            last_yolo_detections = []  # Clear cached

            # ---- Step 3: Anomaly Detection ----
            alerts = anomaly_detector.update(frame, detections, frame_number)

            # ---- Step 4: Frame Scoring ----
            anomaly_flag = anomaly_detector.has_active_anomaly()
            score, category = scorer.calculate_score(
                detections, diff_score, frame.shape,
                anomaly_flag=anomaly_flag
            )

            # ---- Step 5: Compression + Storage (only every few frames) ----
            if category == "EVENT":
                if frame_number % 3 == 0:
                    frame_path, _, _ = compressor.compress_event(
                        frame, detections, frame_number
                    )
                else:
                    frame_path = None
                # Log event every 10th frame to avoid DB spam
                if frame_number % 10 == 0:
                    event_id = database.log_event(
                        frame_number=frame_number, score=score,
                        category=category, detections=detections,
                        event_type="PERSON_DETECTED",
                        severity="HIGH", frame_path=frame_path,
                        compression_type="zstd+roi"
                    )
                # Start or continue event clip
                if clip_state['writer'] is None:
                    start_event_clip(frame, frame_number, category='EVENT')
                elif clip_state['category'] != 'EVENT':
                    # Upgrade clip quality if category escalated
                    clip_state['category'] = 'EVENT'
                clip_state['cooldown'] = 0
                clip_state['writer'].write(frame)

            elif category == "NORMAL":
                if frame_number % 5 == 0:
                    compressor.compress_normal(frame, frame_number)
                # Start NORMAL clip if person detected but not EVENT level
                if clip_state['writer'] is None and len([d for d in detections if d.get('is_person')]) > 0:
                    start_event_clip(frame, frame_number, category='NORMAL')
                if clip_state['writer'] is not None:
                    clip_state['cooldown'] += 1
                    clip_state['writer'].write(frame)
                    if clip_state['cooldown'] > 45:
                        path, dur = stop_event_clip(frame_number)
                        if path:
                            print(f"🎬 Event clip saved: {path} ({dur:.1f}s)")
            else:
                if frame_number % 5 == 0:
                    compressor.compress_idle(frame, frame_number)

            # ---- Step 6: Handle Alerts ----
            for alert in alerts:
                prebuf_info = prebuffer.save_pre_event(alert['type'])
                event_id = database.log_event(
                    frame_number=frame_number, score=95.0,
                    category="EVENT", detections=detections,
                    event_type=alert['type'], severity="CRITICAL",
                    anomaly_flag=True, duration=alert.get('duration_sec', 0),
                    prebuffer_path=prebuf_info['filepath'] if prebuf_info else None,
                    compression_type="zstd+roi"
                )
                database.log_alert(event_id, alert['type'], alert['message'])
                pipeline_state['last_alert'] = alert

            # Update state (ensure native Python types for JSON serialization)
            pipeline_state['current_score'] = float(score)
            pipeline_state['current_category'] = str(category)
            pipeline_state['current_detections'] = detections

        # Draw crosshair + bounding boxes ONLY if detections exist (re-encode)
        dets = pipeline_state.get('current_detections', [])
        if dets:
            display_frame = frame.copy()
            for det in dets:
                x1, y1, x2, y2 = det['box']
                cls = det.get('class_name', '?')
                conf = det.get('confidence', 0)
                color = (0, 255, 100) if det.get('is_person') else (255, 200, 0)
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, 2)
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                cross_size = 12
                cv2.line(display_frame, (cx - cross_size, cy), (cx + cross_size, cy), color, 1)
                cv2.line(display_frame, (cx, cy - cross_size), (cx, cy + cross_size), color, 1)
                blen = min(20, (x2 - x1) // 4, (y2 - y1) // 4)
                for (cx1, cy1), (dx, dy) in [
                    ((x1, y1), (1, 1)), ((x2, y1), (-1, 1)),
                    ((x1, y2), (1, -1)), ((x2, y2), (-1, -1))
                ]:
                    cv2.line(display_frame, (cx1, cy1), (cx1 + blen * dx, cy1), color, 2)
                    cv2.line(display_frame, (cx1, cy1), (cx1, cy1 + blen * dy), color, 2)
                label = f"{cls} {conf:.0%}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.rectangle(display_frame, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
                cv2.putText(display_frame, label, (x1 + 3, y1 - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
            # Re-encode with overlays
            _, buffer = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 55])
            pipeline_state['current_frame'] = base64.b64encode(buffer).decode('utf-8')

        # FPS calculation
        if time.time() - fps_timer >= 1.0:
            pipeline_state['fps'] = fps_counter
            # Debug: print every second so user can see pipeline is alive
            if fps_counter > 0:
                print(f"📊 FPS={fps_counter} | Frame#{frame_number} | Cat={pipeline_state['current_category']} | Score={pipeline_state['current_score']} | Spike={pipeline_state['snn_spike']}")
            fps_counter = 0
            fps_timer = time.time()

        # Auto-recalibrate SNN every 24 hours per camera
        if frame_number % 1296000 == 0:
            spike_gate.auto_recalibrate()

        # Log system stats every 1000 frames
        if frame_number % 1000 == 0:
            database.log_system_stats(
                total_frames=frame_number,
                processed_frames=spike_gate.spike_count,
                compute_savings=spike_gate.get_compute_savings(),
                storage_savings=compressor.get_savings_percent(),
                spike_rate=spike_gate.get_spike_rate(),
                avg_score=scorer.get_avg_score()
            )

        # Adaptive frame rate control
        cat = pipeline_state['current_category']
        target_fps = 15 if cat == 'EVENT' else 12 if cat == 'NORMAL' else 8
        elapsed = time.time() - start_time
        sleep_time = max(0, (1 / target_fps) - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

      except Exception as e:
        print(f"⚠️ Pipeline error: {e}")
        import traceback
        traceback.print_exc()
        time.sleep(0.1)
        continue

    # Cleanup
    if clip_state['writer'] is not None:
        stop_event_clip(frame_number)
    reader.release()
    pipeline_state['running'] = False
    pipeline_state['current_frame'] = None
    pipeline_state['fps'] = 0
    pipeline_state['snn_spike'] = False
    print("⏹️ Pipeline stopped.")


# ============================================================
# FASTAPI SERVER
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pipeline does NOT auto-start — user toggles camera manually from dashboard
    yield
    pipeline_state['running'] = False

app = FastAPI(
    title="EdgeVid LowBand API",
    version="1.0.0",
    description="The Camera That Thinks — Neuromorphic Edge-AI DVR",
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
    # Serve root-level build assets (manifest.json, logos, robots.txt, etc.)
    app.mount("/build", StaticFiles(directory=_BUILD_DIR), name="build_root")


# ---- REST Endpoints ----

@app.get("/")
def root():
    return {
        "name": "EdgeVid LowBand",
        "version": "1.0.0",
        "status": "running" if pipeline_state['running'] else "stopped",
        "tagline": "The Camera That Thinks"
    }


@app.get("/api/stats")
def get_stats():
    return {
        "frame_count": pipeline_state['frame_count'],
        "current_score": pipeline_state['current_score'],
        "current_category": pipeline_state['current_category'],
        "fps": pipeline_state['fps'],
        "spike_rate": spike_gate.get_spike_rate(),
        "compute_savings": spike_gate.get_compute_savings(),
        "storage_savings": compressor.get_savings_percent(),
        "avg_score": scorer.get_avg_score(),
        "active_tracks": anomaly_detector.get_active_tracks(),
        "total_alerts": len(anomaly_detector.alerts),
        "score_distribution": scorer.get_score_distribution(),
        "buffer_status": prebuffer.get_buffer_status()
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
    global pipeline_thread
    return {
        "camera_on": pipeline_state['running'],
        "frame_count": pipeline_state['frame_count'],
        "fps": pipeline_state['fps']
    }


@app.post("/api/camera/start")
def camera_start(source: int = 0, session_name: str = None):
    global pipeline_thread
    if pipeline_state['running']:
        return {"status": "already_running"}
    # Set session
    pipeline_state['session_name'] = session_name or f"Demo_{datetime.now().strftime('%H%M%S')}"
    pipeline_state['session_start'] = datetime.now().isoformat()
    # Mark running=True BEFORE thread starts so status poll sees it immediately
    pipeline_state['running'] = True
    pipeline_state['current_category'] = 'IDLE'
    pipeline_state['current_score'] = 0
    pipeline_thread = threading.Thread(target=run_pipeline, args=(source,), daemon=True)
    pipeline_thread.start()
    return {"status": "started", "source": source, "session": pipeline_state['session_name']}


@app.post("/api/camera/stop")
def camera_stop():
    global pipeline_thread
    if not pipeline_state['running']:
        return {"status": "already_stopped"}
    pipeline_state['running'] = False
    pipeline_state['current_frame'] = None
    pipeline_state['current_score'] = 0
    pipeline_state['current_category'] = 'IDLE'
    pipeline_state['current_detections'] = []
    pipeline_state['fps'] = 0
    pipeline_state['snn_spike'] = False
    pipeline_state['snn_membrane'] = 0.0
    pipeline_state['snn_diff'] = 0.0
    # Reset SNN state so restart is clean
    spike_gate.prev_frame = None
    spike_gate.membrane_potential = 0.0
    spike_gate.lif_neuron.reset()
    return {"status": "stopped"}


@app.get("/api/clips")
def list_clips():
    """List all saved MP4 event clips with timeline metadata"""
    clips_dir = "storage/clips"
    if not os.path.exists(clips_dir):
        return {"clips": []}
    clips = []
    for f in sorted(os.listdir(clips_dir), reverse=True):
        if f.endswith('.mp4'):
            fpath = os.path.join(clips_dir, f)
            size_kb = os.path.getsize(fpath) / 1024
            # Try to read metadata JSON
            meta_path = fpath.replace('.mp4', '.json')
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r') as mf:
                        meta = json.load(mf)
                except Exception:
                    pass
            # Parse timestamp from filename: clip_CATEGORY_FRAME_YYYYMMDD_HHMMSS.mp4
            # or legacy: event_clip_FRAME_YYYYMMDD_HHMMSS.mp4
            parts = f.replace('.mp4', '').split('_')
            timestamp_str = ''
            cat_from_name = 'EVENT'
            try:
                if f.startswith('clip_'):
                    # New format: clip_EVENT_123_20260305_120000.mp4
                    cat_from_name = parts[1]
                    timestamp_str = f"{parts[3]}_{parts[4]}"
                else:
                    # Legacy: event_clip_123_20260305_120000.mp4
                    timestamp_str = f"{parts[3]}_{parts[4]}"
            except (IndexError, ValueError):
                pass
            # Format human-readable timestamp
            readable_time = meta.get('start_time', '')
            if not readable_time and timestamp_str:
                try:
                    dt = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    readable_time = dt.isoformat()
                except ValueError:
                    readable_time = ''
            category = meta.get('category', cat_from_name)
            quality = meta.get('quality', 'HD' if category == 'EVENT' else 'MEDIUM' if category == 'NORMAL' else 'LOW')
            clips.append({
                "filename": f,
                "size_kb": round(size_kb, 1),
                "category": category,
                "quality": quality,
                "fps": meta.get('fps', 15),
                "duration_sec": meta.get('duration_sec', round(size_kb / 50, 1)),
                "start_time": readable_time,
                "end_time": meta.get('end_time', ''),
            })
    return {"clips": clips, "count": len(clips)}


@app.get("/api/clips/{filename}")
def download_clip(filename: str):
    """Download a specific MP4 clip"""
    filepath = os.path.join("storage", "clips", filename)
    if os.path.exists(filepath):
        return FileResponse(filepath, media_type="video/mp4", filename=filename)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/session/clear")
def clear_session():
    """Clear all data for a fresh demo session"""
    # Wipe SQLite tables
    cursor = database.conn.cursor()
    cursor.execute("DELETE FROM events")
    cursor.execute("DELETE FROM alerts")
    cursor.execute("DELETE FROM system_stats")
    database.conn.commit()

    # Clear in-memory state
    spike_gate.frame_count = 0
    spike_gate.spike_count = 0
    spike_gate.spike_history.clear()
    spike_gate.diff_history.clear()
    spike_gate.prev_frame = None
    spike_gate.membrane_potential = 0.0
    spike_gate.lif_neuron.reset()
    scorer.score_history.clear()
    anomaly_detector.tracked_objects.clear()
    anomaly_detector.alerts.clear()
    anomaly_detector.frame_counter = 0
    compressor.stats = {k: 0 for k in compressor.stats}
    compressor.idle_batch.clear()
    pipeline_state['frame_count'] = 0
    pipeline_state['current_score'] = 0
    pipeline_state['current_category'] = 'IDLE'
    pipeline_state['last_alert'] = None
    pipeline_state['snn_spike'] = False
    pipeline_state['snn_membrane'] = 0.0
    return {"status": "cleared", "timestamp": datetime.now().isoformat()}


@app.get("/api/savings")
def get_savings():
    storage_pct = compressor.get_savings_percent()
    return {
        "storage_savings_percent": storage_pct,
        "monthly_savings_inr": compressor.get_savings_rupees(40000),
        "yearly_savings_inr": compressor.get_savings_rupees(40000) * 12,
        "compression_stats": compressor.stats,
        "frames_processed": spike_gate.spike_count,
        "frames_skipped": spike_gate.frame_count - spike_gate.spike_count
    }


@app.get("/api/summary")
def get_summary():
    return {
        "event_summary": database.get_event_summary(),
        "total_events": len(database.get_recent_events(limit=99999)),
        "compute_savings": spike_gate.get_compute_savings(),
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


# ---- Video Stream (MJPEG) ----

def generate_mjpeg():
    while pipeline_state['running']:
        if pipeline_state['current_frame']:
            frame_bytes = base64.b64decode(pipeline_state['current_frame'])
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


# ---- WebSocket: Real-time Data Feed ----

def _safe_number(v):
    """Convert numpy/other numeric types to native Python for JSON serialization"""
    if v is None:
        return 0
    try:
        if isinstance(v, (int, float, bool)):
            return v
        return float(v)
    except (TypeError, ValueError):
        return 0


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    print("\u2705 WebSocket client connected")
    try:
        while True:
            cat = str(pipeline_state.get('current_category', 'IDLE'))
            target_fps = 15 if cat == 'EVENT' else 12 if cat == 'NORMAL' else 8

            # Build safe detections list (convert any numpy types)
            safe_dets = []
            for d in pipeline_state.get('current_detections', []):
                try:
                    box = d.get('box', (0,0,0,0))
                    safe_dets.append({
                        'box': [int(b) for b in box],
                        'class': str(d.get('class_name', '?')),
                        'conf': float(d.get('confidence', 0))
                    })
                except Exception:
                    pass

            data = {
                "frame": pipeline_state.get('current_frame'),
                "score": _safe_number(pipeline_state.get('current_score', 0)),
                "category": cat,
                "fps": int(pipeline_state.get('fps', 0)),
                "target_fps": int(target_fps),
                "frame_count": int(pipeline_state.get('frame_count', 0)),
                "spike_rate": float(round(_safe_number(spike_gate.get_spike_rate()), 1)),
                "active_tracks": int(anomaly_detector.get_active_tracks()),
                "alerts": int(len(anomaly_detector.alerts)),
                "snn_spike": bool(pipeline_state.get('snn_spike', False)),
                "snn_membrane": _safe_number(pipeline_state.get('snn_membrane', 0)),
                "snn_diff": _safe_number(pipeline_state.get('snn_diff', 0)),
                "snn_threshold": float(spike_gate.threshold),
                "last_alert": pipeline_state.get('last_alert'),
                "session_name": pipeline_state.get('session_name'),
                "timestamp": datetime.now().isoformat(),
                "detections": safe_dets
            }

            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(0.066)  # ~15fps
    except WebSocketDisconnect:
        print("\u274c WebSocket client disconnected")
    except Exception as e:
        print(f"\u26a0\ufe0f WebSocket error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        ws_clients.discard(websocket)


# ============================================================
# RUN SERVER
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🧠 EdgeVid LowBand — The Camera That Thinks")
    print("=" * 60)
    print("Starting server on http://localhost:8000")
    print("Dashboard will connect to this server")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)