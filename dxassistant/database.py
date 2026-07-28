from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any
import time


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS decodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    heard_at TEXT NOT NULL,
    wsjtx_time_ms INTEGER NOT NULL,
    snr INTEGER NOT NULL,
    delta_time REAL NOT NULL,
    delta_frequency INTEGER NOT NULL,
    mode TEXT NOT NULL,
    message TEXT NOT NULL,
    call TEXT NOT NULL,
    grid TEXT,
    entity_id INTEGER,
    entity_name TEXT,
    continent TEXT,
    cq_zone INTEGER,
    itu_zone INTEGER,
    flag TEXT,
    distance REAL,
    bearing REAL,
    priority INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    wanted INTEGER NOT NULL DEFAULT 0,
    worked_call INTEGER NOT NULL DEFAULT 0,
    worked_entity INTEGER NOT NULL DEFAULT 0,
    confirmed_entity INTEGER NOT NULL DEFAULT 0,
    needed_on_band INTEGER NOT NULL DEFAULT 0,
    band TEXT
);

CREATE INDEX IF NOT EXISTS idx_decodes_heard_at ON decodes(heard_at DESC);
CREATE INDEX IF NOT EXISTS idx_decodes_call ON decodes(call);
CREATE INDEX IF NOT EXISTS idx_decodes_entity_id ON decodes(entity_id);
CREATE INDEX IF NOT EXISTS idx_qso_entity_id ON qso(entity_id);
CREATE INDEX IF NOT EXISTS idx_decodes_continent ON decodes(continent);
CREATE INDEX IF NOT EXISTS idx_decodes_cq_zone ON decodes(cq_zone);
CREATE INDEX IF NOT EXISTS idx_decodes_itu_zone ON decodes(itu_zone);

CREATE TABLE IF NOT EXISTS qso (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call TEXT NOT NULL,
    band TEXT,
    mode TEXT,
    grid TEXT,
    entity_id INTEGER,
    confirmed INTEGER NOT NULL DEFAULT 0,
    qso_date TEXT,
    time_on TEXT,
    UNIQUE(call, band, mode, qso_date)
);

CREATE TABLE IF NOT EXISTS wanted (
    pattern TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'call',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._cache_award = None
        self._cache_award_time = 0
        self._cache_top = None
        self._cache_top_time = 0
        with self.conn:
            # Migration: Recreate the qso table if it has the old schema (which included time_on in UNIQUE constraint)
            row = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='qso'").fetchone()
            if row and "time_on)" in row["sql"]:
                self.conn.execute("DROP TABLE qso")
                self.log_event("INFO", "Migrated qso table schema for robust deduplication")
            self.conn.executescript(SCHEMA)
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self.lock, self.conn:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def log_event(self, level: str, message: str) -> None:
        self.execute(
            "INSERT INTO app_events(level, message) VALUES (?, ?)",
            (level, message),
        )

    def wanted_patterns(self) -> list[dict[str, Any]]:
        return self.query("SELECT pattern, kind, note FROM wanted ORDER BY pattern")

    def is_wanted(self, call: str) -> bool:
        call = call.upper()
        for item in self.wanted_patterns():
            pattern = item["pattern"].upper()
            if item["kind"] == "prefix" and call.startswith(pattern):
                return True
            if item["kind"] == "call" and call == pattern:
                return True
        return False

    def worked_call(self, call: str) -> bool:
        return bool(self.scalar("SELECT 1 FROM qso WHERE call=? LIMIT 1", (call.upper(),)))

    def worked_entity(self, entity_id: int) -> bool:
        if not entity_id:
            return False
        return bool(self.scalar("SELECT 1 FROM qso WHERE entity_id=? LIMIT 1", (entity_id,)))

    def confirmed_entity(self, entity_id: int) -> bool:
        if not entity_id:
            return False
        return bool(self.scalar(
            "SELECT 1 FROM qso WHERE entity_id=? AND confirmed=1 LIMIT 1",
            (entity_id,),
        ))

    def entity_on_band(self, entity_id: int, band: str) -> bool:
        if not entity_id or not band:
            return False
        return bool(self.scalar(
            "SELECT 1 FROM qso WHERE entity_id=? AND band=? LIMIT 1",
            (entity_id, band),
        ))


    def award_breakdown(self) -> dict[str, Any]:
        if self._cache_award and time.monotonic() - self._cache_award_time < 5.0:
            return self._cache_award
        bands = self.query(
            """SELECT COALESCE(band,'Unknown') AS band,
                      COUNT(DISTINCT entity_id) AS worked,
                      COUNT(DISTINCT CASE WHEN confirmed=1 THEN entity_id END) AS confirmed
               FROM qso
               WHERE entity_id IS NOT NULL AND entity_id>0
               GROUP BY band
               ORDER BY worked DESC"""
        )
        continents = self.query(
            """SELECT d.continent AS continent,
                      COUNT(DISTINCT q.entity_id) AS worked
               FROM qso q
               JOIN decodes d ON d.entity_id=q.entity_id
               WHERE q.entity_id IS NOT NULL AND q.entity_id>0
               GROUP BY d.continent"""
        )
        zones = {
            "cq": self.scalar(
                """SELECT COUNT(DISTINCT d.cq_zone)
                   FROM qso q JOIN decodes d ON d.entity_id=q.entity_id
                   WHERE d.cq_zone>0"""
            ) or 0,
            "itu": self.scalar(
                """SELECT COUNT(DISTINCT d.itu_zone)
                   FROM qso q JOIN decodes d ON d.entity_id=q.entity_id
                   WHERE d.itu_zone>0"""
            ) or 0,
        }
        self._cache_award = {"bands": bands, "continents": continents, "zones": zones}
        self._cache_award_time = time.monotonic()
        return self._cache_award

    def top_entities(self, limit: int = 20) -> list[dict[str, Any]]:
        if self._cache_top and time.monotonic() - self._cache_top_time < 5.0:
            return self._cache_top
        res = self.query(
            """SELECT entity_name, flag, continent,
                      COUNT(*) AS heard,
                      MAX(priority) AS best_score,
                      MAX(heard_at) AS last_heard
               FROM decodes
               WHERE entity_id IS NOT NULL AND entity_id>0
               GROUP BY entity_id, entity_name, flag, continent
               ORDER BY heard DESC
               LIMIT ?""",
            (limit,),
        )


    def propagation_history(self, hours: int = 12) -> list[dict[str, Any]]:
        hours = max(1, min(hours, 168))
        return self.query(
            """SELECT substr(heard_at, 1, 13) || ':00:00Z' AS bucket,
                      continent,
                      COUNT(*) AS stations,
                      ROUND(AVG(snr), 1) AS avg_snr,
                      ROUND(AVG(distance), 0) AS avg_distance,
                      MAX(distance) AS max_distance
               FROM decodes
               WHERE heard_at >= datetime('now', ?)
                 AND continent IS NOT NULL AND continent <> ''
               GROUP BY bucket, continent
               ORDER BY bucket ASC, stations DESC""",
            (f"-{hours} hours",),
        )

    def radar(self, minutes: int = 15) -> list[dict[str, Any]]:
        minutes = max(1, min(minutes, 180))
        return self.query(
            """SELECT entity_id, entity_name, flag, continent,
                      COUNT(DISTINCT call) AS stations,
                      COUNT(*) AS decodes,
                      ROUND(AVG(snr), 1) AS avg_snr,
                      MAX(snr) AS best_snr,
                      ROUND(AVG(distance), 0) AS avg_distance,
                      MAX(priority) AS best_score,
                      MAX(heard_at) AS last_heard,
                      MAX(wanted) AS wanted,
                      MIN(worked_entity) AS has_new_entity,
                      MAX(needed_on_band) AS needed_on_band
               FROM decodes
               WHERE heard_at >= datetime('now', ?)
                 AND entity_id IS NOT NULL AND entity_id > 0
               GROUP BY entity_id, entity_name, flag, continent
               ORDER BY best_score DESC, stations DESC, avg_snr DESC
               LIMIT 80""",
            (f"-{minutes} minutes",),
        )

    def band_summary(self, minutes: int = 15) -> list[dict[str, Any]]:
        minutes = max(1, min(minutes, 180))
        return self.query(
            """SELECT COALESCE(band, 'Unknown') AS band,
                      COUNT(*) AS decodes,
                      COUNT(DISTINCT call) AS stations,
                      COUNT(DISTINCT entity_id) AS entities,
                      ROUND(AVG(snr), 1) AS avg_snr,
                      ROUND(AVG(distance), 0) AS avg_distance,
                      MAX(distance) AS max_distance
               FROM decodes
               WHERE heard_at >= datetime('now', ?)
               GROUP BY band
               ORDER BY stations DESC""",
            (f"-{minutes} minutes",),
        )

    def searchable_history(
        self,
        *,
        limit: int = 500,
        query: str = "",
        band: str = "",
        entity: str = "",
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if query:
            clauses.append("(call LIKE ? OR grid LIKE ? OR message LIKE ? OR entity_name LIKE ?)")
            like = f"%{query.upper()}%"
            params.extend([like, like, like, like])
        if band:
            clauses.append("band=?")
            params.append(band)
        if entity:
            clauses.append("entity_name LIKE ?")
            params.append(f"%{entity}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(limit, 1), 5000))
        return self.query(
            f"""SELECT * FROM decodes{where}
                ORDER BY id DESC LIMIT ?""",
            tuple(params),
        )

    def entity_profile(self, entity_id: int) -> dict[str, Any]:
        summary = self.query(
            """SELECT entity_id, entity_name, flag, continent, cq_zone, itu_zone,
                      COUNT(*) AS total_decodes,
                      COUNT(DISTINCT call) AS calls_heard,
                      MAX(snr) AS best_snr,
                      ROUND(AVG(snr), 1) AS avg_snr,
                      MIN(heard_at) AS first_heard,
                      MAX(heard_at) AS last_heard,
                      ROUND(MAX(distance), 0) AS farthest
               FROM decodes WHERE entity_id=? GROUP BY entity_id""",
            (entity_id,),
        )
        bands = self.query(
            """SELECT band, COUNT(*) AS decodes, COUNT(DISTINCT call) AS calls,
                      MAX(snr) AS best_snr, ROUND(AVG(snr),1) AS avg_snr
               FROM decodes WHERE entity_id=? GROUP BY band ORDER BY decodes DESC""",
            (entity_id,),
        )
        return {"summary": summary[0] if summary else {}, "bands": bands}

    def stats(self) -> dict[str, int]:
        return {
            "decodes": self.scalar("SELECT COUNT(*) FROM decodes") or 0,
            "qsos": self.scalar("SELECT COUNT(*) FROM qso") or 0,
            "dxcc_worked": self.scalar(
                "SELECT COUNT(DISTINCT entity_id) FROM qso WHERE entity_id IS NOT NULL AND entity_id>0"
            ) or 0,
            "dxcc_confirmed": self.scalar(
                "SELECT COUNT(DISTINCT entity_id) FROM qso WHERE confirmed=1 AND entity_id IS NOT NULL AND entity_id>0"
            ) or 0,
            "wanted": self.scalar("SELECT COUNT(*) FROM wanted") or 0,
        }
