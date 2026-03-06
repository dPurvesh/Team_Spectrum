# VICSTA Hackathon – Grand Finale

**VIT College, Kondhwa Campus | 5th – 6th March**

---

## Team Details

- **Team Name:** Team Spectrum
- **Members:** Veer Gandhi (Team Leader), Sanchit Borikar, Purvesh Didpaye, Ashraf Ahmed
- **Domain:** Productivity & Security (PS-04)

---

## Project

**Problem:** Over 20 million cameras globally record everything equally, wasting approximately Rs. 40,000/month per mid-sized deployment on storing empty, idle footage that no one ever watches. Furthermore, traditional market solutions rely on binary motion detection, which triggers false alarms from background shadows and remains completely blind to stillness (such as a loiterer).

**Solution:** EdgeVid LowBand is a neuromorphic edge-AI DVR that fundamentally changes how video is processed.

- **SNN Spike Gate:** Acts as a neuromorphic pre-filter, bypassing 80% of compute load by skipping idle frames entirely before object detection runs.
- **Hardware-Accelerated YOLOv8:** Runs precision object detection strictly on SNN spikes, grading every frame with an Intelligence Score (0-100).
- **Dynamic ROI Compression:** Applies extreme spatial compression to backgrounds (15% quality) while keeping the target subject crystal clear (88% quality), achieving a 70% overall reduction in storage overhead.
- **Forensic Auditing:** Features a predictive 30-second pre-buffer for anomaly events (like loitering) and logs all metrics to an immutable SQLite database without relying on external cloud APIs.

---

## System Architecture

```mermaid
flowchart TD
    classDef cam    fill:#1e293b,stroke:#60a5fa,stroke-width:2px,color:#f1f5f9
    classDef snn    fill:#2e1065,stroke:#a855f7,stroke-width:2px,color:#f1f5f9
    classDef yolo   fill:#172554,stroke:#3b82f6,stroke-width:2px,color:#f1f5f9
    classDef score  fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#f1f5f9
    classDef skip   fill:#1e293b,stroke:#64748b,stroke-width:2px,color:#94a3b8
    classDef alert  fill:#450a0a,stroke:#ef4444,stroke-width:2px,color:#f1f5f9
    classDef compress fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#f1f5f9
    classDef infra  fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#f1f5f9

    CAM["Multi-Camera Input\nWebcam / IP / RTSP"]:::cam

    subgraph EDGE ["Edge Processing Pipeline"]
        direction TB
        SNN["SNN Spike Gate\nSkips 80% of idle frames"]:::snn
        YOLO["YOLOv8-nano\nObject Detection on Spike"]:::yolo
        SCORE["Frame Intelligence Score\n0 - 100"]:::score
        DEC{{"Score Threshold"}}:::score
        HEAVY["Heavy Compress\nScore below 30 — 15% JPEG"]:::skip
        ROI["ROI Compression\nScore above 60 — Subject 88%, BG 12%"]:::compress
        SKIP["Skip Frame\nZero Compute"]:::skip
        ANOM["Anomaly Detector\nLoitering Detection"]:::alert
    end

    PREBUF["Pre-Event Recorder\n30s Circular Buffer"]:::snn

    subgraph SERVE ["Data Layer & Dashboard"]
        direction TB
        DB[("SQLite Forensic Log\nEvents, Alerts, Metrics")]:::infra
        API["FastAPI App Server\nWebSocket Stream"]:::infra
        DASH["React Dashboard\nLive Feed / Clips / Compression / Alerts"]:::compress
    end

    CAM -->|"15 fps"| SNN
    CAM -..->|"Always running"| PREBUF

    SNN -->|"No spike"| SKIP
    SNN -->|"Spike"| YOLO
    SNN -..->|"Motion detected"| ANOM

    YOLO -->|"Object history"| ANOM
    YOLO --> SCORE
    SCORE --> DEC

    DEC -->|"below 30"| HEAVY
    DEC -->|"above 60 EVENT"| ROI

    PREBUF -..->|"Pre-event clip"| DB
    ANOM -..->|"Alert"| DB
    ANOM -..->|"Alert"| API

    ROI --> API
    HEAVY --> API

    DB <-->|"Query / Serve"| API
    API <-->|"Live data"| DASH
```

---

## Attribution

| Library | Role | License |
|---|---|---|
| **SpikingJelly** | Neuromorphic SNN spike gate | Apache-2.0 |
| **YOLOv8-nano** (Ultralytics) | Real-time object detection and frame scoring | AGPL-3.0 |
| **OpenCV** | Camera capture, frame processing, ROI extraction | Apache-2.0 |
| **FastAPI** | Async backend API and WebSocket server | MIT |
| **React.js** | Real-time surveillance dashboard | MIT |
| **SQLite** | Local forensic event database | Public Domain |
| **zstandard (zstd)** | High-speed lossless compression for EVENT frames | BSD |
| **py7zr** | LZMA2 batch archiving for IDLE frame sequences | LGPL-2.1 |

---

> "The world is not enough — but it is such a perfect place to start." — James Bond

All the best to every team. Build something great.
