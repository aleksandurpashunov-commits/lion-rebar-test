"""P0-2 — Undo/audit trail за масовите одобрения от Review Center.

ЗАЩО СЪЩЕСТВУВА
---------------
Review Center има четири "Приеми останалите ..." бутона (силни, силни по
геометрия, F006, финално потвърждение). Всеки от тях пише едновременно в
два файла — ``working/approved_geometry_links.json`` (production истината)
и ``working/review_center_state.json`` (UI решенията) — но до сега нямаше
никакъв път назад: грешен клик върху 40+ позиции можеше да се оправи само
ръчно, позиция по позиция. Project Instructions, т. 12 изрично изисква
"undo / audit trail за масовите решения".

ПРИНЦИП (същият като ``review/apply_to_group.py``)
---------------------------------------------------
Байтов snapshot на двата файла ПРЕДИ операцията, не логическо връщане с
нови записи. Може да се върне само последната активна операция (stack
дисциплина) и никога след production export на резултата от нея.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

JOURNAL_NAME = "bulk_approval_operations.jsonl"
LEDGER_NAME = "approved_geometry_links.json"
STATE_NAME = "review_center_state.json"
SCHEMA = "lion-rebar-bulk-approval-operation/v1"

#: Стойности, приемани от ``mode`` — трябва да съвпадат с бутоните.
KNOWN_MODES = frozenset(
    {"STRICT_SAMPLE", "GEOMETRY_SAMPLE", "F006_SAMPLE", "FINAL_USER_CONFIRMATION"}
)


class BulkApprovalError(RuntimeError):
    """Масовата операция/връщането ѝ е отказано. Нищо не е променено."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class IdentityStamp:
    project_id: str = ""
    source_sha256: str = ""
    source_generation_id: str = ""
    analysis_run_id: str = ""

    @classmethod
    def from_identity(cls, identity: Any) -> "IdentityStamp":
        if identity is None:
            return cls()
        return cls(
            project_id=_text(getattr(identity, "project_id", "")),
            source_sha256=_text(getattr(identity, "source_sha256", "")),
            source_generation_id=_text(getattr(identity, "source_generation_id", "")),
            analysis_run_id=_text(getattr(identity, "analysis_run_id", "")),
        )


def record_bulk_operation(
    project_root: Path | str,
    *,
    mode: str,
    applied_by: str,
    affected_handles: list[str],
    ledger_snapshot: bytes | None,
    state_snapshot: bytes | None,
    identity: Any = None,
    note: str = "",
) -> str:
    """Записва масовата операция с обратимо предишно състояние на двата файла.

    ``ledger_snapshot``/``state_snapshot`` са байтовете на съответните
    файлове ОТ ПРЕДИ операцията (``None``, ако файлът не е съществувал).
    Връща ``operation_id``.
    """

    mode = _text(mode).upper()
    if mode not in KNOWN_MODES:
        raise BulkApprovalError(f"Непознат режим на масово одобрение: {mode}")
    if not _text(applied_by):
        raise BulkApprovalError("Липсва автор на масовата операция.")
    if not affected_handles:
        raise BulkApprovalError("Няма засегнати позиции за запис.")

    stamp = IdentityStamp.from_identity(identity)
    operation_id = uuid.uuid4().hex
    entry = {
        "schema": SCHEMA,
        "operation_id": operation_id,
        "action": "BULK_APPROVE",
        "mode": mode,
        "at": _now(),
        "at_unix_ns": time.time_ns(),
        "applied_by": _text(applied_by),
        "note": _text(note),
        "affected_handles": list(affected_handles),
        "ledger_snapshot": ledger_snapshot.hex() if ledger_snapshot is not None else None,
        "state_snapshot": state_snapshot.hex() if state_snapshot is not None else None,
        "project_id": stamp.project_id,
        "source_sha256": stamp.source_sha256,
        "source_generation_id": stamp.source_generation_id,
        "analysis_run_id": stamp.analysis_run_id,
    }
    path = Path(project_root) / "working" / JOURNAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return operation_id


def load_bulk_operations(project_root: Path | str) -> list[dict]:
    path = Path(project_root) / "working" / JOURNAL_NAME
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def active_bulk_operations(project_root: Path | str) -> list[dict]:
    """Приложени и НЕ върнати масови операции, в реда, в който са станали."""

    operations = load_bulk_operations(project_root)
    undone = {
        op.get("operation_id") for op in operations if op.get("action") == "UNDO_BULK_APPROVE"
    }
    return [
        op for op in operations
        if op.get("action") == "BULK_APPROVE" and op.get("operation_id") not in undone
    ]


def _record_undo(
    project_root: Path | str, *, operation_id: str, undone_by: str, note: str = ""
) -> None:
    path = Path(project_root) / "working" / JOURNAL_NAME
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema": SCHEMA, "action": "UNDO_BULK_APPROVE", "operation_id": operation_id,
            "at": _now(), "undone_by": _text(undone_by), "note": _text(note),
        }, ensure_ascii=False) + "\n")


def _restore_snapshot(path: Path, snapshot_hex: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if snapshot_hex is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(path.suffix + ".rollback")
    temporary.write_bytes(bytes.fromhex(snapshot_hex))
    temporary.replace(path)


def _export_happened_after(project_root: Path | str, operation: Mapping[str, Any]) -> bool:
    """Има ли записан PRODUCTION VERIFIED export след масовата операция.

    Логиката огледално повтаря ``apply_to_group._verified_export_after`` —
    отделен, малък дубликат вместо споделяне на частна функция между два
    журнала, за да остане всеки модул самостоятелно проверим.
    """

    journal = Path(project_root) / "working" / "production_export_history.jsonl"
    if not journal.is_file():
        return False
    operation_ns = int(operation.get("at_unix_ns") or 0)
    operation_at = str(operation.get("at") or "")
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("event") != "PRODUCTION_VERIFIED":
            continue
        export_ns = int(entry.get("at_unix_ns") or 0)
        if operation_ns and export_ns and export_ns > operation_ns:
            return True
        if not operation_ns and str(entry.get("at") or "") > operation_at:
            return True
    return False


def undo_bulk_operation(
    project_root: Path | str,
    operation_id: str,
    *,
    undone_by: str,
    note: str = "",
    exported_after: Callable[[Mapping[str, Any]], bool] | None = None,
) -> int:
    """РЕАЛНО връща масовата операция. Връща броя възстановени позиции.

    Отказва, когато: операцията не съществува/вече е върната; не е
    последната активна масова операция; след нея вече има production
    export (изнесеният опис не се пренаписва мълчаливо).
    """

    operations = load_bulk_operations(project_root)
    target = next(
        (op for op in operations
         if op.get("operation_id") == operation_id and op.get("action") == "BULK_APPROVE"),
        None,
    )
    if target is None:
        raise BulkApprovalError("Няма такава масова операция.")
    if any(op.get("action") == "UNDO_BULK_APPROVE" and op.get("operation_id") == operation_id
           for op in operations):
        raise BulkApprovalError("Операцията вече е върната.")
    active = active_bulk_operations(project_root)
    if not active or active[-1].get("operation_id") != operation_id:
        raise BulkApprovalError(
            "Може да се върне само последната активна масова операция."
        )
    export_check = exported_after or (
        lambda operation: _export_happened_after(project_root, operation)
    )
    if export_check(target):
        raise BulkApprovalError(
            "След тази операция е направен production export; "
            "изнесеният опис не се пренаписва."
        )

    root = Path(project_root)
    _restore_snapshot(root / "working" / LEDGER_NAME, target.get("ledger_snapshot"))
    _restore_snapshot(root / "working" / STATE_NAME, target.get("state_snapshot"))

    _record_undo(root, operation_id=operation_id, undone_by=undone_by, note=note)

    try:
        from lion_rebar.session.decision_contract import append_project_decision_event

        append_project_decision_event(
            root,
            "BULK_APPROVE_UNDONE",
            {
                "operation_id": operation_id,
                "mode": target.get("mode"),
                "annotation_handles": list(target.get("affected_handles") or []),
                "undone_by": undone_by,
                "note": note,
            },
        )
    except Exception:  # noqa: BLE001 - одитният запис не бива да блокира връщането
        pass

    try:
        from lion_rebar.project.final_control_lifecycle import invalidate_final_control

        invalidate_final_control(
            root, event="BULK_APPROVE_UNDONE", actor=undone_by,
            note=f"Върната масова операция {operation_id}",
        )
    except Exception:  # noqa: BLE001
        pass

    return len(target.get("affected_handles") or [])
