"""SQLite storage. One connection guarded by a lock; all timestamps are UTC epoch seconds."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, lat REAL, lon REAL, zone TEXT,
  ph REAL, "do" REAL, temp REAL, ec REAL, nitrate REAL, turbidity REAL,
  source TEXT, mission_id INTEGER, flagged INTEGER DEFAULT 0,
  truth REAL                       -- simulator only: hidden plume strength 0..1. Never given to the agents.
);
CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts);
CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY, started REAL, ended REAL, zone TEXT, lat REAL, lon REAL,
  params TEXT, peak TEXT, baseline TEXT, status TEXT, linked_to INTEGER,
  source_lat REAL, source_lon REAL, source_note TEXT, replan TEXT
);
CREATE TABLE IF NOT EXISTS diagnoses (id INTEGER PRIMARY KEY, anomaly_id INTEGER, ts REAL, body TEXT);
CREATE TABLE IF NOT EXISTS missions (
  id INTEGER PRIMARY KEY, ts REAL, request TEXT, summary TEXT, planner TEXT, waypoints TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, ts REAL, title TEXT, ai INTEGER, markdown TEXT, pdf TEXT);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, ts REAL, anomaly_id INTEGER, body TEXT);
"""
JSON_COLS = {"params", "peak", "baseline", "body", "waypoints", "replan", "source_info", "meta"}
TABLES = ("readings", "anomalies", "diagnoses", "missions", "reports", "alerts")
READING_COLS = ("ts", "lat", "lon", "zone", "ph", "do", "temp", "ec", "nitrate", "turbidity",
                "source", "mission_id", "flagged", "truth")


class Store:
    def __init__(self, path: Path | str = config.DB_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            # Columns added after the first release; ignored when they already exist
            for table, col in (("anomalies", "source_info"), ("missions", "meta")):
                try:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            self._db.execute("DELETE FROM readings WHERE ts < ?", (time.time() - 14 * 86400,))
            self._db.commit()

    # ------------------------------------------------------------------ generic helpers
    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._decode(dict(r)) for r in rows]

    @staticmethod
    def _decode(row: dict) -> dict:
        for k in JSON_COLS & row.keys():
            if isinstance(row[k], str):
                try:
                    row[k] = json.loads(row[k])
                except ValueError:
                    pass
        return row

    def insert(self, table: str, **cols) -> int:
        assert table in TABLES
        vals = [json.dumps(v) if k in JSON_COLS and not isinstance(v, (str, type(None))) else v for k, v in cols.items()]
        names = ",".join(f'"{k}"' for k in cols)
        with self._lock:
            cur = self._db.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join('?' * len(cols))})", vals)
            self._db.commit()
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, **cols) -> None:
        assert table in TABLES
        vals = [json.dumps(v) if k in JSON_COLS and not isinstance(v, (str, type(None))) else v for k, v in cols.items()]
        sets = ",".join(f'"{k}"=?' for k in cols)
        with self._lock:
            self._db.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*vals, row_id))
            self._db.commit()

    def get(self, table: str, row_id: int) -> dict | None:
        assert table in TABLES
        rows = self._rows(f"SELECT * FROM {table} WHERE id=?", (row_id,))
        return rows[0] if rows else None

    def latest(self, table: str, limit: int = 20, since: float | None = None, ts_col: str = "ts") -> list[dict]:
        assert table in TABLES
        if since is None:
            return self._rows(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,))
        return self._rows(f"SELECT * FROM {table} WHERE {ts_col}>=? ORDER BY id DESC LIMIT ?", (since, limit))

    def reset(self) -> None:
        with self._lock:
            for t in TABLES:
                self._db.execute(f"DELETE FROM {t}")
            self._db.commit()

    # ------------------------------------------------------------------ readings
    def add_reading(self, r: dict) -> int:
        return self.insert("readings", **{k: r.get(k) for k in READING_COLS})

    def recent_readings(self, limit: int = 600, zone: str | None = None, since: float | None = None,
                        with_truth: bool = False) -> list[dict]:
        where, args = ["1=1"], []
        if zone:
            where.append("zone=?")
            args.append(zone)
        if since is not None:
            where.append("ts>=?")
            args.append(since)
        rows = self._rows(f"SELECT * FROM readings WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*args, limit))
        if not with_truth:
            for r in rows:
                r.pop("truth", None)
        return rows[::-1]

    def stats(self, param: str, since: float, until: float | None = None) -> list[dict]:
        """Per-zone count/min/mean/max of one parameter between two times."""
        assert param in config.PARAMS
        until = until or time.time() + 1
        return self._rows(
            f'SELECT zone, COUNT("{param}") n, MIN("{param}") min, AVG("{param}") mean, MAX("{param}") max, '
            f'SUM(flagged) flagged FROM readings WHERE ts>=? AND ts<? AND "{param}" IS NOT NULL GROUP BY zone ORDER BY zone',
            (since, until))

    def confusion(self, since: float, truth_min: float) -> dict:
        """Reading-level detector results against the simulator's hidden plume strength."""
        r = self._rows(
            "SELECT SUM(flagged=1 AND truth>=?) tp, SUM(flagged=1 AND truth<?) fp, SUM(flagged=0 AND truth>=?) fn, "
            "SUM(flagged=0 AND truth<?) tn, COUNT(*) n FROM readings WHERE ts>=? AND truth IS NOT NULL",
            (truth_min, truth_min, truth_min, truth_min, since))[0]
        return {k: int(v or 0) for k, v in r.items()}

    def count_readings(self, since: float) -> int:
        return int(self._rows("SELECT COUNT(*) n FROM readings WHERE ts>=?", (since,))[0]["n"])
