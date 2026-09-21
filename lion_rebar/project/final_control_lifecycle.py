"""P0-5 — автоматично обезсилване на Final Control при production промяна.

Договор: след промяна на MASTER форма, брой, Ø, L, сегменти, геометрия,
beam/frame assignment, engineer override или NON_PRODUCTION_POSITION,
старият freeze трябва да стане INVALID и export да не може да го ползва.

Модулът е чист (без Qt), за да е тестваем. Той НЕ трие нищо: маркира
freeze-а като невалиден и записва одитна следа защо. Изтриването на
production доказателство е по-лошо от запазването му с ясен статус.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

FREEZE_NAME = "final_control_freeze.json"
INVALIDATION_LOG = "final_control_invalidations.jsonl"

#: Полета, чиято промяна е production-impacting по договора.
PRODUCTION_IMPACTING_FIELDS: tuple[str, ...] = (
    "master_shape_code",
    "shape_code",
    "production_count",
    "annotation_count",
    "production_diameter_mm",
    "diameter_mm",
    "production_length_cm",
    "declared_length_cm",
    "production_segments_cm",
    "canonical_segment_lengths_cm",
    "geometry_handles",
    "candidate_handles",
    "beam_name",
    "beam_assignment",
    "frame_index",
    "frame_category",
    "engineer_override",
    "decision_source",
    "non_production_position",
    "final_status",
)

#: Събития, които сами по себе си са production-impacting.
PRODUCTION_IMPACTING_EVENTS: tuple[str, ...] = (
    "ENGINEER_DECISION",
    "ENGINEER_OVERRIDE",
    "NON_PRODUCTION_POSITION",
    "GEOMETRY_DETACHED",
    "APPLY_TO_GROUP",
    "APPLY_TO_SIMILAR",
    "FRAME_CATEGORY_DECISION",
    # Потвърденият профил на чертожната конвенция влиза в разпознаването
    # (``RecognitionPolicy.declared_rebar_geometry_layers``), тоест мени кои
    # обекти са армировъчна геометрия. Стар freeze след такава промяна не бива
    # да остава валиден.
    "CONVENTION_PROFILE_DECISION",
    "BEAM_ASSIGNMENT_CHANGED",
    "NEW_BATCH_ANALYSIS",
    "SOURCE_GENERATION_CHANGED",
)

STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"


@dataclass(frozen=True)
class InvalidationResult:
    changed: bool
    status: str
    reason: str
    fields: tuple[str, ...] = ()
    freeze_path: str = ""

    @property
    def export_allowed(self) -> bool:
        return self.status == STATUS_VALID


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def detect_production_impact(
    *,
    event: str = "",
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    changed_fields: Iterable[str] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Определя дали промяната е production-impacting и кои полета я правят."""

    if event and str(event).strip().upper() in PRODUCTION_IMPACTING_EVENTS:
        return True, (str(event).strip().upper(),)

    fields: list[str] = []
    if changed_fields:
        fields.extend(str(f) for f in changed_fields if str(f) in PRODUCTION_IMPACTING_FIELDS)
    if before is not None and after is not None:
        for field_name in PRODUCTION_IMPACTING_FIELDS:
            if field_name in before or field_name in after:
                if _norm(before.get(field_name)) != _norm(after.get(field_name)):
                    fields.append(field_name)
    unique = tuple(dict.fromkeys(fields))
    return bool(unique), unique


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(_norm(item) for item in value)
    return " ".join(str(value).split()).strip()


#: Freeze без записан статус — стар формат (до rc2) или непълен запис.
#: НЕ е същото като обезсилен след production промяна и съобщението не бива
#: да твърди причина, която не е настъпила.
STATUS_MISSING = "MISSING_STATUS"


def freeze_state(project_root: Path | str) -> tuple[str, str]:
    """Връща (статус, точна причина) за freeze манифеста.

    Разделя трите случая, които преди се сливаха в ``INVALID``:
      * реално обезсилен след production промяна;
      * записан без статус (стар формат) -> ``MISSING_STATUS``;
      * нечетим/повреден -> ``INVALID`` с причина ``UNREADABLE``.
    """

    path = Path(project_root) / "working" / FREEZE_NAME
    if not path.is_file():
        return "", "NO_FREEZE"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return STATUS_INVALID, "UNREADABLE"
    if not isinstance(payload, dict):
        return STATUS_INVALID, "NOT_AN_OBJECT"
    if "status" not in payload:
        return STATUS_MISSING, "LEGACY_FREEZE_WITHOUT_STATUS"
    status = str(payload.get("status") or "").strip()
    if not status:
        return STATUS_MISSING, "EMPTY_STATUS"
    return status, "RECORDED"


def freeze_status(project_root: Path | str) -> str:
    """Текущият статус на freeze-а.

    Връща точно записаната стойност; липсващ файл -> ``""``; нечетим или
    непълен freeze -> ``INVALID``.

    ВАЖНО (одит RC1, находка 11): по-ранната версия правеше
    ``payload.get("status") or STATUS_VALID`` и така ЛИПСВАЩ или празен
    статус се приемаше за валиден. Липсата на доказателство не е
    доказателство — тя вече е INVALID.
    """

    status, _reason = freeze_state(project_root)
    return STATUS_INVALID if status == STATUS_MISSING else status


def invalidate_final_control(
    project_root: Path | str,
    *,
    event: str = "",
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    changed_fields: Iterable[str] | None = None,
    actor: str = "SYSTEM",
    note: str = "",
) -> InvalidationResult:
    """Обезсилва freeze-а, ако промяната е production-impacting.

    Idempotent: повторно извикване върху вече невалиден freeze не променя
    нищо и връща ``changed=False``.
    """

    impacted, fields = detect_production_impact(
        event=event, before=before, after=after, changed_fields=changed_fields)
    root = Path(project_root)
    freeze_path = root / "working" / FREEZE_NAME
    if not impacted:
        return InvalidationResult(False, freeze_status(root) or "", "NOT_PRODUCTION_IMPACTING")
    if not freeze_path.is_file():
        return InvalidationResult(False, "", "NO_FREEZE", fields)

    try:
        payload = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if str(payload.get("status") or STATUS_VALID) == STATUS_INVALID:
        return InvalidationResult(False, STATUS_INVALID, "ALREADY_INVALID", fields,
                                  str(freeze_path))

    reason = f"PRODUCTION_CHANGE:{','.join(fields)}" if fields else "PRODUCTION_CHANGE"
    payload["status"] = STATUS_INVALID
    payload["invalidated_at"] = _now()
    payload["invalidated_reason"] = reason
    payload["invalidated_by"] = actor
    payload["invalidated_fields"] = list(fields)
    if note:
        payload["invalidated_note"] = note

    tmp = freeze_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(freeze_path)

    log = root / "working" / INVALIDATION_LOG
    entry = {
        "at": _now(), "event": event, "actor": actor,
        "fields": list(fields), "reason": reason, "note": note,
    }
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return InvalidationResult(True, STATUS_INVALID, reason, fields, str(freeze_path))


def assert_export_allowed(project_root: Path | str) -> list[str]:
    """Проблеми, които блокират export. Празен списък = export е разрешен.

    Разрешава САМО при точен статус ``VALID`` (allowlist, не blocklist).
    Всяка друга стойност — непозната, празна, липсваща — блокира.
    """

    status, reason = freeze_state(project_root)
    if not status:
        return ["NO_FINAL_CONTROL: липсва финален контрол за този проект"]
    if status == STATUS_VALID:
        return []
    if status == STATUS_MISSING:
        # Не твърдим "обезсилен след production промяна" — това не е вярно.
        return ["FINAL_CONTROL_INCOMPLETE: финалният контрол е записан без "
                "статус (стар формат). Пусни Финален контрол наново с "
                "текущата версия."]
    if status == STATUS_INVALID:
        if reason in ("UNREADABLE", "NOT_AN_OBJECT"):
            return ["FINAL_CONTROL_UNREADABLE: freeze манифестът е повреден; "
                    "пусни Финален контрол наново"]
        return ["FINAL_CONTROL_INVALID: финалният контрол е обезсилен след "
                "production промяна; пусни го наново"]
    return [f"FINAL_CONTROL_UNKNOWN_STATUS: непознат статус {status!r}; "
            "export е блокиран до нов финален контрол"]
