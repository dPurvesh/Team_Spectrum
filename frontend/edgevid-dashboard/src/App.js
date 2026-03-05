import React, { useState, useEffect, useRef, useCallback } from 'react';
import useWebSocket, { ReadyState } from 'react-use-websocket';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer
} from 'recharts';
import './App.css';

const WS_URL = 'ws://localhost:8000/ws/live';
const API_BASE = 'http://localhost:8000';

const PIPELINE_STAGES = [
  { key: 'cam', icon: '📷', label: 'CAMERA' },
  { key: 'snn', icon: '🧠', label: 'SNN GATE' },
  { key: 'yolo', icon: '🎯', label: 'YOLOv8' },
  { key: 'anomaly', icon: '🔍', label: 'ANOMALY' },
  { key: 'score', icon: '📊', label: 'SCORING' },
  { key: 'compress', icon: '🗜️', label: 'COMPRESS' },
  { key: 'db', icon: '🗄️', label: 'FORENSIC' },
  { key: 'dash', icon: '📡', label: 'DASHBOARD' },
];

function App() {
  const [data, setData] = useState(null);
  const [scoreHistory, setScoreHistory] = useState([]);
  const [events, setEvents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [clips, setClips] = useState([]);
  const [cameraOn, setCameraOn] = useState(false);
  const [cameraLoading, setCameraLoading] = useState(false);
  const [backendAlive, setBackendAlive] = useState(false);
  const [uptime, setUptime] = useState(0);
  const [sessionName, setSessionName] = useState('');
  const [currentTime, setCurrentTime] = useState(new Date());
  const imgRef = useRef(null);
  const uptimeRef = useRef(null);
  const pollPauseRef = useRef(false); // pause status poll briefly after user action
  const fpsCounterRef = useRef(0);
  const [clientFps, setClientFps] = useState(0);

  const { lastJsonMessage, readyState } = useWebSocket(WS_URL, {
    shouldReconnect: () => true,
    reconnectInterval: 2000,
  });

  const wsConnected = readyState === ReadyState.OPEN;

  // ---- Client-side FPS counter ----
  useEffect(() => {
    const t = setInterval(() => {
      setClientFps(fpsCounterRef.current);
      fpsCounterRef.current = 0;
    }, 1000);
    return () => clearInterval(t);
  }, []);

  // ---- Live clock ----
  useEffect(() => {
    const t = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // ---- Uptime counter ----
  useEffect(() => {
    if (cameraOn) {
      uptimeRef.current = setInterval(() => setUptime(u => u + 1), 1000);
    } else {
      clearInterval(uptimeRef.current);
      setUptime(0);
    }
    return () => clearInterval(uptimeRef.current);
  }, [cameraOn]);

  const formatUptime = (s) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
  };

  const formatDate = (d) => {
    return d.toLocaleDateString('en-IN', {
      weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'
    });
  };

  const formatTime = (d) => {
    return d.toLocaleTimeString('en-IN', { hour12: false });
  };

  // ---- Poll camera status ----
  useEffect(() => {
    const fetchStatus = async () => {
      if (pollPauseRef.current) return; // skip poll for 4s after user action
      try {
        const res = await fetch(`${API_BASE}/api/camera/status`);
        const json = await res.json();
        setCameraOn(json.camera_on);
        setBackendAlive(true);
      } catch (e) {
        setBackendAlive(false);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, []);

  // ---- Toggle camera ----
  const toggleCamera = useCallback(async () => {
    setCameraLoading(true);
    // Pause the background status poll for 5s so it doesn't override our state
    pollPauseRef.current = true;
    setTimeout(() => { pollPauseRef.current = false; }, 5000);
    try {
      if (cameraOn) {
        await fetch(`${API_BASE}/api/camera/stop`, { method: 'POST' });
        setCameraOn(false);
      } else {
        const name = sessionName || `Demo_${new Date().toLocaleTimeString('en-IN', { hour12: false }).replace(/:/g, '')}`;
        const res = await fetch(`${API_BASE}/api/camera/start?session_name=${encodeURIComponent(name)}`, { method: 'POST' });
        const json = await res.json();
        if (json.status === 'started' || json.status === 'already_running') {
          setCameraOn(true);
          setBackendAlive(true);
        }
      }
    } catch (e) { }
    setCameraLoading(false);
  }, [cameraOn, sessionName]);

  // ---- Clear session ----
  const clearSession = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/session/clear`, { method: 'POST' });
      setScoreHistory([]);
      setEvents([]);
      setAlerts([]);
      setClips([]);
    } catch (e) { }
  }, []);

  // ---- Fetch events ----
  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/events?limit=25`);
        const json = await res.json();
        setEvents(json.events || []);
      } catch (e) { }
    };
    const interval = setInterval(fetchEvents, 3000);
    fetchEvents();
    return () => clearInterval(interval);
  }, []);

  // ---- Fetch alerts ----
  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/alerts?unacknowledged_only=true`);
        const json = await res.json();
        setAlerts(json.alerts || []);
      } catch (e) { }
    };
    const interval = setInterval(fetchAlerts, 2000);
    fetchAlerts();
    return () => clearInterval(interval);
  }, []);

  // ---- Fetch clips ----
  useEffect(() => {
    const fetchClips = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/clips`);
        const json = await res.json();
        setClips(json.clips || []);
      } catch (e) { }
    };
    const interval = setInterval(fetchClips, 5000);
    fetchClips();
    return () => clearInterval(interval);
  }, []);

  // ---- Process WebSocket data ----
  useEffect(() => {
    if (lastJsonMessage) {
      fpsCounterRef.current += 1;
      setData(lastJsonMessage);
      setBackendAlive(true); // if we get WS data, backend is definitely alive
      setScoreHistory(prev => {
        const now = new Date();
        const updated = [...prev, {
          time: now.toLocaleTimeString('en-IN', { hour12: false }),
          score: lastJsonMessage.score || 0,
          spike: lastJsonMessage.snn_spike ? 100 : 0,
        }];
        return updated.slice(-120);
      });
    }
  }, [lastJsonMessage]);

  // ---- Helpers ----
  const getScoreColor = (score) => {
    if (score > 60) return '#00ff88';
    if (score > 30) return '#ffaa00';
    return '#ff4d4d';
  };

  const getCategoryLabel = (cat) => {
    if (cat === 'EVENT') return '🔴 EVENT';
    if (cat === 'NORMAL') return '🟡 NORMAL';
    return '🟢 IDLE';
  };

  const getCategoryClass = (cat) => {
    if (cat === 'EVENT') return 'severity-critical';
    if (cat === 'NORMAL') return 'severity-high';
    return 'severity-low';
  };

  const score = data?.score || 0;
  const category = data?.category || 'IDLE';
  const detections = data?.detections || [];
  const snnSpike = data?.snn_spike || false;
  const snnMembrane = data?.snn_membrane || 0;
  const targetFps = data?.target_fps || 15;
  const displayFps = (data?.fps && data.fps > 0) ? data.fps : clientFps;
  const fpsLabel = category === 'EVENT' ? '15 (MAX)' : category === 'NORMAL' ? '12 (MED)' : '8 (LOW)';

  return (
    <div className="app">
      {/* ======== HEADER ======== */}
      <header className="header">
        <div className="header-top-row">
          <div className="header-badge">HACKARENA'26 // TEAM SPECTRUM</div>
          <div className="header-clock">
            <div className="clock-date">{formatDate(currentTime)}</div>
            <div className="clock-time">{formatTime(currentTime)}</div>
          </div>
        </div>
        <h1>EDGEVID <span className="accent">LOWBAND</span></h1>
        <p className="subtitle">NEUROMORPHIC EDGE-AI DVR — THE CAMERA THAT THINKS</p>
      </header>

      {/* ======== PIPELINE INDICATOR ======== */}
      <div className="pipeline-bar">
        {PIPELINE_STAGES.map((stage, i) => {
          let stepClass = 'idle';
          if (cameraOn) {
            if (stage.key === 'snn' && snnSpike) stepClass = 'active spiking';
            else if (stage.key === 'yolo' && snnSpike && detections.length > 0) stepClass = 'active detecting';
            else if (stage.key === 'anomaly' && data?.active_tracks > 0) stepClass = 'active tracking';
            else stepClass = 'active';
          }
          return (
            <React.Fragment key={stage.key}>
              <div className={`pipeline-step ${stepClass}`}>
                <span>{stage.icon}</span>
                <span>{stage.label}</span>
              </div>
              {i < PIPELINE_STAGES.length - 1 && (
                <span className={`pipeline-arrow ${cameraOn ? 'flowing' : ''}`}>→</span>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* ======== TOP METRICS ======== */}
      <div className="metrics-bar">
        <div className="metric-item">
          <div className={`metric-value ${snnSpike ? 'green pulse' : 'red'}`}>
            {snnSpike ? '⚡ SPIKE' : '— SKIP'}
          </div>
          <div className="metric-label">🧠 SNN GATE</div>
        </div>
        <div className="metric-item">
          <div className="metric-value cyan">{displayFps} <span style={{fontSize:11, opacity:0.6}}>/ {fpsLabel}</span></div>
          <div className="metric-label">🎬 ADAPTIVE FPS</div>
        </div>
        <div className="metric-item">
          <div className="metric-value orange">{data?.spike_rate || 0}%</div>
          <div className="metric-label">📡 SPIKE RATE</div>
        </div>
        <div className="metric-item">
          <div className="metric-value red">{data?.alerts || 0}</div>
          <div className="metric-label">🚨 ALERTS</div>
        </div>
        <div className="metric-item">
          <div className="metric-value cyan">{data?.active_tracks || 0}</div>
          <div className="metric-label">👤 TRACKED</div>
        </div>
        <div className="metric-item">
          <div className="metric-value green">{data?.frame_count || 0}</div>
          <div className="metric-label">🎞️ FRAMES</div>
        </div>
      </div>

      {/* ======== MAIN GRID ======== */}
      <div className="main-grid">

        {/* ---- Live Feed ---- */}
        <div className="panel feed-panel">
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className={cameraOn ? 'live-dot' : 'live-dot off'}></span>
              <span>LIVE FEED — CAM_01</span>
              {cameraOn && (
                <span className="uptime-badge">{formatUptime(uptime)}</span>
              )}
              {data?.session_name && (
                <span className="session-badge">{data.session_name}</span>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div className={`connection-status ${wsConnected || backendAlive ? 'connected' : 'disconnected'}`}>
                <span className="status-dot"></span>
                {wsConnected || backendAlive ? 'LIVE' : 'OFFLINE'}
              </div>
              <button
                className={`camera-toggle ${cameraOn ? 'on' : 'off'}`}
                onClick={toggleCamera}
                disabled={cameraLoading || !backendAlive}
              >
                {cameraLoading ? '⏳ ...' : cameraOn ? '⏹ STOP' : '▶ START'}
              </button>
            </div>
          </div>
          <div className={`feed-container ${category === 'IDLE' ? 'feed-idle' : category === 'EVENT' ? 'feed-event' : ''}`}>
            {cameraOn && data?.frame ? (
              <img
                ref={imgRef}
                src={`data:image/jpeg;base64,${data.frame}`}
                alt="Live Feed"
                className={`live-feed-img ${category === 'IDLE' ? 'feed-blur' : ''}`}
              />
            ) : (
              <div className="feed-placeholder">
                {!backendAlive
                  ? '⛔ Backend offline — start the server first'
                  : cameraOn
                    ? '⏳ Connecting to camera...'
                    : '📷 Camera is OFF — Click ▶ START to begin'}
              </div>
            )}
            {/* Score overlay */}
            <div className="score-overlay" style={{ borderColor: getScoreColor(score) }}>
              <div className="score-big" style={{ color: getScoreColor(score) }}>
                {score}
              </div>
              <div className="score-label-small">/100</div>
              <div className="score-cat">{getCategoryLabel(category)}</div>
            </div>
            {/* SNN membrane bar */}
            {cameraOn && (
              <div className="membrane-bar-container">
                <div className="membrane-bar-label">SNN MEMBRANE</div>
                <div className="membrane-bar-track">
                  <div
                    className={`membrane-bar-fill ${snnSpike ? 'spiked' : ''}`}
                    style={{ width: `${Math.min(snnMembrane / (data?.snn_threshold || 0.15) * 100, 100)}%` }}
                  />
                  <div className="membrane-threshold" style={{ left: '100%' }} />
                </div>
                <div className="membrane-bar-value">{snnMembrane.toFixed(3)}</div>
              </div>
            )}
          </div>

          {/* Detection badges */}
          {detections.length > 0 && (
            <div className="stat-row">
              {detections.slice(0, 5).map((det, i) => (
                <div key={i} className="stat-card">
                  <div className="stat-card-value" style={{ fontSize: 14 }}>
                    {det.class}
                  </div>
                  <div className="stat-card-label">
                    {(det.conf * 100).toFixed(0)}% CONF
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ---- Score Chart ---- */}
        <div className="panel">
          <div className="panel-header">📊 FRAME INTELLIGENCE SCORE</div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={scoreHistory}>
              <defs>
                <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#00d4ff" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="spikeGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00ff88" stopOpacity={0.2}/>
                  <stop offset="95%" stopColor="#00ff88" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#0d2d50" />
              <XAxis dataKey="time" stroke="#4a7a9b" tick={false} />
              <YAxis domain={[0, 100]} stroke="#4a7a9b" fontSize={10} />
              <Tooltip
                contentStyle={{
                  background: '#071428',
                  border: '1px solid #0d2d50',
                  borderRadius: 8,
                  fontSize: 12,
                  fontFamily: 'JetBrains Mono, monospace'
                }}
                labelStyle={{ color: '#4a7a9b' }}
              />
              <Area
                type="monotone" dataKey="spike"
                stroke="#00ff8855" strokeWidth={0}
                fill="url(#spikeGrad)" dot={false}
              />
              <Area
                type="monotone" dataKey="score"
                stroke="#00d4ff" strokeWidth={2}
                fill="url(#scoreGrad)" dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
          <div className="score-bar">
            <div className="zone zone-idle">IDLE 0–30</div>
            <div className="zone zone-normal">NORMAL 30–60</div>
            <div className="zone zone-event">EVENT 60–100</div>
          </div>

          {/* Adaptive FPS + SNN stats */}
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-card-value">{displayFps} fps</div>
              <div className="stat-card-label">CURRENT FPS</div>
            </div>
            <div className="stat-card">
              <div className="stat-card-value">{targetFps} fps</div>
              <div className="stat-card-label">TARGET FPS</div>
            </div>
            <div className="stat-card">
              <div className="stat-card-value">{data?.spike_rate || 0}%</div>
              <div className="stat-card-label">SPIKE RATE</div>
            </div>
          </div>
        </div>

        {/* ---- Alert Panel ---- */}
        <div className="panel alert-panel">
          <div className="panel-header">
            <span>🚨 REAL-TIME ANOMALY ALERTS</span>
            <span style={{ fontSize: 10, color: '#4a7a9b' }}>
              {alerts.length} active
            </span>
          </div>
          <div className="alert-list">
            {alerts.length === 0 && !data?.last_alert ? (
              <div className="no-alerts">✅ No active alerts — system nominal</div>
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
                      <span className="alert-timestamp">
                        {data.last_alert.timestamp ? new Date(data.last_alert.timestamp).toLocaleTimeString('en-IN', { hour12: false }) : ''}
                      </span>
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
                        {alert.timestamp ? new Date(alert.timestamp).toLocaleTimeString('en-IN', { hour12: false }) : ''}
                      </span>
                      <span className="alert-date">
                        {alert.timestamp ? new Date(alert.timestamp).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : ''}
                      </span>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>

        {/* ---- Event Clips (MP4) ---- */}
        <div className="panel clips-panel">
          <div className="panel-header">
            <span>🎬 RECORDED CLIPS — TIMELINE</span>
            <span style={{ fontSize: 10, color: '#4a7a9b' }}>
              {clips.length} clips • newest first
            </span>
          </div>
          <div className="clips-list">
            {clips.length === 0 ? (
              <div className="no-alerts">No clips yet — events auto-record when persons detected</div>
            ) : (
              clips.map((clip, i) => {
                const catClass = clip.category === 'EVENT' ? 'clip-event' : clip.category === 'NORMAL' ? 'clip-normal' : 'clip-idle';
                const qualityLabel = clip.quality || (clip.category === 'EVENT' ? 'HD' : clip.category === 'NORMAL' ? 'MEDIUM' : 'LOW');
                const qualityClass = qualityLabel === 'HD' ? 'quality-hd' : qualityLabel === 'MEDIUM' ? 'quality-med' : 'quality-low';
                const timeStr = clip.start_time ? new Date(clip.start_time).toLocaleTimeString('en-IN', { hour12: false }) : '';
                const dateStr = clip.start_time ? new Date(clip.start_time).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : '';
                const dur = clip.duration_sec ? `${clip.duration_sec}s` : '';
                return (
                  <a
                    key={i}
                    className={`clip-item ${catClass}`}
                    href={`${API_BASE}/api/clips/${clip.filename}`}
                    target="_blank" rel="noopener noreferrer"
                  >
                    <div className="clip-timeline-dot"></div>
                    <div className="clip-info">
                      <div className="clip-top-row">
                        <span className={`clip-quality-badge ${qualityClass}`}>{qualityLabel}</span>
                        <span className={`clip-category-badge ${catClass}`}>{clip.category}</span>
                        <span className="clip-fps">{clip.fps || 15} FPS</span>
                      </div>
                      <div className="clip-time-row">
                        <span className="clip-date">{dateStr}</span>
                        <span className="clip-time">{timeStr}</span>
                        <span className="clip-duration">{dur}</span>
                      </div>
                      <div className="clip-bottom-row">
                        <span className="clip-name-small">{clip.filename}</span>
                        <span className="clip-size">{clip.size_kb.toFixed(0)} KB</span>
                        <span className="clip-download">⬇ DOWNLOAD</span>
                      </div>
                    </div>
                  </a>
                );
              })
            )}
          </div>
        </div>

        {/* ---- Forensic Event Log ---- */}
        <div className="panel log-panel">
          <div className="panel-header">
            <span>📋 FORENSIC EVENT LOG — SMART CCTV DIARY</span>
            <span style={{ fontSize: 10, color: '#4a7a9b' }}>
              {events.length} records
            </span>
          </div>
          <div className="event-table-wrapper">
            <table className="event-table">
              <thead>
                <tr>
                  <th>DATE</th>
                  <th>TIME</th>
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
                    <td colSpan={8} style={{ textAlign: 'center', padding: 30, color: '#4a7a9b' }}>
                      No events recorded yet — start the camera to begin
                    </td>
                  </tr>
                ) : (
                  events.map((event, i) => {
                    const ts = event.timestamp ? new Date(event.timestamp) : null;
                    return (
                      <tr key={i}>
                        <td>{ts ? ts.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : '-'}</td>
                        <td style={{ fontFamily: 'JetBrains Mono, monospace' }}>
                          {ts ? ts.toLocaleTimeString('en-IN', { hour12: false }) : '-'}
                        </td>
                        <td>{event.frame_number}</td>
                        <td style={{ color: getScoreColor(event.score), fontWeight: 700 }}>
                          {event.score}
                        </td>
                        <td className={getCategoryClass(event.category)}>
                          {getCategoryLabel(event.category)}
                        </td>
                        <td>{event.event_type}</td>
                        <td>{event.person_count}</td>
                        <td className={`severity-${event.severity?.toLowerCase()}`}>
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
          <input
            className="session-input"
            type="text"
            placeholder="Session name (e.g. Demo_Loitering)"
            value={sessionName}
            onChange={e => setSessionName(e.target.value)}
          />
          <button className="session-btn clear-btn" onClick={clearSession} disabled={cameraOn}>
            🗑️ CLEAR DATA
          </button>
          <a
            className="session-btn export-btn"
            href={`${API_BASE}/api/export`}
            target="_blank" rel="noopener noreferrer"
          >
            📥 EXPORT CSV
          </a>
        </div>
      </div>

      {/* ======== FOOTER ======== */}
      <footer className="footer">
        <div className="quote">
          "Traditional CCTV records everything. EdgeVid LowBand remembers what matters."
        </div>
        <div className="footer-info">
          TEAM SPECTRUM // HACKARENA'26 // PS-04 // No cloud. No GPU. Just intelligence.
        </div>
      </footer>
    </div>
  );
}

export default App;
