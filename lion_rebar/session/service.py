from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.config.constants import (
    APP_DATA_FOLDER,
    APP_VERSION,
    RECOGNITION_MATCHER_SHA256,
)
from lion_rebar.session.database import SessionDatabase
from lion_rebar.session.learning_pack import (
    LearningPackResult,
    LearningPackStorageError,
    build_learning_pack,
    publish_learning_pack,
)
from lion_rebar.utils.app_paths import AppPaths
from lion_rebar.utils.files import atomic_write_json


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    started_at: str
    status: str
    event_count: int
    decision_count: int
    project_count: int
    event_counts: dict[str, int]
    last_event_at: str | None


class SessionService:
    """Persistent session coordinator shared by all project folders."""

    def __init__(self, paths: AppPaths, *, app_version: str = APP_VERSION):
        self.paths = paths
        self.app_version = app_version
        self.database = SessionDatabase(paths.session_db)
        self._session_id: str | None = None
        self._started_in_process = False
        self.start_or_resume()

    @property
    def has_active_session(self) -> bool:
        return bool(
            self._session_id
            and self.database.active_session(self._session_id) is not None
        )

    @property
    def session_id(self) -> str:
        return self.start_or_resume()

    def start_or_resume(self) -> str:
        if self._session_id:
            active = self.database.active_session(self._session_id)
            if active is not None:
                return self._session_id
            self._session_id = None
        state = self._read_state()
        candidate = str(state.get("session_id") or "")
        if candidate and self.database.active_session(candidate) is not None:
            self._session_id = candidate
            if not self._started_in_process:
                self.record_system_event("SESSION_RESUMED", {"host": socket.gethostname()})
            self._started_in_process = True
            return candidate

        now = _now()
        session_id = f"S-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.database.create_session(
            session_id=session_id,
            started_at=now,
            app_version=self.app_version,
        )
        self._session_id = session_id
        self._write_state({"session_id": session_id, "started_at": now, "status": "ACTIVE"})
        self._started_in_process = True
        self.record_system_event(
            "SESSION_STARTED",
            {
                "host": socket.gethostname(),
                "process_id": os.getpid(),
                "application_version": self.app_version,
            },
        )
        return session_id

    def record_system_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        severity: str = "INFO",
    ) -> int:
        session_id = self._ensure_active_without_recursion()
        event_id = self.database.record_event(
            session_id=session_id,
            occurred_at=_now(),
            event_type=event_type,
            payload=payload,
            severity=severity,
            matcher_sha256=RECOGNITION_MATCHER_SHA256,
        )
        self.checkpoint()
        return event_id

    def record_project_event(
        self,
        project: Any,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        severity: str = "INFO",
        report_path: Path | str | None = None,
        occurred_at: str | None = None,
    ) -> int:
        session_id = self._ensure_active_without_recursion()
        root = Path(str(getattr(project, "root", "") or ""))
        extensions = getattr(project, "extensions", {})
        extensions = extensions if isinstance(extensions, dict) else {}
        event_payload = dict(payload or {})
        event_payload.setdefault("application_version", self.app_version)
        human_decision = event_payload.get("human_decision")
        human_decision = human_decision if isinstance(human_decision, Mapping) else {}
        stable_event_id = str(
            event_payload.get("stable_event_id") or human_decision.get("event_id") or ""
        ).strip() or None
        event_id = self.database.record_event(
            session_id=session_id,
            occurred_at=occurred_at or _now(),
            event_type=event_type,
            payload=event_payload,
            severity=severity,
            project_name=str(getattr(project, "name", root.name) or root.name),
            project_root=str(root),
            source_sha256=str(extensions.get("source_sha256") or "") or None,
            engine_id=str(getattr(project, "engine_id", "") or "") or None,
            engine_version=str(getattr(project, "engine_version", "") or "") or None,
            engine_sha256=str(getattr(project, "engine_sha256", "") or "") or None,
            matcher_sha256=RECOGNITION_MATCHER_SHA256,
            annotation_handle=_first_text(event_payload, "annotation_handle"),
            annotation_text=_first_text(event_payload, "annotation_text"),
            report_path=str(report_path) if report_path else None,
            stable_event_id=stable_event_id,
        )
        self.checkpoint()
        return event_id

    def summary(self) -> SessionSummary:
        session_id = self.session_id
        return self._summary_for(session_id)

    def active_summary(self) -> SessionSummary | None:
        if not self.has_active_session:
            return None
        return self._summary_for(str(self._session_id))

    def _summary_for(self, session_id: str) -> SessionSummary:
        payload = self.database.summary(session_id)
        return SessionSummary(
            session_id=session_id,
            started_at=str(payload.get("started_at") or ""),
            status=str(payload.get("status") or "ACTIVE"),
            event_count=int(payload.get("event_count") or 0),
            decision_count=int(payload.get("decision_count") or 0),
            project_count=int(payload.get("project_count") or 0),
            event_counts={
                str(key): int(value or 0)
                for key, value in dict(payload.get("event_counts") or {}).items()
            },
            last_event_at=str(payload.get("last_event_at")) if payload.get("last_event_at") else None,
        )

    def checkpoint(self) -> Path:
        session_id = self._session_id
        if not session_id:
            return self.paths.session_recovery_file
        try:
            summary = self.database.summary(session_id)
            events = self.database.list_recent_events(session_id, limit=25)
        except Exception:
            return self.paths.session_recovery_file
        payload = {
            "schema": "lion-rebar-session-recovery/v1",
            "updated_at": _now(),
            "summary": summary,
            "recent_events": events[-25:],
        }
        atomic_write_json(self.paths.session_recovery_file, payload)
        return self.paths.session_recovery_file

    def end_current_session(
        self,
        *,
        publish_dir: Path | None = None,
        output_dir: Path | None = None,
    ) -> LearningPackResult:
        """Close the active session after a verified local pack exists.

        ``output_dir`` is retained as a compatibility alias for older UI/tests;
        it is treated only as a secondary publication target.  The canonical
        archive is always created in local application storage first.
        """

        if publish_dir is None and output_dir is not None:
            publish_dir = Path(output_dir)

        session_id = self.session_id
        record = self.database.get_session(session_id)
        ended_at = _now()
        events = self.database.list_events(session_id)
        projects = self.database.list_projects(session_id)

        primary_destination = Path(self.paths.learning_packs)
        local_storage_warning: str | None = None
        try:
            result = build_learning_pack(
                session_id=session_id,
                started_at=record.started_at,
                ended_at=ended_at,
                events=events,
                projects=projects,
                output_dir=primary_destination,
            )
        except LearningPackStorageError as primary_exc:
            # Last-resort local fallback.  A corrupt/stale AppData LATEST or a
            # filesystem filter must not discard a whole workday.
            fallback_destination = (
                Path(tempfile.gettempdir())
                / "LionBuild"
                / APP_DATA_FOLDER
                / "learning_pack_recovery"
            )
            try:
                result = build_learning_pack(
                    session_id=session_id,
                    started_at=record.started_at,
                    ended_at=ended_at,
                    events=events,
                    projects=projects,
                    output_dir=fallback_destination,
                )
            except Exception as fallback_exc:
                raise LearningPackStorageError(
                    "Learning Pack не можа да бъде създаден нито в основната "
                    f"локална папка ({primary_destination}), нито в аварийната "
                    f"локална папка ({fallback_destination}). Основна грешка: "
                    f"{primary_exc}. Аварийна грешка: {fallback_exc}"
                ) from fallback_exc
            local_storage_warning = (
                "Основната локална папка за Learning Pack даде грешка, но "
                "пакетът е създаден и проверен в аварийната локална папка. "
                f"Основна грешка: {primary_exc}"
            )
            result = replace(
                result,
                warning_count=result.warning_count + 1,
                local_storage_warning=local_storage_warning,
            )

        # Publication is explicitly secondary.  Even an unexpected exception in
        # the publisher cannot reopen or invalidate the verified local session.
        if publish_dir is not None:
            try:
                result = publish_learning_pack(
                    result,
                    destination_dir=Path(publish_dir),
                )
            except Exception as exc:  # defensive: publisher normally catches all
                result = replace(
                    result,
                    warning_count=result.warning_count + 1,
                    publication_warning=(
                        "Learning Pack е създаден и проверен локално, но "
                        f"публикуването към {publish_dir} прекъсна: {exc}"
                    ),
                )

        summary = self.database.summary(session_id)
        summary.update(
            {
                # The pack expands grouped consequences and is the canonical
                # source for the final learning counters.
                "decision_count": result.decision_count,
                "active_decision_count": result.active_decision_count,
                "candidate_count": result.candidate_count,
                "calibration_group_count": result.calibration_group_count,
                "independent_drawing_family_count": result.independent_drawing_family_count,
                "activated_rule_count": 0,
                "ended_at": ended_at,
                "learning_pack": str(result.path),
                "learning_pack_sha256": _sha256(result.path),
                "learning_pack_local_storage_warning": result.local_storage_warning,
                "learning_pack_published": (
                    str(result.published_path) if result.published_path else None
                ),
                "learning_pack_publication_warning": result.publication_warning,
            }
        )
        self.database.finish_session(
            session_id=session_id,
            ended_at=ended_at,
            summary=summary,
        )
        self._write_state(
            {
                "session_id": session_id,
                "started_at": record.started_at,
                "ended_at": ended_at,
                "status": "ENDED",
                "learning_pack": str(result.path),
                "learning_pack_local_storage_warning": result.local_storage_warning,
                "learning_pack_published": (
                    str(result.published_path) if result.published_path else None
                ),
                "learning_pack_publication_warning": result.publication_warning,
            }
        )
        atomic_write_json(
            self.paths.session_recovery_file,
            {
                "schema": "lion-rebar-session-recovery/v1",
                "updated_at": ended_at,
                "status": "ENDED",
                "summary": summary,
                "learning_pack": str(result.path),
                "learning_pack_latest": str(result.latest_path),
                "learning_pack_local_storage_warning": result.local_storage_warning,
                "learning_pack_published": (
                    str(result.published_path) if result.published_path else None
                ),
                "learning_pack_published_latest": (
                    str(result.published_latest_path)
                    if result.published_latest_path
                    else None
                ),
                "learning_pack_publication_warning": result.publication_warning,
            },
        )
        self._session_id = None
        self._started_in_process = False
        return result

    def _ensure_active_without_recursion(self) -> str:
        if self._session_id and self.database.active_session(self._session_id) is not None:
            return self._session_id
        state = self._read_state()
        candidate = str(state.get("session_id") or "")
        if candidate and self.database.active_session(candidate) is not None:
            self._session_id = candidate
            return candidate
        now = _now()
        session_id = f"S-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.database.create_session(
            session_id=session_id,
            started_at=now,
            app_version=self.app_version,
        )
        self._session_id = session_id
        self._write_state({"session_id": session_id, "started_at": now, "status": "ACTIVE"})
        return session_id

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.paths.session_state_file.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, payload: Mapping[str, Any]) -> None:
        atomic_write_json(self.paths.session_state_file, dict(payload))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _first_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None and isinstance(payload.get("decision"), Mapping):
        value = payload["decision"].get(key)
    text = str(value or "").strip()
    return text or None


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
