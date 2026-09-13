from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from lion_rebar.config.constants import APP_VERSION
from lion_rebar.diagnostics.bundle import create_diagnostic_bundle
from lion_rebar.engineer_decision import (
    ACTION_CONFIRMATION,
    ACTION_CORRECTION,
    ACTION_EXCLUSION,
    ACTION_OVERRIDE,
    ACTION_PROBLEM,
    classify_engineer_action,
    derive_review_reason_codes,
)
from lion_rebar.utils.files import safe_filename
from lion_rebar.session.decision_contract import (
    first_explicit_segments,
    positive_integer,
    positive_number,
)


LEARNING_PACK_SCHEMA = "lion-rebar-learning-pack/v1"
DECISION_EVENT_TYPES = {
    "APPROVED",
    "BULK_APPROVED",
    "ENGINEER_RESOLVED",
    "PROBLEM_RECORDED",
    "EXCLUDED_BY_ENGINEER",
    "NON_PRODUCTION_POSITION",
    "MANUAL_ASSIGNED",
    "MANUAL_SHAPE_DRAFT",
    # Legacy names remain readable for old session databases.  They are
    # diagnostic problems, not completed production decisions.
    "FLAGGED",
    "NO_VALID_CANDIDATE",
    "MISSING_MASTER",
    "GROUP_APPLIED",
    "GROUP_UNDONE",
}


class LearningPackStorageError(RuntimeError):
    """The verified local Learning Pack could not be persisted safely."""


@dataclass(frozen=True)
class LearningPackResult:
    path: Path
    latest_path: Path
    session_id: str
    project_count: int
    event_count: int
    decision_count: int
    warning_count: int
    active_decision_count: int = 0
    candidate_count: int = 0
    calibration_group_count: int = 0
    independent_drawing_family_count: int = 0
    local_storage_warning: str | None = None
    published_path: Path | None = None
    published_latest_path: Path | None = None
    publication_warning: str | None = None


def build_learning_pack(
    *,
    session_id: str,
    started_at: str,
    ended_at: str,
    events: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    output_dir: Path,
) -> LearningPackResult:
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    token = safe_filename(session_id, "SESSION")
    timestamp = _compact_timestamp(ended_at)
    destination = destination_dir / f"LION_REBAR_LEARNING_{timestamp}_{token}.zip"
    latest = destination_dir / "LION_REBAR_LEARNING_PACK_LATEST.zip"

    warnings: list[str] = []
    files: dict[str, bytes] = {}
    project_aliases = _project_aliases(projects)
    sanitized_events = [_sanitize_event(event, project_aliases) for event in events]
    decision_rows = _decision_rows(sanitized_events)
    active_decisions = _current_learning_rows(decision_rows)
    rule_candidates = _build_rule_candidates(decision_rows)
    calibration_evidence = _build_calibration_evidence(decision_rows)
    decision_metrics = _decision_metrics(decision_rows)
    summary = _build_summary(
        session_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        events=sanitized_events,
        projects=projects,
        decision_rows=decision_rows,
        warnings=warnings,
    )

    summary["learning_metrics"] = decision_metrics
    summary["active_decision_count"] = len(active_decisions)
    summary["candidate_count"] = len(rule_candidates)
    summary["calibration_group_count"] = len(calibration_evidence)
    summary["activated_rule_count"] = 0
    files["session/summary.json"] = _json_bytes(summary)
    files["session/decision_metrics.json"] = _json_bytes(
        {"schema": "lion-rebar-decision-metrics/v1", **decision_metrics}
    )
    files["session/events.jsonl"] = _jsonl_bytes(sanitized_events)
    files["session/decision_journal.csv"] = _decision_csv_bytes(decision_rows)
    files["session/decision_journal.json"] = _json_bytes(
        {"schema": "lion-rebar-decision-journal/v1", "decisions": decision_rows}
    )
    files["session/rule_candidates.json"] = _json_bytes(
        {
            "schema": "lion-rebar-rule-candidates/v2",
            "policy": (
                "Само CORRECTION/OVERRIDE/PROBLEM могат да породят кандидат за правило. "
                "CONFIRMATION се пази отделно като calibration evidence."
            ),
            "candidates": rule_candidates,
        }
    )
    files["session/calibration_evidence.json"] = _json_bytes(
        {
            "schema": "lion-rebar-calibration-evidence/v1",
            "policy": (
                "Потвържденията калибрират вече работещото разпознаване и не се "
                "превръщат автоматично в нови Engine/Matcher правила."
            ),
            # ``confirmations`` is the canonical v2 name.  ``groups`` is
            # retained as a read-only compatibility alias for existing audit
            # scripts and Recovery 6.1 Learning Pack consumers.
            "confirmations": calibration_evidence,
            "groups": calibration_evidence,
        }
    )
    layer_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lion-rebar-learning-") as temp_name:
        temp_root = Path(temp_name)
        for project in projects:
            project_root = Path(str(project.get("project_root") or ""))
            alias = project_aliases.get(str(project_root), safe_filename(project_root.name, "PROJECT"))
            if not project_root.is_dir():
                warnings.append(f"Проектната папка липсва: {project.get('project_name') or alias}")
                continue
            classification = project_root / "working" / "layer_classification.json"
            if classification.is_file():
                try:
                    payload = json.loads(classification.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    warnings.append(f"Layer classification не се чете за {alias}: {exc}")
                else:
                    for row in payload.get("layers", []):
                        if isinstance(row, dict):
                            layer_rows.append(
                                {
                                    "project": alias,
                                    "layer": row.get("layer"),
                                    "role": row.get("role"),
                                    "confidence": row.get("confidence"),
                                    "relevant_for_analysis": row.get("relevant_for_analysis"),
                                    "default_visible": row.get("default_visible"),
                                    "entity_count": row.get("entity_count"),
                                    "rebar_text_count": row.get("rebar_text_count"),
                                    "reasons": " | ".join(str(v) for v in row.get("reasons", [])),
                                }
                            )
            report_paths = _report_paths_for_project(events, str(project_root))
            report_path = report_paths[-1] if report_paths else None
            report_index: list[dict[str, Any]] = []
            for index, historical_report in enumerate(report_paths[-10:], start=1):
                try:
                    raw_payload = json.loads(
                        historical_report.read_text(encoding="utf-8-sig")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    warnings.append(
                        f"Batch report не се чете за {alias}: {historical_report.name}: {exc}"
                    )
                    continue
                sanitized_report = _sanitize_paths(raw_payload, str(project_root))
                archive_name = (
                    f"projects/{alias}/reports/"
                    f"{index:02d}_{safe_filename(historical_report.stem, 'report')}.json"
                )
                encoded_report = _json_bytes(sanitized_report)
                files[archive_name] = encoded_report
                report_index.append(
                    {
                        "archive_path": archive_name,
                        "original_filename": historical_report.name,
                        "sha256": hashlib.sha256(historical_report.read_bytes()).hexdigest(),
                        "size": historical_report.stat().st_size,
                    }
                )
            if report_index:
                files[f"projects/{alias}/reports/index.json"] = _json_bytes(
                    {
                        "schema": "lion-rebar-session-report-index/v1",
                        "reports": report_index,
                        "note": "Включени са до 10 отчета от текущата сесия; последният е и в AI diagnostic пакета.",
                    }
                )
            diagnostic_path = temp_root / f"{alias}_AI_FEEDBACK.zip"
            try:
                created = create_diagnostic_bundle(
                    project_root,
                    output_path=diagnostic_path,
                    include_source_dxf=True,
                    latest_report_path=report_path,
                )
            except Exception as exc:
                warnings.append(f"Диагностичният пакет за {alias} не е създаден: {exc}")
            else:
                files[f"projects/{alias}/{created.name}"] = created.read_bytes()

    files["session/layer_classification.csv"] = _layer_csv_bytes(layer_rows)
    summary["warnings"] = warnings
    summary["warning_count"] = len(warnings)
    files["session/summary.json"] = _json_bytes(summary)
    files["session/session_database_extract.json"] = _json_bytes(
        {
            "schema": "lion-rebar-session-database-extract/v1",
            "session": summary,
            "projects": [
                {
                    "project": project_aliases.get(str(project.get("project_root") or "")),
                    "project_name": project.get("project_name"),
                    "source_sha256": project.get("source_sha256"),
                    "first_seen_at": project.get("first_seen_at"),
                    "last_seen_at": project.get("last_seen_at"),
                }
                for project in projects
            ],
            "events": sanitized_events,
        }
    )
    files["README_BG.md"] = _readme_text(summary).encode("utf-8")
    files["START_PROMPT_BG.md"] = _start_prompt_text(summary).encode("utf-8")

    manifest = {
        "schema": LEARNING_PACK_SCHEMA,
        "application_version": APP_VERSION,
        "session_id": session_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "entries": [
            {
                "path": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in sorted(files.items())
        ],
    }
    files["manifest.json"] = _json_bytes(manifest)

    try:
        # Build and validate the ZIP fully in memory first.  The filesystem only
        # receives already-verified bytes, so cloud filters, stale partial files
        # or an existing corrupt LATEST cannot poison archive construction.
        archive_payload = _build_verified_archive_bytes(files)
        _atomic_write_verified_archive(destination, archive_payload)
        _atomic_write_verified_archive(latest, archive_payload)
        _verify_identical_archive_copy(destination, latest)
    except Exception as exc:
        if isinstance(exc, LearningPackStorageError):
            raise
        raise LearningPackStorageError(
            f"Локалният Learning Pack не можа да бъде записан и проверен: {exc}"
        ) from exc

    return LearningPackResult(
        path=destination,
        latest_path=latest,
        session_id=session_id,
        project_count=len(projects),
        event_count=len(events),
        decision_count=len(decision_rows),
        warning_count=len(warnings),
        active_decision_count=len(active_decisions),
        candidate_count=len(rule_candidates),
        calibration_group_count=len(calibration_evidence),
        independent_drawing_family_count=int(
            decision_metrics.get("independent_drawing_family_count") or 0
        ),
    )


def _build_verified_archive_bytes(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(
            buffer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for name, payload in sorted(files.items()):
                archive.writestr(name, payload)
        payload = buffer.getvalue()
        verify_learning_pack_bytes(payload)
        return payload
    except Exception as exc:
        if isinstance(exc, LearningPackStorageError):
            raise
        raise LearningPackStorageError(
            f"ZIP съдържанието не можа да бъде изградено и проверено в паметта: {exc}"
        ) from exc


def _atomic_write_verified_archive(destination: Path, payload: bytes) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected_sha = hashlib.sha256(payload).hexdigest()
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".verified.tmp",
        dir=str(destination.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256_file(temporary) != expected_sha:
            raise LearningPackStorageError(
                f"Временният локален ZIP има различен SHA256: {temporary}"
            )
        verify_learning_pack(temporary)
        os.replace(temporary, destination)
        if _sha256_file(destination) != expected_sha:
            raise LearningPackStorageError(
                f"Записаният локален ZIP има различен SHA256: {destination}"
            )
        verify_learning_pack(destination)
        return destination
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, LearningPackStorageError):
            raise
        raise LearningPackStorageError(
            f"Провереният локален ZIP не можа да бъде публикуван атомарно в {destination}: {exc}"
        ) from exc


def verify_learning_pack_bytes(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise LearningPackStorageError("Learning Pack ZIP съдържанието е празно")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            return _verify_learning_pack_archive(archive)
    except zipfile.BadZipFile as exc:
        raise LearningPackStorageError(
            "Learning Pack байтовете не представляват валиден ZIP"
        ) from exc


def publish_learning_pack(
    result: LearningPackResult,
    *,
    destination_dir: Path,
) -> LearningPackResult:
    """Publish a verified copy without risking the canonical local pack.

    The canonical Learning Pack is always created first in local application
    storage. Cloud/OneDrive/Google Drive publication is a secondary best-effort
    operation. A publication failure therefore returns a warning but never
    invalidates the local pack or the completed session.
    """

    target_dir = Path(destination_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        local_dir = result.path.parent.resolve()
        try:
            target_resolved = target_dir.resolve()
        except OSError:
            target_resolved = target_dir.absolute()
        if target_resolved == local_dir:
            return result

        published = _publish_verified_copy(
            result.path,
            target_dir / result.path.name,
        )
        published_latest = _publish_verified_copy(
            result.path,
            target_dir / "LION_REBAR_LEARNING_PACK_LATEST.zip",
        )
        return replace(
            result,
            published_path=published,
            published_latest_path=published_latest,
        )
    except Exception as exc:
        message = (
            "Learning Pack е създаден и проверен локално, но публикуването "
            f"към {target_dir} не успя: {exc}"
        )
        return replace(
            result,
            warning_count=result.warning_count + 1,
            publication_warning=message,
        )


def verify_learning_pack(path: Path) -> dict[str, Any]:
    """Verify ZIP structure plus every manifest-declared payload."""

    candidate = Path(path)
    if not candidate.is_file():
        raise LearningPackStorageError(f"Learning Pack файлът липсва: {candidate}")
    try:
        with zipfile.ZipFile(candidate, "r") as archive:
            return _verify_learning_pack_archive(archive)
    except zipfile.BadZipFile as exc:
        raise LearningPackStorageError(
            f"Learning Pack не е валиден ZIP: {candidate}"
        ) from exc


def _verify_learning_pack_archive(archive: zipfile.ZipFile) -> dict[str, Any]:
    bad = archive.testzip()
    if bad:
        raise LearningPackStorageError(f"Повреден файл в Learning Pack: {bad}")
    names = set(archive.namelist())
    if "manifest.json" not in names:
        raise LearningPackStorageError("Learning Pack няма manifest.json")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LearningPackStorageError(
            f"Невалиден Learning Pack manifest: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != LEARNING_PACK_SCHEMA:
        raise LearningPackStorageError("Learning Pack manifest schema не съвпада")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise LearningPackStorageError("Learning Pack manifest entries липсват")
    declared_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise LearningPackStorageError("Невалиден manifest entry")
        name = str(entry.get("path") or "")
        if not name or name in declared_names:
            raise LearningPackStorageError(
                f"Невалиден/дублиран manifest path: {name!r}"
            )
        declared_names.add(name)
        if name not in names:
            raise LearningPackStorageError(f"Липсва manifest файл в ZIP: {name}")
        payload = archive.read(name)
        raw_size = entry.get("size")
        expected_size = int(raw_size) if raw_size is not None else -1
        expected_sha = str(entry.get("sha256") or "")
        if len(payload) != expected_size:
            raise LearningPackStorageError(f"Размерът не съвпада за {name}")
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            raise LearningPackStorageError(f"SHA256 не съвпада за {name}")
    extras = names - declared_names - {"manifest.json"}
    if extras:
        raise LearningPackStorageError(
            "Learning Pack съдържа недекларирани файлове: "
            + ", ".join(sorted(extras)[:10])
        )
    return manifest


def _publish_verified_copy(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".publishing",
        dir=str(destination.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _copy_file_with_fsync(source, temporary)
        _verify_identical_archive_copy(source, temporary)
        os.replace(temporary, destination)
        _verify_identical_archive_copy(source, destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _copy_file_with_fsync(source: Path, destination: Path) -> None:
    with source.open("rb") as read_stream, destination.open("wb") as write_stream:
        shutil.copyfileobj(read_stream, write_stream, length=1024 * 1024)
        write_stream.flush()
        os.fsync(write_stream.fileno())


def _verify_identical_archive_copy(source: Path, copy_path: Path) -> None:
    verify_learning_pack(copy_path)
    source_sha = _sha256_file(source)
    copy_sha = _sha256_file(copy_path)
    if source_sha != copy_sha:
        raise RuntimeError(
            f"Публикуваното копие има различен SHA256: {copy_path}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_summary(
    *,
    session_id: str,
    started_at: str,
    ended_at: str,
    events: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    counts = Counter(str(event.get("event_type") or "") for event in events)
    aliases = _project_aliases(projects)
    project_summaries: list[dict[str, Any]] = []
    for project in projects:
        root = str(project.get("project_root") or "")
        alias = aliases.get(root)
        project_events = [event for event in events if str(event.get("project_key") or "") == _path_key(root)]
        latest_integrity = next(
            (
                event
                for event in reversed(project_events)
                if event.get("event_type") in {"FINAL_INTEGRITY", "FINAL_INTEGRITY_AUTO"}
            ),
            None,
        )
        integrity_payload = (latest_integrity or {}).get("payload")
        integrity_payload = integrity_payload if isinstance(integrity_payload, Mapping) else None
        project_status = _project_completion_status(integrity_payload)
        project_summaries.append(
            {
                "project_name": project.get("project_name"),
                "source_sha256": project.get("source_sha256"),
                "event_count": len(project_events),
                "decision_count": sum(
                    1 for row in decision_rows if row.get("project") == alias
                ),
                "completion_status": project_status,
                "latest_final_integrity": integrity_payload,
            }
        )
    return {
        "schema": LEARNING_PACK_SCHEMA,
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "application_version": APP_VERSION,
        "event_count": len(events),
        "decision_count": len(decision_rows),
        "decision_action_counts": dict(
            sorted(Counter(str(row.get("action_class") or "UNKNOWN") for row in decision_rows).items())
        ),
        "project_count": len(projects),
        "event_counts": dict(sorted(counts.items())),
        "projects": project_summaries,
        "warning_count": len(warnings),
        "warnings": list(warnings),
        "engineering_rule": (
            "CONFIRMATION е calibration evidence. Само CORRECTION/OVERRIDE/PROBLEM "
            "може да стане кандидат за правило; никое USER решение не променя "
            "автоматично Engine/Matcher без regression gate."
        ),
    }


def _project_completion_status(integrity: Mapping[str, Any] | None) -> str:
    if not integrity:
        return "NO_FINAL_CONTROL"
    full = bool(integrity.get("full_specification_complete"))
    unresolved = _int_or_none(integrity.get("unresolved_positions"))
    exclusions = _int_or_none(integrity.get("explicit_exclusions")) or 0
    missing = _int_or_none(integrity.get("intrinsic_missing_master")) or 0
    duplicates = _int_or_none(integrity.get("duplicate_groups_remaining")) or 0
    if full or (
        bool(integrity.get("integrity_passed"))
        and unresolved == 0
        and exclusions == 0
        and missing == 0
        and duplicates == 0
    ):
        return "COMPLETE"
    return "INCOMPLETE"



def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _project_aliases(projects: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    used: Counter[str] = Counter()
    for project in projects:
        root = str(project.get("project_root") or "")
        base = safe_filename(str(project.get("project_name") or Path(root).name), "PROJECT")
        used[base] += 1
        alias = base if used[base] == 1 else f"{base}_{used[base]}"
        result[root] = alias
    return result


def _sanitize_event(event: Mapping[str, Any], aliases: Mapping[str, str]) -> dict[str, Any]:
    root = str(event.get("project_root") or "")
    alias = aliases.get(root)
    payload = _sanitize_paths(event.get("payload") or {}, root)
    return {
        "event_id": event.get("event_id"),
        "session_id": event.get("session_id"),
        "at": event.get("at"),
        "event_type": event.get("event_type"),
        "severity": event.get("severity"),
        "project": alias,
        "project_key": _path_key(root) if root else None,
        "project_name": event.get("project_name"),
        "source_sha256": event.get("source_sha256"),
        "engine_id": event.get("engine_id"),
        "engine_version": event.get("engine_version"),
        "engine_sha256": event.get("engine_sha256"),
        "matcher_sha256": event.get("matcher_sha256"),
        "annotation_handle": event.get("annotation_handle"),
        "annotation_text": event.get("annotation_text"),
        "report": Path(str(event.get("report_path") or "")).name or None,
        "payload": payload,
    }


def _decision_rows(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    source_events = list(events)
    invalidated_operations: set[str] = set()
    superseded_event_ids: set[str] = set()
    for event in source_events:
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        envelope = payload.get("human_decision") if isinstance(payload, Mapping) else None
        envelope = envelope if isinstance(envelope, Mapping) else {}
        event_type = str(envelope.get("event_type") or event.get("event_type") or "").upper()
        if event_type == "GROUP_UNDONE":
            operation_id = str(
                envelope.get("invalidates_operation_id")
                or payload.get("invalidates_operation_id")
                or payload.get("operation_id")
                or ""
            )
            if operation_id:
                invalidated_operations.add(operation_id)
        superseded_event_ids.update(
            str(value) for value in (envelope.get("supersedes_event_ids") or []) if value
        )

    rows: list[dict[str, Any]] = []
    for event in source_events:
        event_type = str(event.get("event_type") or "").upper()
        if event_type not in DECISION_EVENT_TYPES:
            continue
        raw_payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        envelope = raw_payload.get("human_decision") if isinstance(raw_payload, Mapping) else None
        envelope = envelope if isinstance(envelope, Mapping) else {}
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), Mapping) else raw_payload
        event_type = str(envelope.get("event_type") or event_type).upper()
        if event_type == "GROUP_UNDONE":
            continue
        items = None
        if isinstance(payload, Mapping):
            items = payload.get("decisions") or payload.get("consequences")
        if event_type in {"BULK_APPROVED", "GROUP_APPLIED"} and isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                row = _decision_row(event, event_type, payload, item=item)
                rows.append(_decorate_decision_row(row, event, envelope, item))
            continue
        row = _decision_row(event, event_type, payload)
        rows.append(_decorate_decision_row(row, event, envelope, None))

    for row in rows:
        if str(row.get("operation_id") or "") in invalidated_operations:
            row["active"] = False
            row["invalidated_by_undo"] = True
        if str(row.get("event_id") or "") in superseded_event_ids:
            row["active"] = False
            row["superseded"] = True
    return rows


def _decorate_decision_row(
    row: dict[str, Any],
    event: Mapping[str, Any],
    envelope: Mapping[str, Any],
    item: Mapping[str, Any] | None,
) -> dict[str, Any]:
    item = item if isinstance(item, Mapping) else {}
    source_identity = envelope.get("source_identity")
    source_identity = source_identity if isinstance(source_identity, Mapping) else {}
    source_sha = str(source_identity.get("source_sha256") or event.get("source_sha256") or "")
    project = str(row.get("project") or event.get("project") or "")
    drawing_basis = source_sha or str(source_identity.get("source_generation_id") or project)
    drawing_family_id = str(source_identity.get("drawing_family_id") or "")
    if not drawing_family_id:
        drawing_family_id = hashlib.sha256(drawing_basis.encode("utf-8")).hexdigest()
    operation_id = str(
        item.get("operation_id")
        or envelope.get("operation_id")
        or _mapping(envelope.get("payload")).get("operation_id")
        or ""
    )
    stable_event_id = str(
        envelope.get("event_id")
        or event.get("stable_event_id")
        or event.get("event_id")
        or ""
    )
    handle = str(row.get("annotation_handle") or "")
    instance_key = hashlib.sha256(
        f"{drawing_family_id}|{handle}".encode("utf-8")
    ).hexdigest()
    observation_basis = operation_id or instance_key
    row.update(
        {
            "event_id": stable_event_id,
            "decision_revision": int(envelope.get("decision_revision") or 1),
            "operation_id": operation_id or None,
            "observation_mode": str(envelope.get("operation_kind") or (
                "BULK" if row.get("event_type") in {"BULK_APPROVED", "GROUP_APPLIED"} else "INDIVIDUAL"
            )),
            "active": bool(envelope.get("active", True)),
            "drawing_family_id": drawing_family_id,
            "source_identity_key": hashlib.sha256(
                f"{source_sha}|{source_identity.get('source_generation_id') or ''}".encode("utf-8")
            ).hexdigest(),
            "instance_id": instance_key,
            "independent_observation_id": hashlib.sha256(
                f"{drawing_family_id}|{observation_basis}".encode("utf-8")
            ).hexdigest(),
            "source_generation_id": source_identity.get("source_generation_id"),
            "analysis_run_id": source_identity.get("analysis_run_id"),
        }
    )
    return row


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "" and value != [] and value != ():
            return value
    return None


def _decision_row(
    event: Mapping[str, Any],
    event_type: str,
    payload: Mapping[str, Any],
    *,
    item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item = _mapping(item)
    decision = _mapping(payload.get("decision"))
    context = _mapping(payload.get("row_context"))
    original = _mapping(_first(item.get("original"), payload.get("original"), decision.get("original")))
    resolved = _mapping(_first(item.get("resolved"), payload.get("resolved"), decision.get("resolved")))
    geometry = _mapping(_first(item.get("geometry"), payload.get("geometry"), decision.get("geometry")))

    annotation_handle = str(
        _first(
            item.get("annotation_handle"),
            event.get("annotation_handle"),
            payload.get("annotation_handle"),
            decision.get("annotation_handle"),
            _mapping(payload.get("annotation")).get("handle"),
        )
        or ""
    )
    source_annotation_text = str(
        _first(
            item.get("source_annotation_text"),
            _mapping(payload.get("annotation")).get("text"),
            payload.get("annotation_text"),
            event.get("annotation_text"),
            context.get("annotation_text"),
        )
        or ""
    )
    resolved_annotation_text = str(
        _first(
            item.get("resolved_annotation_text"),
            _mapping(payload.get("annotation")).get("resolved_text"),
            payload.get("resolved_annotation_text"),
            resolved.get("annotation_text"),
            source_annotation_text,
        )
        or ""
    )

    suggested_shape_code = str(
        _first(
            original.get("shape_code"),
            item.get("master_shape_code"),
            context.get("master_shape_code"),
            payload.get("master_shape_code"),
        )
        or ""
    ).upper()
    suggested_shape_name = _first(
        original.get("shape_name"),
        item.get("master_shape_name"),
        context.get("master_shape_name"),
        payload.get("master_shape_name"),
    )
    accepted_shape_code = str(
        _first(
            resolved.get("shape_code"),
            item.get("shape_code"),
            payload.get("shape_code"),
            decision.get("shape_code"),
        )
        or ""
    ).upper()
    accepted_shape_name = _first(
        resolved.get("shape_name"),
        item.get("shape_name"),
        payload.get("shape_name"),
        decision.get("shape_name"),
    )

    suggested_handles = list(
        _first(
            original.get("geometry_handles"),
            item.get("candidate_handles"),
            context.get("candidate_handles"),
            payload.get("geometry_handles"),
            [],
        )
        or []
    )
    resolved_handles = list(
        _first(
            geometry.get("handles"),
            item.get("geometry_handles"),
            payload.get("resolved_geometry_handles"),
            suggested_handles,
        )
        or []
    )
    suggested_segments, suggested_segment_source, suggested_segments_valid = first_explicit_segments(
        original.get("segment_lengths_cm"),
        item.get("canonical_segment_lengths_cm"),
        context.get("canonical_segment_lengths_cm"),
        payload.get("dimensions"),
    )
    resolved_segments, resolved_segment_source, resolved_segments_valid = first_explicit_segments(
        resolved.get("segment_lengths_cm"),
        resolved,
        item.get("production_segment_lengths_cm"),
        payload.get("production_segment_lengths_cm"),
        payload.get("dimensions"),
    )

    reason_code = str(
        _first(item.get("reason_code"), payload.get("reason_code"), decision.get("reason_code"))
        or ""
    ).upper()
    reason = _first(item.get("reason"), payload.get("reason"), decision.get("reason"))
    note = _first(item.get("note"), payload.get("note"), decision.get("note"))
    correct_specification = _first(
        item.get("correct_specification"),
        payload.get("correct_specification"),
        decision.get("correct_specification"),
    )
    explicit_action = str(
        _first(item.get("action_class"), payload.get("action_class"), decision.get("action_class"))
        or ""
    ).upper()

    if not accepted_shape_code and event_type in {"APPROVED", "BULK_APPROVED"}:
        accepted_shape_code = suggested_shape_code
        accepted_shape_name = accepted_shape_name or suggested_shape_name

    # Only an explicit confirmation may inherit the proven suggested vector.
    # A correction/override that supplies only a form must remain partial; using
    # the old suggestion here would silently manufacture dimensions for the
    # newly accepted shape.
    confirmation_hint = (
        explicit_action == ACTION_CONFIRMATION
        or event_type in {"APPROVED", "BULK_APPROVED"}
    )
    if confirmation_hint and not resolved_segments and suggested_segments:
        resolved_segments = list(suggested_segments)
        resolved_segment_source = "INHERITED_FROM_EXPLICIT_CONFIRMED_SUGGESTION"
        resolved_segments_valid = suggested_segments_valid

    if explicit_action in {
        ACTION_CONFIRMATION,
        ACTION_CORRECTION,
        ACTION_OVERRIDE,
        ACTION_EXCLUSION,
        ACTION_PROBLEM,
    }:
        action_class = explicit_action
    elif event_type in {"APPROVED", "BULK_APPROVED"}:
        action_class = ACTION_CONFIRMATION
    elif event_type == "EXCLUDED_BY_ENGINEER":
        action_class = ACTION_EXCLUSION
    elif event_type in {"PROBLEM_RECORDED", "FLAGGED", "NO_VALID_CANDIDATE", "MISSING_MASTER"}:
        action_class = ACTION_PROBLEM
    else:
        action_class = classify_engineer_action(
            reason_code=reason_code,
            suggested_shape_code=suggested_shape_code,
            resolved_shape_code=accepted_shape_code,
            original_count=_first(original.get("count"), context.get("annotation_count")),
            resolved_count=_first(resolved.get("count"), item.get("annotation_count"), context.get("annotation_count")),
            original_diameter_mm=_first(original.get("diameter_mm"), context.get("diameter_mm")),
            resolved_diameter_mm=_first(resolved.get("diameter_mm"), item.get("diameter_mm"), context.get("diameter_mm")),
            original_length_cm=_first(original.get("declared_length_cm"), context.get("declared_length_cm")),
            resolved_length_cm=_first(resolved.get("production_length_cm"), item.get("production_length_cm"), context.get("declared_length_cm")),
            suggested_segments=suggested_segments,
            resolved_segments=resolved_segments,
            suggested_geometry_handles=suggested_handles,
            resolved_geometry_handles=resolved_handles,
        )

    # Context-dependent problem reasons are derived even when the UI had no
    # explicit reason selector in an older project/session.
    reason_context = dict(context)
    reason_context.update(
        {
            "candidate_handles": suggested_handles,
            "master_shape_code": suggested_shape_code,
            "review_reason_codes": _first(
                item.get("review_reason_codes"),
                decision.get("review_reason_codes"),
                context.get("review_reason_codes"),
                [],
            ),
        }
    )
    review_reason_codes = list(derive_review_reason_codes(reason_context))
    if reason_code and reason_code not in review_reason_codes and action_class != ACTION_CONFIRMATION:
        review_reason_codes.insert(0, reason_code)

    accepted_count = _first(resolved.get("count"), item.get("annotation_count"), context.get("annotation_count"))
    accepted_diameter = _first(resolved.get("diameter_mm"), item.get("diameter_mm"), context.get("diameter_mm"))
    accepted_length = _first(
        resolved.get("production_length_cm"),
        item.get("production_length_cm"),
        context.get("declared_length_cm"),
    )
    if action_class == ACTION_CONFIRMATION:
        accepted_shape_code = accepted_shape_code or suggested_shape_code
        accepted_shape_name = accepted_shape_name or suggested_shape_name

    if not resolved_segments and accepted_shape_code == "F001":
        straight_length = positive_number(accepted_length)
        if straight_length is not None:
            resolved_segments = [straight_length]
            resolved_segment_source = "DERIVED_FROM_EXPLICIT_ACCEPTED_TOTAL_FOR_F001"
            resolved_segments_valid = True
    accepted_revision = _first(
        resolved.get("shape_revision"),
        item.get("master_shape_revision"),
        context.get("master_shape_revision"),
        payload.get("master_shape_revision"),
    )
    field_completeness = {
        "shape": bool(accepted_shape_code),
        "shape_revision": accepted_revision is not None,
        "count": positive_integer(accepted_count) is not None,
        "diameter_mm": positive_number(accepted_diameter) is not None,
        "production_length_cm": positive_number(accepted_length) is not None,
        "production_segments_cm": bool(resolved_segments) and resolved_segments_valid,
        "geometry": bool(resolved_handles),
    }
    complete_resolution = bool(
        field_completeness["shape"]
        and field_completeness["shape_revision"]
        and field_completeness["count"]
        and field_completeness["diameter_mm"]
        and field_completeness["production_length_cm"]
        and field_completeness["production_segments_cm"]
        and action_class not in {ACTION_PROBLEM, ACTION_EXCLUSION}
    )

    return {
        "at": event.get("at"),
        "project": event.get("project"),
        "event_type": event_type,
        "bulk_mode": payload.get("mode") if event_type == "BULK_APPROVED" else None,
        "action_class": action_class,
        "annotation_handle": annotation_handle,
        "annotation_text": source_annotation_text,
        "source_annotation_text": source_annotation_text,
        "resolved_annotation_text": resolved_annotation_text,
        "decision": _first(item.get("decision"), decision.get("decision"), payload.get("decision_code"), event_type),
        "reason_code": reason_code or None,
        "reason": reason,
        "note": note,
        "correct_specification": correct_specification,
        "suggested_shape_code": suggested_shape_code or None,
        "suggested_shape_name": suggested_shape_name,
        "accepted_shape_code": accepted_shape_code or None,
        "accepted_shape_name": accepted_shape_name,
        # Legacy aliases are kept for consumers of v1 decision journals.
        "chosen_shape_code": accepted_shape_code or None,
        "chosen_shape_name": accepted_shape_name,
        "original_count": _first(original.get("count"), context.get("annotation_count")),
        "accepted_count": accepted_count,
        "original_diameter_mm": _first(original.get("diameter_mm"), context.get("diameter_mm")),
        "accepted_diameter_mm": accepted_diameter,
        "original_length_cm": _first(original.get("declared_length_cm"), context.get("declared_length_cm")),
        "accepted_length_cm": accepted_length,
        "suggested_segment_lengths_cm": suggested_segments,
        "accepted_segment_lengths_cm": resolved_segments,
        "accepted_shape_revision": accepted_revision,
        "field_completeness": field_completeness,
        "field_provenance": {
            "suggested_segments": suggested_segment_source,
            "accepted_segments": resolved_segment_source,
        },
        "shape_semantics_status": (
            "VERSIONED" if event.get("engine_version") and accepted_revision is not None
            else "REQUIRES_VERSIONED_SHAPE_REVIEW"
        ),
        "candidate_handles": suggested_handles,
        "accepted_geometry_handles": resolved_handles,
        "batch_status": _first(item.get("batch_status"), context.get("batch_status")),
        "matcher_disposition": _first(item.get("matcher_disposition"), context.get("matcher_disposition")),
        "matcher_evidence_band": _first(item.get("matcher_evidence_band"), context.get("matcher_evidence_band")),
        "contextual_rule_code": _first(item.get("contextual_rule_code"), context.get("contextual_rule_code")),
        "review_reason_codes": review_reason_codes,
        "canonical_signature": _first(item.get("canonical_signature"), context.get("canonical_signature")),
        "numeric_difference": _first(item.get("numeric_difference"), context.get("numeric_difference")),
        "ownership_conflict": bool(
            _first(
                item.get("geometry_owner_conflict"),
                item.get("global_conflict"),
                context.get("geometry_owner_conflict"),
                context.get("global_conflict"),
                False,
            )
        ),
        "complete_production_resolution": complete_resolution,
        "source_sha256": event.get("source_sha256"),
        "engine_version": event.get("engine_version"),
        "matcher_sha256": event.get("matcher_sha256"),
    }


def _segment_signature(values: Iterable[Any]) -> str:
    normalized: list[str] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        normalized.append(f"{number:.3f}")
    return ",".join(normalized)


def _row_resolution_signature(row: Mapping[str, Any]) -> tuple:
    return (
        str(row.get("action_class") or ""),
        str(row.get("accepted_shape_code") or ""),
        positive_integer(row.get("accepted_count")),
        positive_number(row.get("accepted_diameter_mm")),
        positive_number(row.get("accepted_length_cm")),
        _segment_signature(row.get("accepted_segment_lengths_cm") or []),
    )


def _current_learning_rows(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [dict(row) for row in decisions if row.get("active", True)]
    by_instance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active:
        instance_id = str(row.get("instance_id") or "")
        if not instance_id:
            family = str(row.get("drawing_family_id") or row.get("source_sha256") or row.get("project") or "")
            handle = str(row.get("annotation_handle") or "")
            instance_id = hashlib.sha256(f"{family}|{handle}".encode("utf-8")).hexdigest()
            row["instance_id"] = instance_id
        by_instance[instance_id].append(row)

    result: list[dict[str, Any]] = []
    for members in by_instance.values():
        unique: dict[tuple, dict[str, Any]] = {}
        for row in members:
            signature = _row_resolution_signature(row)
            previous = unique.get(signature)
            if previous is None or (
                int(row.get("decision_revision") or 0), str(row.get("at") or "")
            ) > (
                int(previous.get("decision_revision") or 0), str(previous.get("at") or "")
            ):
                unique[signature] = row
        if len(unique) > 1:
            for row in unique.values():
                row["decision_conflict"] = True
                result.append(row)
        elif unique:
            result.append(next(iter(unique.values())))
    return result


def _decision_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    current = _current_learning_rows(decisions)
    unique_events = {str(row.get("event_id") or "") for row in decisions if row.get("event_id")}
    return {
        "event_row_count": len(decisions),
        "unique_event_count": len(unique_events),
        "duplicate_event_row_count": max(0, len(decisions) - len(unique_events)),
        "current_labeled_instance_count": len(current),
        "independent_observation_count": len({str(row.get("independent_observation_id") or row.get("instance_id") or "") for row in current}),
        "independent_drawing_family_count": len({str(row.get("drawing_family_id") or "") for row in current if row.get("drawing_family_id")}),
        "synthetic_variant_count": sum(bool(row.get("synthetic_variant")) for row in current),
        "individual_confirmation_count": sum(row.get("action_class") == ACTION_CONFIRMATION and str(row.get("observation_mode") or "INDIVIDUAL") == "INDIVIDUAL" for row in current),
        "bulk_confirmation_count": sum(row.get("action_class") == ACTION_CONFIRMATION and str(row.get("observation_mode") or "") == "BULK" for row in current),
        "auto_decision_count": sum(str(row.get("observation_mode") or "") == "AUTO" for row in current),
        "incomplete_instance_count": sum(not bool(row.get("complete_production_resolution")) for row in current),
        "conflict_instance_count": len({str(row.get("instance_id") or "") for row in current if row.get("decision_conflict")}),
        "activated_rule_count": 0,
    }


def _build_calibration_evidence(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _current_learning_rows(decisions):
        if str(row.get("action_class") or "") != ACTION_CONFIRMATION:
            continue
        key_parts = (
            str(row.get("accepted_shape_code") or ""), str(row.get("contextual_rule_code") or ""),
            str(row.get("batch_status") or ""), str(row.get("matcher_disposition") or ""),
            str(row.get("matcher_evidence_band") or ""), str(row.get("canonical_signature") or ""),
            _annotation_pattern(str(row.get("source_annotation_text") or "")),
            _segment_signature(row.get("accepted_segment_lengths_cm") or []),
        )
        groups["|".join(key_parts)].append(row)
    result: list[dict[str, Any]] = []
    for key, members in groups.items():
        projects = sorted({str(v.get("project") or "") for v in members if v.get("project")})
        families = sorted({str(v.get("drawing_family_id") or "") for v in members if v.get("drawing_family_id")})
        observations = {str(v.get("independent_observation_id") or v.get("instance_id") or "") for v in members}
        example = members[0]
        repeat_count = len({str(v.get("instance_id") or "") for v in members})
        if len(families) >= 2 and len(observations) >= 2:
            maturity = "CROSS_DRAWING_FAMILY_CALIBRATION"
        elif len(observations) >= 2:
            maturity = "PROJECT_CALIBRATION"
        else:
            maturity = "SINGLE_CONFIRMATION"
        result.append({
            "calibration_key": hashlib.sha256(key.encode("utf-8")).hexdigest(),
            "maturity": maturity, "repeat_count": repeat_count,
            "project_count": len(projects), "independent_observation_count": len(observations),
            "independent_drawing_family_count": len(families),
            "individual_confirmation_count": sum(str(v.get("observation_mode") or "INDIVIDUAL") == "INDIVIDUAL" for v in members),
            "bulk_confirmation_count": sum(str(v.get("observation_mode") or "") == "BULK" for v in members),
            "projects": projects, "drawing_families": families,
            "accepted_shape_code": example.get("accepted_shape_code"),
            "contextual_rule_code": example.get("contextual_rule_code"),
            "batch_status": example.get("batch_status"),
            "matcher_disposition": example.get("matcher_disposition"),
            "matcher_evidence_band": example.get("matcher_evidence_band"),
            "annotation_pattern": _annotation_pattern(str(example.get("source_annotation_text") or "")),
            "examples": [{
                "project": member.get("project"), "annotation_handle": member.get("annotation_handle"),
                "source_annotation_text": member.get("source_annotation_text"),
                "resolved_annotation_text": member.get("resolved_annotation_text"),
                "segments": member.get("accepted_segment_lengths_cm"),
                "complete": member.get("field_completeness"),
            } for member in members[:10]],
            "status": "CALIBRATION_ONLY_NOT_A_RULE_CANDIDATE",
        })
    result.sort(key=lambda row: (-int(row.get("repeat_count") or 0), str(row.get("accepted_shape_code") or "")))
    return result


def _candidate_type(row: Mapping[str, Any]) -> str:
    reason = str(row.get("reason_code") or "").upper()
    suggested = str(row.get("suggested_shape_code") or "")
    accepted = str(row.get("accepted_shape_code") or "")
    if reason == "MISSING_MASTER" or (accepted and not suggested):
        return "MISSING_MASTER_OR_NEW_SHAPE"
    if reason in {
        "WRONG_GEOMETRY",
        "WRONG_OBJECT",
        "PARTIAL_GEOMETRY",
        "DUPLICATE_GEOMETRY",
    }:
        return "OWNERSHIP_OR_ASSOCIATION_CORRECTION"
    if reason == "NO_GEOMETRY":
        return "ANNOTATION_ONLY_PRODUCTION_SPECIFICATION"
    if reason in {"WRONG_COUNT", "WRONG_DIAMETER", "WRONG_LENGTH", "WRONG_SEGMENTS"}:
        return "PRODUCTION_PARAMETER_CORRECTION"
    if reason in {"PROJECTANT_ERROR", "DXF_DIMENSION_CONFLICT"}:
        return "DESIGNER_DATA_ISSUE"
    if accepted and suggested and accepted != suggested:
        return "SHAPE_CORRECTION"
    if str(row.get("action_class") or "") == ACTION_PROBLEM:
        return "UNRESOLVED_REVIEW_PATTERN"
    return "ENGINEER_OVERRIDE_PATTERN"


def _build_rule_candidates(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    eligible_actions = {ACTION_CORRECTION, ACTION_OVERRIDE, ACTION_PROBLEM}
    for row in _current_learning_rows(decisions):
        action = str(row.get("action_class") or "")
        if action not in eligible_actions:
            continue
        key_parts = (
            action, _candidate_type(row), str(row.get("reason_code") or ""),
            str(row.get("suggested_shape_code") or ""), str(row.get("accepted_shape_code") or ""),
            str(row.get("contextual_rule_code") or ""), str(row.get("canonical_signature") or ""),
            _annotation_pattern(str(row.get("source_annotation_text") or "")),
            _segment_signature(row.get("accepted_segment_lengths_cm") or []),
            ",".join(sorted(str(v) for v in row.get("review_reason_codes") or [])),
        )
        groups["|".join(key_parts)].append(row)
    candidates: list[dict[str, Any]] = []
    for key, members in groups.items():
        projects = sorted({str(v.get("project") or "") for v in members if v.get("project")})
        families = sorted({str(v.get("drawing_family_id") or "") for v in members if v.get("drawing_family_id")})
        observations = {str(v.get("independent_observation_id") or v.get("instance_id") or "") for v in members}
        instances = {str(v.get("instance_id") or "") for v in members}
        example = members[0]
        has_conflict = any(bool(v.get("decision_conflict")) for v in members)
        if has_conflict:
            maturity, priority = "CONFLICT_REQUIRES_REVIEW", "HIGH"
        elif len(families) >= 2 and len(observations) >= 2:
            maturity, priority = "GLOBAL_CANDIDATE", "HIGH"
        elif len(observations) >= 2:
            maturity, priority = "PROJECT_STYLE_CANDIDATE", "MEDIUM"
        else:
            maturity, priority = "OBSERVATION", "LOW"
        candidates.append({
            "candidate_key": hashlib.sha256(key.encode("utf-8")).hexdigest(),
            "candidate_type": _candidate_type(example), "action_class": example.get("action_class"),
            "maturity": maturity, "priority": priority, "repeat_count": len(instances),
            "current_labeled_instance_count": len(instances),
            "independent_observation_count": len(observations),
            "independent_drawing_family_count": len(families),
            "project_count": len(projects), "projects": projects, "drawing_families": families,
            "annotation_pattern": _annotation_pattern(str(example.get("source_annotation_text") or "")),
            "suggested_shape_code": example.get("suggested_shape_code"),
            "accepted_shape_code": example.get("accepted_shape_code"),
            "chosen_shape_code": example.get("accepted_shape_code"),
            "reason_code": example.get("reason_code"),
            "review_reason_codes": example.get("review_reason_codes") or [],
            "contextual_rule_code": example.get("contextual_rule_code"),
            "has_conflicting_labels": has_conflict,
            "examples": [{
                "project": member.get("project"), "annotation_handle": member.get("annotation_handle"),
                "source_annotation_text": member.get("source_annotation_text"),
                "resolved_annotation_text": member.get("resolved_annotation_text"),
                "note": member.get("note"), "correct_specification": member.get("correct_specification"),
                "suggested_shape_code": member.get("suggested_shape_code"),
                "accepted_shape_code": member.get("accepted_shape_code"),
                "suggested_segments": member.get("suggested_segment_lengths_cm"),
                "accepted_segments": member.get("accepted_segment_lengths_cm"),
                "field_completeness": member.get("field_completeness"),
            } for member in members[:10]],
            "status": "CANDIDATE_ONLY_REQUIRES_AUDIT_AND_REGRESSION",
        })
    candidates.sort(key=lambda row: (
        {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(row.get("priority")), 3),
        -int(row.get("repeat_count") or 0), str(row.get("candidate_type") or ""),
    ))
    return candidates


def _annotation_pattern(text: str) -> str:
    value = str(text or "").upper().replace("Ø", "N").replace("Ф", "N")
    value = re.sub(r"\b\d+\s*N\s*\d+(?:\.\d+)?\s*[XХ]\s*\d+(?:\.\d+)?", "<Q>N<D>X<L>", value)
    value = re.sub(r"\b\d+\s*N\s*\d+(?:\.\d+)?\s*/\s*S\s*=\s*\d+(?:\.\d+)?", "<Q>N<D>/S<S>", value)
    value = re.sub(r"\d+(?:\.\d+)?", "#", value)
    return re.sub(r"\s+", " ", value).strip()


def _report_paths_for_project(events: list[dict[str, Any]], project_root: str) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for event in events:
        if str(event.get("project_root") or "") != project_root:
            continue
        candidates: list[str] = []
        report = str(event.get("report_path") or "")
        if report:
            candidates.append(report)
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        for key in ("report_path", "json_path", "batch_report_path"):
            value = str(payload.get(key) or "")
            if value:
                candidates.append(value)
        for value in candidates:
            path = Path(value)
            normalized = str(path).casefold()
            if normalized in seen or not path.is_file():
                continue
            seen.add(normalized)
            result.append(path)
    result.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)))
    return result


def _latest_report_path_for_project(events: list[dict[str, Any]], project_root: str) -> Path | None:
    paths = _report_paths_for_project(events, project_root)
    return paths[-1] if paths else None


def _decision_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    fields = [
        "at", "project", "event_type", "action_class", "annotation_handle",
        "source_annotation_text", "resolved_annotation_text", "decision",
        "reason_code", "reason", "note", "correct_specification", "suggested_shape_code",
        "accepted_shape_code", "original_count", "accepted_count",
        "original_diameter_mm", "accepted_diameter_mm", "original_length_cm",
        "accepted_length_cm", "batch_status", "matcher_disposition",
        "matcher_evidence_band", "contextual_rule_code", "numeric_difference",
        "ownership_conflict", "complete_production_resolution", "source_sha256",
        "engine_version", "matcher_sha256", "candidate_handles",
        "accepted_geometry_handles", "suggested_segment_lengths_cm",
        "accepted_segment_lengths_cm", "review_reason_codes",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        encoded = dict(row)
        for key in (
            "candidate_handles",
            "accepted_geometry_handles",
            "suggested_segment_lengths_cm",
            "accepted_segment_lengths_cm",
            "review_reason_codes",
        ):
            encoded[key] = json.dumps(encoded.get(key) or [], ensure_ascii=False)
        writer.writerow(encoded)
    return output.getvalue().encode("utf-8-sig")


def _layer_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    fields = [
        "project", "layer", "role", "confidence", "relevant_for_analysis",
        "default_visible", "entity_count", "rebar_text_count", "reasons",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _readme_text(summary: Mapping[str, Any]) -> str:
    return f"""# LION REBAR — Learning Pack

Този ZIP е автоматичният край на работната сесия `{summary.get('session_id')}`.

## Съдържание

- `session/summary.json` — обобщение на сесията;
- `session/events.jsonl` — хронология на действията;
- `session/session_database_extract.json` — преносим extract на текущата Session Database без машинните абсолютни пътища;
- `session/decision_journal.csv` — решенията на оператора в удобен табличен вид;
- `session/rule_candidates.json` — само корекции/override/проблеми, групирани като кандидати за правила;
- `session/calibration_evidence.json` — обикновените потвърждения, отделени от новото знание;
- `session/layer_classification.csv` — роли на слоевете по проекти;
- `projects/*/*_AI_FEEDBACK.zip` — възпроизводим пакет за всеки проект;
- `START_PROMPT_BG.md` — начална инструкция за съвместния анализ.

## Инженерен принцип

Решенията на оператора са доказателства. CONFIRMATION се пази като calibration evidence. Само CORRECTION, OVERRIDE и PROBLEM могат да породят кандидат за правило. Нищо не променя автоматично Engine или Matcher. Ново правило влиза само след преглед, независим holdout и regression gate.

Проекти: **{summary.get('project_count', 0)}**  
Събития: **{summary.get('event_count', 0)}**  
Решения: **{summary.get('decision_count', 0)}**
"""


def _start_prompt_text(summary: Mapping[str, Any]) -> str:
    return f"""# Начална задача за анализ на Learning Pack

Работи върху сесия `{summary.get('session_id')}` на LION REBAR.

1. Прочети първо `session/summary.json`, `session/decision_journal.csv`, `session/rule_candidates.json` и `session/calibration_evidence.json`.
2. След това отвори диагностичните ZIP пакети по проекти.
3. Не променяй Engine, Matcher, допуски или production gate преди baseline audit.
4. Раздели находките на: проектантска грешка, нов стил на чертане, слабост на layer classification, слабост на ownership/dimension resolver, липсваща MASTER форма, потенциално общо правило.
5. За всяко предложено правило посочи доказателствата, броя независими случаи, риска от silent wrong и минималния regression test.
6. USER решенията са факти за конкретния случай: CONFIRMATION е calibration evidence; CORRECTION/OVERRIDE/PROBLEM може да стане кандидат за правило; EXCLUSION е само одитна следа.
7. Завърши с приоритизиран план: най-голям ефект при най-малък риск.
"""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    ).encode("utf-8")


def _sanitize_paths(value: Any, project_root: str) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_paths(item, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_paths(item, project_root) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_paths(item, project_root) for item in value]
    if isinstance(value, str) and project_root:
        return value.replace(project_root, ".")
    return value


def _path_key(path: str) -> str:
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:16] if path else ""


def _compact_timestamp(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return (digits[:14] or datetime.now().strftime("%Y%m%d%H%M%S"))
