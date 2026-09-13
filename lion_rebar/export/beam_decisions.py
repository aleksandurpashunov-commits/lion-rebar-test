"""Операторски workflow за позиция, която автоматиката не свърза с греда.

Собственик, 2026-09-13, проект ``s-9``: Финалният контрол блокира експорта с
осем ``BEAM_ASSIGNMENT_NOT_UNIQUE`` и подкани „отстрани посочената техническа
причина", но НЕ съществуваше механизъм, с който това да се направи. За
рамките отдавна има операторско решение (``frame_decisions``); за гредите
нямаше нищо, така че проектът оставаше блокиран без изход.

Защо автоматиката се отказва и не бива да ѝ се разхлабват праговете:
детайлът в този чертеж понякога излиза НАД заглавието на своята греда (до
201 единици при праг 117) или ВЛЯВО от него (до 560 при праг 71). Разширяване
на прозореца дотолкова, че да покрие тези осем, беше измерено върху всичките
460 позиции -- решава 3 нови, но ПРЕМЕСТВА 27 вече верни на съседна греда.
Праговете правят точно това, за което са: пазят.

Затова отговорът е човешки, а не по-широка евристика. Правилата повтарят
``frame_decisions``:

  * решава се САМО позиция, която автоматиката е оставила несвързана;
  * решението изисква съществуващо заглавие на греда, автор и мотив;
  * записва се с identity на проекта и с одитна следа;
  * решение от друг DXF/анализ НЕ се наследява (identity gate);
  * решението може да бъде оттеглено, като оттеглянето също се записва.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.project.data_identity import DataIdentity, mismatch_reason

DECISIONS_NAME = "beam_assignment_decisions.json"
AUDIT_NAME = "beam_assignment_audit.jsonl"
SCHEMA = "lion-rebar-beam-assignment-decisions/v1"

#: Методът, записан в ledger-а, за да личи произходът на връзката.
OPERATOR_METHOD = "OPERATOR_BEAM_DECISION"

MIN_NOTE_LENGTH = 3


class BeamDecisionError(ValueError):
    """Невалидно операторско решение -- отказва се, не се записва."""


@dataclass(frozen=True)
class BeamDecision:
    annotation_handle: str
    beam_label_handle: str
    beam_name: str
    decided_by: str
    note: str
    at: str
    project_id: str
    source_sha256: str
    source_generation_id: str
    analysis_run_id: str

    def to_dict(self) -> dict:
        return {
            "annotation_handle": self.annotation_handle,
            "beam_label_handle": self.beam_label_handle,
            "beam_name": self.beam_name,
            "decided_by": self.decided_by,
            "note": self.note,
            "at": self.at,
            "project_id": self.project_id,
            "source_sha256": self.source_sha256,
            "source_generation_id": self.source_generation_id,
            "analysis_run_id": self.analysis_run_id,
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
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _audit(project_root: Path | str, entry: Mapping[str, Any]) -> None:
    _, audit = _paths(project_root)
    audit.parent.mkdir(parents=True, exist_ok=True)
    with open(audit, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")


def validate_decision(
    *,
    beam_label_handle: str,
    decided_by: str,
    note: str,
    assignment_is_unresolved: bool,
    known_beam_handles: Mapping[str, str],
) -> None:
    """Отказва всичко, което не е добросъвестно решение на реален случай."""

    if not assignment_is_unresolved:
        raise BeamDecisionError(
            "Позицията вече е свързана автоматично с греда; операторско "
            "решение не се приема по този път."
        )
    if str(beam_label_handle or "") not in known_beam_handles:
        raise BeamDecisionError(
            f"Няма такова заглавие на греда в чертежа: {beam_label_handle!r}"
        )
    if not str(decided_by or "").strip():
        raise BeamDecisionError("Липсва автор на решението.")
    if len(str(note or "").strip()) < MIN_NOTE_LENGTH:
        raise BeamDecisionError("Изисква се инженерна бележка (мотив).")


def record_beam_decision(
    project_root: Path | str,
    *,
    annotation_handle: str,
    beam_label_handle: str,
    decided_by: str,
    note: str,
    identity: DataIdentity,
    assignment_is_unresolved: bool,
    known_beam_handles: Mapping[str, str],
) -> BeamDecision:
    """Записва към коя греда принадлежи позиция, която автоматиката не реши."""

    validate_decision(
        beam_label_handle=beam_label_handle,
        decided_by=decided_by,
        note=note,
        assignment_is_unresolved=assignment_is_unresolved,
        known_beam_handles=known_beam_handles,
    )
    decision = BeamDecision(
        annotation_handle=str(annotation_handle),
        beam_label_handle=str(beam_label_handle),
        beam_name=str(known_beam_handles[str(beam_label_handle)]),
        decided_by=str(decided_by).strip(),
        note=str(note).strip(),
        at=_now(),
        project_id=identity.project_id,
        source_sha256=identity.source_sha256,
        source_generation_id=identity.source_generation_id,
        analysis_run_id=identity.analysis_run_id,
    )
    path, _ = _paths(project_root)
    payload = _read(path)
    payload["schema"] = SCHEMA
    payload["decisions"] = [
        item
        for item in payload["decisions"]
        if not (
            isinstance(item, Mapping)
            and str(item.get("annotation_handle")) == decision.annotation_handle
            and not mismatch_reason(item, identity, require_run=True)
        )
    ]
    payload["decisions"].append(decision.to_dict())
    _write(path, payload)
    _audit(project_root, {"action": "RECORD", **decision.to_dict()})
    return decision


def withdraw_beam_decision(
    project_root: Path | str,
    *,
    annotation_handle: str,
    withdrawn_by: str,
    note: str,
) -> bool:
    path, _ = _paths(project_root)
    payload = _read(path)
    before = len(payload["decisions"])
    payload["decisions"] = [
        item
        for item in payload["decisions"]
        if not (
            isinstance(item, Mapping)
            and str(item.get("annotation_handle")) == str(annotation_handle)
        )
    ]
    if len(payload["decisions"]) == before:
        return False
    _write(path, payload)
    _audit(
        project_root,
        {
            "action": "WITHDRAW",
            "annotation_handle": str(annotation_handle),
            "withdrawn_by": withdrawn_by,
            "note": note,
            "at": _now(),
        },
    )
    return True


def record_box_decisions(
    project_root: Path | str,
    resolution,
    *,
    decided_by: str,
    note: str,
    identity: DataIdentity,
    known_beam_handles: Mapping[str, str],
) -> list[BeamDecision]:
    """Записва решенията от една операторска рамка -- по едно на позиция.

    Рамката е само бърз начин за много решения; всяко остава пълноценно
    решение със свой ред в одитната следа. Виж
    ``export/operator_group_box.py`` защо това е за предпочитане пред
    отделно състояние за рамки.

    Всичко или нищо: ако едно от решенията бъде отказано, вече записаните се
    оттеглят, за да не остане половин рамка.
    """

    recorded: list[BeamDecision] = []
    try:
        for handle in resolution.annotation_handles:
            recorded.append(
                record_beam_decision(
                    project_root,
                    annotation_handle=handle,
                    beam_label_handle=resolution.beam_label_handle,
                    decided_by=decided_by,
                    note=note,
                    identity=identity,
                    assignment_is_unresolved=True,
                    known_beam_handles=known_beam_handles,
                )
            )
    except BeamDecisionError:
        for decision in recorded:
            withdraw_beam_decision(
                project_root,
                annotation_handle=decision.annotation_handle,
                withdrawn_by=decided_by,
                note="оттеглено — рамката не беше записана докрай",
            )
        raise
    return recorded


def load_beam_decisions(
    project_root: Path | str, identity: DataIdentity
) -> dict[str, str]:
    """Решения, валидни за ТЕКУЩАТА identity: надпис -> handle на гредата.

    Решение от друг проект / друго source generation / друг analysis run се
    игнорира -- същата защита срещу наследяване както при рамките.
    """

    path, _ = _paths(project_root)
    payload = _read(path)
    result: dict[str, str] = {}
    for item in payload["decisions"]:
        if not isinstance(item, Mapping):
            continue
        if mismatch_reason(item, identity, require_run=True):
            continue
        handle = str(item.get("annotation_handle") or "")
        label = str(item.get("beam_label_handle") or "")
        if handle and label:
            result[handle] = label
    return result


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
