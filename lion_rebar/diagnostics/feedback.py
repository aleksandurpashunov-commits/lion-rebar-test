from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.config.constants import APP_VERSION


FEEDBACK_EVENT_SCHEMA = "lion-rebar-review-feedback-event/v1"
DECISION_JOURNAL_SCHEMA = "lion-rebar-decision-journal-event/v1"
DECISION_EVENT_TYPES = {
    "APPROVED",
    "BULK_APPROVED",
    "ENGINEER_RESOLVED",
    "PROBLEM_RECORDED",
    "EXCLUDED_BY_ENGINEER",
    "NON_PRODUCTION_POSITION",
    "MANUAL_ASSIGNED",
    "MANUAL_SHAPE_DRAFT",
    # Legacy diagnostic event names are preserved in old projects and remain
    # part of the journal for audit.  They are not treated as resolved by the
    # current integrity pipeline.
    "FLAGGED",
    "NO_VALID_CANDIDATE",
    "MISSING_MASTER",
    "GROUP_APPLIED",
    "GROUP_UNDONE",
}


def append_feedback_event(
    project_root: Path,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    occurred_at: str | None = None,
) -> Path:
    """Append one structured owner/matcher decision to the project journal.

    The journal is deliberately project-local and contains no whole-program
    state.  It is included automatically in the AI diagnostic bundle, which
    makes future regressions reproducible without screenshots or copied notes.
    """

    root = Path(project_root)
    event_name = str(event_type or "").strip().upper()
    if not event_name:
        raise ValueError("event_type е задължителен.")
    working = root / "working"
    working.mkdir(parents=True, exist_ok=True)
    project = _read_project_metadata(working / "project.json")
    extensions = project.get("extensions")
    extensions = extensions if isinstance(extensions, dict) else {}
    row = {
        "schema": FEEDBACK_EVENT_SCHEMA,
        "at": occurred_at
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event_name,
        "app_version": APP_VERSION,
        "project_name": str(project.get("name") or root.name),
        "source_sha256": extensions.get("source_sha256"),
        "engine": {
            "id": project.get("engine_id"),
            "version": project.get("engine_version"),
            "sha256": project.get("engine_sha256"),
        },
        "payload": _json_safe(dict(payload or {})),
    }
    path = working / "review_feedback_events.jsonl"
    encoded = (
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        # The authoritative decision is persisted separately before this
        # diagnostic-only event is appended. Closing the stream flushes the
        # record without forcing a full disk sync on every Review Center click.

    if event_name in DECISION_EVENT_TYPES:
        journal_path = working / "decision_journal.jsonl"
        journal_rows: list[dict[str, Any]] = []
        decisions = row["payload"].get("decisions") if isinstance(row.get("payload"), dict) else None
        if event_name == "BULK_APPROVED" and isinstance(decisions, list) and decisions:
            for item in decisions:
                if not isinstance(item, Mapping):
                    continue
                decision_row = dict(row)
                decision_row["schema"] = DECISION_JOURNAL_SCHEMA
                decision_row["payload"] = {
                    "mode": row["payload"].get("mode"),
                    "bulk_event": True,
                    **_json_safe(dict(item)),
                }
                journal_rows.append(decision_row)
        if not journal_rows:
            decision_row = dict(row)
            decision_row["schema"] = DECISION_JOURNAL_SCHEMA
            journal_rows.append(decision_row)
        with journal_path.open("a", encoding="utf-8", newline="\n") as stream:
            for decision_row in journal_rows:
                stream.write(
                    json.dumps(
                        decision_row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
    return path


def _read_project_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        # Recording absolute machine paths is unnecessary and makes a feedback
        # fixture non-portable.  Keep only the final filename.
        return value.name
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
