"""P1-3 — Apply to All Similar.

Разликата спрямо ``Apply to Group``
-----------------------------------
``Apply to Group`` изисква ПЪЛНО съвпадение на строгия подпис и работи
само върху една вече показана група. ``Apply to All Similar`` търси в
целия отчет и затова е по-рискован.

Тук подписът е по-широк по едно-единствено измерение: игнорира се
``geometry_class``, защото повторените екземпляри на една и съща форма
имат различен брой/различни handles. ВСИЧКО останало от договора остава
задължително — project identity, source generation, нормализиран надпис,
count, Ø, L, MASTER, проблемен код и provenance class.

Защити, които остават в сила:
  * задължителен preview с точния списък засегнати позиции, преди Apply
    да е достъпен изобщо;
  * таван на обхвата (``MAX_SIMILAR_TARGETS``) — над него операцията се
    отказва, вместо да мине мълчаливо;
  * позиция с ownership конфликт или без геометрия никога не се включва;
  * решение НЕ се прилага върху вече решена позиция;
  * пълен обратим journal (byte-snapshot + undo), огледален на
    ``apply_to_group.py``.

Собственикът включи функцията съзнателно на 2026-09-09, след като горните
защити бяха реално изпълнени и покрити с функционални тестове
(``tests/test_apply_to_similar_wiring.py``) — виж ``FEATURE_ENABLED_DEFAULT``.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from lion_rebar.project.data_identity import DataIdentity, mismatch_reason
from lion_rebar.review.apply_to_group import (
    APPLICABLE_FIELDS,
    _number,
    _text,
    _upper,
    problem_class,
    provenance_class,
)
from lion_rebar.session.decision_contract import append_project_decision_event

JOURNAL_NAME = "similar_operations.jsonl"
SCHEMA = "lion-rebar-similar-operation/v1"

#: Собственикът съзнателно включи функцията на 2026-09-09, след като
#: preview/cap/undo защитите бяха довършени и покрити с тестове (виж
#: docstring-а по-горе). Преди тази дата стоеше ``False`` (LEVEL 2).
FEATURE_ENABLED_DEFAULT = True

#: Над този брой засегнати позиции операцията се отказва.
MAX_SIMILAR_TARGETS = 200

#: Статуси, които вече са решени и не бива да се пипат групово.
RESOLVED_STATUSES = frozenset({
    "ОДОБРЕНА ВРЪЗКА", "NON_PRODUCTION_POSITION",
})


class SimilarApplyDisabled(RuntimeError):
    """Функцията е изключена — не се изпълнява мълчаливо."""


def similar_signature(row: Mapping[str, Any], identity: DataIdentity) -> tuple:
    """По-широкият подпис: без geometry_class, всичко останало е задължително."""

    return (
        identity.project_id,
        identity.source_generation_id,
        _text(row.get("annotation_text")),
        _number(row.get("annotation_count") or row.get("production_count")),
        _number(row.get("diameter_mm") or row.get("production_diameter_mm")),
        _number(row.get("declared_length_cm") or row.get("production_length_cm")),
        _upper(row.get("master_shape_code")),
        problem_class(row),
        provenance_class(row),
    )


@dataclass(frozen=True)
class SimilarTarget:
    annotation_handle: str
    annotation_text: str
    review_group_key: str
    included: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "annotation_handle": self.annotation_handle,
            "annotation_text": self.annotation_text,
            "review_group_key": self.review_group_key,
            "included": self.included, "reason": self.reason,
        }


@dataclass
class SimilarApplicationPlan:
    representative_handle: str
    enabled: bool
    signature: tuple
    changes: dict[str, Any] = field(default_factory=dict)
    targets: list[SimilarTarget] = field(default_factory=list)
    refusal: str = ""

    @property
    def included(self) -> list[SimilarTarget]:
        return [t for t in self.targets if t.included]

    @property
    def excluded(self) -> list[SimilarTarget]:
        return [t for t in self.targets if not t.included]

    @property
    def affected_count(self) -> int:
        return len(self.included)

    @property
    def affected_groups(self) -> int:
        return len({t.review_group_key for t in self.included if t.review_group_key})

    @property
    def can_apply(self) -> bool:
        return (self.enabled and not self.refusal
                and self.affected_count > 0 and bool(self.changes))

    def to_dict(self) -> dict:
        return {
            "representative_handle": self.representative_handle,
            "enabled": self.enabled, "refusal": self.refusal,
            "changes": dict(self.changes),
            "affected_count": self.affected_count,
            "affected_groups": self.affected_groups,
            "excluded_count": len(self.excluded),
            "targets": [t.to_dict() for t in self.targets],
        }


def build_similar_plan(
    representative: Mapping[str, Any],
    all_rows: Sequence[Mapping[str, Any]],
    identity: DataIdentity,
    *,
    changes: Mapping[str, Any] | None = None,
    enabled: bool = FEATURE_ENABLED_DEFAULT,
    max_targets: int = MAX_SIMILAR_TARGETS,
) -> SimilarApplicationPlan:
    """Изгражда preview за Apply to All Similar.

    Планът може да се построи и когато функцията е изключена — така
    операторът вижда какво БИ станало, без нищо да се променя. Прилагането
    обаче е блокирано (``can_apply`` е False).
    """

    rep_handle = _text(representative.get("annotation_handle"))
    signature = similar_signature(representative, identity)
    applicable = {
        key: value for key, value in (changes or {}).items()
        if key in APPLICABLE_FIELDS
    }
    plan = SimilarApplicationPlan(
        representative_handle=rep_handle, enabled=bool(enabled),
        signature=signature, changes=applicable)

    for row in all_rows:
        handle = _text(row.get("annotation_handle"))
        common = {
            "annotation_handle": handle,
            "annotation_text": _text(row.get("annotation_text")),
            "review_group_key": _text(row.get("review_group_key")),
        }
        if handle == rep_handle:
            plan.targets.append(SimilarTarget(included=False, reason="REPRESENTATIVE",
                                              **common))
            continue
        identity_problem = mismatch_reason(row, identity)
        if identity_problem:
            plan.targets.append(SimilarTarget(included=False, reason=identity_problem,
                                              **common))
            continue
        if similar_signature(row, identity) != signature:
            continue  # различна позиция — изобщо не е кандидат, не се показва
        status = _text(row.get("batch_status")).upper()
        if status in {s.upper() for s in RESOLVED_STATUSES}:
            plan.targets.append(SimilarTarget(included=False, reason="ALREADY_RESOLVED",
                                              **common))
            continue
        if bool(row.get("global_conflict")) or bool(row.get("geometry_owner_conflict")):
            plan.targets.append(SimilarTarget(included=False,
                                              reason="OWNERSHIP_CONFLICT", **common))
            continue
        if not _text(row.get("candidate_handles")):
            plan.targets.append(SimilarTarget(included=False, reason="NO_GEOMETRY",
                                              **common))
            continue
        plan.targets.append(SimilarTarget(included=True, **common))

    if plan.affected_count > max_targets:
        plan.refusal = (
            f"SCOPE_TOO_LARGE: {plan.affected_count} позиции надхвърлят тавана "
            f"от {max_targets}; раздели решението на групи")
    if not plan.enabled:
        plan.refusal = plan.refusal or "FEATURE_DISABLED"
    return plan


def assert_similar_enabled(plan: SimilarApplicationPlan) -> None:
    """Използва се преди реално прилагане: изключената функция не мълчи."""

    if not plan.enabled:
        raise SimilarApplyDisabled(
            "Apply to All Similar е изключена (LEVEL 2). Включи я съзнателно "
            "след доказана безопасност.")
    if plan.refusal:
        raise SimilarApplyDisabled(plan.refusal)


# ---------------------------------------------------------------- обратимост


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_similar_operation(
    project_root: Path | str,
    plan: SimilarApplicationPlan,
    *,
    applied_by: str,
    note: str,
    identity: DataIdentity,
    before_state: Mapping[str, Mapping[str, Any]] | None = None,
    persistence_snapshot: str = "",
) -> str:
    """Записва действието с обратимо предишно състояние. Връща ``operation_id``.

    Огледало на ``apply_to_group.record_group_operation`` — същият journal/
    undo принцип, отделен файл, за да остане всеки модул самостоятелно
    проверим.
    """

    if not plan.can_apply:
        raise ValueError("Няма приложими членове или няма зададени промени.")
    if not _text(applied_by):
        raise ValueError("Липсва автор на действието.")
    if not _text(note):
        raise ValueError("Действието изисква инженерна бележка.")

    operation_id = uuid.uuid4().hex
    entry = {
        "schema": SCHEMA,
        "operation_id": operation_id,
        "action": "APPLY_TO_SIMILAR",
        "at": _now(),
        "at_unix_ns": time.time_ns(),
        "applied_by": _text(applied_by),
        "note": _text(note),
        "representative_handle": plan.representative_handle,
        "changes": dict(plan.changes),
        "affected_handles": [t.annotation_handle for t in plan.included],
        "persistence_snapshot": persistence_snapshot,
        "excluded": [
            {"annotation_handle": t.annotation_handle, "reason": t.reason}
            for t in plan.excluded
        ],
        "before_state": {
            handle: dict(state)
            for handle, state in (before_state or {}).items()
        },
        "project_id": identity.project_id,
        "source_sha256": identity.source_sha256,
        "source_generation_id": identity.source_generation_id,
        "analysis_run_id": identity.analysis_run_id,
    }
    path = Path(project_root) / "working" / JOURNAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return operation_id


def load_similar_operations(project_root: Path | str) -> list[dict]:
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


def active_similar_operations(project_root: Path | str) -> list[dict]:
    """Приложени и НЕ върнати действия."""

    operations = load_similar_operations(project_root)
    undone = {
        op.get("operation_id") for op in operations if op.get("action") == "UNDO_SIMILAR"
    }
    return [
        op for op in operations
        if op.get("action") == "APPLY_TO_SIMILAR" and op.get("operation_id") not in undone
    ]


def _verified_export_after(project_root: Path | str, operation: Mapping[str, Any]) -> bool:
    """Има ли записан PRODUCTION VERIFIED export след действието."""

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


class SimilarApplicationError(RuntimeError):
    """Действието не е приложено. Нищо не е променено."""


def _restore_snapshot(persistence: Path, snapshot: bytes | None) -> None:
    persistence.parent.mkdir(parents=True, exist_ok=True)
    if snapshot is None:
        persistence.unlink(missing_ok=True)
        return
    temporary = persistence.with_suffix(".rollback")
    temporary.write_bytes(snapshot)
    temporary.replace(persistence)


def record_similar_operation_failure(
    project_root: Path | str, plan: SimilarApplicationPlan, *,
    applied_by: str, note: str, identity: DataIdentity, error: str,
) -> None:
    """Провалът също е одитно събитие -- иначе изчезва без следа."""

    path = Path(project_root) / "working" / JOURNAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema": SCHEMA, "action": "APPLY_TO_SIMILAR_FAILED",
            "at": _now(), "applied_by": _text(applied_by), "note": _text(note),
            "representative_handle": plan.representative_handle,
            "error": error, "project_id": identity.project_id,
        }, ensure_ascii=False) + "\n")


def apply_similar_plan(
    project_root: Path | str,
    plan: SimilarApplicationPlan,
    rows_by_handle: Mapping[str, Mapping[str, Any]],
    identity: DataIdentity,
    *,
    applied_by: str,
    note: str,
    save_decision=None,
) -> str:
    """Прилага решението АТОМАРНО върху всички включени позиции.

    Огледало на ``apply_to_group.apply_group_plan``: минава през същия
    ``save_engineer_decision`` контракт (Apply to Similar ускорява
    ВЪВЕЖДАНЕТО, не е втори път до production истината), атомарен byte
    snapshot на persistence файла преди прилагането, при провал на КОЯТО и
    да е позиция всичко се връща и не остава частично приложено. Отказва
    веднага, ако функцията е изключена (LEVEL 2) — виж
    :func:`assert_similar_enabled`.
    """

    assert_similar_enabled(plan)
    if not _text(applied_by):
        raise SimilarApplicationError("Липсва автор на действието.")
    if not _text(note):
        raise SimilarApplicationError("Действието изисква инженерна бележка.")

    if save_decision is None:
        from lion_rebar.engineer_decision import save_engineer_decision

        save_decision = save_engineer_decision

    root = Path(project_root)
    persistence = root / "working" / "approved_geometry_links.json"
    snapshot: bytes | None = None
    if persistence.is_file():
        snapshot = persistence.read_bytes()

    before_state: dict[str, dict] = {}
    applied: list[str] = []
    try:
        for target in plan.included:
            row = rows_by_handle.get(target.annotation_handle)
            if row is None:
                raise SimilarApplicationError(
                    f"Позиция {target.annotation_handle} липсва в отчета.")
            before_state[target.annotation_handle] = dict(row)
            from lion_rebar.review.apply_to_group import _decision_payload

            save_decision(root, _decision_payload(
                row, plan.changes, note, plan.representative_handle))
            applied.append(target.annotation_handle)
    except Exception as error:  # noqa: BLE001
        _restore_snapshot(persistence, snapshot)
        record_similar_operation_failure(
            root, plan, applied_by=applied_by, note=note,
            identity=identity, error=str(error))
        raise SimilarApplicationError(
            f"Действието е отменено изцяло: {error}") from error

    operation_id = record_similar_operation(
        root, plan, applied_by=applied_by, note=note,
        identity=identity, before_state=before_state,
        persistence_snapshot=snapshot.hex() if snapshot is not None else "")
    append_project_decision_event(
        root,
        "SIMILAR_APPLIED",
        {
            "operation_id": operation_id,
            "mode": "APPLY_TO_SIMILAR",
            "annotation_handles": applied,
            "representative_handle": plan.representative_handle,
            "applied_by": applied_by,
            "note": note,
            "data_identity": identity.to_dict(),
        },
    )

    try:
        from lion_rebar.project.final_control_lifecycle import invalidate_final_control

        invalidate_final_control(
            root, event="APPLY_TO_SIMILAR", actor=applied_by,
            note=f"Apply to Similar {operation_id} върху {len(applied)} позиции")
    except Exception:  # noqa: BLE001
        pass
    return operation_id


def undo_similar_operation(
    project_root: Path | str, operation_id: str, *,
    undone_by: str, note: str = "", save_decision=None,
    exported_after=None,
) -> int:
    """РЕАЛНО връща действие. Връща броя възстановени позиции.

    Отказва, когато: действието не съществува/вече е върнато; не е
    последното активно действие; след него вече има production export.
    """

    operations = load_similar_operations(project_root)
    target = next(
        (op for op in operations
         if op.get("operation_id") == operation_id
         and op.get("action") == "APPLY_TO_SIMILAR"), None)
    if target is None:
        raise SimilarApplicationError("Няма такова действие.")
    if any(op.get("action") == "UNDO_SIMILAR"
           and op.get("operation_id") == operation_id for op in operations):
        raise SimilarApplicationError("Действието вече е върнато.")
    active = active_similar_operations(project_root)
    if not active or active[-1].get("operation_id") != operation_id:
        raise SimilarApplicationError(
            "Може да се върне само последното активно действие.")
    export_check = exported_after or (
        lambda operation: _verified_export_after(project_root, operation)
    )
    if export_check(target):
        raise SimilarApplicationError(
            "След това действие е направен production export; "
            "изнесеният опис не се пренаписва.")

    root = Path(project_root)
    persistence = root / "working" / "approved_geometry_links.json"
    snapshot_hex = target.get("persistence_snapshot")
    before_state = target.get("before_state") or {}
    restored = list(before_state)
    if snapshot_hex is not None:
        _restore_snapshot(
            persistence,
            bytes.fromhex(snapshot_hex) if snapshot_hex else None)

    path = Path(project_root) / "working" / JOURNAL_NAME
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema": SCHEMA, "action": "UNDO_SIMILAR", "operation_id": operation_id,
            "at": _now(), "undone_by": _text(undone_by), "note": _text(note),
            "restored_handles": restored,
        }, ensure_ascii=False) + "\n")
    append_project_decision_event(
        root,
        "SIMILAR_UNDONE",
        {
            "operation_id": operation_id,
            "invalidates_operation_id": operation_id,
            "annotation_handles": restored,
            "undone_by": undone_by,
            "note": note,
        },
    )
    try:
        from lion_rebar.project.final_control_lifecycle import invalidate_final_control

        invalidate_final_control(
            root, event="APPLY_TO_SIMILAR", actor=undone_by,
            note=f"Върнато Apply to Similar {operation_id}")
    except Exception:  # noqa: BLE001
        pass
    return len(restored)
