from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from lion_rebar.config.constants import APP_VERSION
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.export.engine_shape_parameters import LEGACY_MASTER_CODES, parameters_for_shape
from lion_rebar.export.errors import ExcelExportError
from lion_rebar.engine.bend_allowance import (
    explain_mismatch,
    segments_match_total,
)
from lion_rebar.project.data_identity import (
    identity_from_project_payload,
    stamp_payload,
)
from lion_rebar.project.approval_origin import approval_origin
from lion_rebar.review_center_model import is_position_key_legend_reference

ACTION_CONFIRMATION = "CONFIRMATION"
ACTION_CORRECTION = "CORRECTION"
ACTION_OVERRIDE = "OVERRIDE"
ACTION_EXCLUSION = "EXCLUSION"
ACTION_PROBLEM = "PROBLEM"

REASON_OPTIONS: tuple[tuple[str, str], ...] = (
    ("CONFIRM_CORRECT", "Предложението е правилно — потвърждение"),
    ("WRONG_SHAPE", "Грешна MASTER форма"),
    ("WRONG_GEOMETRY", "Грешно свързана геометрия / ownership"),
    ("WRONG_OBJECT", "Маркирана е чужда позиция / грешен обект"),
    ("PARTIAL_GEOMETRY", "Маркирана е само част от желязото"),
    ("DUPLICATE_GEOMETRY", "Геометрията е присвоена на повече от една позиция"),
    ("WRONG_COUNT", "Грешен брой"),
    ("WRONG_DIAMETER", "Грешен диаметър"),
    ("WRONG_LENGTH", "Грешна обща дължина"),
    ("WRONG_SEGMENTS", "Грешни производствени сегменти"),
    ("NO_GEOMETRY", "Липсва или не е необходима отделна геометрия"),
    ("NON_PRODUCTION_POSITION", "Не е реална производствена позиция — изключи от описа"),
    ("MISSING_MASTER", "Липсва MASTER форма"),
    ("DESIGNER_STYLE", "Проектантски стил / нетипично изчертаване"),
    ("PROJECTANT_ERROR", "Проектантска грешка"),
    ("DXF_DIMENSION_CONFLICT", "DXF и надпис/размери си противоречат"),
    ("OTHER", "Друга причина"),
)
REASON_LABELS = dict(REASON_OPTIONS)
PROBLEM_REASON_ALIASES = {
    "WRONG_MASTER": "WRONG_SHAPE",
    "NO_VALID_CANDIDATE": "WRONG_GEOMETRY",
}


@dataclass(frozen=True)
class EngineerNotePolicy:
    """One policy used by both the editor and the persistence gate."""

    required: bool
    code: str
    explanation: str
    acknowledgement_required: bool = False


def engineer_note_policy(
    *,
    action_class: str | None,
    reason_code: str | None,
    geometry_handles: Iterable[str] = (),
    structured_evidence: Mapping[str, Any] | None = None,
) -> EngineerNotePolicy:
    """Return the free-text note requirement for a structured decision.

    Ordinary, fully validated corrections are self-explanatory through their
    reason code and exact before/after values.  Safety exceptions stay strict
    and are checked again when the decision is written, so the UI cannot
    bypass the rule.
    """

    action = str(action_class or "").strip().upper()
    reason = str(reason_code or "OTHER").strip().upper()
    handles = tuple(str(value) for value in geometry_handles if str(value))
    evidence = dict(structured_evidence or {})
    if reason == "NON_PRODUCTION_POSITION" or action == ACTION_EXCLUSION:
        return EngineerNotePolicy(
            True,
            "NON_PRODUCTION_EVIDENCE",
            "Задължителна бележка: посочи защо надписът не е реална производствена позиция.",
            True,
        )
    if not handles:
        return EngineerNotePolicy(
            True,
            "NO_RELIABLE_GEOMETRY",
            "Задължителна бележка: решението е без надеждна DXF геометрия.",
            True,
        )
    if reason == "OTHER":
        return EngineerNotePolicy(
            True,
            "OTHER_REQUIRES_EXPLANATION",
            "Задължителна бележка: избраната причина „Друга“ не обяснява решението.",
        )
    if reason in {"PROJECTANT_ERROR", "DXF_DIMENSION_CONFLICT"} and not _has_structured_explanation(evidence):
        return EngineerNotePolicy(
            True,
            "SOURCE_CONFLICT_REQUIRES_EXPLANATION",
            "Задължителна бележка: обясни защо противоречивият източник е отхвърлен.",
        )
    return EngineerNotePolicy(
        False,
        "STRUCTURED_RECORD_SUFFICIENT",
        "Бележката е по желание; причината и проверените стойности се пазят автоматично.",
    )


def automatic_decision_audit(
    *,
    annotation_handle: str,
    annotation_text: str,
    action_class: str,
    reason_code: str,
    original: Mapping[str, Any],
    resolved: Mapping[str, Any],
    geometry: Mapping[str, Any],
    at: str,
    context: Mapping[str, Any] | None = None,
    operator_note: str = "",
) -> dict[str, Any]:
    """Build a system-authored audit record, distinct from operator prose."""

    before = {
        "shape_code": original.get("shape_code"),
        "count": original.get("count"),
        "diameter_mm": original.get("diameter_mm"),
        "production_length_cm": original.get("declared_length_cm"),
        "segment_lengths_cm": list(original.get("segment_lengths_cm") or ()),
        "geometry_handles": sorted(str(value) for value in original.get("geometry_handles") or () if str(value)),
    }
    after = {
        "shape_code": resolved.get("shape_code"),
        "count": resolved.get("count"),
        "diameter_mm": resolved.get("diameter_mm"),
        "production_length_cm": resolved.get("production_length_cm"),
        "segment_lengths_cm": list(resolved.get("segment_lengths_cm") or ()),
        "geometry_handles": sorted(str(value) for value in geometry.get("handles") or () if str(value)),
    }
    changes = {
        key: {"before": before[key], "after": after[key]}
        for key in before
        if not _audit_values_equal(before[key], after[key])
    }
    audit_context = {"application_version": APP_VERSION}
    audit_context.update({str(key): value for key, value in dict(context or {}).items() if value not in (None, "")})
    return {
        "schema": "lion-rebar-automatic-decision-audit/v1",
        "generated_by": "LION_REBAR_SYSTEM",
        "generated_at": at,
        "operator_note_supplied": bool(str(operator_note or "").strip()),
        "action_class": str(action_class or "").upper(),
        "reason_code": str(reason_code or "OTHER").upper(),
        "before": before,
        "after": after,
        "changes": changes,
        "source": {
            "annotation_handle": str(annotation_handle or ""),
            "annotation_text": str(annotation_text or ""),
            "candidate_basis": str(geometry.get("basis") or ""),
            "candidate_notes": list(geometry.get("notes") or ()),
        },
        "context": audit_context,
    }


def _has_structured_explanation(evidence: Mapping[str, Any]) -> bool:
    if evidence.get("diagnostic_problem"):
        return True
    if evidence.get("candidate_notes"):
        return True
    if evidence.get("review_reason_codes"):
        return True
    basis = str(evidence.get("candidate_basis") or "").strip().upper()
    return bool(basis and basis not in {"OWNER_ENGINEER_DECISION", "UNKNOWN"})


def _audit_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-6
    return left == right


def reason_label(code: str | None) -> str:
    normalized = str(code or "OTHER").strip().upper()
    return REASON_LABELS.get(normalized, REASON_LABELS["OTHER"])


def engineer_reason_from_problem(code: str | None) -> str | None:
    """Map a diagnostic Review Center reason to the resolution editor."""

    normalized = str(code or "").strip().upper()
    if not normalized:
        return None
    candidate = PROBLEM_REASON_ALIASES.get(normalized, normalized)
    return candidate if candidate in REASON_LABELS else "OTHER"


def positive_int(value: Any, *, field: str | None = None) -> int | None:
    try:
        raw = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        if field is not None:
            raise ValueError(f"{field} трябва да е положително цяло число.") from exc
        return None
    rounded = round(raw)
    if raw <= 0 or abs(raw - rounded) > 1e-9:
        if field is not None:
            raise ValueError(f"{field} трябва да е положително цяло число.")
        return None
    return int(rounded)


def positive_float(value: Any, *, field: str | None = None) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        if field is not None:
            raise ValueError(f"{field} трябва да е положително число.") from exc
        return None
    if not math.isfinite(number) or number <= 0:
        if field is not None:
            raise ValueError(f"{field} трябва да е положително крайно число.")
        return None
    return number


#: Таван на ЗАЯВЕНАТА добавка за огъване. Не е инженерно правило, а защита от
#: изпусната клавиша: самото число се приема само когато ТОЧНО затваря
#: уравнението (``engine.bend_allowance.declared_allowance_closes``), затова
#: таванът не решава нищо — той само спира безсмислено голяма стойност, преди
#: да стигне до проверката. Най-големият измерен недостиг в корпуса е 8 cm
#: (F006), а тук е оставен троен запас.
DECLARED_BEND_ALLOWANCE_MAX_CM = 25.0


def declared_bend_allowance(resolved: Mapping[str, Any]) -> float | None:
    """Числото, което инженерът е заявил за добавката по ТОЗИ детайл.

    ``None`` значи „не е заявено“ — тогава важат само измерените правила.
    Празно поле е същото като незаявено; отрицателно, нула или извън тавана е
    ГРЕШКА, а не мълчаливо пренебрегване: операторът е написал нещо и трябва да
    узнае, че не е прието.
    """

    сурово = resolved.get("bend_allowance_cm")
    if сурово is None or str(сурово).strip() == "":
        return None
    число = positive_float(сурово, field="Заявена добавка за огъване")
    assert число is not None
    if число > DECLARED_BEND_ALLOWANCE_MAX_CM:
        raise ValueError(
            f"Заявената добавка за огъване ({число:g} cm) надвишава защитния "
            f"максимум {DECLARED_BEND_ALLOWANCE_MAX_CM:g} cm."
        )
    return число


def parse_segment_text(value: str | Iterable[Any]) -> tuple[float, ...]:
    if isinstance(value, str):
        normalized = (
            value.replace("×", "+")
            .replace("x", "+")
            .replace("X", "+")
            .replace(";", "+")
            .replace("/", "+")
        )
        # A sequence such as ``40,14,40`` is common shorthand for three
        # segments, while ``4,6 + 10`` uses a decimal comma.  Only interpret
        # commas as separators when no other separator exists and there are at
        # least two of them.
        if "+" not in normalized and normalized.count(",") >= 2:
            normalized = normalized.replace(",", "+")
        raw_values: list[Any] = []
        for part in normalized.split("+"):
            raw_values.extend(token for token in part.strip().split() if token)
    else:
        raw_values = list(value)
    result: list[float] = []
    for raw in raw_values:
        parsed = positive_float(raw, field="Всеки сегмент")
        assert parsed is not None
        result.append(parsed)
    return tuple(result)


def production_annotation_text(
    *,
    count: int,
    diameter_mm: float,
    production_length_cm: float | None = None,
    length_cm: float | None = None,
) -> str:
    count_value = positive_int(count, field="Брой")
    diameter_value = positive_float(diameter_mm, field="Диаметър")
    length_value = positive_float(
        production_length_cm if production_length_cm is not None else length_cm,
        field="Обща дължина",
    )
    assert count_value is not None and diameter_value is not None and length_value is not None
    return f"{count_value}N{_number(diameter_value)}x{_number(length_value)}"


def classify_engineer_action(
    row: Mapping[str, Any] | None = None,
    *,
    reason_code: str | None = None,
    shape_code: str | None = None,
    geometry_handles: Iterable[str] = (),
    count: int | None = None,
    diameter_mm: float | None = None,
    production_length_cm: float | None = None,
    production_segments_cm: Iterable[float] = (),
    suggested_shape_code: str | None = None,
    resolved_shape_code: str | None = None,
    original_count: Any = None,
    resolved_count: Any = None,
    original_diameter_mm: Any = None,
    resolved_diameter_mm: Any = None,
    original_length_cm: Any = None,
    resolved_length_cm: Any = None,
    suggested_segments: Iterable[Any] = (),
    resolved_segments: Iterable[Any] = (),
    suggested_geometry_handles: Iterable[str] = (),
    resolved_geometry_handles: Iterable[str] = (),
) -> str:
    """Classify a user action without turning confirmations into rule candidates.

    Supports both the direct row-based API and the explicit before/after API
    used by the manual geometry assignment dialog.
    """

    reason = str(reason_code or "").upper()
    if row is not None:
        suggested_shape_code = str(row.get("master_shape_code") or "")
        resolved_shape_code = str(shape_code or "")
        original_count = row.get("annotation_count")
        resolved_count = count
        original_diameter_mm = row.get("diameter_mm")
        resolved_diameter_mm = diameter_mm
        original_length_cm = row.get("declared_length_cm")
        resolved_length_cm = production_length_cm
        # Предложението на ПРОГРАМАТА също е „предложено“. Ако операторът
        # го приеме както е, това е ПОТВЪРЖДЕНИЕ, не корекция — иначе
        # всяко приемане иска бележка, макар нищо да не е променено.
        suggested_segments = (
            row.get("canonical_segment_lengths_cm")
            or row.get("production_segments_cm")
            or row.get("suggested_segments_cm")
            or ()
        )
        resolved_segments = production_segments_cm
        suggested_geometry_handles = row.get("candidate_handles") or ()
        resolved_geometry_handles = geometry_handles

    if reason == "NON_PRODUCTION_POSITION":
        return ACTION_EXCLUSION
    if reason in {
        "WRONG_GEOMETRY",
        "WRONG_OBJECT",
        "PARTIAL_GEOMETRY",
        "DUPLICATE_GEOMETRY",
        "NO_GEOMETRY",
        "MISSING_MASTER",
    }:
        return ACTION_OVERRIDE
    if reason in {
        "WRONG_SHAPE",
        "WRONG_COUNT",
        "WRONG_DIAMETER",
        "WRONG_LENGTH",
        "WRONG_SEGMENTS",
        "PROJECTANT_ERROR",
        "DXF_DIMENSION_CONFLICT",
    }:
        return ACTION_CORRECTION

    suggested_shape = str(suggested_shape_code or "").upper()
    resolved_shape = str(resolved_shape_code or "").upper()
    if not suggested_shape:
        return ACTION_OVERRIDE
    if not resolved_shape or resolved_shape != suggested_shape:
        return ACTION_CORRECTION

    suggested_handles = tuple(sorted(str(v) for v in suggested_geometry_handles if str(v)))
    resolved_handles = tuple(sorted(str(v) for v in resolved_geometry_handles if str(v)))
    if suggested_handles != resolved_handles:
        return ACTION_OVERRIDE

    before_count = _optional_int(original_count)
    after_count = _optional_int(resolved_count)
    before_diameter = _optional_float(original_diameter_mm)
    after_diameter = _optional_float(resolved_diameter_mm)
    before_length = _optional_float(original_length_cm)
    after_length = _optional_float(resolved_length_cm)
    if before_count is None or after_count is None or before_count != after_count:
        return ACTION_CORRECTION
    if before_diameter is None or after_diameter is None or abs(before_diameter - after_diameter) > 1e-6:
        return ACTION_CORRECTION
    if before_length is None or after_length is None or abs(before_length - after_length) > 1e-6:
        return ACTION_CORRECTION

    before_segments = tuple(float(v) for v in suggested_segments if _optional_float(v) is not None)
    after_segments = tuple(float(v) for v in resolved_segments if _optional_float(v) is not None)
    if before_segments and after_segments and not _same_numbers(before_segments, after_segments):
        return ACTION_CORRECTION
    if not before_segments and after_segments and resolved_shape != "F001":
        # Подаването на липсвали размери е нова производствена истина —
        # ОСВЕН когато програмата сама ги е предложила и операторът ги
        # приема без промяна. Тогава няма какво да се обяснява.
        return ACTION_CORRECTION
    return ACTION_CONFIRMATION


def derive_review_reason_codes(row: Mapping[str, Any]) -> tuple[str, ...]:
    codes: list[str] = []
    for value in row.get("review_reason_codes", []) or []:
        _append_unique(codes, str(value).upper())
    for value in row.get("matcher_blocker_codes", []) or []:
        _append_unique(codes, str(value).upper())
    if row.get("geometry_owner_conflict") or row.get("global_conflict"):
        _append_unique(codes, "OWNERSHIP_CONFLICT")
    if row.get("master_conflict") or row.get("master_ambiguous_shape_codes"):
        _append_unique(codes, "MASTER_AMBIGUITY")
    if not row.get("candidate_handles"):
        _append_unique(codes, "NO_GEOMETRY")
    if not row.get("master_shape_code"):
        _append_unique(codes, "NO_MASTER")
    difference = _optional_float(
        row.get("raw_numeric_difference")
        if row.get("raw_numeric_difference") is not None
        else row.get("numeric_difference")
    )
    declared = _optional_float(row.get("declared_length_cm"))
    if difference is not None and declared is not None and difference > max(5.0, declared * 0.007):
        _append_unique(codes, "LENGTH_MISMATCH")
    disposition = str(row.get("matcher_disposition") or "").upper()
    if disposition in {"MANUAL", "REVIEW"} and not codes:
        _append_unique(codes, "MATCHER_REVIEW")
    return tuple(codes)


def _same_numbers(left: tuple[float, ...], right: tuple[float, ...], tolerance: float = 1e-6) -> bool:
    return len(left) == len(right) and all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _optional_int(value: Any) -> int | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    rounded = round(raw)
    if raw <= 0 or abs(raw - rounded) > 1e-9:
        return None
    return int(rounded)


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _number(value: float) -> str:
    rounded = round(float(value), 6)
    if abs(rounded - round(rounded)) < 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


class GeometryTakeoverRequired(ValueError):
    """Геометрията принадлежи на друга ОДОБРЕНА позиция.

    Не е отказ — изисква изрично потвърждение, защото позицията, която
    губи геометрията си, е била одобрена и ще се върне за преглед.
    """

    def __init__(self, message: str, *, losing_annotation: str = ""):
        super().__init__(message)
        self.losing_annotation = losing_annotation


def save_engineer_decision(project_root, payload: Mapping[str, Any],
                           *, allow_geometry_takeover: bool = False):
    """Persist one terminal owner decision.

    Production decisions become approved links. ``NON_PRODUCTION_POSITION`` is
    stored as a terminal, evidenced exclusion and never enters Excel/DXF.
    """

    import json
    from datetime import datetime
    from pathlib import Path

    root = Path(project_root)
    working = root / "working"
    working.mkdir(parents=True, exist_ok=True)
    annotation = payload.get("annotation") if isinstance(payload.get("annotation"), Mapping) else {}
    resolved = payload.get("resolved") if isinstance(payload.get("resolved"), Mapping) else {}
    geometry = payload.get("geometry") if isinstance(payload.get("geometry"), Mapping) else {}
    handle = str(annotation.get("handle") or "")
    if not handle:
        raise ValueError("Инженерното решение няма annotation handle.")
    reason_code = str(payload.get("reason_code") or "OTHER").strip().upper()
    note = str(payload.get("note") or "").strip()
    if reason_code == "NON_PRODUCTION_POSITION":
        return _save_non_production_position(
            root,
            handle=handle,
            annotation=annotation,
            payload=payload,
            note=note,
        )

    shape_code = str(resolved.get("shape_code") or "").strip().upper()
    shape_name = str(resolved.get("shape_name") or shape_code)
    if not shape_code:
        raise ValueError("Инженерното решение няма MASTER форма.")
    count = positive_int(resolved.get("count"), field="Брой")
    diameter = positive_float(resolved.get("diameter_mm"), field="Диаметър")
    length = positive_float(resolved.get("production_length_cm"), field="Обща дължина")
    assert count is not None and diameter is not None and length is not None
    if count > 1_000_000:
        raise ValueError("Броят надвишава защитния максимум 1 000 000.")
    if diameter > 100:
        raise ValueError("Диаметърът надвишава защитния максимум 100 mm.")
    if length > 20_000:
        raise ValueError("Дължината надвишава защитния максимум 20 000 cm.")
    segments = parse_segment_text(resolved.get("segment_lengths_cm") or ())
    if shape_code == "F001" and not segments:
        segments = (length,)
    if shape_code != "F001" and not segments:
        raise ValueError("Огъната форма е без производствени сегменти.")
    # Единен източник на истина за добавката за огъване.
    #
    # Заявената добавка се пита ЕДВА след като обикновените правила са отказали.
    # Така числото на инженера не отслабва нито едно измерено правило и се
    # записва само когато наистина е свършило работа — иначе в решението би
    # стояло число, което нищо не е променило.
    заявена_добавка = declared_bend_allowance(resolved)
    добавката_е_нужна = not segments_match_total(shape_code, sum(segments), length)
    if добавката_е_нужна and not segments_match_total(
        shape_code, sum(segments), length, declared_allowance_cm=заявена_добавка
    ):
        raise ValueError(
            explain_mismatch(
                shape_code,
                sum(segments),
                length,
                declared_allowance_cm=заявена_добавка,
            )
        )
    записана_добавка = заявена_добавка if добавката_е_нужна else None

    shape_raw = payload.get("shape_definition")
    if isinstance(shape_raw, Mapping):
        shape = ShapeDefinition.from_dict(dict(shape_raw))
    elif shape_code in LEGACY_MASTER_CODES:
        # Locked legacy forms remain backward compatible with older callers.
        shape = ShapeDefinition(
            code=shape_code,
            name=shape_name,
            state="LOCKED",
            locked=True,
            active=True,
        )
    else:
        raise ValueError(
            f"{shape_code} не може да се използва като производствен MASTER без "
            "актуалната Engine дефиниция. Избери формата от динамичната библиотека."
        )
    if str(shape.code or "").strip().upper() != shape_code:
        raise ValueError("Избраната форма не съвпада с подадената Engine дефиниция.")
    try:
        parameters = parameters_for_shape(
            shape,
            segments,
            production_length_cm=length,
            turn_angles_deg=tuple(resolved.get("turn_angles_deg") or ()),
            declared_allowance_cm=записана_добавка,
        )
    except ExcelExportError as exc:
        raise ValueError(f"Производствените размери не минават MASTER проверката на Engine: {exc}") from exc
    segments = tuple(parameters.expanded_segments_cm)

    geometry_handles = tuple(sorted(str(v) for v in geometry.get("handles", []) if str(v)))
    original = dict(payload.get("original") or {})
    action_class = classify_engineer_action(
        reason_code=reason_code,
        suggested_shape_code=original.get("shape_code"),
        resolved_shape_code=shape_code,
        original_count=original.get("count"),
        resolved_count=count,
        original_diameter_mm=original.get("diameter_mm"),
        resolved_diameter_mm=diameter,
        original_length_cm=original.get("declared_length_cm"),
        resolved_length_cm=length,
        suggested_segments=original.get("segment_lengths_cm") or (),
        resolved_segments=segments,
        suggested_geometry_handles=original.get("geometry_handles") or (),
        resolved_geometry_handles=geometry_handles,
    )
    geometryless_reason_codes = {
        "NO_GEOMETRY",
        "WRONG_GEOMETRY",
        "WRONG_OBJECT",
        "PARTIAL_GEOMETRY",
        "DUPLICATE_GEOMETRY",
    }
    if not geometry_handles and reason_code not in geometryless_reason_codes:
        raise ValueError(
            "Инженерно решение без DXF геометрия е допустимо само при изрично "
            "NO_GEOMETRY/WRONG_GEOMETRY решение и задължителна инженерна бележка."
        )
    if not geometry_handles and action_class == ACTION_CONFIRMATION:
        raise ValueError(
            "Потвърждение без DXF геометрия не е допустимо. Избери коригираща причина "
            "и опиши защо производствената спецификация е зададена ръчно."
        )
    structured_evidence = {
        "candidate_basis": geometry.get("basis"),
        "candidate_notes": geometry.get("notes"),
        "diagnostic_problem": payload.get("diagnostic_problem"),
        "review_reason_codes": original.get("review_reason_codes"),
    }
    note_policy = engineer_note_policy(
        action_class=action_class,
        reason_code=reason_code,
        geometry_handles=geometry_handles,
        structured_evidence=structured_evidence,
    )
    if note_policy.required and not note:
        raise ValueError(note_policy.explanation)

    approved_path = working / "approved_geometry_links.json"
    data = _load_approved_payload(approved_path, root.name)
    links = [dict(v) for v in data.get("links", []) if isinstance(v, dict)]
    history = [dict(v) for v in data.get("history", []) if isinstance(v, dict)]
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

    replaceable_geometry_owners: set[str] = set()
    new_is_legend_reference = is_position_key_legend_reference({
        "candidate_basis": geometry.get("basis"),
        "candidate_notes": geometry.get("notes"),
        "review_reason_codes": original.get("review_reason_codes"),
    })
    for existing in links:
        existing_annotation = str(existing.get("annotation_handle") or "")
        existing_geometry = tuple(sorted(str(v) for v in existing.get("geometry_handles", []) if str(v)))
        if geometry_handles and existing_geometry == geometry_handles and existing_annotation and existing_annotation != handle:
            if new_is_legend_reference or is_position_key_legend_reference(existing):
                continue
            origin = approval_origin(existing)
            if origin == "MANUAL":
                raise ValueError(
                    "Избраната геометрия вече е потвърдена ръчно за друга позиция: "
                    f"{existing_annotation}. Отмени първо едното инженерно решение."
                )
            if origin != "AUTO":
                raise ValueError(
                    "Избраната геометрия вече е одобрена за друга позиция, но произходът "
                    f"е неясен: {existing_annotation}. Прегледай старата връзка."
                )
            # AUTO собственикът се измества, но НЕ мълчаливо.
            #
            # Реален случай: операторът решава 4 позиции, пуска Финален
            # контрол и вижда 13 нови за преглед. Причината е, че всяко
            # решение е отнело геометрия от вече одобрена позиция, а тя
            # е загубила одобрението си без нито дума.
            #
            # Позицията, която губи геометрията си, беше ВЯРНА. Затова
            # изместването изисква изрично потвърждение.
            if not allow_geometry_takeover:
                raise GeometryTakeoverRequired(
                    "Избраната геометрия вече е автоматично одобрена за "
                    f"позиция {existing_annotation}.\n\n"
                    "Ако продължиш, тя ще загуби одобрението си и ще се върне "
                    "за преглед.\n\n"
                    "Провери дали не си избрал чужда геометрия.",
                    losing_annotation=existing_annotation,
                )
            replaceable_geometry_owners.add(existing_annotation)

    retained: list[dict[str, Any]] = []
    for existing in links:
        existing_annotation = str(existing.get("annotation_handle") or "")
        if existing_annotation == handle or existing_annotation in replaceable_geometry_owners:
            archived = dict(existing)
            archived.update(
                {
                    "replaced_at": timestamp,
                    "replaced_by": "OWNER_ENGINEER_DECISION",
                    "replacement_annotation_handle": handle,
                    "replacement_reason": (
                        "SAME_ANNOTATION_UPDATED"
                        if existing_annotation == handle
                        else "GEOMETRY_OWNERSHIP_REASSIGNED_BY_ENGINEER"
                    ),
                }
            )
            history.append(archived)
        else:
            retained.append(existing)

    source_text = str(annotation.get("text") or "")
    resolved_text = str(
        annotation.get("resolved_text")
        or production_annotation_text(count=count, diameter_mm=diameter, production_length_cm=length)
    )
    normalized_resolved = dict(resolved)
    normalized_resolved.update(
        {
            "shape_code": shape_code,
            "shape_name": shape_name,
            "count": count,
            "diameter_mm": diameter,
            "production_length_cm": length,
            "segment_lengths_cm": list(segments),
            # Прието или не — числото стои в одитната следа такова, каквото е
            # БИЛО ПОЛЗВАНО. ``None`` значи, че решението е минало по
            # измерените правила и заявка не е била нужна.
            "bend_allowance_cm": записана_добавка,
        }
    )
    audit_context = _project_audit_context(root)
    audit_context.update(
        {
            str(key): value
            for key, value in dict(payload.get("context") or {}).items()
            if value not in (None, "")
        }
    )
    system_audit = automatic_decision_audit(
        annotation_handle=handle,
        annotation_text=source_text,
        action_class=action_class,
        reason_code=reason_code,
        original=original,
        resolved=normalized_resolved,
        geometry=dict(geometry),
        at=timestamp,
        context=audit_context,
        operator_note=note,
    )
    decision_payload: dict[str, Any] = {
        "schema": "lion-rebar-engineer-decision/v2",
        "action_class": action_class,
        "reason_code": reason_code,
        "reason": str(payload.get("reason") or reason_label(reason_code)),
        "note": note,
        "operator_note": note,
        "note_policy": {
            "required": note_policy.required,
            "code": note_policy.code,
            "explanation": note_policy.explanation,
        },
        "automatic_audit": system_audit,
        "source_annotation_text": source_text,
        "resolved_annotation_text": resolved_text,
        "original": original,
        "resolved": normalized_resolved,
        "geometry": dict(geometry),
        "diagnostic_problem": dict(payload.get("diagnostic_problem") or {}),
    }
    identity = _current_project_identity(root)
    if identity is not None:
        stamp_payload(decision_payload, identity)

    link: dict[str, Any] = {
        "annotation_handle": handle,
        "annotation_text": resolved_text,
        "source_annotation_text": source_text,
        "resolved_annotation_text": resolved_text,
        "annotation_count": count,
        "production_count": count,
        "diameter_mm": diameter,
        "production_diameter_mm": diameter,
        "declared_length_cm": original.get("declared_length_cm"),
        "production_length_cm": length,
        "production_length_source": "OWNER_ENGINEER_DECISION",
        "production_segment_lengths_cm": list(segments),
        # Заявената добавка пътува С решението. Без нея експортът би отказал
        # същата позиция по-късно — проверката там се прави наново.
        "production_bend_allowance_cm": записана_добавка,
        "canonical_segment_lengths_cm": list(segments),
        "canonical_turn_angles_deg": list(resolved.get("turn_angles_deg") or []),
        "geometry_handles": list(geometry_handles),
        "geometry_handles_count": len(geometry_handles),
        "geometry_provenance": "DXF_HANDLES" if geometry_handles else "NONE_ENGINEER_OVERRIDE",
        "geometry_layer": str(geometry.get("layer") or ""),
        "geometry_optional_by_engineer": not bool(geometry_handles),
        "segment_count": len(segments),
        "geometry_length_raw": original.get("geometry_length_raw"),
        "effective_geometry_length_cm": length,
        "numeric_difference": 0.0,
        "raw_numeric_difference": original.get("raw_numeric_difference"),
        "diameter_layer_match": False,
        "candidate_confidence_before_approval": "ENGINEER_RESOLVED",
        "candidate_basis": str(geometry.get("basis") or "OWNER_ENGINEER_DECISION"),
        "candidate_notes": list(geometry.get("notes") or []),
        "master_shape_code": shape_code,
        "master_shape_name": shape_name,
        "master_shape_revision": resolved.get("shape_revision"),
        "engine_shape_parameter_source": parameters.validation_source,
        "approval_status": "ENGINEER_RESOLVED — ПЪЛНА ПРОИЗВОДСТВЕНА СПЕЦИФИКАЦИЯ",
        "approved_at": timestamp,
        "approved_by": "OWNER_ENGINEER_DECISION",
        "engineer_decision": decision_payload,
        "manual_assignment": {
            "assigned_by": "OWNER_ENGINEER_DECISION",
            "shape_code": shape_code,
            "shape_name": shape_name,
            "count": count,
            "diameter_mm": diameter,
            "production_length_cm": length,
            "production_segment_lengths_cm": list(segments),
            "production_bend_allowance_cm": записана_добавка,
            "turn_angles_deg": list(resolved.get("turn_angles_deg") or []),
            "reason_code": reason_code,
            "note": note,
        },
        "automatic_outputs": {
            "position": False,
            "shape": True,
            "zone": False,
            "spacing": False,
            "specification_row": True,
        },
    }
    if identity is not None:
        stamp_payload(link, identity)
    retained.append(link)
    data.update(
        {
            "schema": "lion-rebar-approved-geometry-links/v1",
            "project_name": data.get("project_name") or root.name,
            "updated_at": timestamp,
            "links": retained,
            "history": history,
        }
    )
    if identity is not None:
        stamp_payload(data, identity)
    _write_json_atomic(approved_path, data)
    _invalidate_final_control(working)
    return approved_path


def _save_non_production_position(
    root,
    *,
    handle: str,
    annotation: Mapping[str, Any],
    payload: Mapping[str, Any],
    note: str,
):
    import json
    from datetime import datetime
    from pathlib import Path

    if not note:
        raise ValueError(
            "За изключване като непроизводствена позиция е задължително доказателство/бележка."
        )
    working = Path(root) / "working"
    approved_path = working / "approved_geometry_links.json"
    data = _load_approved_payload(approved_path, Path(root).name)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    retained: list[dict[str, Any]] = []
    history = [dict(v) for v in data.get("history", []) if isinstance(v, dict)]
    for existing in data.get("links", []):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("annotation_handle") or "") == handle:
            archived = dict(existing)
            archived.update(
                {
                    "revoked_at": timestamp,
                    "revoked_by": "OWNER_ENGINEER_DECISION",
                    "revocation_reason": "NON_PRODUCTION_POSITION",
                }
            )
            history.append(archived)
        else:
            retained.append(dict(existing))
    identity = _current_project_identity(Path(root))
    data.update(
        {
            "schema": "lion-rebar-approved-geometry-links/v1",
            "project_name": data.get("project_name") or Path(root).name,
            "updated_at": timestamp,
            "links": retained,
            "history": history,
        }
    )
    if identity is not None:
        stamp_payload(data, identity)
    _write_json_atomic(approved_path, data)

    state_path = working / "review_center_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig")) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    decisions = state.get("decisions") if isinstance(state.get("decisions"), dict) else {}
    decision: dict[str, Any] = {
        "decision": "NON_PRODUCTION_POSITION",
        "action_class": ACTION_EXCLUSION,
        "reason_code": "NON_PRODUCTION_POSITION",
        "reason": reason_label("NON_PRODUCTION_POSITION"),
        "note": note,
        "operator_note": note,
        "annotation_text": str(annotation.get("text") or ""),
        "evidence": {
            "evidence_type": "OWNER_ENGINEER_ORIGINAL_DXF_CHECK",
            "original_dxf_checked": True,
            "operator_note": note,
            "source_annotation_text": str(annotation.get("text") or ""),
            "diagnostic_problem": dict(payload.get("diagnostic_problem") or {}),
            "original": dict(payload.get("original") or {}),
        },
        "at": timestamp,
    }
    decision["note_policy"] = {
        "required": True,
        "code": "NON_PRODUCTION_EVIDENCE",
        "explanation": engineer_note_policy(
            action_class=ACTION_EXCLUSION,
            reason_code="NON_PRODUCTION_POSITION",
        ).explanation,
    }
    decision["automatic_audit"] = automatic_decision_audit(
        annotation_handle=handle,
        annotation_text=str(annotation.get("text") or ""),
        action_class=ACTION_EXCLUSION,
        reason_code="NON_PRODUCTION_POSITION",
        original=dict(payload.get("original") or {}),
        resolved=dict(payload.get("resolved") or {}),
        geometry=dict(payload.get("geometry") or {}),
        at=timestamp,
        context={
            **_project_audit_context(Path(root)),
            **{
                str(key): value
                for key, value in dict(payload.get("context") or {}).items()
                if value not in (None, "")
            },
        },
        operator_note=note,
    )
    if identity is not None:
        stamp_payload(decision, identity)
        stamp_payload(state, identity)
    decisions[str(handle)] = decision
    state["schema"] = state.get("schema") or "lion-rebar-review-center-state/v2"
    state["decisions"] = decisions
    state["updated_at"] = timestamp
    _write_json_atomic(state_path, state)
    _invalidate_final_control(working)
    return state_path


def _load_approved_payload(path, project_name: str) -> dict[str, Any]:
    import json

    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("approved_geometry_links.json не може да бъде прочетен.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("links", []), list):
            raise ValueError("approved_geometry_links.json е с неочаквана структура.")
        return data
    return {
        "schema": "lion-rebar-approved-geometry-links/v1",
        "project_name": project_name,
        "links": [],
        "history": [],
    }


def _current_project_identity(root):
    import json
    from pathlib import Path

    project_path = Path(root) / "working" / "project.json"
    if not project_path.is_file():
        return None
    try:
        payload = json.loads(project_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return identity_from_project_payload(payload) if isinstance(payload, dict) else None


def _project_audit_context(root: Path) -> dict[str, Any]:
    """Read only stable project/version identifiers for a decision audit."""

    import json

    project_path = Path(root) / "working" / "project.json"
    payload: dict[str, Any] = {}
    if project_path.is_file():
        try:
            candidate = json.loads(project_path.read_text(encoding="utf-8-sig"))
            if isinstance(candidate, dict):
                payload = candidate
        except (OSError, json.JSONDecodeError):
            payload = {}
    return {
        "application_version": APP_VERSION,
        "project_name": str(payload.get("name") or Path(root).name),
        "project_version": str(payload.get("version") or "unknown"),
        "project_schema": str(payload.get("schema") or "unknown"),
        "engine_version": str(payload.get("engine_version") or "unknown"),
        "engine_id": str(payload.get("engine_id") or "unknown"),
    }


def _write_json_atomic(path, payload: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _invalidate_final_control(working, *, event: str = "ENGINEER_DECISION",
                             actor: str = "ENGINEER", note: str = "") -> None:
    """Обезсилва Final Control през ЕДИННИЯ lifecycle.

    До Milestone 1 тук се ТРИЕХА трите артефакта. Изтриването на
    production доказателство е по-лошо от запазването му с ясен статус:
    след него не може да се докаже какво е било замразено и защо е
    отпаднало. Сега freeze-ът остава на диска като ``INVALID`` с причина,
    автор и одитна следа, а export-ът го отказва по статус.
    """

    from lion_rebar.project.final_control_lifecycle import invalidate_final_control

    invalidate_final_control(Path(working).parent, event=event, actor=actor, note=note)
