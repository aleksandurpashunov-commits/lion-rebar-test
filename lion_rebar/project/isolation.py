"""P0-6 — гаранция срещу изтичане на данни между проекти.

Договор: Project B не наследява от Project A позиции, approvals, geometry
ownership, beam/frame assignment, Final Ledger редове или export състояние.
Историята в Session DB може да се пази, но не влияе на текущия проект без
доказана identity.

ФОРМАТИ (снети от реалния приет reference ``Ръчно-4``)
------------------------------------------------------
Пазените артефакти НЕ са еднакви по структура и не могат да се четат с
един общ парсер. Първата реализация допускаше точно това и обявяваше
чистия reference за нарушение (``UNREADABLE`` за CSV Ledger-а и
``MISSING_DATA_IDENTITY`` за журнала). Реалните формати са:

``final_integrity_ledger.csv``
    CSV с identity колони НА ВСЕКИ РЕД (``project_id``, ``source_sha256``,
    ``source_generation_id``, ``analysis_run_id``). Не е JSON.

``final_control_freeze.json`` / ``final_integrity_report.json`` /
``approved_geometry_links.json`` / ``review_center_state.json``
    JSON обект с вложен ключ ``data_identity``.

``decision_journal.jsonl``
    Поток от СЪБИТИЯ с друга форма: ``project_name``, ``source_sha256``,
    ``event``, ``payload``. Тези редове нямат ``project_id`` и
    ``source_generation_id`` — журналът е одитен лог, не production
    dataset. Сравнява се по ``source_sha256``.

Затова всеки артефакт има собствен четец и собствено правило.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from lion_rebar.project.data_identity import DataIdentity, mismatch_reason

LEDGER_NAME = "final_integrity_ledger.csv"
JOURNAL_NAME = "decision_journal.jsonl"

#: Работни артефакти, които никога не бива да носят чужда identity.
GUARDED_ARTIFACTS: tuple[str, ...] = (
    LEDGER_NAME,
    "final_integrity_report.json",
    "final_control_freeze.json",
    "approved_geometry_links.json",
    "review_center_state.json",
    JOURNAL_NAME,
)

_LEDGER_IDENTITY_COLUMNS = (
    "project_id", "source_sha256", "source_generation_id", "analysis_run_id")


@dataclass
class IsolationViolation:
    artifact: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"artifact": self.artifact, "reason": self.reason, "detail": self.detail}


@dataclass
class IsolationReport:
    project_id: str
    checked: int = 0
    violations: list[IsolationViolation] = field(default_factory=list)
    inspected: dict[str, str] = field(default_factory=dict)

    @property
    def isolated(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "checked_artifacts": self.checked,
            "isolated": self.isolated,
            "inspected": dict(self.inspected),
            "violations": [v.to_dict() for v in self.violations],
        }


# --------------------------------------------------------------- четци


def _check_json_object(path: Path, current: DataIdentity) -> tuple[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return "UNREADABLE", ""
    if not isinstance(payload, Mapping):
        return "UNEXPECTED_FORMAT", type(payload).__name__
    reason = mismatch_reason(payload, current)
    detail = ""
    if reason:
        raw = payload.get("data_identity")
        source = raw if isinstance(raw, Mapping) else payload
        detail = str(source.get("project_id") or "")[:40]
    return reason, detail


def _check_ledger_csv(path: Path, current: DataIdentity) -> tuple[str, str]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            if not columns:
                return "UNREADABLE", ""
            if not any(column in columns for column in _LEDGER_IDENTITY_COLUMNS):
                return "MISSING_DATA_IDENTITY", "няма identity колони"
            for index, row in enumerate(reader, start=1):
                reason = mismatch_reason(row, current)
                if reason:
                    return reason, f"ред {index}: {str(row.get('project_id') or '')[:40]}"
    except (OSError, UnicodeDecodeError, csv.Error):
        return "UNREADABLE", ""
    return "", ""


def _check_decision_journal(path: Path, current: DataIdentity) -> tuple[str, str]:
    """Одитен журнал: липсата на пълна identity НЕ е нарушение."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "UNREADABLE", ""
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return "UNREADABLE", f"ред {index}"
        if not isinstance(row, Mapping):
            return "UNEXPECTED_FORMAT", f"ред {index}"
        if isinstance(row.get("data_identity"), Mapping):
            reason = mismatch_reason(row, current)
            if reason:
                return reason, f"ред {index}"
            continue
        recorded = str(row.get("source_sha256") or "").strip()
        if recorded and current.source_sha256 and recorded != current.source_sha256:
            return "SOURCE_SHA256_MISMATCH", f"ред {index}: {recorded[:16]}"
    return "", ""


_HANDLERS: dict[str, Callable[[Path, DataIdentity], tuple[str, str]]] = {
    LEDGER_NAME: _check_ledger_csv,
    JOURNAL_NAME: _check_decision_journal,
}


def _handler_for(name: str) -> Callable[[Path, DataIdentity], tuple[str, str]]:
    if name in _HANDLERS:
        return _HANDLERS[name]
    if name.endswith(".csv"):
        return _check_ledger_csv
    if name.endswith(".jsonl"):
        return _check_decision_journal
    return _check_json_object


# --------------------------------------------------------------- одит


def audit_project_isolation(
    project_root: Path | str,
    current: DataIdentity,
    *,
    artifacts: Iterable[str] = GUARDED_ARTIFACTS,
) -> IsolationReport:
    """Проверява работните артефакти за чужда identity.

    Липсващ артефакт НЕ е нарушение (чист проект). Артефакт с identity на
    друг проект/поколение Е нарушение.
    """

    root = Path(project_root)
    report = IsolationReport(project_id=current.project_id)
    for name in artifacts:
        path = root / "working" / name
        if not path.is_file():
            continue
        report.checked += 1
        reason, detail = _handler_for(name)(path, current)
        report.inspected[name] = reason or "OK"
        if reason:
            report.violations.append(IsolationViolation(name, reason, detail))
    return report


def ledger_rows_isolated(rows: Iterable[Mapping[str, Any]], current: DataIdentity) -> list[str]:
    """Проверява редовете на Final Ledger за чужда identity."""

    problems: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        seen.add((
            str(row.get("project_id") or ""),
            str(row.get("source_generation_id") or ""),
            str(row.get("analysis_run_id") or ""),
        ))
        reason = mismatch_reason(row, current, require_run=True)
        if reason:
            problems.append(f"ред {index}: {reason}")
            if len(problems) >= 10:
                problems.append("… (спряно след 10)")
                break
    if len(seen) > 1:
        problems.insert(0, f"MULTIPLE_IDENTITIES_IN_LEDGER: {len(seen)} различни")
    return problems


def assert_clean_for_new_project(project_root: Path | str) -> list[str]:
    """Нов проект не бива да стартира с наследени production артефакти."""

    root = Path(project_root) / "working"
    return [
        f"INHERITED_ARTIFACT: {name}"
        for name in GUARDED_ARTIFACTS
        if (root / name).is_file()
    ]
