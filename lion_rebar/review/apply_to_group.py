"""P1-2 — Apply to Group: прилагане на едно инженерно решение към доказано
еквивалентни членове на групата.

ДВА РАЗЛИЧНИ ПОДПИСА
--------------------
``review_group_key`` (съществуващ, непроменен) служи за ПОКАЗВАНЕ на групи
``×N`` в Review Center. Той е нарочно по-широк, за да събере визуално
сходните случаи пред оператора.

Прилагането на решение изисква ПО-СТРОГ подпис. Договорът (Project
Instructions, т. 13) изброява минимум:

    project identity · source generation · normalized annotation ·
    count semantics · diameter · total length · MASTER candidate ·
    problem/reason code · geometry class · provenance class

Затова тук се изгражда отделен ``apply_signature``. Член на визуалната
група, който не съвпада по строгия подпис, се ИЗКЛЮЧВА от груповото
действие и се показва на оператора с причина. Никога не се прилага
решение „защото текстът е еднакъв“.

ОБРАТИМОСТ
----------
Всяко групово действие получава ``operation_id`` и записва предишното
състояние на всеки засегнат ред, за да може да бъде върнато.
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
from lion_rebar.session.decision_contract import append_project_decision_event

JOURNAL_NAME = "group_operations.jsonl"
SCHEMA = "lion-rebar-group-operation/v1"

#: Статуси, при които позицията вече е решена и НЕ се пипа групово.
RESOLVED_STATUSES = frozenset({
    "ОДОБРЕНА ВРЪЗКА", "NON_PRODUCTION_POSITION", "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)",
})

#: Полета, чиято промяна групово действие може да пренесе.
APPLICABLE_FIELDS: tuple[str, ...] = (
    "master_shape_code",
    "production_segments_cm",
    "production_length_cm",
    "production_diameter_mm",
    "production_count",
    "reason_code",
    "note",
)


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _number(value: Any) -> str:
    """Каноничен числов запис: 100, 100.0 и 100.000 са едно и също."""

    raw = _text(value).replace(",", ".")
    if raw == "":
        return ""
    try:
        from decimal import Decimal

        dec = Decimal(raw)
        if not dec.is_finite():
            return raw
        normalized = dec.normalize()
        return format(normalized, "f")
    except Exception:  # noqa: BLE001
        return raw


def _list(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        parts = [_number(item) for item in value]
    else:
        parts = [_number(part) for part in _text(value).split(",") if _text(part)]
    return "|".join(parts)


def geometry_class(row: Mapping[str, Any]) -> str:
    """Груб клас на геометрията — без handles (те са различни по дефиниция)."""

    handles = [h for h in _text(row.get("candidate_handles")).split(",") if _text(h)]
    if not handles:
        return "NO_GEOMETRY"
    if bool(row.get("global_conflict")) or bool(row.get("geometry_owner_conflict")):
        return "CONFLICTED"
    return f"HANDLES:{len(handles)}"


def provenance_class(row: Mapping[str, Any]) -> str:
    marker = _upper(row.get("geometry_provenance"))
    if marker:
        return marker
    return "DXF_HANDLES" if _text(row.get("candidate_handles")) else "NONE"


def problem_class(row: Mapping[str, Any]) -> str:
    """Проблемният код определя дали решението изобщо е приложимо."""

    codes = set()
    for field_name in ("matcher_blocker_codes", "review_reason_codes"):
        value = row.get(field_name)
        if isinstance(value, (list, tuple)):
            codes.update(_upper(item) for item in value if _text(item))
        else:
            codes.update(_upper(part) for part in _text(value).split(",") if _text(part))
    return ",".join(sorted(codes)) if codes else "NONE"


def segment_shape(row: Mapping[str, Any]) -> str:
    """Каноничната сегментна форма — РЕАЛНИТЕ размери, не само сборът.

    Одит RC1, находка 1: стреме 34x30 и стреме 44x20 имат еднакви Ø, L,
    MASTER код и проблемен код. Без този компонент подписите им съвпадаха и
    решението за едното се пренасяше върху другото — тихо грешен
    производствен размер.
    """

    for field_name in ("production_segments_cm", "canonical_segment_lengths_cm"):
        value = row.get(field_name)
        text = _list(value)
        if text:
            return text
    return ""


def apply_signature(row: Mapping[str, Any], identity: DataIdentity) -> tuple:
    """Строгият подпис за групово прилагане (12 компонента).

    Десетте по договора + сегментна форма + ъгли на огъване. Последните две
    са добавени след одит RC1: без тях две геометрично различни позиции
    получаваха един и същ подпис.
    """

    return (
        identity.project_id,
        identity.source_generation_id,
        _text(row.get("annotation_text")),
        _number(row.get("annotation_count") or row.get("production_count")),
        _number(row.get("diameter_mm") or row.get("production_diameter_mm")),
        _number(row.get("declared_length_cm") or row.get("production_length_cm")),
        _upper(row.get("master_shape_code")),
        problem_class(row),
        geometry_class(row),
        provenance_class(row),
        segment_shape(row),
        _list(row.get("canonical_turn_angles_deg")),
    )


def signature_fields(row: Mapping[str, Any], identity: DataIdentity) -> dict[str, str]:
    """Същият подпис, но с имена — за показване на оператора."""

    values = apply_signature(row, identity)
    names = ("project_id", "source_generation_id", "annotation_text", "count",
             "diameter_mm", "length_cm", "master_shape_code", "problem_class",
             "geometry_class", "provenance_class", "segment_shape", "turn_angles")
    return dict(zip(names, values))


@dataclass(frozen=True)
class GroupMember:
    annotation_handle: str
    annotation_text: str
    eligible: bool
    reason: str = ""
    diameter_mm: str = ""
    length_cm: str = ""
    master_shape_code: str = ""
    problem_class: str = ""
    geometry_handles: str = ""

    def to_dict(self) -> dict:
        return {
            "annotation_handle": self.annotation_handle,
            "annotation_text": self.annotation_text,
            "eligible": self.eligible,
            "reason": self.reason,
            "diameter_mm": self.diameter_mm,
            "length_cm": self.length_cm,
            "master_shape_code": self.master_shape_code,
            "problem_class": self.problem_class,
            "geometry_handles": self.geometry_handles,
        }


@dataclass
class GroupApplicationPlan:
    """Предварителен преглед: какво точно ще се промени, преди да се приложи."""

    representative_handle: str
    group_key: str
    signature: dict[str, str]
    members: list[GroupMember] = field(default_factory=list)
    changes: dict[str, Any] = field(default_factory=dict)
    refusal: str = ""

    @property
    def eligible(self) -> list[GroupMember]:
        return [m for m in self.members if m.eligible]

    @property
    def excluded(self) -> list[GroupMember]:
        return [m for m in self.members if not m.eligible]

    @property
    def affected_count(self) -> int:
        return len(self.eligible)

    @property
    def can_apply(self) -> bool:
        return (not self.refusal) and self.affected_count > 0 and bool(self.changes)

    def to_dict(self) -> dict:
        return {
            "representative_handle": self.representative_handle,
            "group_key": self.group_key,
            "refusal": self.refusal,
            "signature": dict(self.signature),
            "changes": dict(self.changes),
            "affected_count": self.affected_count,
            "excluded_count": len(self.excluded),
            "members": [m.to_dict() for m in self.members],
        }


def build_group_plan(
    representative: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    identity: DataIdentity,
    *,
    changes: Mapping[str, Any] | None = None,
) -> GroupApplicationPlan:
    """Изгражда преглед за групово прилагане.

    ``members`` са редовете от визуалната група (включително representative).
    Всеки член се проверява срещу строгия подпис И срещу identity-то.
    """

    rep_handle = _text(representative.get("annotation_handle"))
    # Одит RC1, находка 2: representative-ът също се проверява. Групово
    # действие, водено от позиция на друг проект/поколение, е недопустимо.
    rep_problem = mismatch_reason(representative, identity)
    rep_signature = apply_signature(representative, identity)
    applicable = {
        key: value for key, value in (changes or {}).items()
        if key in APPLICABLE_FIELDS
    }
    plan = GroupApplicationPlan(
        representative_handle=rep_handle,
        group_key=_text(representative.get("review_group_key")),
        signature=signature_fields(representative, identity),
        changes=applicable,
        refusal=f"REPRESENTATIVE_IDENTITY:{rep_problem}" if rep_problem else "",
    )
    if rep_problem:
        # Никой член не се предлага: цялото действие е отказано.
        return plan
    for row in members:
        handle = _text(row.get("annotation_handle"))
        common = {
            "annotation_handle": handle,
            "annotation_text": _text(row.get("annotation_text")),
            "diameter_mm": _number(row.get("diameter_mm")),
            "length_cm": _number(row.get("declared_length_cm")),
            "master_shape_code": _upper(row.get("master_shape_code")),
            "problem_class": problem_class(row),
            "geometry_handles": _text(row.get("candidate_handles")),
        }
        if handle == rep_handle:
            plan.members.append(GroupMember(eligible=False, reason="REPRESENTATIVE",
                                            **common))
            continue
        identity_problem = mismatch_reason(row, identity)
        if identity_problem:
            plan.members.append(GroupMember(eligible=False, reason=identity_problem,
                                            **common))
            continue
        status = _text(row.get("batch_status")).upper()
        if status in {value.upper() for value in RESOLVED_STATUSES}:
            plan.members.append(GroupMember(eligible=False, reason="ALREADY_RESOLVED",
                                            **common))
            continue
        if bool(row.get("global_conflict")) or bool(row.get("geometry_owner_conflict")):
            plan.members.append(GroupMember(eligible=False, reason="OWNERSHIP_CONFLICT",
                                            **common))
            continue
        if apply_signature(row, identity) != rep_signature:
            differing = [
                name for name, (a, b) in zip(
                    signature_fields(representative, identity),
                    zip(rep_signature, apply_signature(row, identity)))
                if a != b
            ]
            plan.members.append(GroupMember(
                eligible=False,
                reason="SIGNATURE_MISMATCH:" + ",".join(differing) if differing
                else "SIGNATURE_MISMATCH",
                **common))
            continue
        plan.members.append(GroupMember(eligible=True, **common))
    return plan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_group_operation(
    project_root: Path | str,
    plan: GroupApplicationPlan,
    *,
    applied_by: str,
    note: str,
    identity: DataIdentity,
    before_state: Mapping[str, Mapping[str, Any]] | None = None,
    persistence_snapshot: str = "",
) -> str:
    """Записва груповото действие с обратимо предишно състояние.

    Връща ``operation_id``. Предишното състояние на всеки засегнат ред се
    пази, за да може действието да бъде върнато.
    """

    if not plan.can_apply:
        raise ValueError("Няма приложими членове или няма зададени промени.")
    if not _text(applied_by):
        raise ValueError("Липсва автор на груповото действие.")
    if not _text(note):
        raise ValueError("Груповото действие изисква инженерна бележка.")

    operation_id = uuid.uuid4().hex
    entry = {
        "schema": SCHEMA,
        "operation_id": operation_id,
        "action": "APPLY_TO_GROUP",
        "at": _now(),
        "at_unix_ns": time.time_ns(),
        "applied_by": _text(applied_by),
        "note": _text(note),
        "representative_handle": plan.representative_handle,
        "group_key": plan.group_key,
        "signature": dict(plan.signature),
        "changes": dict(plan.changes),
        "affected_handles": [m.annotation_handle for m in plan.eligible],
        # Байтов snapshot на persistence файла ПРЕДИ прилагането. Прави
        # връщането точно, вместо да го възстановява с нови записи.
        "persistence_snapshot": persistence_snapshot,
        "excluded": [
            {"annotation_handle": m.annotation_handle, "reason": m.reason}
            for m in plan.excluded
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


def record_group_undo(
    project_root: Path | str, *, operation_id: str, undone_by: str, note: str = ""
) -> bool:
    """Записва връщане на групово действие. Не пренаписва историята."""

    operations = load_group_operations(project_root)
    target = next(
        (op for op in operations
         if op.get("operation_id") == operation_id and op.get("action") == "APPLY_TO_GROUP"),
        None)
    if target is None:
        return False
    if any(op.get("action") == "UNDO_GROUP" and op.get("operation_id") == operation_id
           for op in operations):
        return False
    path = Path(project_root) / "working" / JOURNAL_NAME
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema": SCHEMA, "action": "UNDO_GROUP", "operation_id": operation_id,
            "at": _now(), "undone_by": _text(undone_by), "note": _text(note),
            "restored_handles": list(target.get("before_state", {})),
        }, ensure_ascii=False) + "\n")
    return True


def load_group_operations(project_root: Path | str) -> list[dict]:
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


def active_group_operations(project_root: Path | str) -> list[dict]:
    """Приложени и НЕвърнати групови действия."""

    operations = load_group_operations(project_root)
    undone = {
        op.get("operation_id") for op in operations if op.get("action") == "UNDO_GROUP"
    }
    return [
        op for op in operations
        if op.get("action") == "APPLY_TO_GROUP" and op.get("operation_id") not in undone
    ]


def _verified_export_after(project_root: Path | str, operation: Mapping[str, Any]) -> bool:
    """Има ли записан PRODUCTION VERIFIED export след груповото действие."""

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


# ---------------------------------------------------------------- прилагане


class GroupApplicationError(RuntimeError):
    """Груповото действие не е приложено. Нищо не е променено."""


def _decision_payload(row: Mapping[str, Any], changes: Mapping[str, Any],
                      note: str, representative_handle: str) -> dict:
    """Сглобява payload за съществуващия път ``save_engineer_decision``.

    Груповото действие НЕ пише директно в Ledger-а. То минава през същия
    контракт като единичното решение, за да не съществуват два пътя до
    production истината.
    """

    resolved = {
        "shape_code": changes.get("master_shape_code")
                      or _text(row.get("master_shape_code")),
        "count": changes.get("production_count")
                 or row.get("annotation_count") or row.get("production_count"),
        "diameter_mm": changes.get("production_diameter_mm")
                       or row.get("diameter_mm") or row.get("production_diameter_mm"),
        # ИМЕНАТА трябва да съвпадат с контракта на save_engineer_decision.
        # Независим одит на rc21 (P0-3): подавахме total_length_cm и
        # segments_cm, а пътят очаква production_length_cm и
        # segment_lengths_cm -- прилагането се проваляше с „Обща дължина
        # трябва да е положително число“, а mock тестовете не го хващаха,
        # защото проверяваха само че е направено извикване.
        "production_length_cm": changes.get("production_length_cm")
                                or row.get("declared_length_cm")
                                or row.get("production_length_cm"),
    }
    segments = changes.get("production_segments_cm")
    if segments is None:
        segments = row.get("production_segments_cm")
    if segments:
        resolved["segment_lengths_cm"] = list(segments)
    handles = [
        _text(value)
        for value in (row.get("candidate_handles") or [])
        if _text(value)
    ]
    return {
        "annotation": {
            "handle": _text(row.get("annotation_handle")),
            "text": _text(row.get("annotation_text")),
        },
        "resolved": resolved,
        "geometry": {"handles": handles},
        "reason_code": changes.get("reason_code") or "APPLY_TO_GROUP",
        "note": note,
        "source": "APPLY_TO_GROUP",
        "representative_handle": representative_handle,
    }


def apply_group_plan(
    project_root: Path | str,
    plan: GroupApplicationPlan,
    rows_by_handle: Mapping[str, Mapping[str, Any]],
    identity: DataIdentity,
    *,
    applied_by: str,
    note: str,
    save_decision=None,
) -> str:
    """Прилага груповото решение АТОМАРНО и връща ``operation_id``.

    При провал на която и да е позиция всички вече приложени се връщат от
    ``before_state`` и се вдига :class:`GroupApplicationError`. Частично
    приложена група е недопустима: тя оставя описа в състояние, което
    никой не е одобрявал.

    Прилагането минава през ``save_engineer_decision`` -- същия контракт
    като единичното решение. Групата ускорява ВЪВЕЖДАНЕТО, тя не е втори
    път до production истината.
    """

    if not plan.can_apply:
        raise GroupApplicationError(
            plan.refusal or "Няма приложими членове или няма зададени промени.")
    if not _text(applied_by):
        raise GroupApplicationError("Липсва автор на груповото действие.")
    if not _text(note):
        raise GroupApplicationError("Груповото действие изисква инженерна бележка.")

    if save_decision is None:
        from lion_rebar.engineer_decision import save_engineer_decision

        save_decision = save_engineer_decision

    root = Path(project_root)
    # ИСТИНСКИ ATOMIC: snapshot на самия persistence файл, не логическо
    # връщане чрез повторни записи. По-ранната версия „връщаше“ като
    # правеше нови решения, което оставяше следа и можеше да се провали
    # частично.
    persistence = root / "working" / "approved_geometry_links.json"
    snapshot: bytes | None = None
    if persistence.is_file():
        snapshot = persistence.read_bytes()

    before_state: dict[str, dict] = {}
    applied: list[str] = []
    try:
        for member in plan.eligible:
            row = rows_by_handle.get(member.annotation_handle)
            if row is None:
                raise GroupApplicationError(
                    f"Позиция {member.annotation_handle} липсва в отчета.")
            before_state[member.annotation_handle] = dict(row)
            save_decision(root, _decision_payload(
                row, plan.changes, note, plan.representative_handle))
            applied.append(member.annotation_handle)
    except Exception as error:  # noqa: BLE001
        _restore_snapshot(persistence, snapshot)
        record_group_operation_failure(
            root, plan, applied_by=applied_by, note=note,
            identity=identity, error=str(error))
        raise GroupApplicationError(
            f"Груповото действие е отменено изцяло: {error}") from error

    operation_id = record_group_operation(
        root, plan, applied_by=applied_by, note=note,
        identity=identity, before_state=before_state,
        persistence_snapshot=snapshot.hex() if snapshot is not None else "")
    consequences = []
    for handle in applied:
        row = rows_by_handle[handle]
        decision = _decision_payload(row, plan.changes, note, plan.representative_handle)
        consequences.append(
            {
                "annotation_handle": handle,
                "source_annotation_text": row.get("annotation_text"),
                "annotation_count": plan.changes.get("production_count") or row.get("annotation_count"),
                "diameter_mm": plan.changes.get("production_diameter_mm") or row.get("diameter_mm"),
                "production_length_cm": plan.changes.get("production_length_cm") or row.get("declared_length_cm"),
                "master_shape_code": plan.changes.get("master_shape_code") or row.get("master_shape_code"),
                "production_segment_lengths_cm": list(
                    plan.changes.get("production_segments_cm")
                    or row.get("production_segments_cm")
                    or []
                ),
                "candidate_handles": list(row.get("candidate_handles") or []),
                "action_class": "CONFIRMATION",
                "reason_code": "APPLY_TO_GROUP",
                "decision": decision,
            }
        )
    append_project_decision_event(
        root,
        "GROUP_APPLIED",
        {
            "operation_id": operation_id,
            "mode": "APPLY_TO_GROUP",
            "annotation_handles": applied,
            "representative_handle": plan.representative_handle,
            "applied_by": applied_by,
            "note": note,
            "data_identity": identity.to_dict(),
            "consequences": consequences,
        },
    )

    # Груповото решение е production-impacting: старият Final Control
    # престава да важи.
    try:
        from lion_rebar.project.final_control_lifecycle import invalidate_final_control

        invalidate_final_control(
            root, event="APPLY_TO_GROUP", actor=applied_by,
            note=f"Групово решение {operation_id} върху {len(applied)} позиции")
    except Exception:  # noqa: BLE001 - записът вече е направен
        pass
    return operation_id


def _restore_snapshot(persistence: Path, snapshot: bytes | None) -> None:
    """Възстановява persistence файла байт по байт.

    Това е разликата между „логическо“ и ИСТИНСКО връщане: състоянието
    става точно каквото е било, без нови записи и без частичен резултат.
    """

    persistence.parent.mkdir(parents=True, exist_ok=True)
    if snapshot is None:
        # Файлът не е съществувал преди операцията.
        persistence.unlink(missing_ok=True)
        return
    temporary = persistence.with_suffix(".rollback")
    temporary.write_bytes(snapshot)
    temporary.replace(persistence)


def record_group_operation_failure(
    project_root: Path | str, plan: GroupApplicationPlan, *,
    applied_by: str, note: str, identity: DataIdentity, error: str,
) -> None:
    """Провалът също е одитно събитие -- иначе изчезва без следа."""

    path = Path(project_root) / "working" / JOURNAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema": SCHEMA, "action": "APPLY_TO_GROUP_FAILED",
            "at": _now(), "applied_by": _text(applied_by), "note": _text(note),
            "representative_handle": plan.representative_handle,
            "error": error, "project_id": identity.project_id,
        }, ensure_ascii=False) + "\n")


def undo_group_operation(
    project_root: Path | str, operation_id: str, *,
    undone_by: str, note: str = "", save_decision=None,
    exported_after=None,
) -> int:
    """РЕАЛНО връща групово действие. Връща броя възстановени позиции.

    Отказва, когато:
      * действието не съществува или вече е върнато;
      * след него е направен production export (изнесеният опис не се
        пренаписва мълчаливо).
    """

    operations = load_group_operations(project_root)
    target = next(
        (op for op in operations
         if op.get("operation_id") == operation_id
         and op.get("action") == "APPLY_TO_GROUP"), None)
    if target is None:
        raise GroupApplicationError("Няма такова групово действие.")
    if any(op.get("action") == "UNDO_GROUP"
           and op.get("operation_id") == operation_id for op in operations):
        raise GroupApplicationError("Действието вече е върнато.")
    active = active_group_operations(project_root)
    if not active or active[-1].get("operation_id") != operation_id:
        raise GroupApplicationError(
            "Може да се върне само последното активно групово действие."
        )
    export_check = exported_after or (
        lambda operation: _verified_export_after(project_root, operation)
    )
    if export_check(target):
        raise GroupApplicationError(
            "След това действие е направен production export; "
            "изнесеният опис не се пренаписва.")

    if save_decision is None:
        from lion_rebar.engineer_decision import save_engineer_decision

        save_decision = save_engineer_decision

    root = Path(project_root)
    persistence = root / "working" / "approved_geometry_links.json"
    snapshot_hex = target.get("persistence_snapshot")
    before_state = target.get("before_state") or {}

    if snapshot_hex is not None:
        # Байтов snapshot от момента ПРЕДИ прилагането -- най-точното
        # възможно връщане.
        _restore_snapshot(
            persistence,
            bytes.fromhex(snapshot_hex) if snapshot_hex else None)
        restored = list(before_state)
    else:
        restored = []
        for handle, previous in before_state.items():
            save_decision(root, _decision_payload(
                previous, {},
                f"ВРЪЩАНЕ на групово действие {operation_id}: {note}", ""))
            restored.append(handle)

    record_group_undo(root, operation_id=operation_id,
                      undone_by=undone_by, note=note)
    append_project_decision_event(
        root,
        "GROUP_UNDONE",
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
            root, event="APPLY_TO_GROUP", actor=undone_by,
            note=f"Върнато групово решение {operation_id}")
    except Exception:  # noqa: BLE001
        pass
    return len(restored)
