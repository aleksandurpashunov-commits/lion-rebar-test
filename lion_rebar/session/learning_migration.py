from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from lion_rebar.session.learning_pack import (
    _build_rule_candidates,
    _current_learning_rows,
    _decision_metrics,
    _decision_rows,
)


def audit_learning_pack(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    with zipfile.ZipFile(source, "r") as archive:
        names = set(archive.namelist())
        manifest = _json_member(archive, "manifest.json") if "manifest.json" in names else {}
        if "session/events.jsonl" in names:
            events = _jsonl_member(archive, "session/events.jsonl")
            decisions = _decision_rows(events)
        elif "session/decision_journal.json" in names:
            payload = _json_member(archive, "session/decision_journal.json")
            decisions = [dict(row) for row in payload.get("decisions", []) if isinstance(row, dict)]
        else:
            decisions = []
    current = _current_learning_rows(decisions)
    metrics = _decision_metrics(decisions)
    candidates = _build_rule_candidates(decisions)
    return {
        "path": str(source),
        "pack_schema": manifest.get("schema"),
        "application_version": manifest.get("application_version"),
        "status": "AUDITED_NOT_IMPORTED_NOT_ACTIVATED",
        "accepted_complete": sum(bool(row.get("complete_production_resolution")) for row in current),
        "incomplete": sum(not bool(row.get("complete_production_resolution")) for row in current),
        "conflict": len({str(row.get("instance_id") or "") for row in current if row.get("decision_conflict")}),
        "duplicate": int(metrics.get("duplicate_event_row_count") or 0),
        "candidate_count": len(candidates),
        "activated_rule_count": 0,
        "metrics": metrics,
    }


def audit_learning_packs(paths: Iterable[Path | str]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            reports.append(audit_learning_pack(path))
        except Exception as exc:  # migration audit must report, never guess
            errors.append({"path": str(path), "error": str(exc)})
    return {
        "schema": "lion-rebar-learning-migration-report/v1",
        "policy": "READ_ONLY_AUDIT_NO_RULE_ACTIVATION",
        "pack_count": len(reports),
        "error_count": len(errors),
        "totals": {
            key: sum(int(row.get(key) or 0) for row in reports)
            for key in (
                "accepted_complete",
                "incomplete",
                "conflict",
                "duplicate",
                "candidate_count",
                "activated_rule_count",
            )
        },
        "packs": reports,
        "errors": errors,
    }


def report_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    lines = [
        "# LION REBAR rc39 — миграционен одит на Learning Pack",
        "",
        "Режим: **само прочит; няма импортирано или активирано правило**.",
        "",
        f"- Проверени пакети: {report.get('pack_count', 0)}",
        f"- Грешки при четене: {report.get('error_count', 0)}",
        f"- Пълни активни примери: {totals.get('accepted_complete', 0)}",
        f"- Частични примери: {totals.get('incomplete', 0)}",
        f"- Конфликтни instance групи: {totals.get('conflict', 0)}",
        f"- Дубликати: {totals.get('duplicate', 0)}",
        f"- Кандидат-правила: {totals.get('candidate_count', 0)}",
        f"- Активирани правила: {totals.get('activated_rule_count', 0)}",
        "",
        "## Пакети",
        "",
    ]
    for row in report.get("packs", []):
        lines.append(
            f"- `{Path(str(row.get('path') or '')).name}` — "
            f"complete {row.get('accepted_complete', 0)}, incomplete {row.get('incomplete', 0)}, "
            f"conflict {row.get('conflict', 0)}, duplicate {row.get('duplicate', 0)}, "
            f"candidates {row.get('candidate_count', 0)}."
        )
    for row in report.get("errors", []):
        lines.append(f"- ERROR `{row.get('path')}` — {row.get('error')}")
    return "\n".join(lines) + "\n"


def _json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    value = json.loads(archive.read(name).decode("utf-8-sig"))
    return value if isinstance(value, dict) else {}


def _jsonl_member(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in archive.read(name).decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            result.append(value)
    return result
