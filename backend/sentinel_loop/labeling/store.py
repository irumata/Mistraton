"""Labeling store (SQLite): labels, policy versions, reviewed alerts, retrain triggers.

Persisted for reproducibility. Postgres can replace this 1:1 in production —
the schema maps directly onto tables.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import AlertEvent, LabelledWindow, WindowState, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS labelled_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    label TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL DEFAULT '',
    validated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS policy_versions (
    version TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    verified INTEGER,
    reviewer TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrain_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- scheduled | new_incident | drift
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    handled INTEGER NOT NULL DEFAULT 0
);
"""


class LabelingStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (existing DBs)."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
        if "explanation" not in cols:
            conn.execute("ALTER TABLE alerts ADD COLUMN explanation TEXT NOT NULL DEFAULT ''")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- labels -------------------------------------------------------------
    def save_window(self, w: LabelledWindow) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO labelled_windows (metric, incident_id, window_start, window_end, label, evidence, policy_version, validated)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (w.metric, w.incident_id, w.window_start.isoformat(), w.window_end.isoformat(),
                 w.label.value, w.evidence, w.policy_version, int(w.validated)),
            )

    def save_windows(self, windows: list[LabelledWindow]) -> None:
        for w in windows:
            self.save_window(w)

    def get_windows(self, metric: str | None = None, incident_id: str | None = None) -> list[LabelledWindow]:
        query = "SELECT * FROM labelled_windows WHERE 1=1"
        args: list = []
        if metric:
            query += " AND metric=?"
            args.append(metric)
        if incident_id:
            query += " AND incident_id=?"
            args.append(incident_id)
        with self._conn() as conn:
            rows = conn.execute(query, args).fetchall()
        return [
            LabelledWindow(
                metric=r["metric"], incident_id=r["incident_id"],
                window_start=datetime.fromisoformat(r["window_start"]),
                window_end=datetime.fromisoformat(r["window_end"]),
                label=WindowState(r["label"]), evidence=r["evidence"],
                policy_version=r["policy_version"], validated=bool(r["validated"]),
            )
            for r in rows
        ]

    # -- policies -------------------------------------------------------------
    def save_policy_version(self, version: str, kind: str, content: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO policy_versions (version, kind, content, created_at) VALUES (?,?,?,?)",
                (version, kind, content, utcnow().isoformat()),
            )

    def policy_version(self, kind: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT version FROM policy_versions WHERE kind=? ORDER BY created_at DESC LIMIT 1", (kind,)
            ).fetchone()
        return row["version"] if row else ""

    # -- alerts / feedback ----------------------------------------------------
    def save_alert(self, alert: AlertEvent) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO alerts (alert_id, event_json, verified, explanation, reviewer, decided_at, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (alert.alert_id, alert.event.model_dump_json(),
                 None if alert.verified is None else int(alert.verified),
                 alert.explanation or "",
                 alert.reviewer,
                 alert.decided_at.isoformat() if alert.decided_at else None,
                 alert.created_at.isoformat()),
            )

    def _row_to_alert(self, row: sqlite3.Row) -> AlertEvent:
        from ..schemas import DetectionEvent

        return AlertEvent(
            alert_id=row["alert_id"],
            event=DetectionEvent.model_validate_json(row["event_json"]),
            verified=None if row["verified"] is None else bool(row["verified"]),
            explanation=row["explanation"] or None,
            reviewer=row["reviewer"],
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_alert(self, alert_id: str) -> AlertEvent | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if not row:
            return None
        return self._row_to_alert(row)

    def list_alerts(self, limit: int = 100) -> list[AlertEvent]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def record_review(self, alert_id: str, decision: str, reviewer: str) -> AlertEvent | None:
        """decision: 'confirmed' | 'rejected'. Flows back into the labeling store."""
        alert = self.get_alert(alert_id)
        if alert is None:
            return None
        alert.verified = decision == "confirmed"
        alert.reviewer = reviewer
        alert.decided_at = utcnow()
        self.save_alert(alert)
        return alert

    def reviewed_precision(self) -> tuple[float, int]:
        """Precision over HUMAN-reviewed alerts only (decided_at set by /feedback).

        Shieldstral verification at fire time is not a review; suppressed
        candidates are not stored as alerts at all."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT SUM(verified) AS c, COUNT(*) AS n FROM alerts WHERE decided_at IS NOT NULL"
            ).fetchone()
        n = row["n"] or 0
        c = row["c"] or 0
        return (c / n if n else 1.0), n

    # -- retrain triggers -------------------------------------------------------
    def add_trigger(self, kind: str, detail: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO retrain_triggers (kind, detail, created_at) VALUES (?,?,?)",
                (kind, detail, utcnow().isoformat()),
            )

    def pending_triggers(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM retrain_triggers WHERE handled=0").fetchall()
        return [dict(r) for r in rows]

    def mark_triggers_handled(self, ids: list[int]) -> None:
        with self._conn() as conn:
            for i in ids:
                conn.execute("UPDATE retrain_triggers SET handled=1 WHERE id=?", (i,))
