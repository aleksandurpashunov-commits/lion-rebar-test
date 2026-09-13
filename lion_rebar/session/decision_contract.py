from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


DECISION_CONTRACT_SCHEMA = "lion-rebar-human-decision/v2"
DECISION_JOURNAL_SCHEMA = "lion-rebar-human-decision-journal/v2"
DECISION_OUTBOX_SCHEMA = "lion-rebar-human-decision-outbox/v1"

DECISION_EVENT_TYPES = frozenset(
    {
        "APPROVED",
        "BULK_APPROVED",
        "ENGINEER_RESOLVED",
        "PROBLEM_RECORDED",
        "EXCLUDED_BY_ENGINEER",
        "NON_PRODUCTION_POSITION",
        "MANUAL_ASSIGNED",
        "MANUAL_SHAPE_DRAFT",
        "FLAGGED",
        "NO_VALID_CANDIDATE",
        "MISSING_MASTER",
        "GROUP_APPLIED",
        "GROUP_UNDONE",
        "SIMILAR_APPLIED",
        "SIMILAR_UNDONE",
    }
)

#: Undo-type events: never themselves superseded, and mark the operation inactive.
UNDO_EVENT_TYPES = frozenset({"GROUP_UNDONE", "SIMILAR_UNDONE"})

#: Bulk/group-shaped events for the ``operation_kind`` classification below.
BULK_EVENT_TYPES = frozenset({"BULK_APPROVED", "GROUP_APPLIED", "SIMILAR_APPLIED"})

JOURNAL_NAME = "decision_events_v2.jsonl"
OUTBOX_NAME = "decision_event_outbox.jsonl"


def is_human_decision_event(event_type: str) -> bool:
    return str(event_type or "").strip().upper() in DECISION_EVENT_TYPES


def positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def positive_integer(value: Any) -> int | None:
    number = positive_number(value)
    if number is None or abs(number - round(number)) > 1e-6:
        return None
    return int(round(number))


def normalized_segments(value: Any) -> tuple[list[float], str, bool]:
    """Read only explicit production segments from old and current schemas.

    A legacy ``dimensions`` object is a mapping.  Iterating it used to turn
    field names such as ``declared_length_cm`` into segment values.  Mappings
    are now inspected only through named production fields.  Invalid or empty
    values stay explicitly incomplete; geometry is never used as a fallback.
    """

    source = "MISSING"
    candidate = value
    if isinstance(value, Mapping):
        candidate = None
        for key in (
            "production_segment_lengths_cm",
            "segment_lengths_cm",
            "canonical_segment_lengths_cm",
        ):
            nested = value.get(key)
            if isinstance(nested, (list, tuple)):
                candidate = nested
                source = f"EXPLICIT:{key}"
                break
        if candidate is None:
            return [], "LEGACY_DIMENSIONS_OBJECT_WITHOUT_SEGMENTS", False
    elif isinstance(value, (list, tuple)):
        source = "EXPLICIT_LIST"
    else:
        return [], source, False

    numbers: list[float] = []
    valid = True
    for raw in candidate:
        number = positive_number(raw)
        if number is None:
            valid = False
            continue
        numbers.append(number)
    if not numbers:
        return [], source, False
    return numbers, source, valid and len(numbers) == len(candidate)


def first_explicit_segments(*values: Any) -> tuple[list[float], str, bool]:
    incomplete_source = "MISSING"
    for value in values:
        segments, source, complete = normalized_segments(value)
        if segments:
            return segments, source, complete
        if source != "MISSING":
            incomplete_source = source
    return [], incomplete_source, False


def derive_straight_segment(
    shape_code: str,
    segments: Iterable[Any],
    production_length_cm: Any,
) -> tuple[list[float], str, bool]:
    explicit, source, complete = normalized_segments(list(segments))
    if explicit:
        return explicit, source, complete
    length = positive_number(production_length_cm)
    if str(shape_code or "").strip().upper() == "F001" and length is not None:
        return [length], "DERIVED_FROM_EXPLICIT_ACCEPTED_TOTAL_FOR_F001", True
    return [], source, False


def append_project_decision_event(
    project_root: Path | str,
    event_type: str,
    payload: Mapping[str, Any] | None,
    *,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Persist a human action before best-effort Session DB delivery.

    The project state is authoritative.  This function appends an immutable
    v2 event and a durable PENDING outbox entry with ``fsync``.  A later retry
    reuses the same event id and therefore cannot increase the Session DB
    count twice.
    """

    root = Path(project_root)
    working = root / "working"
    working.mkdir(parents=True, exist_ok=True)
    project = _read_json(working / "project.json")
    safe_payload = _json_safe(dict(payload or {}))
    timestamp = occurred_at or datetime.now().astimezone().isoformat(timespec="seconds")
    event_name = str(event_type or "").strip().upper()
    if not is_human_decision_event(event_name):
        raise ValueError(f"Неподдържано човешко decision събитие: {event_name}")

    operation_id = str(
        safe_payload.get("operation_id")
        or _mapping(safe_payload.get("human_decision")).get("operation_id")
        or ""
    )
    handles = _decision_handles(safe_payload)
    prior = _read_unique_events(working / JOURNAL_NAME)
    revisions = [
        int(row.get("decision_revision") or 0)
        for row in prior.values()
        if set(_event_handles(row)) & set(handles)
    ]
    decision_revision = max(revisions, default=0) + 1

    identity = _identity(project, safe_payload, root)
    supersedes = sorted(
        {
            str(row.get("event_id"))
            for row in prior.values()
            if row.get("active", True)
            and set(_event_handles(row)) & set(handles)
            and str(row.get("event_type") or "") not in UNDO_EVENT_TYPES
        }
    )
    content_key = {
        "event_type": event_name,
        "identity": identity,
        "operation_id": operation_id,
        "handles": handles,
        "decision_revision": decision_revision,
        "payload": safe_payload,
    }
    stable_id = str(safe_payload.get("stable_event_id") or "")
    if not stable_id:
        digest = hashlib.sha256(_canonical_json(content_key).encode("utf-8")).hexdigest()
        stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lion-rebar:{digest}"))

    envelope = {
        "schema": DECISION_CONTRACT_SCHEMA,
        "event_id": stable_id,
        "occurred_at": timestamp,
        "event_type": event_name,
        "operation_kind": (
            "UNDO" if event_name in UNDO_EVENT_TYPES else
            "BULK" if event_name in BULK_EVENT_TYPES else
            "INDIVIDUAL"
        ),
        "operation_id": operation_id or None,
        "decision_revision": decision_revision,
        "supersedes_event_ids": supersedes,
        "invalidates_operation_id": (
            str(safe_payload.get("invalidates_operation_id") or operation_id or "") or None
            if event_name in UNDO_EVENT_TYPES
            else None
        ),
        "active": event_name not in UNDO_EVENT_TYPES,
        "annotation_handles": handles,
        "source_identity": identity,
        "payload": safe_payload,
    }

    if stable_id not in prior:
        _append_jsonl_fsync(
            working / OUTBOX_NAME,
            {
                "schema": DECISION_OUTBOX_SCHEMA,
                "state": "PENDING",
                "event_id": stable_id,
                "at": timestamp,
                "event": envelope,
            },
        )
        _append_jsonl_fsync(working / JOURNAL_NAME, envelope)
    return envelope


def mark_project_decision_delivered(
    project_root: Path | str,
    event_id: str,
    *,
    session_id: str,
) -> None:
    root = Path(project_root)
    if not event_id:
        return
    _append_jsonl_fsync(
        root / "working" / OUTBOX_NAME,
        {
            "schema": DECISION_OUTBOX_SCHEMA,
            "state": "DELIVERED",
            "event_id": str(event_id),
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "session_id": str(session_id),
        },
    )


def pending_project_decision_events(project_root: Path | str) -> list[dict[str, Any]]:
    path = Path(project_root) / "working" / OUTBOX_NAME
    if not path.is_file():
        return []
    pending: dict[str, dict[str, Any]] = {}
    delivered: set[str] = set()
    for row in _read_jsonl(path):
        event_id = str(row.get("event_id") or "")
        if not event_id:
            continue
        if str(row.get("state") or "") == "DELIVERED":
            delivered.add(event_id)
            pending.pop(event_id, None)
            continue
        event = row.get("event")
        if str(row.get("state") or "") == "PENDING" and isinstance(event, Mapping):
            pending[event_id] = dict(event)
    return [pending[key] for key in sorted(pending) if key not in delivered]


def flush_project_decision_outbox(
    project_root: Path | str,
    deliver: Callable[[Mapping[str, Any]], str],
) -> tuple[int, int]:
    """Deliver pending events; return ``(delivered, remaining)``."""

    root = Path(project_root)
    delivered_count = 0
    for envelope in pending_project_decision_events(root):
        event_id = str(envelope.get("event_id") or "")
        # Repair the narrow crash window between PENDING fsync and journal fsync.
        journal = _read_unique_events(root / "working" / JOURNAL_NAME)
        if event_id and event_id not in journal:
            _append_jsonl_fsync(root / "working" / JOURNAL_NAME, dict(envelope))
        session_id = deliver(envelope)
        mark_project_decision_delivered(root, event_id, session_id=session_id)
        delivered_count += 1
    return delivered_count, len(pending_project_decision_events(root))


def _identity(project: Mapping[str, Any], payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    extensions = _mapping(project.get("extensions"))
    nested = _mapping(payload.get("data_identity"))
    if not nested:
        nested = _mapping(extensions.get("data_identity"))
    source_sha = str(
        nested.get("source_sha256")
        or payload.get("source_sha256")
        or extensions.get("source_sha256")
        or ""
    )
    project_id = str(nested.get("project_id") or project.get("project_id") or project.get("id") or root.name)
    drawing_basis = source_sha or str(nested.get("source_generation_id") or project_id)
    drawing_family_id = hashlib.sha256(drawing_basis.encode("utf-8")).hexdigest()
    return {
        "project_id": project_id,
        "project_name": str(project.get("name") or root.name),
        "source_sha256": source_sha or None,
        "source_generation_id": nested.get("source_generation_id") or payload.get("source_generation_id"),
        "analysis_run_id": nested.get("analysis_run_id") or payload.get("analysis_run_id"),
        "drawing_revision": nested.get("drawing_revision") or payload.get("drawing_revision"),
        "drawing_family_id": drawing_family_id,
        "engine_id": project.get("engine_id"),
        "engine_version": project.get("engine_version"),
        "engine_sha256": project.get("engine_sha256"),
    }


def _decision_handles(payload: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    if isinstance(payload.get("annotation_handles"), list):
        values.extend(payload.get("annotation_handles") or [])
    handle = payload.get("annotation_handle")
    if handle:
        values.append(handle)
    annotation = _mapping(payload.get("annotation"))
    if annotation.get("handle"):
        values.append(annotation.get("handle"))
    decision = _mapping(payload.get("decision"))
    if decision.get("annotation_handle"):
        values.append(decision.get("annotation_handle"))
    for key in ("decisions", "consequences"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping) and row.get("annotation_handle"):
                values.append(row.get("annotation_handle"))
    return sorted({str(value) for value in values if str(value or "").strip()})


def _event_handles(event: Mapping[str, Any]) -> list[str]:
    raw = event.get("annotation_handles")
    return [str(value) for value in raw] if isinstance(raw, list) else []


def _append_jsonl_fsync(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_json(_json_safe(dict(row))) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _read_unique_events(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        event_id = str(row.get("event_id") or "")
        if event_id:
            result[event_id] = row
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
