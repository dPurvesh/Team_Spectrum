import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import "./App.css";

const WS_URL = "ws://localhost:8000/ws/live";
const API_BASE = "http://localhost:8000";

const CAMERA_COLORS = [
  "#00d4ff",
  "#ff6b6b",
  "#ffd93d",
  "#6bff6b",
  "#ff6bff",
  "#ff9f43",
];

/* Error Boundary to catch React rendering errors */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    console.error("React Error Boundary caught:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            padding: 40,
            color: "#ff4d4d",
            background: "#071428",
            minHeight: "100vh",
            fontFamily: "monospace",
          }}
        >
          <h2>⚠️ Dashboard Error</h2>
          <p>{this.state.error?.message || "Unknown error"}</p>
          <button
            onClick={() => {
              this.setState({ hasError: false });
              window.location.reload();
            }}
            style={{
              padding: "8px 16px",
              background: "#00d4ff",
              color: "#000",
              border: "none",
              cursor: "pointer",
              borderRadius: 4,
            }}
          >
            🔄 Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const PIPELINE_STAGES = [
  { key: "cam", icon: "📷", label: "CAMERA" },
  { key: "snn", icon: "🧠", label: "SNN GATE" },
  { key: "yolo", icon: "🎯", label: "YOLOv8" },
  { key: "anomaly", icon: "🔍", label: "ANOMALY" },
  { key: "score", icon: "📊", label: "SCORING" },
  { key: "compress", icon: "🗜️", label: "COMPRESS" },
  { key: "db", icon: "🗄️", label: "FORENSIC" },
  { key: "dash", icon: "📡", label: "DASHBOARD" },
];

/* ============================================================
   CameraFeedPanel — Renders one camera's live feed + overlays
   ============================================================ */
function CameraFeedPanel({ camId, camData, onStop, clientFps }) {
  const imgRef = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isDimmed, setIsDimmed] = useState(false);
  const lastActiveTime = useRef(Date.now());
  const IDLE_TIMEOUT = 10000; // 10 seconds before dimming

  const score = camData?.score || 0;
  const category = camData?.category || "IDLE";
  const detections = camData?.detections || [];
  const snnSpike = camData?.snn_spike || false;
  const snnMembrane = camData?.snn_membrane || 0;
  // Use backend FPS if available, otherwise client-counted FPS
  const fps = camData?.fps && camData.fps > 0 ? camData.fps : clientFps || 0;

  const getScoreColor = (s) => {
    if (s > 60) return "#00ff88";
    if (s > 30) return "#ffaa00";
    return "#ff4d4d";
  };

  const getCategoryLabel = (cat) => {
    if (cat === "EVENT") return "🔴 EVENT";
    if (cat === "NORMAL") return "🟡 NORMAL";
    return "🟢 IDLE";
  };

  // Close fullscreen on Escape
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") setIsFullscreen(false);
    };
    if (isFullscreen) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isFullscreen]);

  // Time-based idle detection: No spike AND score < 30 for 10+ seconds
  useEffect(() => {
    const isActive = snnSpike || score >= 30;

    if (isActive) {
      // Activity detected - reset timer and immediately un-dim
      lastActiveTime.current = Date.now();
      setIsDimmed(false);
    }

    // Check idle status every second
    const interval = setInterval(() => {
      const idleDuration = Date.now() - lastActiveTime.current;
      if (idleDuration >= IDLE_TIMEOUT) {
        setIsDimmed(true);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [snnSpike, score]);

  return (
    <div
      className={`cam-feed-panel ${category === "EVENT" ? "cam-event" : category === "NORMAL" ? "cam-normal" : ""} ${isFullscreen ? "cam-fullscreen" : ""} ${isDimmed ? "cam-idle" : ""}`}
    >
      <div className="cam-feed-header">
        <div className="cam-feed-title">
          <span className="live-dot"></span>
          <span>{camId.toUpperCase().replace("_", " ")}</span>
          <span className="cam-fps-badge">{fps} FPS</span>
          <span className={`cam-spike-badge ${snnSpike ? "spike-active" : ""}`}>
            {snnSpike ? "⚡ SPIKE" : "— SKIP"}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button
            className="cam-fullscreen-btn"
            onClick={() => setIsFullscreen((f) => !f)}
            title={isFullscreen ? "Exit fullscreen (Esc)" : "Fullscreen"}
          >
            {isFullscreen ? "⊡" : "⛶"}
          </button>
          <button className="cam-stop-btn" onClick={() => onStop(camId)}>
            ⏹ STOP
          </button>
        </div>
      </div>

      <div
        className={`cam-feed-container ${category === "IDLE" ? "feed-idle" : category === "EVENT" ? "feed-event" : ""}`}
      >
        {camData?.frame ? (
          <img
            ref={imgRef}
            src={`data:image/jpeg;base64,${camData.frame}`}
            alt={`${camId} Feed`}
            className={`live-feed-img ${isDimmed ? "feed-dim" : ""}`}
          />
        ) : (
          <div className="feed-placeholder">⏳ Connecting to {camId}...</div>
        )}

        {/* Score overlay */}
        <div
          className="score-overlay"
          style={{ borderColor: getScoreColor(score) }}
        >
          <div className="score-big" style={{ color: getScoreColor(score) }}>
            {score}
          </div>
          <div className="score-label-small">/100</div>
          <div className="score-cat">{getCategoryLabel(category)}</div>
        </div>

        {/* SNN membrane bar */}
        <div className="membrane-bar-container">
          <div className="membrane-bar-label">SNN</div>
          <div className="membrane-bar-track">
            <div
              className={`membrane-bar-fill ${snnSpike ? "spiked" : ""}`}
              style={{
                width: `${Math.min((snnMembrane / (camData?.snn_threshold || 0.15)) * 100, 100)}%`,
              }}
            />
          </div>
          <div className="membrane-bar-value">{snnMembrane.toFixed(3)}</div>
        </div>
      </div>

      {/* Detection badges */}
      {detections.length > 0 && (
        <div className="cam-det-badges">
          {detections.slice(0, 5).map((det, i) => (
            <span
              key={i}
              className={`cam-det-badge ${det.is_person ? "det-person" : "det-object"}`}
            >
              {det.class} {(det.conf * 100).toFixed(0)}%
            </span>
          ))}
        </div>
      )}
    </div>
  );
}


function App() {
  const [data, setData] = useState(null);
  const [camerasData, setCamerasData] = useState({});
  const [scoreHistory, setScoreHistory] = useState({});
  const [events, setEvents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [clips, setClips] = useState([]);
  const [prebufferClips, setPrebufferClips] = useState([]);
  const [clipDateFilter, setClipDateFilter] = useState("");
  const [videoModal, setVideoModal] = useState(null); // { url, title, type }
  const [spikeTrainHistory, setSpikeTrainHistory] = useState([]);
  const [availableCameras, setAvailableCameras] = useState([]);
  const [activeCamIds, setActiveCamIds] = useState([]);
  const [cameraLoading, setCameraLoading] = useState({});
  const [detecting, setDetecting] = useState(false);
  const [detectMessage, setDetectMessage] = useState(""); // Status message during detection
  const [backendAlive, setBackendAlive] = useState(false);
  const [uptime, setUptime] = useState(0);
  const [sessionName, setSessionName] = useState("");
  const [currentTime, setCurrentTime] = useState(new Date());
  const uptimeRef = useRef(null);
  const pollPauseRef = useRef(false);
  const fpsCounterRef = useRef({}); // Per-camera FPS counters
  const [perCameraFps, setPerCameraFps] = useState({}); // Per-camera FPS values
  const [cameraStartTimes, setCameraStartTimes] = useState({}); // Track when each camera started
  const cameraStartTimesRef = useRef({}); // Ref for use in callbacks

  // Network Camera Modal state
  const [showNetworkModal, setShowNetworkModal] = useState(false);
  const [networkCamForm, setNetworkCamForm] = useState({
    camera_id: "cam_network",
    ip_address: "",
    port: "4747",
    path: "/video",
  });
  const [networkConnecting, setNetworkConnecting] = useState(false);

  // Clear confirmation modal state
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [clearTarget, setClearTarget] = useState(null); // 'clips', 'prebuffer', 'events', 'all'

  // Compression system state
  const [compressionProof, setCompressionProof] = useState(null);
  const [compressionLive, setCompressionLive] = useState(null);
  // Baseline captured at WS connect time so stats reset on browser refresh
  const compressionBaselineRef = useRef(null);

  // Keep ref in sync with state
  useEffect(() => {
    cameraStartTimesRef.current = cameraStartTimes;
  }, [cameraStartTimes]);

  const { lastJsonMessage, readyState } = useWebSocket(WS_URL, {
    shouldReconnect: () => true,
    reconnectInterval: 2000,
  });

  const wsConnected = readyState === ReadyState.OPEN;

  // ---- Client-side FPS counter (per-camera) ----
  useEffect(() => {
    const t = setInterval(() => {
      // Only update FPS for active cameras
      if (activeCamIds.length > 0) {
        const newFps = {};
        for (const camId of activeCamIds) {
          newFps[camId] = fpsCounterRef.current[camId] || 0;
          fpsCounterRef.current[camId] = 0;
        }
        setPerCameraFps(newFps);
      } else {
        // Reset all FPS when no cameras active
        fpsCounterRef.current = {};
        setPerCameraFps({});
      }
    }, 1000);
    return () => clearInterval(t);
  }, [activeCamIds]);

  // Reset FPS counters when cameras change
  useEffect(() => {
    // Clean up FPS for cameras that were removed
    const currentIds = new Set(activeCamIds);
    fpsCounterRef.current = Object.fromEntries(
      Object.entries(fpsCounterRef.current).filter(([k]) => currentIds.has(k)),
    );
    setPerCameraFps((prev) =>
      Object.fromEntries(
        Object.entries(prev).filter(([k]) => currentIds.has(k)),
      ),
    );
  }, [activeCamIds]);

  // ---- Live clock ----
  useEffect(() => {
    const t = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // ---- Uptime counter ----
  useEffect(() => {
    if (activeCamIds.length > 0) {
      uptimeRef.current = setInterval(() => setUptime((u) => u + 1), 1000);
    } else {
      clearInterval(uptimeRef.current);
      setUptime(0);
    }
    return () => clearInterval(uptimeRef.current);
  }, [activeCamIds.length]);

  const formatUptime = (s) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
  };

  const formatDate = (d) =>
    d.toLocaleDateString("en-IN", {
      weekday: "short",
      day: "2-digit",
      month: "short",
      year: "numeric",
    });

  const formatTime = (d) => d.toLocaleTimeString("en-IN", { hour12: false });

  // ---- Poll camera status ----
  useEffect(() => {
    const fetchStatus = async () => {
      if (pollPauseRef.current) return;
      try {
        const res = await fetch(`${API_BASE}/api/camera/status`);
        const json = await res.json();
        const running = json.cameras
          ? Object.entries(json.cameras)
              .filter(([, v]) => v.running)
              .map(([k]) => k)
          : [];
        setActiveCamIds(running);
        setBackendAlive(true);
      } catch (e) {
        setBackendAlive(false);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, []);

  // ---- Detect available cameras ----
  const detectCameras = useCallback(async (forceRefresh = false) => {
    setDetecting(true);
    setDetectMessage("Scanning...");

    // Helper: fetch with timeout
    const fetchWithTimeout = async (url, timeoutMs = 10000) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(url, { signal: controller.signal });
        clearTimeout(timeoutId);
        return res;
      } catch (e) {
        clearTimeout(timeoutId);
        throw e;
      }
    };

    // Show extended message after 3 seconds
    const slowTimer = setTimeout(() => {
      setDetectMessage("Still scanning... (checking hardware)");
    }, 3000);

    const doFetch = async (refresh) => {
      const url = `${API_BASE}/api/cameras/detect${refresh ? "?refresh=true" : ""}`;
      const res = await fetchWithTimeout(url, 12000);
      return res.json();
    };

    try {
      let json = await doFetch(forceRefresh);

      // Auto-retry once with force refresh if no cameras found
      if ((!json.cameras || json.cameras.length === 0) && !forceRefresh) {
        setDetectMessage("No cameras found, retrying...");
        await new Promise((r) => setTimeout(r, 500));
        json = await doFetch(true);
      }

      setAvailableCameras(json.cameras || []);
      if (json.cameras?.length === 0) {
        setDetectMessage("No cameras detected");
        setTimeout(() => setDetectMessage(""), 3000);
      } else {
        setDetectMessage("");
      }
    } catch (e) {
      console.error("Camera detection failed:", e);
      setDetectMessage(
        e.name === "AbortError" ? "Detection timed out" : "Detection failed",
      );
      setTimeout(() => setDetectMessage(""), 3000);
    }

    clearTimeout(slowTimer);
    setDetecting(false);
  }, []);

  // Auto-detect on mount
  useEffect(() => {
    if (backendAlive) detectCameras();
  }, [backendAlive, detectCameras]);

  // ---- Start a camera ----
  const startCamera = useCallback(
    async (source) => {
      setCameraLoading((prev) => ({ ...prev, [source]: true }));
      pollPauseRef.current = true;
      setTimeout(() => {
        pollPauseRef.current = false;
      }, 5000);
      try {
        const name =
          sessionName ||
          `Demo_${new Date().toLocaleTimeString("en-IN", { hour12: false }).replace(/:/g, "")}`;
        const res = await fetch(
          `${API_BASE}/api/camera/start?source=${source}&session_name=${encodeURIComponent(name)}`,
          { method: "POST" },
        );
        const json = await res.json();
        if (json.status === "started" || json.status === "already_running") {
          setActiveCamIds((prev) => [...new Set([...prev, json.cam_id])]);
          setBackendAlive(true);
          // Track camera start time for elapsed display
          setCameraStartTimes((prev) => ({
            ...prev,
            [json.cam_id]: prev[json.cam_id] || Date.now(),
          }));
        }
      } catch (e) {}
      setCameraLoading((prev) => ({ ...prev, [source]: false }));
    },
    [sessionName],
  );

  // ---- Stop a camera ----
  const stopCamera = useCallback(async (camId) => {
    pollPauseRef.current = true;
    setTimeout(() => {
      pollPauseRef.current = false;
    }, 5000);
    try {
      await fetch(`${API_BASE}/api/camera/stop?cam_id=${camId}`, {
        method: "POST",
      });
      setActiveCamIds((prev) => prev.filter((id) => id !== camId));
      // Clear camera start time
      setCameraStartTimes((prev) => {
        const updated = { ...prev };
        delete updated[camId];
        return updated;
      });
    } catch (e) {}
  }, []);

  // ---- Stop all ----
  const stopAll = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/camera/stop_all`, { method: "POST" });
      setActiveCamIds([]);
    } catch (e) {}
  }, []);

  // ---- Clear session ----
  const clearSession = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/session/clear`, { method: "POST" });
      setScoreHistory({});
      setEvents([]);
      setAlerts([]);
      setClips([]);
      setCameraStartTimes({});
    } catch (e) {}
  }, []);

  // ---- Connect Network Camera ----
  const connectNetworkCamera = useCallback(async () => {
    if (!networkCamForm.ip_address || !networkCamForm.port) {
      return;
    }
    setNetworkConnecting(true);
    try {
      const params = new URLSearchParams({
        camera_id: networkCamForm.camera_id || "cam_network",
        ip_address: networkCamForm.ip_address,
        port: networkCamForm.port,
        path: networkCamForm.path || "/video",
      });
      const res = await fetch(`${API_BASE}/api/cameras/connect?${params}`, {
        method: "POST",
      });
      const json = await res.json();
      if (json.status === "started" || json.status === "already_running") {
        const camId = json.cam_id;
        setActiveCamIds((prev) => [...new Set([...prev, camId])]);
        setCameraStartTimes((prev) => ({
          ...prev,
          [camId]: prev[camId] || Date.now(),
        }));
        setShowNetworkModal(false);
        // Reset form for next use
        setNetworkCamForm((prev) => ({
          ...prev,
          camera_id: `cam_network_${Date.now() % 1000}`,
          ip_address: "",
        }));
      }
    } catch (e) {
      console.error("Failed to connect network camera:", e);
    }
    setNetworkConnecting(false);
  }, [networkCamForm]);

  // ---- Clear specific data ----
  const clearForensicEvents = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/events/clear`, { method: "DELETE" });
      setEvents([]);
    } catch (e) {}
  }, []);

  const clearAlerts = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/alerts/clear`, { method: "DELETE" });
      setAlerts([]);
    } catch (e) {}
  }, []);

  const clearAllLogs = useCallback(async () => {
    try {
      // Clear events and alerts only (not clips/videos)
      await fetch(`${API_BASE}/api/events/clear`, { method: "DELETE" });
      await fetch(`${API_BASE}/api/alerts/clear`, { method: "DELETE" });
      setScoreHistory({});
      setEvents([]);
      setAlerts([]);
      setCameraStartTimes({});
    } catch (e) {}
  }, []);

  const handleClearConfirm = useCallback(() => {
    if (clearTarget === "events") clearForensicEvents();
    else if (clearTarget === "alerts") clearAlerts();
    else if (clearTarget === "all") clearAllLogs();
    setShowClearConfirm(false);
    setClearTarget(null);
  }, [
    clearTarget,
    clearForensicEvents,
    clearAlerts,
    clearAllLogs,
  ]);

  const promptClear = useCallback((target) => {
    setClearTarget(target);
    setShowClearConfirm(true);
  }, []);

  // ---- Fetch events ----
  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/events?limit=25`);
        const json = await res.json();
        setEvents(json.events || []);
      } catch (e) {}
    };
    const interval = setInterval(fetchEvents, 3000);
    fetchEvents();
    return () => clearInterval(interval);
  }, []);

  // ---- Fetch alerts ----
  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/alerts?unacknowledged_only=true`,
        );
        const json = await res.json();
        setAlerts(json.alerts || []);
      } catch (e) {}
    };
    const interval = setInterval(fetchAlerts, 2000);
    fetchAlerts();
    return () => clearInterval(interval);
  }, []);

  // ---- Fetch clips (sorted newest-first by start_time) ----
  useEffect(() => {
    const fetchClips = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/clips`, { cache: "no-store" });
        const json = await res.json();
        const sorted = (json.clips || []).sort((a, b) => {
          const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
          const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
          return tb - ta; // newest first
        });
        setClips(sorted);
      } catch (e) {}
    };
    const interval = setInterval(fetchClips, 5000);
    fetchClips();
    return () => clearInterval(interval);
  }, []);

  // ---- Fetch prebuffer recordings (sorted newest-first by start_time) ----
  useEffect(() => {
    const fetchPB = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/prebuffer`, {
          cache: "no-store",
        });
        const json = await res.json();
        const sorted = (json.prebuffer || []).sort((a, b) => {
          const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
          const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
          return tb - ta; // newest first
        });
        setPrebufferClips(sorted);
      } catch (e) {}
    };
    const interval = setInterval(fetchPB, 5000);
    fetchPB();
    return () => clearInterval(interval);
  }, []);

  // ---- Fetch compression-proof disk scan (ONLY while camera is active) ----
  const fetchCompressionProof = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/compression-proof`, { cache: 'no-store' });
      const json = await res.json();
      setCompressionProof(json);
    } catch (e) { }
  }, []);

  // Only poll ON DISK stats when at least one camera is active
  useEffect(() => {
    if (activeCamIds.length === 0) {
      // No cameras active: clear ON DISK section and reset live baseline
      setCompressionProof(null);
      compressionBaselineRef.current = null;
      return;
    }
    // Camera is active: poll every 3s
    fetchCompressionProof();
    const interval = setInterval(fetchCompressionProof, 3000);
    return () => clearInterval(interval);
  }, [activeCamIds.length, fetchCompressionProof]);

  // ---- Process WebSocket data ----
  useEffect(() => {
    if (lastJsonMessage) {
      // Increment per-camera FPS counters only for active cameras
      if (lastJsonMessage.cameras && activeCamIds.length > 0) {
        for (const camId of activeCamIds) {
          if (lastJsonMessage.cameras[camId]) {
            fpsCounterRef.current[camId] =
              (fpsCounterRef.current[camId] || 0) + 1;
          }
        }
      }
      setData(lastJsonMessage);
      setBackendAlive(true);

      // Store per-camera data
      if (lastJsonMessage.cameras) {
        setCamerasData(lastJsonMessage.cameras);

        // Update per-camera score history with elapsed time
        const now = Date.now();
        setScoreHistory((prev) => {
          const updated = { ...prev };
          for (const [camId, cd] of Object.entries(lastJsonMessage.cameras)) {
            const rawScore = cd.score || 0;
            // Store score with 2 decimal precision
            const preciseScore =
              typeof rawScore === "number"
                ? parseFloat(rawScore.toFixed(2))
                : 0;
            // Calculate elapsed time from camera start
            const startTime = cameraStartTimesRef.current[camId] || now;
            const elapsed = Math.floor((now - startTime) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            const timeStr = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
            const camHist = [
              ...(updated[camId] || []),
              {
                time: timeStr,
                timestamp: now,
                score: preciseScore,
                spike: cd.snn_spike ? 100 : 0,
              },
            ];
            updated[camId] = camHist.slice(-120);
          }
          return updated;
        });

        // Update spike train history (last 60 data points)
        setSpikeTrainHistory((prev) => {
          const entry = { time: Date.now() };
          for (const [camId, cd] of Object.entries(lastJsonMessage.cameras)) {
            entry[camId] = cd.snn_spike ? 1 : 0;
            entry[`diff_${camId}`] = cd.snn_diff || 0;
            entry[`membrane_${camId}`] = cd.snn_membrane || 0;
          }
          return [...prev, entry].slice(-60);
        });
      }

      // Update live compression stats — subtract baseline so refresh = starts at 0
      if (lastJsonMessage.compression) {
        const raw = lastJsonMessage.compression;
        if (!compressionBaselineRef.current) {
          // First message after connect: capture baseline
          compressionBaselineRef.current = {
            original_bytes:   raw.original_bytes,
            compressed_bytes: raw.compressed_bytes,
            event_frames:     raw.event_frames,
            idle_frames:      raw.idle_frames,
            normal_frames:    raw.normal_frames,
            batches_archived: raw.batches_archived,
          };
        }
        const b = compressionBaselineRef.current;
        const origDelta = Math.max(0, raw.original_bytes   - b.original_bytes);
        const compDelta = Math.max(0, raw.compressed_bytes - b.compressed_bytes);
        setCompressionLive({
          ...raw,
          original_bytes:   origDelta,
          compressed_bytes: compDelta,
          event_frames:     Math.max(0, raw.event_frames    - b.event_frames),
          idle_frames:      Math.max(0, raw.idle_frames     - b.idle_frames),
          normal_frames:    Math.max(0, raw.normal_frames   - b.normal_frames),
          batches_archived: Math.max(0, raw.batches_archived - b.batches_archived),
          savings_pct: origDelta > 0
            ? parseFloat(((1 - compDelta / origDelta) * 100).toFixed(1))
            : 0,
        });
      }
    }
  }, [lastJsonMessage, activeCamIds]);

  // ---- Helpers ----
  const getScoreColor = (score) => {
    if (score > 60) return "#00ff88";
    if (score > 30) return "#ffaa00";
    return "#ff4d4d";
  };

  const getCategoryLabel = (cat) => {
    if (cat === "EVENT") return "🔴 EVENT";
    if (cat === "NORMAL") return "🟡 NORMAL";
    return "🟢 IDLE";
  };

  const getCategoryClass = (cat) => {
    if (cat === "EVENT") return "severity-critical";
    if (cat === "NORMAL") return "severity-high";
    return "severity-low";
  };

  // Aggregate bandwidth stats across all cameras
  const bandwidthStats = useMemo(() => {
    let totalFrames = 0,
      spikeFrames = 0,
      skippedFrames = 0;
    const perCam = [];
    for (const camId of activeCamIds) {
      const cd = camerasData[camId];
      if (cd) {
        totalFrames += cd.frame_count || 0;
        spikeFrames += cd.spike_count || 0;
        skippedFrames += cd.frames_skipped || 0;
        perCam.push({ camId, savings: cd.compute_savings || 0 });
      }
    }
    const savings =
      totalFrames > 0 ? ((skippedFrames / totalFrames) * 100).toFixed(1) : 0;
    return { totalFrames, spikeFrames, skippedFrames, savings, perCam };
  }, [camerasData, activeCamIds]);

  // Aggregate stats from first camera for backward compat
  const firstCamId = activeCamIds[0] || "";
  const firstCamData = (firstCamId && camerasData[firstCamId]) || data || {};
  const detections = firstCamData.detections || [];
  const snnSpike = firstCamData.snn_spike || false;
  const activeCount = data?.active_count || 0;

  // Merge all cameras into a single chart dataset
  const chartCamIds =
    activeCamIds.length > 0 ? activeCamIds : Object.keys(scoreHistory);
  const mergedChartData = useMemo(() => {
    if (chartCamIds.length === 0) return [];
    let maxLen = 0;
    for (const id of chartCamIds) {
      maxLen = Math.max(maxLen, (scoreHistory[id] || []).length);
    }
    const merged = [];
    for (let i = 0; i < maxLen; i++) {
      const entry = {};
      for (const camId of chartCamIds) {
        const hist = scoreHistory[camId] || [];
        if (hist[i]) {
          entry.time = entry.time || hist[i].time;
          entry[`score_${camId}`] = hist[i].score;
        }
      }
      if (entry.time) merged.push(entry);
    }
    return merged;
  }, [scoreHistory, chartCamIds]);

  return (
    <div className="app">
      {/* ======== HEADER ======== */}
      <header className="header" style={{ position: 'relative', textAlign: 'center' }}>
        {/* Clock — absolutely positioned top-right so it doesn't affect centering */}
        <div className="header-clock" style={{ position: 'absolute', top: 0, right: 0, textAlign: 'right' }}>
          <div className="clock-date">{formatDate(currentTime)}</div>
          <div className="clock-time">{formatTime(currentTime)}</div>
        </div>
        <h1 style={{ margin: '0 0 4px' }}>
          EDGEVID <span className="accent">LOWBAND</span>
        </h1>
        <p className="subtitle">
          NEUROMORPHIC EDGE-AI DVR — MULTI-CAMERA SURVEILLANCE
        </p>
      </header>


      {/* ======== PIPELINE INDICATOR ======== */}
      <div className="pipeline-bar">
        {PIPELINE_STAGES.map((stage, i) => {
          let stepClass = "idle";
          if (activeCamIds.length > 0) {
            if (stage.key === "snn" && snnSpike) stepClass = "active spiking";
            else if (stage.key === "yolo" && snnSpike && detections.length > 0)
              stepClass = "active detecting";
            else if (stage.key === "anomaly" && data?.active_tracks > 0)
              stepClass = "active tracking";
            else stepClass = "active";
          }
          return (
            <React.Fragment key={stage.key}>
              <div className={`pipeline-step ${stepClass}`}>
                <span>{stage.icon}</span>
                <span>{stage.label}</span>
              </div>
              {i < PIPELINE_STAGES.length - 1 && (
                <span
                  className={`pipeline-arrow ${activeCamIds.length > 0 ? "flowing" : ""}`}
                >
                  →
                </span>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* ======== TOP METRICS ======== */}
      <div className="metrics-bar">
        <div className="metric-item">
          <div className="metric-value cyan">
            {activeCount}{" "}
            <span style={{ fontSize: 11, opacity: 0.6 }}>
              CAM{activeCount !== 1 ? "S" : ""}
            </span>
          </div>
          <div className="metric-label">📷 ACTIVE</div>
        </div>
        <div className="metric-item">
          <div className={`metric-value ${snnSpike ? "green pulse" : "red"}`}>
            {snnSpike ? "⚡ SPIKE" : "— SKIP"}
          </div>
          <div className="metric-label">🧠 SNN GATE</div>
        </div>
        {/* Dynamic FPS per camera */}
        {activeCamIds.length === 0 ? (
          <div className="metric-item">
            <div className="metric-value cyan">0 fps</div>
            <div className="metric-label">🎬 FPS</div>
          </div>
        ) : (
          activeCamIds.map((camId, idx) => {
            const camFps = camerasData[camId]?.fps || perCameraFps[camId] || 0;
            const camLabel = camId.replace(/_/g, " ").toUpperCase();
            return (
              <div key={camId} className="metric-item">
                <div className="metric-value cyan">{camFps} fps</div>
                <div className="metric-label">
                  🎬 {activeCamIds.length > 1 ? camLabel : "FPS"}
                </div>
              </div>
            );
          })
        )}
        <div className="metric-item">
          <div className="metric-value orange">{data?.spike_rate || 0}%</div>
          <div className="metric-label">📡 SPIKE RATE</div>
        </div>
        <div className="metric-item">
          <div className="metric-value red">{data?.alerts || 0}</div>
          <div className="metric-label">🚨 ALERTS</div>
        </div>
        <div className="metric-item">
          <div className="metric-value green">{data?.frame_count || 0}</div>
          <div className="metric-label">🎞️ FRAMES</div>
        </div>
        {activeCamIds.length > 0 && (
          <div className="metric-item">
            <div className="metric-value cyan">{formatUptime(uptime)}</div>
            <div className="metric-label">⏱️ UPTIME</div>
          </div>
        )}
      </div>

      {/* ======== CAMERA MANAGER ======== */}
      <div className="panel camera-manager-panel">
        <div className="panel-header">
          <span>📷 CAMERA MANAGER — MULTI-CAM CONTROL</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              className="session-input-header"
              type="text"
              placeholder="Session name..."
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
              title="Session name for organizing clips (e.g. Demo_Loitering)"
            />
            <div
              className={`connection-status ${wsConnected || backendAlive ? "connected" : "disconnected"}`}
            >
              <span className="status-dot"></span>
              {wsConnected || backendAlive ? "LIVE" : "OFFLINE"}
            </div>
            <button
              className="detect-btn"
              onClick={() => detectCameras(false)}
              disabled={detecting || !backendAlive}
              title="Hold Shift and click to force refresh"
              onClickCapture={(e) => e.shiftKey && detectCameras(true)}
            >
              {detecting
                ? `🔍 ${detectMessage || "Scanning..."}`
                : "🔍 DETECT CAMERAS"}
            </button>
            <button
              className="network-cam-btn"
              onClick={() => setShowNetworkModal(true)}
            >
              📡 ADD NETWORK CAM
            </button>
            {activeCamIds.length > 1 && (
              <button className="cam-stop-all-btn" onClick={stopAll}>
                ⏹ STOP ALL
              </button>
            )}
          </div>
        </div>

        <div className="camera-grid-manager">
          {availableCameras.length === 0 && !detecting && (
            <div className="no-cameras-msg">
              {detectMessage ||
                (backendAlive
                  ? 'Click "DETECT CAMERAS" to scan for connected cameras'
                  : "⛔ Backend offline — start the server first")}
            </div>
          )}
          {detecting && availableCameras.length === 0 && (
            <div className="no-cameras-msg detecting-msg">
              🔍 {detectMessage || "Scanning for cameras..."}
            </div>
          )}
          {availableCameras.map((cam) => {
            const camId = cam.cam_id || `cam_${cam.index}`;
            const isActive = activeCamIds.includes(camId);
            const isLoading = cameraLoading[cam.index];
            return (
              <div
                key={cam.index}
                className={`camera-card ${isActive ? "cam-active" : ""}`}
              >
                <div className="camera-card-icon">
                  {cam.index === 0 ? "💻" : "📹"}
                </div>
                <div className="camera-card-info">
                  <div className="camera-card-name">{cam.name}</div>
                  <div className="camera-card-res">
                    {cam.resolution} • Index {cam.index}
                  </div>
                </div>
                <button
                  className={`camera-card-btn ${isActive ? "active" : ""}`}
                  onClick={() =>
                    isActive ? stopCamera(camId) : startCamera(cam.index)
                  }
                  disabled={isLoading}
                >
                  {isLoading ? "⏳" : isActive ? "⏹ STOP" : "▶ START"}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* ======== TOP ROW: Camera + Score Side-by-Side ======== */}
      <div className="top-row-grid">
        {/* ---- Camera Section (Left) ---- */}
        <div className="camera-section">
          {activeCamIds.length > 0 ? (
            <div
              className={`multi-cam-grid cam-count-${Math.min(activeCamIds.length, 4)}`}
            >
              {activeCamIds.map((camId) => (
                <CameraFeedPanel
                  key={camId}
                  camId={camId}
                  camData={camerasData[camId]}
                  onStop={stopCamera}
                  clientFps={perCameraFps[camId] || 0}
                />
              ))}
            </div>
          ) : (
            <div className="panel feed-panel">
              <div className="panel-header">
                <span className="live-dot off"></span>
                <span>LIVE FEED</span>
              </div>
              <div className="feed-container">
                <div className="feed-placeholder">
                  {!backendAlive
                    ? "⛔ Backend offline — start the server first"
                    : "📷 No cameras active — Use Camera Manager above to start"}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* ---- Score Chart (Right) ---- */}
        <div className="panel">
          <div className="panel-header">
            📊 FRAME INTELLIGENCE SCORE{" "}
            {chartCamIds.length === 1
              ? `— ${chartCamIds[0].toUpperCase().replace("_", " ")}`
              : chartCamIds.length > 1
                ? "— ALL CAMERAS"
                : ""}
          </div>
          <div className="chart-container">
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart
                data={mergedChartData}
                margin={{ top: 5, right: 10, left: -10, bottom: 5 }}
              >
                <defs>
                  {chartCamIds.map((camId, idx) => (
                    <linearGradient
                      key={camId}
                      id={`scoreGrad_${camId}`}
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="5%"
                        stopColor={CAMERA_COLORS[idx % CAMERA_COLORS.length]}
                        stopOpacity={0.15}
                      />
                      <stop
                        offset="95%"
                        stopColor={CAMERA_COLORS[idx % CAMERA_COLORS.length]}
                        stopOpacity={0}
                      />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#0d2d50" />
                <XAxis
                  dataKey="time"
                  stroke="#4a7a9b"
                  tick={{ fontSize: 9, fill: "#4a7a9b" }}
                  tickLine={{ stroke: "#0d2d50" }}
                  interval="preserveStartEnd"
                  tickMargin={4}
                  height={20}
                />
                <YAxis
                  domain={[0, 100]}
                  stroke="#4a7a9b"
                  fontSize={10}
                  tickFormatter={(value) => value.toFixed(0)}
                  tickMargin={2}
                  width={30}
                />
                <Tooltip
                  cursor={{
                    stroke: "#00d4ff",
                    strokeWidth: 1,
                    strokeDasharray: "5 5",
                  }}
                  content={({ active, payload, label }) => {
                    if (!active || !payload || payload.length === 0)
                      return null;
                    return (
                      <div
                        style={{
                          background: "#071428",
                          border: "1px solid #0d2d50",
                          borderRadius: 8,
                          padding: "10px 14px",
                          fontFamily: "JetBrains Mono, monospace",
                          fontSize: 11,
                          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
                        }}
                      >
                        <div
                          style={{
                            color: "#4a7a9b",
                            marginBottom: 6,
                            fontWeight: 600,
                          }}
                        >
                          ⏱ {label}
                        </div>
                        {payload.map((entry, i) => {
                          const score =
                            typeof entry.value === "number"
                              ? entry.value.toFixed(2)
                              : "0.00";
                          const category =
                            entry.value > 60
                              ? "EVENT"
                              : entry.value > 30
                                ? "NORMAL"
                                : "IDLE";
                          const catColor =
                            entry.value > 60
                              ? "#ff4d4d"
                              : entry.value > 30
                                ? "#ffaa00"
                                : "#00d4ff";
                          return (
                            <div
                              key={i}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                                marginBottom: 4,
                                padding: "4px 0",
                                borderBottom:
                                  i < payload.length - 1
                                    ? "1px solid rgba(255,255,255,0.05)"
                                    : "none",
                              }}
                            >
                              <span
                                style={{
                                  width: 10,
                                  height: 10,
                                  borderRadius: "50%",
                                  background: entry.color,
                                  flexShrink: 0,
                                }}
                              />
                              <span style={{ color: "#7ec8e3", flex: 1 }}>
                                {entry.name}:
                              </span>
                              <span
                                style={{
                                  color: entry.color,
                                  fontWeight: 700,
                                  minWidth: 55,
                                  textAlign: "right",
                                }}
                              >
                                {score}
                              </span>
                              <span
                                style={{
                                  color: catColor,
                                  fontSize: 9,
                                  fontWeight: 600,
                                  padding: "2px 6px",
                                  background: `${catColor}15`,
                                  borderRadius: 4,
                                  minWidth: 50,
                                  textAlign: "center",
                                }}
                              >
                                {category}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    );
                  }}
                />
                {chartCamIds.length > 1 && (
                  <Legend
                    verticalAlign="top"
                    height={28}
                    iconType="line"
                    wrapperStyle={{
                      fontSize: 11,
                      fontFamily: "JetBrains Mono, monospace",
                      color: "#7ec8e3",
                    }}
                  />
                )}
                {chartCamIds.map((camId, idx) => (
                  <Area
                    key={camId}
                    type="monotone"
                    dataKey={`score_${camId}`}
                    name={camId.replace(/_/g, " ").toUpperCase()}
                    stroke={CAMERA_COLORS[idx % CAMERA_COLORS.length]}
                    strokeWidth={2}
                    fill={`url(#scoreGrad_${camId})`}
                    dot={false}
                    activeDot={{
                      r: 6,
                      fill: CAMERA_COLORS[idx % CAMERA_COLORS.length],
                      stroke: "#fff",
                      strokeWidth: 2,
                      style: {
                        filter: "drop-shadow(0 0 6px rgba(0,212,255,0.8))",
                      },
                    }}
                    isAnimationActive={false}
                    connectNulls={true}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
          {/* Camera Legend */}
          {chartCamIds.length > 0 && (
            <div className="chart-legend">
              {chartCamIds.map((camId, idx) => (
                <div key={camId} className="legend-item">
                  <span
                    className="legend-dot"
                    style={{
                      background: CAMERA_COLORS[idx % CAMERA_COLORS.length],
                    }}
                  />
                  <span className="legend-label">
                    {camId.replace(/_/g, " ").toUpperCase()}
                  </span>
                  {camerasData[camId]?.fps && (
                    <span className="legend-fps">
                      {camerasData[camId].fps} FPS
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="score-bar">
            <div className="zone zone-idle">IDLE 0–30</div>
            <div className="zone zone-normal">NORMAL 30–60</div>
            <div className="zone zone-event">EVENT 60–100</div>
          </div>
        </div>
      </div>

      {/* ======== SNN STATS ROW: Compact 3-Column Grid ======== */}
      <div className="snn-stats-grid">
        {/* ---- Bandwidth Savings ---- */}
        <div className="panel bandwidth-panel">
          <div className="panel-header">📡 SNN BANDWIDTH SAVINGS — LIVE</div>
          <div className="bandwidth-content">
            <div className="bandwidth-hero">
              <div className="bandwidth-ring">
                <svg viewBox="0 0 120 120" className="bandwidth-svg">
                  <circle
                    cx="60"
                    cy="60"
                    r="52"
                    fill="none"
                    stroke="#0d2d50"
                    strokeWidth="8"
                  />
                  <circle
                    cx="60"
                    cy="60"
                    r="52"
                    fill="none"
                    stroke={
                      bandwidthStats.savings > 50
                        ? "#00ff88"
                        : bandwidthStats.savings > 20
                          ? "#ffaa00"
                          : "#ff4d4d"
                    }
                    strokeWidth="8"
                    strokeLinecap="round"
                    strokeDasharray={`${(bandwidthStats.savings / 100) * 327} 327`}
                    transform="rotate(-90 60 60)"
                    style={{ transition: "stroke-dasharray 0.8s ease" }}
                  />
                </svg>
                <div className="bandwidth-ring-text">
                  <span className="bandwidth-pct">
                    {bandwidthStats.savings}%
                  </span>
                  <span className="bandwidth-label">SAVED</span>
                </div>
              </div>
              <div className="bandwidth-stats">
                <div className="bw-stat">
                  <span className="bw-stat-val green">
                    {bandwidthStats.skippedFrames.toLocaleString()}
                  </span>
                  <span className="bw-stat-lbl">FRAMES SKIPPED</span>
                </div>
                <div className="bw-stat">
                  <span className="bw-stat-val cyan">
                    {bandwidthStats.spikeFrames.toLocaleString()}
                  </span>
                  <span className="bw-stat-lbl">FRAMES PROCESSED</span>
                </div>
                <div className="bw-stat">
                  <span className="bw-stat-val orange">
                    {bandwidthStats.totalFrames.toLocaleString()}
                  </span>
                  <span className="bw-stat-lbl">TOTAL FRAMES</span>
                </div>
              </div>
            </div>
            {bandwidthStats.perCam.length > 1 && (
              <div className="bandwidth-per-cam">
                {bandwidthStats.perCam.map(({ camId, savings }) => (
                  <div key={camId} className="bw-cam-row">
                    <span className="bw-cam-name">
                      {camId.replace(/_/g, " ").toUpperCase()}
                    </span>
                    <div className="bw-cam-bar-bg">
                      <div
                        className="bw-cam-bar-fill"
                        style={{
                          width: `${savings}%`,
                          transition: "width 0.5s ease",
                        }}
                      />
                    </div>
                    <span className="bw-cam-pct">{savings.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ---- SNN Spike Train Visualizer ---- */}
        <div className="panel spike-viz-panel">
          <div className="panel-header">
            🧠 SNN SPIKE TRAIN — NEURAL ACTIVITY
          </div>
          <div className="spike-viz-content">
            {activeCamIds.map((camId, camIdx) => {
              const color = CAMERA_COLORS[camIdx % CAMERA_COLORS.length];
              return (
                <div key={camId} className="spike-train-row">
                  {activeCamIds.length > 1 && (
                    <div className="spike-train-label" style={{ color }}>
                      {camId.replace(/_/g, " ").toUpperCase()}
                    </div>
                  )}
                  <div className="spike-train-track">
                    {spikeTrainHistory.map((entry, i) => {
                      const spiked = entry[camId] === 1;
                      const membrane = entry[`membrane_${camId}`] || 0;
                      return (
                        <div
                          key={i}
                          className={`spike-dot ${spiked ? "fired" : ""}`}
                          style={{
                            "--dot-color": color,
                            height: spiked
                              ? "100%"
                              : `${Math.max(8, membrane * 70)}%`,
                            animationDelay: `${i * 15}ms`,
                          }}
                        />
                      );
                    })}
                  </div>
                  <div className="spike-train-stats">
                    <span
                      className="spike-indicator"
                      style={{
                        background: camerasData[camId]?.snn_spike
                          ? color
                          : "#1a3652",
                      }}
                    >
                      {camerasData[camId]?.snn_spike ? "⚡" : "—"}
                    </span>
                    <span className="spike-membrane-val">
                      M: {(camerasData[camId]?.snn_membrane || 0).toFixed(2)}
                    </span>
                    <span className="spike-threshold-val">
                      T: {(camerasData[camId]?.snn_threshold || 0).toFixed(2)}
                    </span>
                  </div>
                </div>
              );
            })}
            {activeCamIds.length === 0 && (
              <div className="spike-empty">
                Start a camera to see neural spike activity
              </div>
            )}
            <div className="spike-legend">
              <span className="spike-legend-item">
                <span className="spike-legend-dot fired-demo"></span> Spike
                (YOLO runs)
              </span>
              <span className="spike-legend-item">
                <span className="spike-legend-dot idle-demo"></span> Membrane
                level (no YOLO)
              </span>
            </div>
          </div>
        </div>

        {/* ---- Real-Time Compression Panel ---- */}
        {(compressionLive || compressionProof) && (
          <div className="panel compression-proof-panel">
            <div className="panel-header">🗜️ REAL-TIME COMPRESSION — LIVE</div>
            <div className="compression-proof-content">

              {/* ---- LIVE SESSION (from WebSocket) ---- */}
              {compressionLive && (
                <>
                  <div className="compression-live-badge">⚡ LIVE SESSION {compressionLive.original_bytes > 0 ? '' : '(waiting for frames...)'}</div>
                  {compressionLive.original_bytes > 0 ? (
                    <>
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
                  ) : (
                    <div style={{color:'#4a7a9b',textAlign:'center',padding:'20px 0'}}>
                      Start a camera to see live compression stats
                    </div>
                  )}
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
      </div>

      {/* ======== ARCHITECTURE — Full Width Below SNN Stats ======== */}
      <div className="architecture-row">
        {/* ---- Architecture Diagram ---- */}
        <div className="panel arch-panel">
          <div className="panel-header">
            🏗️ PIPELINE ARCHITECTURE — LIVE STATUS
          </div>
          <div className="arch-diagram">
            {[
              {
                key: "cam",
                icon: "📷",
                label: "Camera Input",
                desc: "OpenCV capture",
                active: activeCamIds.length > 0,
                stat: `${activeCamIds.length} active`,
              },
              {
                key: "snn",
                icon: "🧠",
                label: "SNN Spike Gate",
                desc: "LIF neuron filter",
                active: snnSpike,
                stat: `${data?.spike_rate || 0}% rate`,
              },
              {
                key: "yolo",
                icon: "🎯",
                label: "YOLOv8-nano",
                desc: "Person detection",
                active: snnSpike && detections.length > 0,
                stat: `${detections.length} det`,
              },
              {
                key: "scorer",
                icon: "📊",
                label: "Frame Scorer",
                desc: "Threat assessment",
                active: (firstCamData?.score || 0) > 0,
                stat: `Score: ${firstCamData?.score || 0}`,
              },
              {
                key: "anomaly",
                icon: "🔍",
                label: "Anomaly Det.",
                desc: "Loiter + scene",
                active: (data?.active_tracks || 0) > 0,
                stat: `${data?.active_tracks || 0} tracks`,
              },
              {
                key: "compress",
                icon: "🗜️",
                label: "Compressor",
                desc: "zstd + 7z",
                active: activeCamIds.length > 0,
                stat: `${bandwidthStats.savings}% saved`,
              },
              {
                key: "db",
                icon: "🗄️",
                label: "Forensic DB",
                desc: "SQLite logging",
                active: events.length > 0 || alerts.length > 0,
                stat: `${events.length} events`,
              },
              {
                key: "dash",
                icon: "📡",
                label: "Dashboard",
                desc: "WebSocket live",
                active: wsConnected,
                stat: wsConnected ? "LIVE" : "OFF",
              },
            ].map((stage, i, arr) => (
              <React.Fragment key={stage.key}>
                <div
                  className={`arch-node ${stage.active ? "arch-active" : "arch-idle"}`}
                >
                  <div
                    className={`arch-status-dot ${stage.active ? "dot-green" : "dot-red"}`}
                  />
                  <div className="arch-icon">{stage.icon}</div>
                  <div className="arch-label">{stage.label}</div>
                  <div className="arch-desc">{stage.desc}</div>
                  <div className="arch-stat">{stage.stat}</div>
                </div>
                {i < arr.length - 1 && (
                  <div
                    className={`arch-arrow ${stage.active ? "arch-arrow-active" : ""}`}
                  >
                    <span>▶</span>
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>

      {/* ======== MAIN GRID: Alerts, Clips, Forensic Log ======== */}
      <div className="main-grid">
        {/* ---- Alert Panel ---- */}
        <div className="panel alert-panel">
          <div className="panel-header">
            <span>🚨 REAL-TIME ANOMALY ALERTS</span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 10, color: "#4a7a9b" }}>
                {alerts.length} active
              </span>
              <button
                className="panel-clear-btn"
                onClick={() => promptClear("alerts")}
                disabled={alerts.length === 0}
                title="Clear all alerts"
              >
                🗑️
              </button>
            </div>
          </div>
          <div className="alert-list">
            {alerts.length === 0 && !data?.last_alert ? (
              <div className="no-alerts">
                ✅ No active alerts — system nominal
              </div>
            ) : (
              <>
                {data?.last_alert && (
                  <div className="alert-item latest">
                    <div className="alert-meta-row">
                      <div className="alert-type">{data.last_alert.type}</div>
                      <div className="alert-severity">CRITICAL</div>
                    </div>
                    <div className="alert-msg">{data.last_alert.message}</div>
                    <div className="alert-time-row">
                      <span className="alert-live-badge">⚡ LIVE</span>
                    </div>
                  </div>
                )}
                {alerts.map((alert, i) => (
                  <div key={i} className="alert-item">
                    <div className="alert-meta-row">
                      <div className="alert-type">{alert.alert_type}</div>
                      <div className="alert-severity">{alert.severity}</div>
                    </div>
                    <div className="alert-msg">{alert.message}</div>
                    <div className="alert-time-row">
                      <span className="alert-timestamp">
                        {alert.timestamp
                          ? new Date(alert.timestamp).toLocaleTimeString(
                              "en-IN",
                              { hour12: false },
                            )
                          : ""}
                      </span>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>

        {/* ---- Video Player Modal ---- */}
        {videoModal && (
          <div
            className="video-modal-overlay"
            onClick={() => setVideoModal(null)}
          >
            <div className="video-modal" onClick={(e) => e.stopPropagation()}>
              <div className="video-modal-header">
                <span>{videoModal.title}</span>
                <button
                  className="video-modal-close"
                  onClick={() => setVideoModal(null)}
                >
                  ✕
                </button>
              </div>
              {videoModal.type === "avi" ? (
                <div className="video-modal-avi-notice">
                  <div style={{ fontSize: 32, marginBottom: 10 }}>📹</div>
                  <div
                    style={{
                      fontWeight: 700,
                      marginBottom: 6,
                      color: "var(--orange)",
                    }}
                  >
                    AVI / XVID — Not supported in browser
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--dim)",
                      marginBottom: 16,
                    }}
                  >
                    Browsers cannot play AVI files natively.
                    <br />
                    Download and open with VLC or Windows Media Player.
                  </div>
                  <a
                    className="video-modal-dl"
                    style={{
                      padding: "8px 20px",
                      background: "rgba(0,212,255,0.12)",
                      borderRadius: 6,
                      border: "1px solid rgba(0,212,255,0.25)",
                    }}
                    href={videoModal.url}
                    download
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    ⬇ DOWNLOAD FILE
                  </a>
                </div>
              ) : (
                <video
                  className="video-modal-player"
                  src={videoModal.url}
                  controls
                  autoPlay
                  onError={(e) => {
                    e.target.style.display = "none";
                    e.target.nextSibling.style.display = "flex";
                  }}
                />
              )}
              {videoModal.type !== "avi" && (
                <div
                  className="video-modal-avi-notice"
                  style={{ display: "none" }}
                >
                  <div style={{ fontSize: 24, marginBottom: 8 }}>⚠️</div>
                  <div
                    style={{
                      fontWeight: 700,
                      marginBottom: 6,
                      color: "var(--orange)",
                    }}
                  >
                    Cannot play in browser
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--dim)",
                      marginBottom: 12,
                    }}
                  >
                    This video codec is not supported natively.
                    <br />
                    Download and open with VLC.
                  </div>
                  <a
                    className="video-modal-dl"
                    href={videoModal.url}
                    download
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    ⬇ DOWNLOAD FILE
                  </a>
                </div>
              )}
              <div className="video-modal-footer">
                <a
                  className="video-modal-dl"
                  href={videoModal.url}
                  download
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  ⬇ DOWNLOAD
                </a>
              </div>
            </div>
          </div>
        )}

        {/* ---- Event Clips with Date Filter ---- */}
        <div className="panel clips-panel">
          <div className="panel-header">
            <span>🎬 RECORDED CLIPS</span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="date"
                className="clip-date-input"
                value={clipDateFilter}
                onChange={(e) => setClipDateFilter(e.target.value)}
                title="Filter clips by date"
              />
              {clipDateFilter && (
                <button
                  className="clip-date-clear"
                  onClick={() => setClipDateFilter("")}
                >
                  ✕
                </button>
              )}
              <span style={{ fontSize: 10, color: "#4a7a9b" }}>
                {(() => {
                  const filtered = clipDateFilter
                    ? clips.filter(
                        (c) =>
                          c.start_time &&
                          c.start_time.startsWith(clipDateFilter),
                      )
                    : clips;
                  return `${filtered.length} clip${filtered.length !== 1 ? "s" : ""}`;
                })()}
              </span>

            </div>
          </div>
          <div className="clips-table-wrapper">
            <table className="clips-table">
              <thead>
                <tr>
                  <th>DATE</th>
                  <th>TIME</th>
                  <th>CAM</th>
                  <th>TYPE</th>
                  <th>QUALITY</th>
                  <th>DURATION</th>
                  <th>SIZE</th>
                  <th>FPS</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(() => {
                  const filtered = clipDateFilter
                    ? clips.filter(
                        (c) =>
                          c.start_time &&
                          c.start_time.startsWith(clipDateFilter),
                      )
                    : clips;
                  if (filtered.length === 0) {
                    return (
                      <tr>
                        <td
                          colSpan={9}
                          style={{
                            textAlign: "center",
                            padding: 30,
                            color: "#4a7a9b",
                          }}
                        >
                          {clips.length === 0
                            ? "No clips yet — events auto-record when persons detected"
                            : "No clips for selected date"}
                        </td>
                      </tr>
                    );
                  }
                  return filtered.map((clip, i) => {
                    const ts = clip.start_time
                      ? new Date(clip.start_time)
                      : null;
                    const dateStr = ts
                      ? ts.toLocaleDateString("en-IN", {
                          day: "2-digit",
                          month: "short",
                          year: "numeric",
                        })
                      : "-";
                    const timeStr = ts
                      ? ts.toLocaleTimeString("en-IN", { hour12: false })
                      : "-";
                    const catClass =
                      clip.category === "EVENT"
                        ? "clip-event"
                        : clip.category === "NORMAL"
                          ? "clip-normal"
                          : "clip-idle";
                    const qualityLabel = clip.quality || "HD";
                    const clipUrl = `${API_BASE}/api/clips/${clip.filename}`;
                    return (
                      <tr key={i} className={catClass}>
                        <td>{dateStr}</td>
                        <td style={{ fontFamily: "JetBrains Mono, monospace" }}>
                          {timeStr}
                        </td>
                        <td>{clip.camera || "cam_0"}</td>
                        <td className={`clip-cat-cell ${catClass}`}>
                          {clip.category}
                        </td>
                        <td>{qualityLabel}</td>
                        <td>
                          {clip.duration_sec ? `${clip.duration_sec}s` : "-"}
                        </td>
                        <td>
                          {clip.size_kb ? `${clip.size_kb.toFixed(0)} KB` : "-"}
                        </td>
                        <td>{clip.fps || 15}</td>
                        <td style={{ display: "flex", gap: 4 }}>
                          <button
                            className="clip-play-btn"
                            onClick={() =>
                              setVideoModal({
                                url: clipUrl,
                                title: clip.filename,
                                type: "mp4",
                              })
                            }
                            title="Play"
                          >
                            ▶
                          </button>
                          <a
                            className="clip-dl-btn"
                            href={clipUrl}
                            download
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Download"
                          >
                            ⬇
                          </a>
                        </td>
                      </tr>
                    );
                  });
                })()}
              </tbody>
            </table>
          </div>
        </div>

        {/* ---- Pre-Buffer Recordings ---- */}
        <div className="panel clips-panel prebuffer-panel">
          <div className="panel-header">
            <span>⏪ PRE-BUFFER RECORDINGS (30s before event)</span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 10, color: "#4a7a9b" }}>
                {prebufferClips.length} recording
                {prebufferClips.length !== 1 ? "s" : ""}
              </span>

            </div>
          </div>
          <div className="clips-table-wrapper">
            <table className="clips-table">
              <thead>
                <tr>
                  <th>DATE</th>
                  <th>TIME</th>
                  <th>EVENT TYPE</th>
                  <th>DURATION</th>
                  <th>SIZE</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {prebufferClips.length === 0 ? (
                  <tr>
                    <td
                      colSpan={6}
                      style={{
                        textAlign: "center",
                        padding: 30,
                        color: "#4a7a9b",
                      }}
                    >
                      No pre-buffer recordings yet — these save 30s before
                      loitering/anomaly alerts
                    </td>
                  </tr>
                ) : (
                  prebufferClips.map((pb, i) => {
                    const ts = pb.start_time ? new Date(pb.start_time) : null;
                    const dateStr = ts
                      ? ts.toLocaleDateString("en-IN", {
                          day: "2-digit",
                          month: "short",
                          year: "numeric",
                        })
                      : "-";
                    const timeStr = ts
                      ? ts.toLocaleTimeString("en-IN", { hour12: false })
                      : "-";
                    const pbUrl = `${API_BASE}/api/prebuffer/${pb.filename}`;
                    return (
                      <tr key={i} className="clip-event">
                        <td>{dateStr}</td>
                        <td style={{ fontFamily: "JetBrains Mono, monospace" }}>
                          {timeStr}
                        </td>
                        <td style={{ color: "var(--orange)", fontWeight: 700 }}>
                          {pb.event_type}
                        </td>
                        <td>{pb.duration_sec ? `${pb.duration_sec}s` : "-"}</td>
                        <td>
                          {pb.size_kb ? `${pb.size_kb.toFixed(0)} KB` : "-"}
                        </td>
                        <td style={{ display: "flex", gap: 4 }}>
                          <button
                            className="clip-play-btn"
                            onClick={() =>
                              setVideoModal({
                                url: pbUrl,
                                title: pb.filename,
                                type: "avi",
                              })
                            }
                            title="Play"
                          >
                            ▶
                          </button>
                          <a
                            className="clip-dl-btn"
                            href={pbUrl}
                            download
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Download"
                          >
                            ⬇
                          </a>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* ---- Forensic Event Log ---- */}
        <div className="panel log-panel">
          <div className="panel-header">
            <span>📋 FORENSIC EVENT LOG — SMART CCTV DIARY</span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 10, color: "#4a7a9b" }}>
                {events.length} records
              </span>
              <button
                className="panel-clear-btn"
                onClick={() => promptClear("events")}
                disabled={events.length === 0}
                title="Delete all forensic events"
              >
                🗑️
              </button>
            </div>
          </div>
          <div className="event-table-wrapper">
            <table className="event-table">
              <thead>
                <tr>
                  <th>DATE</th>
                  <th>TIME</th>
                  <th>CAM</th>
                  <th>FRAME</th>
                  <th>SCORE</th>
                  <th>CATEGORY</th>
                  <th>TYPE</th>
                  <th>PERSONS</th>
                  <th>SEVERITY</th>
                </tr>
              </thead>
              <tbody>
                {events.length === 0 ? (
                  <tr>
                    <td
                      colSpan={9}
                      style={{
                        textAlign: "center",
                        padding: 30,
                        color: "#4a7a9b",
                      }}
                    >
                      No events recorded yet — start a camera to begin
                    </td>
                  </tr>
                ) : (
                  events.map((event, i) => {
                    const ts = event.timestamp
                      ? new Date(event.timestamp)
                      : null;
                    return (
                      <tr key={i}>
                        <td>
                          {ts
                            ? ts.toLocaleDateString("en-IN", {
                                day: "2-digit",
                                month: "short",
                              })
                            : "-"}
                        </td>
                        <td style={{ fontFamily: "JetBrains Mono, monospace" }}>
                          {ts
                            ? ts.toLocaleTimeString("en-IN", { hour12: false })
                            : "-"}
                        </td>
                        <td>{event.camera_id || "CAM_01"}</td>
                        <td>{event.frame_number}</td>
                        <td
                          style={{
                            color: getScoreColor(event.score),
                            fontWeight: 700,
                          }}
                        >
                          {event.score}
                        </td>
                        <td className={getCategoryClass(event.category)}>
                          {getCategoryLabel(event.category)}
                        </td>
                        <td>{event.event_type}</td>
                        <td>{event.person_count}</td>
                        <td
                          className={`severity-${event.severity?.toLowerCase()}`}
                        >
                          {event.severity}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ======== SESSION CONTROLS ======== */}
      <div className="session-bar">
        <div className="session-controls">
          <button
            className="session-btn clear-btn"
            onClick={() => promptClear("all")}
            disabled={activeCamIds.length > 0}
          >
            🗑️ CLEAR ALL LOGS
          </button>
          <a
            className="session-btn export-btn"
            href={`${API_BASE}/api/export`}
            target="_blank"
            rel="noopener noreferrer"
          >
            📥 EXPORT CSV
          </a>
        </div>
      </div>

      {/* ======== NETWORK CAMERA MODAL ======== */}
      {showNetworkModal && (
        <div
          className="modal-overlay"
          onClick={() => setShowNetworkModal(false)}
        >
          <div className="network-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Connect Network Camera</h3>
              <button
                className="modal-close"
                onClick={() => setShowNetworkModal(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>Camera ID</label>
                <input
                  type="text"
                  placeholder="e.g. cam_phone"
                  value={networkCamForm.camera_id}
                  onChange={(e) =>
                    setNetworkCamForm((prev) => ({
                      ...prev,
                      camera_id: e.target.value,
                    }))
                  }
                />
              </div>
              <div className="form-group">
                <label>IP Address</label>
                <input
                  type="text"
                  placeholder="e.g. 192.168.1.5"
                  value={networkCamForm.ip_address}
                  onChange={(e) =>
                    setNetworkCamForm((prev) => ({
                      ...prev,
                      ip_address: e.target.value,
                    }))
                  }
                />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Port</label>
                  <input
                    type="text"
                    placeholder="4747"
                    value={networkCamForm.port}
                    onChange={(e) =>
                      setNetworkCamForm((prev) => ({
                        ...prev,
                        port: e.target.value,
                      }))
                    }
                  />
                </div>
                <div className="form-group">
                  <label>Path</label>
                  <input
                    type="text"
                    placeholder="/video"
                    value={networkCamForm.path}
                    onChange={(e) =>
                      setNetworkCamForm((prev) => ({
                        ...prev,
                        path: e.target.value,
                      }))
                    }
                  />
                </div>
              </div>
              <div className="form-hint">
                DroidCam: port 4747, path /video
                <br />
                IP Webcam: port 8080, path /video
              </div>
            </div>
            <div className="modal-footer">
              <button
                className="modal-btn cancel"
                onClick={() => setShowNetworkModal(false)}
              >
                Cancel
              </button>
              <button
                className="modal-btn connect"
                onClick={connectNetworkCamera}
                disabled={networkConnecting || !networkCamForm.ip_address}
              >
                {networkConnecting ? "Connecting..." : "Initialize Stream"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ======== CLEAR CONFIRMATION MODAL ======== */}
      {showClearConfirm && (
        <div
          className="modal-overlay"
          onClick={() => setShowClearConfirm(false)}
        >
          <div className="confirm-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header warning">
              <h3>⚠️ Confirm Deletion</h3>
              <button
                className="modal-close"
                onClick={() => setShowClearConfirm(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <p className="confirm-message">
                {clearTarget === "events" && (
                  <>
                    Are you sure you want to{" "}
                    <strong>
                      permanently delete all {events.length} forensic event
                      records
                    </strong>
                    ? This action cannot be undone.
                  </>
                )}
                {clearTarget === "alerts" && (
                  <>
                    Are you sure you want to{" "}
                    <strong>
                      permanently clear all {alerts.length} anomaly alerts
                    </strong>
                    ? This action cannot be undone.
                  </>
                )}
                {clearTarget === "all" && (
                  <>
                    <strong style={{ color: "var(--orange)" }}>
                      ⚠️ WARNING: This will clear all log data:
                    </strong>
                    <ul className="clear-list">
                      <li>📋 {events.length} forensic event records</li>
                      <li>🚨 All alert history</li>
                      <li>📊 Score history and statistics</li>
                    </ul>
                    <p style={{ color: "#ff6b6b", fontWeight: 600 }}>
                      This action cannot be undone! (Videos are not affected)
                    </p>
                  </>
                )}
              </p>
            </div>
            <div className="modal-footer">
              <button
                className="modal-btn cancel"
                onClick={() => setShowClearConfirm(false)}
              >
                Cancel
              </button>
              <button className="modal-btn delete" onClick={handleClearConfirm}>
                🗑️ Delete Permanently
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ======== FOOTER ======== */}
      <footer className="footer">
        <div className="quote">
          "Traditional CCTV records everything. EdgeVid LowBand remembers what
          matters."
        </div>
        <div className="footer-info">
          TEAM SPECTRUM // HACKARENA'26 // PS-04 // No cloud. No GPU. Just
          intelligence.
        </div>
      </footer>
    </div>
  );
}

function WrappedApp() {
  return (
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  );
}

export default WrappedApp;
