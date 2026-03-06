# Real-Time Compression — Full Code Reference

> This file documents all code powering the **REAL-TIME COMPRESSION — LIVE** panel
> shown in the EdgeVid LowBand dashboard.

---

## 1. BACKEND — `compressor.py` (Dual Compression Engine)

```python
"""
Dual-Layer Compression Engine
zstd for real-time event frames, py7zr for idle frame batches.
70% overall storage reduction.
"""

import os
import cv2
import numpy as np
import zstandard as zstd
import py7zr
from datetime import datetime


class DualCompressor:
    def __init__(self, storage_dir="storage", zstd_level=3, batch_size=100):
        self.storage_dir = storage_dir
        self.zstd_compressor = zstd.ZstdCompressor(level=zstd_level)
        self.batch_size = batch_size
        self.idle_batch = []
        self.batch_count = 0

        self.stats = {
            'original_bytes': 0,       # raw frame.nbytes total
            'compressed_bytes': 0,     # actual compressed bytes total
            'event_frames': 0,
            'idle_frames': 0,
            'normal_frames': 0,
            'batches_archived': 0
        }

    def compress_event(self, frame, detections, frame_number):
        """
        EVENT frame (score > 60): ROI compression
        Subject at 88% quality, background at 12%, then zstd compress
        """
        h, w = frame.shape[:2]
        original_size = frame.nbytes

        mask = np.zeros((h, w), dtype=np.uint8)
        persons = [d for d in detections if d['is_person']]

        if persons:
            for det in persons:
                x1, y1, x2, y2 = det['box']
                pad = 25
                x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
                x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
                mask[y1:y2, x1:x2] = 255

            blurred = cv2.GaussianBlur(frame, (25, 25), 0)
            mask_3ch = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) / 255.0
            composite = (frame * mask_3ch + blurred * (1 - mask_3ch)).astype(np.uint8)
            _, encoded = cv2.imencode('.jpg', composite, [cv2.IMWRITE_JPEG_QUALITY, 88])
        else:
            _, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])

        jpeg_bytes = encoded.tobytes()
        compressed = self.zstd_compressor.compress(jpeg_bytes)

        filename = f"event_{frame_number}_{datetime.now().strftime('%H%M%S')}.zst"
        filepath = os.path.join(self.storage_dir, "events", filename)
        with open(filepath, 'wb') as f:
            f.write(compressed)

        self.stats['original_bytes'] += original_size
        self.stats['compressed_bytes'] += len(compressed)
        self.stats['event_frames'] += 1

        return filepath, original_size, len(compressed)

    def compress_normal(self, frame, frame_number):
        """NORMAL frame (score 30-60): Standard 50% JPEG"""
        original_size = frame.nbytes
        _, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])

        self.stats['original_bytes'] += original_size
        self.stats['compressed_bytes'] += len(encoded)
        self.stats['normal_frames'] += 1

        return encoded, original_size, len(encoded)

    def compress_idle(self, frame, frame_number):
        """IDLE frame (score < 30): 15% JPEG, batch with py7zr"""
        original_size = frame.nbytes
        _, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 15])

        self.idle_batch.append((frame_number, encoded.tobytes()))
        self.stats['original_bytes'] += original_size
        self.stats['compressed_bytes'] += len(encoded)
        self.stats['idle_frames'] += 1

        if len(self.idle_batch) >= self.batch_size:
            return self._archive_idle_batch()
        return None

    def _archive_idle_batch(self):
        """Compress batch of idle frames using py7zr (maximum compression)"""
        if not self.idle_batch:
            return None

        self.batch_count += 1
        archive_name = f"idle_batch_{self.batch_count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.7z"
        archive_path = os.path.join(self.storage_dir, "idle", archive_name)

        original_total = sum(len(data) for _, data in self.idle_batch)

        with py7zr.SevenZipFile(archive_path, 'w') as archive:
            for fnum, data in self.idle_batch:
                archive.writestr(data, f"frame_{fnum}.jpg")

        compressed_size = os.path.getsize(archive_path)
        self.stats['batches_archived'] += 1
        self.idle_batch = []

        return {
            'archive_path': archive_path,
            'frames': self.batch_size,
            'original_kb': original_total / 1024,
            'compressed_kb': compressed_size / 1024,
            'ratio': round(original_total / max(compressed_size, 1), 1)
        }

    def get_savings_percent(self):
        if self.stats['original_bytes'] == 0:
            return 0
        return round(
            (1 - self.stats['compressed_bytes'] / self.stats['original_bytes']) * 100, 1
        )

    def get_savings_rupees(self, cost_per_month=40000):
        return round(cost_per_month * self.get_savings_percent() / 100)
```

---

## 2. BACKEND — `main.py` (API endpoint + WebSocket payload)

### 2a. `/api/compression-proof` — Disk scan endpoint

```python
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
```

### 2b. WebSocket payload — `compression` field (inside `/ws/live`)

```python
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
```

### 2c. Reset stats on `/api/clear`

```python
compressor.stats = {k: 0 for k in compressor.stats}
compressor.idle_batch.clear()
```

---

## 3. FRONTEND — `App.js` (State, fetching, WebSocket handler)

### 3a. State declarations

```jsx
const [compressionProof, setCompressionProof] = useState(null);
const [compressionLive, setCompressionLive] = useState(null);
```

### 3b. Fetch disk scan every 3 seconds

```jsx
const fetchCompressionProof = useCallback(async () => {
  try {
    const res = await fetch(`${API_BASE}/api/compression-proof`, { cache: 'no-store' });
    const json = await res.json();
    setCompressionProof(json);
  } catch (e) { }
}, []);

useEffect(() => {
  const interval = setInterval(fetchCompressionProof, 3000);
  fetchCompressionProof();
  return () => clearInterval(interval);
}, [fetchCompressionProof]);
```

### 3c. WebSocket handler — update live compression stats

```jsx
useEffect(() => {
  if (lastJsonMessage) {
    // ... other state updates ...

    if (lastJsonMessage.compression) {
      setCompressionLive(lastJsonMessage.compression);
    }
  }
}, [lastJsonMessage]);
```

---

## 4. FRONTEND — `App.js` (JSX Panel)

```jsx
{(compressionLive || compressionProof) && (
  <div className="panel compression-proof-panel">
    <div className="panel-header">🗜️ REAL-TIME COMPRESSION — LIVE</div>
    <div className="compression-proof-content">

      {/* ---- LIVE SESSION (from WebSocket, ~60fps updates) ---- */}
      {compressionLive && compressionLive.original_bytes > 0 && (
        <>
          <div className="compression-live-badge">⚡ LIVE SESSION</div>
          <div className="compression-hero-row">
            <div className="compression-big-stat">
              <div className="compression-big-number" style={{color:'#00ff88'}}>{compressionLive.savings_pct}%</div>
              <div className="compression-big-label">Compressed</div>
            </div>
            <div className="compression-big-stat">
              <div className="compression-big-number">{(compressionLive.original_bytes / 1048576).toFixed(1)}<span style={{fontSize:14,color:'#4a7a9b'}}>MB</span></div>
              <div className="compression-big-label">Raw Input</div>
            </div>
            <div className="compression-big-stat">
              <div className="compression-big-number">{(compressionLive.compressed_bytes / 1048576).toFixed(1)}<span style={{fontSize:14,color:'#4a7a9b'}}>MB</span></div>
              <div className="compression-big-label">After Compression</div>
            </div>
            <div className="compression-big-stat">
              <div className="compression-big-number">{compressionLive.event_frames + compressionLive.idle_frames + compressionLive.normal_frames}</div>
              <div className="compression-big-label">Frames Processed</div>
            </div>
          </div>

          {/* Progress bars */}
          <div className="compression-comparison">
            <div className="compression-bar-group">
              <div className="compression-bar-label">RAW INPUT ({(compressionLive.original_bytes / 1048576).toFixed(1)} MB)</div>
              <div className="compression-bar-track">
                <div className="compression-bar-fill compression-bar-raw" style={{width:'100%'}} />
              </div>
            </div>
            <div className="compression-bar-group">
              <div className="compression-bar-label">AFTER COMPRESSION ({(compressionLive.compressed_bytes / 1048576).toFixed(1)} MB)</div>
              <div className="compression-bar-track">
                <div className="compression-bar-fill compression-bar-compressed" style={{width: `${Math.max(2, (compressionLive.compressed_bytes / Math.max(compressionLive.original_bytes,1)) * 100)}%`}} />
              </div>
            </div>
          </div>

          {/* Per-category cards */}
          <div className="compression-categories">
            {compressionLive.event_frames > 0 && (
              <div className="compression-cat-card">
                <div className="compression-cat-icon">🔴</div>
                <div className="compression-cat-name">EVENT</div>
                <div className="compression-cat-detail">{compressionLive.event_frames} frames</div>
                <div className="compression-cat-detail">ROI JPEG + zstd</div>
              </div>
            )}
            {compressionLive.idle_frames > 0 && (
              <div className="compression-cat-card">
                <div className="compression-cat-icon">🟢</div>
                <div className="compression-cat-name">IDLE</div>
                <div className="compression-cat-detail">{compressionLive.idle_frames} frames</div>
                <div className="compression-cat-detail">15% JPEG + 7z batch</div>
                <div className="compression-cat-detail">{compressionLive.batches_archived} archives</div>
              </div>
            )}
            {compressionLive.normal_frames > 0 && (
              <div className="compression-cat-card">
                <div className="compression-cat-icon">🟡</div>
                <div className="compression-cat-name">NORMAL</div>
                <div className="compression-cat-detail">{compressionLive.normal_frames} frames</div>
                <div className="compression-cat-detail">50% JPEG</div>
              </div>
            )}
          </div>
        </>
      )}

      {/* ---- ON DISK — scanned every 3s ---- */}
      {compressionProof && (
        <>
          <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:8}}>
            <div className="compression-live-badge" style={{background:'#1a2a3a',color:'#4a7a9b',marginBottom:0}}>
              📁 ON DISK — ALL SESSIONS (auto-refreshes every 3s)
            </div>
            <button
              onClick={fetchCompressionProof}
              title="Refresh now"
              style={{background:'#0d2d50',border:'1px solid #00d4ff33',color:'#00d4ff',borderRadius:6,padding:'2px 8px',cursor:'pointer',fontSize:11}}
            >
              ↻ Refresh
            </button>
            <span style={{fontSize:10,color:'#4a7a9b',marginLeft:'auto'}}>
              {compressionProof.timestamp ? new Date(compressionProof.timestamp).toLocaleTimeString('en-IN',{hour12:false}) : ''}
            </span>
          </div>

          {compressionProof.total_frames_compressed > 0 ? (
            <div className="compression-hero-row">
              <div className="compression-big-stat">
                <div className="compression-big-number" style={{color:'#00d4ff',fontSize:22}}>{compressionProof.overall_compression_ratio}</div>
                <div className="compression-big-label">Ratio</div>
              </div>
              <div className="compression-big-stat">
                <div className="compression-big-number" style={{color:'#00ff88',fontSize:22}}>{compressionProof.overall_savings_percent}%</div>
                <div className="compression-big-label">Saved</div>
              </div>
              <div className="compression-big-stat">
                <div className="compression-big-number" style={{fontSize:22}}>{compressionProof.total_frames_compressed.toLocaleString()}</div>
                <div className="compression-big-label">Frames</div>
              </div>
              <div className="compression-big-stat">
                <div className="compression-big-number" style={{fontSize:22}}>
                  {(compressionProof.total_raw_kb/1024).toFixed(0)}<span style={{fontSize:12,color:'#4a7a9b'}}>MB</span>
                  {' → '}
                  {(compressionProof.total_compressed_kb/1024).toFixed(0)}<span style={{fontSize:12,color:'#4a7a9b'}}>MB</span>
                </div>
                <div className="compression-big-label">Raw → Compressed</div>
              </div>
            </div>
          ) : (
            <div style={{color:'#4a7a9b',fontSize:12,padding:'8px 0'}}>No compressed frames on disk yet — start a camera session</div>
          )}

          {/* Per-folder storage breakdown table */}
          <div className="disk-storage-grid">
            {[
              {key:'events',    label:'Events',     icon:'🔴', ext:'.zst'},
              {key:'idle',      label:'Idle',       icon:'🟢', ext:'.7z batch'},
              {key:'normal',    label:'Normal',     icon:'🟡', ext:'.jpg'},
              {key:'clips',     label:'Clips',      icon:'🎬', ext:'.mp4'},
              {key:'prebuffer', label:'Pre-Buffer', icon:'📼', ext:'.avi'},
            ].map(({key,label,icon,ext}) => {
              const v = compressionProof.categories?.[key];
              if (!v) return null;
              return (
                <div key={key} className="disk-storage-row">
                  <span className="disk-row-icon">{icon}</span>
                  <span className="disk-row-label">{label}</span>
                  <span className="disk-row-ext">{ext}</span>
                  <span className="disk-row-files">{v.files} file{v.files !== 1 ? 's' : ''}</span>
                  <span className="disk-row-size" style={{color: v.size_kb > 0 ? '#00d4ff' : '#4a7a9b'}}>
                    {v.size_kb >= 1024 ? `${(v.size_kb/1024).toFixed(1)} MB` : `${v.size_kb} KB`}
                  </span>
                  {v.savings_pct > 0 && (
                    <span className="disk-row-savings">{v.savings_pct}% saved</span>
                  )}
                </div>
              );
            })}
            <div className="disk-storage-row disk-total-row">
              <span className="disk-row-icon">💾</span>
              <span className="disk-row-label">TOTAL ON DISK</span>
              <span className="disk-row-ext"></span>
              <span className="disk-row-files"></span>
              <span className="disk-row-size" style={{color:'#ffd93d',fontWeight:700}}>
                {compressionProof.total_disk_kb >= 1024
                  ? `${(compressionProof.total_disk_kb/1024).toFixed(1)} MB`
                  : `${compressionProof.total_disk_kb} KB`}
              </span>
            </div>
          </div>
        </>
      )}

    </div>
  </div>
)}
```

---

## 5. FRONTEND — `App.css` (All compression styles)

```css
/* ==== Compression Proof Panel ==== */
.compression-proof-panel {
  border: 1px solid #00d4ff33;
  background: linear-gradient(135deg, #0a1828 0%, #0d2137 100%);
}

.compression-proof-content {
  padding: 12px;
}

.compression-live-badge {
  display: inline-block;
  background: #00ff8822;
  color: #00ff88;
  font-size: 10px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 12px;
  margin-bottom: 10px;
  letter-spacing: 1px;
  text-transform: uppercase;
  animation: pulse-badge 2s ease infinite;
}

@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.compression-hero-row {
  display: flex;
  gap: 16px;
  justify-content: space-around;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.compression-big-stat {
  text-align: center;
  min-width: 100px;
}

.compression-big-number {
  font-size: 28px;
  font-weight: 900;
  color: #00d4ff;
  font-family: 'Courier New', monospace;
  line-height: 1.1;
}

.compression-big-label {
  font-size: 10px;
  color: #4a7a9b;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-top: 4px;
}

.compression-comparison {
  background: #0a1520;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
  border: 1px solid #0d2d5044;
}

.compression-bar-group {
  margin-bottom: 8px;
}

.compression-bar-group:last-child {
  margin-bottom: 0;
}

.compression-bar-label {
  font-size: 10px;
  color: #6b9cc0;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.compression-bar-track {
  height: 28px;
  background: #0d1e30;
  border-radius: 4px;
  overflow: hidden;
}

.compression-bar-fill {
  height: 100%;
  display: flex;
  align-items: center;
  padding: 0 8px;
  font-size: 11px;
  font-weight: 700;
  font-family: 'Courier New', monospace;
  border-radius: 4px;
  transition: width 1s ease;
}

.compression-bar-raw {
  background: linear-gradient(90deg, #ff4d4d, #ff6b6b);
  color: #fff;
}

.compression-bar-compressed {
  background: linear-gradient(90deg, #00cc66, #00ff88);
  color: #000;
}

.compression-categories {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.compression-cat-card {
  flex: 1;
  min-width: 120px;
  background: #0a1520;
  border: 1px solid #0d2d5044;
  border-radius: 8px;
  padding: 10px;
  text-align: center;
}

.compression-cat-icon {
  font-size: 20px;
  margin-bottom: 4px;
}

.compression-cat-name {
  font-size: 12px;
  font-weight: 700;
  color: #00d4ff;
  margin-bottom: 4px;
}

.compression-cat-detail {
  font-size: 10px;
  color: #4a7a9b;
  line-height: 1.5;
}

.compression-cat-savings {
  font-size: 14px;
  font-weight: 900;
  color: #00ff88;
  margin-top: 4px;
  font-family: 'Courier New', monospace;
}

/* Disk storage breakdown grid */
.disk-storage-grid {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 10px;
  background: #0a1520;
  border-radius: 8px;
  padding: 10px;
  border: 1px solid #0d2d5044;
}

.disk-storage-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.disk-storage-row:hover {
  background: #0d2d5033;
}

.disk-total-row {
  border-top: 1px solid #0d2d5066;
  margin-top: 4px;
  padding-top: 8px;
}

.disk-row-icon    { font-size: 14px; flex-shrink: 0; }
.disk-row-label   { color: #a0c8e0; font-weight: 600; min-width: 80px; }
.disk-row-ext     { color: #4a7a9b; font-size: 10px; flex: 1; }
.disk-row-files   { color: #6b9cc0; min-width: 60px; text-align: right; }
.disk-row-size    { font-family: 'Courier New', monospace; font-weight: 700; min-width: 70px; text-align: right; }
.disk-row-savings { color: #00ff88; font-size: 10px; min-width: 70px; text-align: right; }
```

---

## How It All Connects

```
Camera frame
     │
     ▼
DualCompressor (compressor.py)
  ├── compress_event()  → ROI JPEG + zstd → storage/events/*.zst
  ├── compress_normal() → 50% JPEG        → storage/compressed/*.jpg
  └── compress_idle()   → 15% JPEG batch  → storage/idle/*.7z
         │
         └── updates compressor.stats (original_bytes, compressed_bytes, etc.)

FastAPI (main.py)
  ├── /ws/live          → pushes compressor.stats every ~66ms  → compressionLive (React state)
  └── /api/compression-proof → scans all folders on disk       → compressionProof (React state)

React (App.js)
  ├── LIVE SESSION section ← compressionLive  (WebSocket, real-time)
  └── ON DISK section      ← compressionProof (poll every 3s + Refresh button)
```
