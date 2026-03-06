# VICSTA Hackathon – Grand Finale

**VIT College, Kondhwa Campus | 5th – 6th March**

---

## Team Details

**Team Name:** Team Spectrum

**Members:**

- Veer Gandhi — Team Leader
- Sanchit Borikar
- Purvesh Didpaye
- Ashraf Ahmed

**Domain:** Productivity & Security (Problem Statement ID: PS-04)

---

## Project

**EdgeVid LowBand — The Camera That Thinks**

### Problem

> Over 20 million surveillance cameras record every frame with identical priority, generating petabytes of footage no one ever watches — at an estimated cost of Rs. 40,000 per month per deployment. Existing motion-detection systems respond to irrelevant stimuli such as passing shadows while remaining completely blind to high-risk stillness, such as a stationary loiterer. The result is a system that wastes compute, storage, and human attention in equal measure.

### Solution

> EdgeVid LowBand is a neuromorphic edge-AI DVR that makes the camera intelligent at the source — with zero cloud dependency and a 70% reduction in storage footprint.

**How it works:**

- **SNN Spike Gate** — A Spiking Neural Network (SpikingJelly) acts as a biological neural filter. It evaluates every incoming frame and fires a spike only when activity warrants YOLO inference, skipping approximately 80% of idle-frame computation before the object detector ever runs.
- **YOLOv8-nano Scoring Engine** — On a spike, YOLOv8-nano runs detection and assigns each frame a threat score from 0 to 100, classifying it as IDLE, NORMAL, or EVENT.
- **Dynamic ROI Compression** — Score-driven dual-layer compression: EVENT frames retain the subject region at full quality (88%) while background is compressed to 12% using ROI JPEG and zstd. NORMAL frames use 50% JPEG. IDLE frames are batched and archived into py7zr (.7z) archives — achieving over 99% compression on static background footage.
- **Predictive Pre-Buffer** — A 30-second circular pre-buffer is maintained at all times. When a loitering or anomaly alert fires, the 30 seconds of footage preceding the event is automatically saved — capturing the build-up, not just the incident.
- **Forensic Event Log** — Every classified event, detection, alert, and compression action is written to a local SQLite database with full metadata for audit and review.
- **Live Dashboard** — A React.js frontend streams real-time feed, compression statistics, neural spike activity, recorded clips, and forensic logs over FastAPI WebSocket — with no external cloud calls.

---

## Rules to Remember

- All development must happen during the hackathon only
- Push code regularly — commit history is monitored
- Use only open-source libraries with compatible licenses and credit them
- Only one submission per team
- All members must be present both days

---

## Attribution

This project is built entirely on open-source technology:

| Library | Role |
|---|---|
| **SpikingJelly** | Neuromorphic Spiking Neural Network (SNN) gate |
| **YOLOv8-nano** (Ultralytics) | Real-time object detection and threat scoring |
| **OpenCV** | Camera capture, frame processing, ROI extraction |
| **FastAPI** | High-performance async backend and WebSocket server |
| **React.js** | Real-time surveillance dashboard frontend |
| **SQLite** | Local forensic event database |
| **zstandard (zstd)** | High-speed lossless compression for EVENT frames |
| **py7zr** | LZMA2 batch archiving for IDLE frame sequences |

All libraries are used under their respective open-source licenses (MIT, AGPL-3.0, Apache-2.0).

---

> "The world is not enough — but it is such a perfect place to start." — James Bond

All the best to every team. Build something great.
