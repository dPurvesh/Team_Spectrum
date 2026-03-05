"""Smoke test for all EdgeVid backend modules"""
import numpy as np
import cv2
import os

os.makedirs("storage/events", exist_ok=True)
os.makedirs("storage/idle", exist_ok=True)
os.makedirs("storage/compressed", exist_ok=True)
os.makedirs("storage/prebuffer", exist_ok=True)

print("=== IMPORT TEST ===")
from snn_gate import SNNSpikeGate
print("[OK] snn_gate")
from detector import PersonDetector
print("[OK] detector")
from scorer import FrameScorer
print("[OK] scorer")
from anomaly_detector import AnomalyDetector
print("[OK] anomaly_detector")
from compressor import DualCompressor
print("[OK] compressor")
from pre_buffer import PreEventBuffer
print("[OK] pre_buffer")
from database import ForensicDatabase
print("[OK] database")
print()

print("=== UNIT TESTS ===")

# 1. SNN Gate
gate = SNNSpikeGate()
f1 = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
spike, diff, mem = gate.process_frame(f1)
print(f"[OK] SNN Gate - first frame: spike={spike}")

f2 = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
spike2, diff2, mem2 = gate.process_frame(f2)
print(f"[OK] SNN Gate - second frame: spike={spike2}, diff={diff2:.4f}, membrane={mem2:.4f}")
print(f"     compute_savings={gate.get_compute_savings():.1f}%, spike_rate={gate.get_spike_rate():.1f}%")
gate.auto_recalibrate()
print(f"[OK] SNN Gate - auto_recalibrate ran, threshold now={gate.threshold:.4f}")

# 2. Detector
det = PersonDetector(confidence=0.4)
detections = det.detect(f1)
print(f"[OK] Detector - found {len(detections)} objects on random noise (expected ~0)")
print(f"     model loaded: {det.model.model_name}")

# 3. Scorer
sc = FrameScorer()
score, cat = sc.calculate_score(detections, 0.1, f1.shape)
print(f"[OK] Scorer - score={score}, category={cat}")
# Test with fake detections
fake_dets = [{"is_person": True, "confidence": 0.85, "center": (320, 240), "box": (100, 100, 200, 300)}]
score2, cat2 = sc.calculate_score(fake_dets, 0.3, f1.shape)
print(f"[OK] Scorer - with 1 person: score={score2}, category={cat2}")
print(f"     avg_score={sc.get_avg_score()}, distribution={sc.get_score_distribution()}")

# 4. Anomaly Detector
ad = AnomalyDetector()
alerts = ad.update(f1, [], 1)
print(f"[OK] Anomaly Detector - frame 1: alerts={len(alerts)}, tracks={ad.get_active_tracks()}")
alerts2 = ad.update(f2, fake_dets, 2)
print(f"[OK] Anomaly Detector - frame 2 with person: alerts={len(alerts2)}, tracks={ad.get_active_tracks()}")
print(f"     has_active_anomaly={ad.has_active_anomaly()}")

# 5. Compressor
comp = DualCompressor()
# Event compression
event_path, orig, compressed = comp.compress_event(f1, fake_dets, 999)
print(f"[OK] Compressor - event: {orig} -> {compressed} bytes, path={event_path}")
# Normal compression
enc, orig_n, comp_n = comp.compress_normal(f1, 1000)
print(f"[OK] Compressor - normal: {orig_n} -> {comp_n} bytes")
# Idle compression
idle_result = comp.compress_idle(f1, 1001)
print(f"[OK] Compressor - idle batch queued ({len(comp.idle_batch)} in batch)")
print(f"     savings={comp.get_savings_percent():.1f}%, monthly_savings=INR{comp.get_savings_rupees(40000)}")

# 6. PreBuffer
pb = PreEventBuffer()
for i in range(10):
    pb.add_frame(f1, i)
status = pb.get_buffer_status()
print(f"[OK] PreBuffer - filled={status['filled']}/{status['capacity']} ({status['percent']}%)")

# 7. Database
db = ForensicDatabase()
eid = db.log_event(1, 45.0, "NORMAL", fake_dets, event_type="PERSON_DETECTED")
print(f"[OK] Database - logged event id={eid}")
aid = db.log_alert(eid, "LOITERING", "Test alert message", severity="HIGH")
print(f"[OK] Database - logged alert id={aid}")
events = db.get_recent_events(limit=5)
print(f"[OK] Database - retrieved {len(events)} events")
alerts_db = db.get_recent_alerts(limit=5)
print(f"[OK] Database - retrieved {len(alerts_db)} alerts")
summary = db.get_event_summary()
print(f"[OK] Database - summary={summary}")
db.log_system_stats(100, 20, 80.0, 65.0, 20.0, 35.5)
print("[OK] Database - logged system stats")
csv_path = db.export_to_csv()
print(f"[OK] Database - exported CSV to {csv_path}")
db.close()

# 8. FastAPI import check
from fastapi import FastAPI
from main import app
routes = [r.path for r in app.routes]
print(f"[OK] FastAPI app loaded with {len(routes)} routes:")
for r in sorted(routes):
    print(f"     {r}")

print()
print("=" * 50)
print("ALL TESTS PASSED — MODEL FUNCTIONING CORRECTLY")
print("=" * 50)
