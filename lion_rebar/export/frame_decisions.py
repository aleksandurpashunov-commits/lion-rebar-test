"""Операторски workflow за рамки с неопределена категория (UNKNOWN).

Продължение на P0-2. Автоматиката вече отказва да гадае; този модул дава
на оператора проследим начин да реши рамката, без да отслабва защитата.

Правила:
  * решава се САМО рамка, която автоматиката е оставила UNKNOWN;
  * решението изисква валидна категория, автор и мотив;
  * всяко решение се записва в audit trail с identity на проекта;
  * решение от друг DXF/анализ НЕ се наследява (identity gate);
  * решението може да бъде оттеглено, като оттеглянето също е записано.

Бележка: ``VERTICALS`` вече е пълноправна категория със собствен лист
„вертикали“ (одобрено 2026-08-24). Списъкът с вътрешни категории е празен,
но механизмът остава за бъдещи случаи без одобрен export mapping.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.export.frame_detection import CATEGORY_SHEET_NAMES
from lion_rebar.project.data_identity import DataIdentity, mismatch_reason

DECISIONS_NAME = "frame_category_decisions.json"
AUDIT_NAME = "frame_category_audit.jsonl"
SCHEMA = "lion-rebar-frame-category-decisions/v1"

#: Категории, които операторът може да избере.
#: ``UNKNOWN`` не е избираема — тя е липса на решение, не решение.
SELECTABLE_CATEGORIES: tuple[str, ...] = tuple(
    code for code in CATEGORY_SHEET_NAMES if code != "UNKNOWN")

#: Вътрешни категории без export mapping.
#: ``VERTICALS`` беше тук, докато липсваше продуктово решение. От
#: 2026-08-24 Product Owner одобри собствен лист „вертикали“ като за всяка
#: друга категория, затова тя вече е пълноправна и не е вътрешна.
INTERNAL_ONLY_CATEGORIES: tuple[str, ...] = ()

MIN_NOTE_LENGTH = 3


class FrameDecisionError(ValueError):
    """Невалидно операторско решение — отказва се, не се записва."""


@dataclass(frozen=True)
class FrameDecision:
    marker_handle: str
    category_code: str
    decided_by: str
    note: str
    at: str
    project_id: str
    source_sha256: str
    source_generation_id: str
    analysis_run_id: str
    export_mapped: bool

    def to_dict(self) -> dict:
        return {
            "marker_handle": self.marker_handle,
            "category_code": self.category_code,
            "decided_by": self.decided_by,
            "note": self.note,
            "at": self.at,
            "project_id": self.project_id,
            "source_sha256": self.source_sha256,
            "source_generation_id": self.source_generation_id,
            "analysis_run_id": self.analysis_run_id,
            "export_mapped": self.export_mapped,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _paths(project_root: Path | str) -> tuple[Path, Path]:
    working = Path(project_root) / "working"
    return working / DECISIONS_NAME, working / AUDIT_NAME


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"schema": SCHEMA, "decisions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "decisions": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        return {"schema": SCHEMA, "decisions": []}
    return payload


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _audit(project_root: Path | str, entry: Mapping[str, Any]) -> None:
    _, audit = _paths(project_root)
    audit.parent.mkdir(parents=True, exist_ok=True)
    with open(audit, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")


def validate_decision(
    *, category_code: str, decided_by: str, note: str, frame_is_unknown: bool
) -> None:
    if not frame_is_unknown:
        raise FrameDecisionError(
            "Рамката вече има доказана автоматична категория; операторско "
            "решение не се приема по този път.")
    allowed = set(SELECTABLE_CATEGORIES) | set(INTERNAL_ONLY_CATEGORIES)
    if category_code not in allowed:
        raise FrameDecisionError(f"Непозната категория: {category_code!r}")
    if not str(decided_by or "").strip():
        raise FrameDecisionError("Липсва автор на решението.")
    if len(str(note or "").strip()) < MIN_NOTE_LENGTH:
        raise FrameDecisionError("Изисква се инженерна бележка (мотив).")


def record_frame_decision(
    project_root: Path | str,
    *,
    marker_handle: str,
    category_code: str,
    decided_by: str,
    note: str,
    identity: DataIdentity,
    frame_is_unknown: bool,
) -> FrameDecision:
    """Записва операторско решение за UNKNOWN рамка с одитна следа."""

    validate_decision(category_code=category_code, decided_by=decided_by,
                      note=note, frame_is_unknown=frame_is_unknown)
    if not str(marker_handle or "").strip():
        raise FrameDecisionError("Липсва marker handle на рамката.")

    decision = FrameDecision(
        marker_handle=str(marker_handle),
        category_code=category_code,
        decided_by=str(decided_by).strip(),
        note=str(note).strip(),
        at=_now(),
        project_id=identity.project_id,
        source_sha256=identity.source_sha256,
        source_generation_id=identity.source_generation_id,
        analysis_run_id=identity.analysis_run_id,
        export_mapped=category_code in SELECTABLE_CATEGORIES,
    )
    path, _ = _paths(project_root)
    payload = _read(path)
    payload["schema"] = SCHEMA
    # Одит RC1: решение за категория на рамка е production-impacting —
    # старият Final Control става INVALID, а не остава валиден.
    try:
        from lion_rebar.project.final_control_lifecycle import invalidate_final_control

        invalidate_final_control(
            Path(project_root), event="FRAME_CATEGORY_DECISION",
            actor=decision.decided_by,
            note=f"Рамка {decision.marker_handle} -> {decision.category_code}")
    except Exception:  # noqa: BLE001 - решението се записва дори при проблем
        pass
    payload["decisions"] = [
        item for item in payload["decisions"]
        if str(item.get("marker_handle")) != decision.marker_handle
    ] + [decision.to_dict()]
    _write(path, payload)
    _audit(project_root, {"action": "DECIDE", **decision.to_dict()})
    return decision


def withdraw_frame_decision(
    project_root: Path | str, *, marker_handle: str, withdrawn_by: str, note: str = ""
) -> bool:
    """Оттегля решение. Оттеглянето също е одитирано."""

    path, _ = _paths(project_root)
    payload = _read(path)
    before = len(payload["decisions"])
    payload["decisions"] = [
        item for item in payload["decisions"]
        if str(item.get("marker_handle")) != str(marker_handle)
    ]
    if len(payload["decisions"]) == before:
        return False
    _write(path, payload)
    _audit(project_root, {"action": "WITHDRAW", "marker_handle": str(marker_handle),
                          "withdrawn_by": withdrawn_by, "note": note, "at": _now()})
    return True


def load_frame_decisions(
    project_root: Path | str, identity: DataIdentity
) -> dict[str, str]:
    """Решения, валидни за ТЕКУЩАТА identity.

    Решение от друг проект / друго source generation / друг analysis run се
    игнорира — това е защитата срещу наследяване (P0-6).
    Категории без export mapping не се връщат за export употреба.
    """

    path, _ = _paths(project_root)
    payload = _read(path)
    result: dict[str, str] = {}
    for item in payload["decisions"]:
        if not isinstance(item, Mapping):
            continue
        if mismatch_reason(item, identity, require_run=True):
            continue
        code = str(item.get("category_code") or "")
        if code in SELECTABLE_CATEGORIES:
            result[str(item.get("marker_handle"))] = code
    return result


def internal_only_decisions(
    project_root: Path | str, identity: DataIdentity
) -> dict[str, str]:
    """Решения с вътрешна категория без export mapping (напр. VERTICALS).

    Те са видими за оператора и в отчетите, но НЕ създават Excel лист —
    mapping-ът чака решение на Product Owner.
    """

    path, _ = _paths(project_root)
    payload = _read(path)
    return {
        str(item.get("marker_handle")): str(item.get("category_code"))
        for item in payload["decisions"]
        if isinstance(item, Mapping)
        and not mismatch_reason(item, identity, require_run=True)
        and str(item.get("category_code")) in INTERNAL_ONLY_CATEGORIES
    }


def audit_trail(project_root: Path | str) -> list[dict]:
    _, audit = _paths(project_root)
    if not audit.is_file():
        return []
    rows: list[dict] = []
    for line in audit.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
