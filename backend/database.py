"""
Forensic Event Log — Smart CCTV Diary
Auto-tagged, timestamped, court-ready. SQLite database.
"""

import sqlite3
import json
import csv
import os
from datetime import datetime


class ForensicDatabase:
    def __init__(self, db_path="storage/forensic_events.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate_tables()

    def _migrate_tables(self):
        """Add missing columns to existing tables (safe migrations)."""
        cursor = self.conn.cursor()
        # Check if session_name column exists in events table
        cursor.execute("PRAGMA table_info(events)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'session_name' not in columns:
            cursor.execute("ALTER TABLE events ADD COLUMN session_name TEXT")
            self.conn.commit()

    def _create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                camera_id TEXT DEFAULT 'CAM_01',
                session_name TEXT,
                frame_number INTEGER,
                score REAL,
                category TEXT,
                event_type TEXT,
                severity TEXT DEFAULT 'LOW',
                person_count INTEGER DEFAULT 0,
                max_confidence REAL DEFAULT 0.0,
                object_classes TEXT,
                zone TEXT,
                anomaly_flag INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0.0,
                frame_path TEXT,
                prebuffer_path TEXT,
                compression_type TEXT,
                description TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                alert_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                message TEXT,
                severity TEXT,
                acknowledged INTEGER DEFAULT 0,
                FOREIGN KEY (event_id) REFERENCES events(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_frames INTEGER,
                processed_frames INTEGER,
                compute_savings REAL,
                storage_savings REAL,
                spike_rate REAL,
                avg_score REAL
            )
        ''')

        self.conn.commit()

    def log_event(self, frame_number, score, category, detections,
                  event_type="ACTIVITY", severity="LOW", camera_id="CAM_01",
                  anomaly_flag=False, duration=0.0, frame_path=None,
                  prebuffer_path=None, compression_type=None, session_name=None):

        person_count = sum(1 for d in detections if d.get('is_person'))
        max_conf = max((d.get('confidence', 0) for d in detections), default=0.0)
        classes = list(set(d.get('class_name', 'unknown') for d in detections))

        description = self._auto_describe(
            event_type, person_count, score, category, duration
        )

        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO events
            (timestamp, camera_id, session_name, frame_number, score, category,
             event_type, severity, person_count, max_confidence,
             object_classes, anomaly_flag, duration_seconds,
             frame_path, prebuffer_path, compression_type, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(), camera_id, session_name, frame_number,
            score, category, event_type, severity, person_count,
            round(max_conf, 3), json.dumps(classes), int(anomaly_flag),
            duration, frame_path, prebuffer_path, compression_type,
            description
        ))
        self.conn.commit()
        return cursor.lastrowid

    def log_alert(self, event_id, alert_type, message, severity="HIGH"):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO alerts (event_id, alert_type, timestamp, message, severity)
            VALUES (?, ?, ?, ?, ?)
        ''', (event_id, alert_type, datetime.now().isoformat(), message, severity))
        self.conn.commit()
        return cursor.lastrowid

    def log_system_stats(self, total_frames, processed_frames,
                         compute_savings, storage_savings,
                         spike_rate, avg_score):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO system_stats
            (timestamp, total_frames, processed_frames, compute_savings,
             storage_savings, spike_rate, avg_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(), total_frames, processed_frames,
            compute_savings, storage_savings, spike_rate, avg_score
        ))
        self.conn.commit()

    def get_recent_events(self, limit=50, category=None):
        cursor = self.conn.cursor()
        query = "SELECT * FROM events"
        params = []
        if category:
            query += " WHERE category = ?"
            params.append(category)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_alerts(self, limit=20, unacknowledged_only=False):
        cursor = self.conn.cursor()
        query = "SELECT * FROM alerts"
        if unacknowledged_only:
            query += " WHERE acknowledged = 0"
        query += " ORDER BY id DESC LIMIT ?"
        cursor.execute(query, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_event_summary(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT category, COUNT(*) as count, AVG(score) as avg_score
            FROM events GROUP BY category
        """)
        return [dict(row) for row in cursor.fetchall()]

    def acknowledge_alert(self, alert_id):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,)
        )
        self.conn.commit()

    def export_to_csv(self, filepath="storage/forensic_export.csv"):
        events = self.get_recent_events(limit=99999)
        if not events:
            return None
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=events[0].keys())
            writer.writeheader()
            writer.writerows(events)
        return filepath

    def _auto_describe(self, event_type, person_count, score, category, duration):
        desc = f"[{category}] {event_type}"
        if person_count > 0:
            desc += f" — {person_count} person(s) detected"
        desc += f" — Score: {score}/100"
        if duration > 0:
            desc += f" — Duration: {duration:.0f}s"
        return desc

    def close(self):
        self.conn.close()