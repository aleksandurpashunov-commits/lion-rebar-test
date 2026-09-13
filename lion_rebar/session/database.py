from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    started_at: str
    ended_at: str | None
    status: str
    app_version: str
    event_count: int
    last_event_at: str | None


class SessionDatabase:
    """Small durable SQLite store for cross-project work sessions.

    The database is deliberately independent from every project folder.  Each
    project keeps its own engineering evidence, while this store provides the
    chronological index needed to reconstruct one workday across many folders.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    last_event_at TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS projects (
                    session_id TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    source_sha256 TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, project_root),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stable_event_id TEXT,
                    session_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'INFO',
                    project_name TEXT,
                    project_root TEXT,
                    source_sha256 TEXT,
                    engine_id TEXT,
                    engine_version TEXT,
                    engine_sha256 TEXT,
                    matcher_sha256 TEXT,
                    annotation_handle TEXT,
                    annotation_text TEXT,
                    report_path TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_events_session_time
                    ON events(session_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_events_session_type
                    ON events(session_id, event_type);
                CREATE INDEX IF NOT EXISTS idx_events_annotation
                    ON events(session_id, annotation_handle);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
            if "stable_event_id" not in columns:
                connection.execute("ALTER TABLE events ADD COLUMN stable_event_id TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_stable_event_id
                ON events(stable_event_id)
                WHERE stable_event_id IS NOT NULL
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def create_session(
        self,
        *,
        session_id: str,
        started_at: str,
        app_version: str,
    ) -> SessionRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, started_at, ended_at, status, app_version,
                    event_count, last_event_at, summary_json
                ) VALUES(?, ?, NULL, 'ACTIVE', ?, 0, NULL, '{}')
                """,
                (session_id, started_at, app_version),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return SessionRecord(
            session_id=str(row["session_id"]),
            started_at=str(row["started_at"]),
            ended_at=str(row["ended_at"]) if row["ended_at"] else None,
            status=str(row["status"]),
            app_version=str(row["app_version"]),
            event_count=int(row["event_count"] or 0),
            last_event_at=str(row["last_event_at"]) if row["last_event_at"] else None,
        )

    def active_session(self, session_id: str) -> SessionRecord | None:
        try:
            record = self.get_session(session_id)
        except KeyError:
            return None
        return record if record.status == "ACTIVE" else None

    def record_event(
        self,
        *,
        session_id: str,
        occurred_at: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        severity: str = "INFO",
        project_name: str | None = None,
        project_root: str | None = None,
        source_sha256: str | None = None,
        engine_id: str | None = None,
        engine_version: str | None = None,
        engine_sha256: str | None = None,
        matcher_sha256: str | None = None,
        annotation_handle: str | None = None,
        annotation_text: str | None = None,
        report_path: str | None = None,
        stable_event_id: str | None = None,
    ) -> int:
        event_name = str(event_type or "").strip().upper()
        if not event_name:
            raise ValueError("event_type is required")
        encoded = json.dumps(
            _json_safe(dict(payload or {})),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connect() as connection:
            session = connection.execute(
                "SELECT status FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None or str(session["status"]) != "ACTIVE":
                raise RuntimeError("The work session is not active.")
            stable_id = str(stable_event_id or "").strip() or None
            if stable_id:
                existing = connection.execute(
                    "SELECT event_id FROM events WHERE stable_event_id = ?",
                    (stable_id,),
                ).fetchone()
                if existing is not None:
                    return int(existing["event_id"])
            cursor = connection.execute(
                """
                INSERT INTO events(
                    stable_event_id, session_id, occurred_at, event_type, severity,
                    project_name, project_root, source_sha256,
                    engine_id, engine_version, engine_sha256, matcher_sha256,
                    annotation_handle, annotation_text, report_path, payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id,
                    session_id,
                    occurred_at,
                    event_name,
                    str(severity or "INFO").upper(),
                    project_name,
                    project_root,
                    source_sha256,
                    engine_id,
                    engine_version,
                    engine_sha256,
                    matcher_sha256,
                    annotation_handle,
                    annotation_text,
                    report_path,
                    encoded,
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET event_count = event_count + 1, last_event_at = ?
                WHERE session_id = ?
                """,
                (occurred_at, session_id),
            )
            if project_root:
                connection.execute(
                    """
                    INSERT INTO projects(
                        session_id, project_root, project_name, source_sha256,
                        first_seen_at, last_seen_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, project_root) DO UPDATE SET
                        project_name = excluded.project_name,
                        source_sha256 = COALESCE(excluded.source_sha256, projects.source_sha256),
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        session_id,
                        project_root,
                        project_name or Path(project_root).name,
                        source_sha256,
                        occurred_at,
                        occurred_at,
                    ),
                )
            return int(cursor.lastrowid)

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE session_id = ?
                ORDER BY occurred_at, event_id
                """,
                (session_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": int(row["event_id"]),
                    "stable_event_id": row["stable_event_id"],
                    "session_id": str(row["session_id"]),
                    "at": str(row["occurred_at"]),
                    "event_type": str(row["event_type"]),
                    "severity": str(row["severity"]),
                    "project_name": row["project_name"],
                    "project_root": row["project_root"],
                    "source_sha256": row["source_sha256"],
                    "engine_id": row["engine_id"],
                    "engine_version": row["engine_version"],
                    "engine_sha256": row["engine_sha256"],
                    "matcher_sha256": row["matcher_sha256"],
                    "annotation_handle": row["annotation_handle"],
                    "annotation_text": row["annotation_text"],
                    "report_path": row["report_path"],
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        return result

    def list_recent_events(self, session_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        count = max(1, int(limit))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE session_id = ?
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT ?
                """,
                (session_id, count),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": int(row["event_id"]),
                    "stable_event_id": row["stable_event_id"],
                    "session_id": str(row["session_id"]),
                    "at": str(row["occurred_at"]),
                    "event_type": str(row["event_type"]),
                    "severity": str(row["severity"]),
                    "project_name": row["project_name"],
                    "project_root": row["project_root"],
                    "source_sha256": row["source_sha256"],
                    "engine_id": row["engine_id"],
                    "engine_version": row["engine_version"],
                    "engine_sha256": row["engine_sha256"],
                    "matcher_sha256": row["matcher_sha256"],
                    "annotation_handle": row["annotation_handle"],
                    "annotation_text": row["annotation_text"],
                    "report_path": row["report_path"],
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        return result

    def list_projects(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM projects
                WHERE session_id = ?
                ORDER BY first_seen_at, project_name
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_session(
        self,
        *,
        session_id: str,
        ended_at: str,
        summary: Mapping[str, Any],
    ) -> None:
        encoded = json.dumps(
            _json_safe(dict(summary)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET ended_at = ?, status = 'ENDED', summary_json = ?
                WHERE session_id = ? AND status = 'ACTIVE'
                """,
                (ended_at, encoded, session_id),
            )

    def summary(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        with self._lock, self._connect() as connection:
            count_rows = connection.execute(
                """
                SELECT event_type, COUNT(*) AS value
                FROM events
                WHERE session_id = ?
                GROUP BY event_type
                """,
                (session_id,),
            ).fetchall()
            project_count_row = connection.execute(
                "SELECT COUNT(*) AS value FROM projects WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            decision_rows = connection.execute(
                """
                SELECT event_type, payload_json
                FROM events
                WHERE session_id = ? AND event_type IN (
                    'APPROVED', 'BULK_APPROVED', 'ENGINEER_RESOLVED',
                    'PROBLEM_RECORDED', 'EXCLUDED_BY_ENGINEER',
                    'MANUAL_ASSIGNED', 'MANUAL_SHAPE_DRAFT',
                    'FLAGGED', 'NO_VALID_CANDIDATE', 'MISSING_MASTER',
                    'GROUP_APPLIED', 'GROUP_UNDONE'
                )
                """,
                (session_id,),
            ).fetchall()
        counts = Counter(
            {str(row["event_type"]): int(row["value"] or 0) for row in count_rows}
        )
        decisions = 0
        for row in decision_rows:
            event_type = str(row["event_type"] or "")
            if event_type == "GROUP_UNDONE":
                continue
            if event_type not in {"BULK_APPROVED", "GROUP_APPLIED"}:
                decisions += 1
                continue
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            items = None
            if isinstance(payload, dict):
                items = payload.get("decisions") or payload.get("consequences")
            handles = payload.get("annotation_handles") if isinstance(payload, dict) else None
            if isinstance(items, list) and items:
                decisions += len(items)
            elif isinstance(handles, list) and handles:
                decisions += len(handles)
            else:
                decisions += 1
        return {
            "session_id": session.session_id,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "status": session.status,
            "app_version": session.app_version,
            "event_count": sum(counts.values()),
            "decision_count": decisions,
            "project_count": int(project_count_row["value"] or 0) if project_count_row else 0,
            "event_counts": dict(sorted(counts.items())),
            "projects": self.list_projects(session_id),
            "last_event_at": session.last_event_at,
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
