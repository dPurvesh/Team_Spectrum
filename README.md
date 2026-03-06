# VICSTA Hackathon – Grand Finale

**VIT College, Kondhwa Campus | 5th – 6th March**

---

## Team Details

- **Team Name:** Team Spectrum
- **Members:**
  - Veer Gandhi (Team Leader)
  - Sanchit Borikar
  - Purvesh Didpaye
  - Ashraf Ahmed
- **Domain:** Productivity & Security (PS-04)

---

## Project

**Problem:** Over 20 million cameras globally record everything equally, wasting approximately Rs. 40,000/month per mid-sized deployment on storing empty, idle footage that no one ever watches. Furthermore, traditional market solutions rely on binary motion detection, which triggers false alarms from background shadows and remains completely blind to stillness (such as a loiterer).

**Solution:** EdgeVid LowBand is a neuromorphic edge-AI DVR that fundamentally changes how video is processed.

- **SNN Spike Gate:** Acts as a neuromorphic pre-filter, bypassing 80% of compute load by skipping idle frames entirely before object detection runs.
- **Hardware-Accelerated YOLOv8:** Runs precision object detection strictly on SNN spikes, grading every frame with an Intelligence Score (0–100).
- **Dynamic ROI Compression:** Applies extreme spatial compression to backgrounds (15% quality) while keeping the target subject crystal clear (88% quality), achieving a 70% overall reduction in storage overhead.
- **Forensic Auditing:** Features a predictive 30-second pre-buffer for anomaly events (like loitering) and logs all metrics to an immutable SQLite database without relying on external cloud APIs.

---

## System Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffffff', 'mainBkg': '#ffffff', 'edgeLabelBackground': '#ffffff', 'lineColor': '#64748b'}}}%%
flowchart LR
    classDef cam      fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a8a
    classDef snn      fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#3b0764
    classDef yolo     fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#1e3a8a
    classDef score    fill:#ffedd5,stroke:#ea580c,stroke-width:2px,color:#7c2d12
    classDef skip     fill:#f1f5f9,stroke:#94a3b8,stroke-width:2px,color:#475569
    classDef alert    fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#7f1d1d
    classDef compress fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d
    classDef infra    fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e

    CAM["Camera Input\nWebcam / RTSP / IP"]:::cam
    PREBUF["Pre-Event Buffer\n30s Circular"]:::snn
    SNN["SNN Spike Gate\nSkips 80% idle frames"]:::snn
    SKIP["Skip Frame\nZero Compute"]:::skip
    YOLO["YOLOv8-nano\nObject Detection"]:::yolo
    ANOM["Anomaly Detector\nLoitering Detection"]:::alert
    SCORE["Frame Score\n0 to 100"]:::score
    DEC{{"Threshold"}}:::score
    HEAVY["Heavy Compress\nScore below 30\n15% JPEG"]:::skip
    ROI["ROI Compress\nScore above 60\nSubject 88% / BG 12%"]:::compress
    DB[("SQLite\nForensic Log")]:::infra
    API["FastAPI Server\nWebSocket"]:::infra
    DASH["React Dashboard\nLive Feed / Clips / Alerts"]:::compress

    CAM --> SNN
    CAM --> PREBUF
    SNN -->|"No Spike"| SKIP
    SNN -->|"Spike"| YOLO
    SNN -->|"Anomaly"| ANOM
    YOLO --> ANOM
    YOLO --> SCORE
    SCORE --> DEC
    DEC -->|"below 30"| HEAVY
    DEC -->|"above 60"| ROI
    PREBUF --> DB
    ANOM --> DB
    ANOM --> API
    ROI --> API
    HEAVY --> API
    DB -->|"Query"| API
    API --> DASH
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
