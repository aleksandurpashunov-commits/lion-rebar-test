"""Операторски области, начертани ПРЕДИ анализа и прочетени ОТ него.

Собственик, 2026-09-13: „да направим рамките преди теста на чертежа, за да
може програмата да го прочете".

Разликата от ``beam_decisions`` е в момента и в предмета. Там операторът
решава ЕДНА позиция, СЛЕД като Финалният контрол я е блокирал. Тук
операторът обявява ОБЛАСТ, преди изобщо да има позиции — а анализът после
сам намира какво е попаднало вътре. Затова се пази правоъгълникът и
заглавието в него, а не списък с handles: списъкът се получава наново при
всеки анализ и остава верен, ако между двата прогона се появи или изчезне
позиция.

Това е ръчният близнак на ``ColumnGroupBox`` в ``dxf/proxy_adapter.py``:
там програмата сама намира затворения контур и заглавието му, тук
операторът ги посочва с два клика. Надолу по веригата двете значат едно и
също — всичко вътре принадлежи на елемента, чието заглавие е в рамката.

ЗА КАКВО СЛУЖИ ОБЛАСТТА
-----------------------
Според вида на заглавието вътре:

``BEAM``   заглавие „Гр. B.19 25/55" в рамка ``BEAMS`` -> дава гредата на
           позициите вътре, там където автоматиката не я е доказала.
``GROUP``  заглавие на шайба („Ш 3-25/600") или на колонна група
           („К23, К24 (35/35) - 2бр.") -> дава груповия етикет, по който
           описът се подрежда блок по блок.

ПРАВИЛА (същите като при другите операторски пътища)
----------------------------------------------------
  * областта изисква ТОЧНО ЕДНО заглавие вътре — нула или две е отказ, не
    догадка;
  * областта НЕ отменя доказана автоматична връзка — попълва само там,
    където автоматиката е оставила празно;
  * изисква автор и мотив; записва се с identity на проекта;
  * област от друг DXF/анализ НЕ се наследява (identity gate);
  * може да бъде оттеглена, като оттеглянето също се записва.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.project.data_identity import DataIdentity, mismatch_reason

AREAS_NAME = "operator_group_areas.json"
AUDIT_NAME = "operator_group_areas_audit.jsonl"
SCHEMA = "lion-rebar-operator-group-areas/v1"

#: Методите, записани в ledger-а, за да личи произходът на връзката.
OPERATOR_AREA_BEAM_METHOD = "OPERATOR_AREA_BEAM"
OPERATOR_AREA_GROUP_METHOD = "OPERATOR_AREA_GROUP"

#: Видовете заглавие, които една област може да носи.
KIND_BEAM = "BEAM"
KIND_GROUP = "GROUP"
KINDS: tuple[str, ...] = (KIND_BEAM, KIND_GROUP)

MIN_NOTE_LENGTH = 3


class OperatorAreaError(ValueError):
    """Невалидна операторска област — отказва се, не се записва."""


@dataclass(frozen=True)
class OperatorArea:
    """Една запазена област.

    ``x0/y0/x1/y1`` са в координатите на DXF-а, не на екрана — виж
    ``ui/drawing_view.py``, където изгледът е с обърнат Y и кликовете се
    превръщат обратно, преди да стигнат дотук.
    """

    area_id: str
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str
    title_handle: str
    title_text: str
    frame_index: int
    decided_by: str
    note: str
    at: str
    project_id: str
    source_sha256: str
    source_generation_id: str
    analysis_run_id: str

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= float(x) <= self.x1 and self.y0 <= float(y) <= self.y1

    def to_dict(self) -> dict:
        return {
            "area_id": self.area_id,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "kind": self.kind,
            "title_handle": self.title_handle,
            "title_text": self.title_text,
            "frame_index": self.frame_index,
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
    return working / AREAS_NAME, working / AUDIT_NAME


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"schema": SCHEMA, "areas": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "areas": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("areas"), list):
        return {"schema": SCHEMA, "areas": []}
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


def normalise_box(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float, float, float]:
    """Двата клика в подреден правоъгълник ``(x0, y0, x1, y1)``.

    Посоката на влаченето няма значение — операторът може да тръгне от кой
    да е ъгъл.
    """

    x0, x1 = sorted((float(start[0]), float(end[0])))
    y0, y1 = sorted((float(start[1]), float(end[1])))
    return (x0, y0, x1, y1)


def _area_id(box: tuple[float, float, float, float], title_handle: str, at: str) -> str:
    """Стабилен ключ за оттегляне и одит.

    Координатите се закръглят до 0.1 единица: две области, начертани на
    практика на едно и също място в един и същи момент, са една и съща
    област, а не две.
    """

    seed = "|".join(
        [
            f"{round(value, 1):.1f}" for value in box
        ] + [title_handle, at]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def validate_area(
    *,
    box: tuple[float, float, float, float],
    kind: str,
    title_handle: str,
    decided_by: str,
    note: str,
) -> None:
    """Отказва невалидна област, преди да е записана."""

    if box[0] >= box[2] or box[1] >= box[3]:
        raise OperatorAreaError(
            "Рамката е с нулева широчина или височина — начертай я наново."
        )
    if kind not in KINDS:
        raise OperatorAreaError(f"Непознат вид област: {kind!r}.")
    if not str(title_handle).strip():
        raise OperatorAreaError("Областта няма заглавие — не може да бъде записана.")
    if not str(decided_by).strip():
        raise OperatorAreaError("Липсва автор на решението.")
    if len(str(note).strip()) < MIN_NOTE_LENGTH:
        raise OperatorAreaError(
            f"Обосновката е задължителна (поне {MIN_NOTE_LENGTH} знака)."
        )


def record_operator_area(
    project_root: Path | str,
    *,
    box: tuple[float, float, float, float],
    kind: str,
    title_handle: str,
    title_text: str,
    frame_index: int,
    decided_by: str,
    note: str,
    identity: DataIdentity,
) -> OperatorArea:
    """Записва една област и одитния ѝ ред."""

    ordered = normalise_box((box[0], box[1]), (box[2], box[3]))
    validate_area(
        box=ordered,
        kind=kind,
        title_handle=title_handle,
        decided_by=decided_by,
        note=note,
    )
    at = _now()
    area = OperatorArea(
        area_id=_area_id(ordered, str(title_handle), at),
        x0=ordered[0],
        y0=ordered[1],
        x1=ordered[2],
        y1=ordered[3],
        kind=str(kind),
        title_handle=str(title_handle),
        title_text=str(title_text),
        frame_index=int(frame_index),
        decided_by=str(decided_by).strip(),
        note=str(note).strip(),
        at=at,
        project_id=identity.project_id,
        source_sha256=identity.source_sha256,
        source_generation_id=identity.source_generation_id,
        analysis_run_id=identity.analysis_run_id,
    )

    path, _audit_path = _paths(project_root)
    payload = _read(path)
    areas = [
        entry
        for entry in payload.get("areas", [])
        if isinstance(entry, dict) and str(entry.get("area_id") or "") != area.area_id
    ]
    areas.append(area.to_dict())
    payload["schema"] = SCHEMA
    payload["areas"] = areas
    _write(path, payload)
    _audit(project_root, {"event": "AREA_RECORDED", **area.to_dict()})
    return area


def withdraw_operator_area(
    project_root: Path | str,
    area_id: str,
    *,
    withdrawn_by: str,
    note: str,
) -> bool:
    """Оттегля област. Връща ``False``, ако такава няма."""

    if not str(withdrawn_by).strip():
        raise OperatorAreaError("Липсва автор на оттеглянето.")
    if len(str(note).strip()) < MIN_NOTE_LENGTH:
        raise OperatorAreaError(
            f"Обосновката за оттегляне е задължителна (поне {MIN_NOTE_LENGTH} знака)."
        )
    path, _audit_path = _paths(project_root)
    payload = _read(path)
    remaining = [
        entry
        for entry in payload.get("areas", [])
        if isinstance(entry, dict) and str(entry.get("area_id") or "") != str(area_id)
    ]
    if len(remaining) == len(payload.get("areas", [])):
        return False
    payload["schema"] = SCHEMA
    payload["areas"] = remaining
    _write(path, payload)
    _audit(
        project_root,
        {
            "event": "AREA_WITHDRAWN",
            "area_id": str(area_id),
            "withdrawn_by": str(withdrawn_by).strip(),
            "note": str(note).strip(),
            "at": _now(),
        },
    )
    return True


def load_operator_areas(
    project_root: Path | str,
    identity: DataIdentity | None,
) -> tuple[OperatorArea, ...]:
    """Областите, валидни за ТОЗИ чертеж.

    Област, записана върху друг проект или друга редакция на чертежа, се
    пропуска мълчаливо — същата защита срещу наследяване, както при
    ``load_beam_decisions`` и ``load_frame_decisions``.

    ВАЖНАТА РАЗЛИКА от тях: тук ``analysis_run_id`` НЕ се сверява
    (``require_run`` остава ``False``). Решението за позиция се взима СЛЕД
    анализа, върху негов резултат, затова там е редно да умре заедно с него.
    Областта се чертае ПРЕДИ анализа и целият ѝ смисъл е анализът да я
    прочете — а той започва с ``begin_analysis_run``, който дава нов
    ``analysis_run_id``. Сверяване по анализ би обезсилвало всяка област в
    мига, в който тя за пръв път трябва да свърши работа.

    Защитата остава пълна: ``project_id`` и ``source_generation_id`` се
    сверяват, а ``source_sha256`` вързва областта за точното съдържание на
    чертежа. Промени ли се чертежът, областите отпадат.
    """

    path, _audit_path = _paths(project_root)
    payload = _read(path)
    result: list[OperatorArea] = []
    for entry in payload.get("areas", []):
        if not isinstance(entry, dict):
            continue
        try:
            area = OperatorArea(
                area_id=str(entry.get("area_id") or ""),
                x0=float(entry["x0"]),
                y0=float(entry["y0"]),
                x1=float(entry["x1"]),
                y1=float(entry["y1"]),
                kind=str(entry.get("kind") or ""),
                title_handle=str(entry.get("title_handle") or ""),
                title_text=str(entry.get("title_text") or ""),
                frame_index=int(entry.get("frame_index") or 0),
                decided_by=str(entry.get("decided_by") or ""),
                note=str(entry.get("note") or ""),
                at=str(entry.get("at") or ""),
                project_id=str(entry.get("project_id") or ""),
                source_sha256=str(entry.get("source_sha256") or ""),
                source_generation_id=str(entry.get("source_generation_id") or ""),
                analysis_run_id=str(entry.get("analysis_run_id") or ""),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not area.area_id or area.kind not in KINDS or not area.title_handle:
            continue
        if area.x0 >= area.x1 or area.y0 >= area.y1:
            continue
        if identity is not None and mismatch_reason(entry, identity):
            continue
        result.append(area)
    return tuple(result)


def area_for_point(
    areas: Sequence[OperatorArea],
    x: float,
    y: float,
    *,
    kind: str,
    frame_index: int | None = None,
) -> OperatorArea | None:
    """Областта от този вид, в която пада точката, или ``None``.

    Ако точката попадне в две припокриващи се области от един и същи вид,
    връща ``None`` — двусмислието е отказ, не догадка, точно както
    „две заглавия в рамката" е отказ при чертането.
    """

    matches = [
        area
        for area in areas
        if area.kind == kind
        and area.contains(x, y)
        and (frame_index is None or area.frame_index == int(frame_index))
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def audit_trail(project_root: Path | str) -> list[dict]:
    _, audit = _paths(project_root)
    if not audit.is_file():
        return []
    entries: list[dict] = []
    for line in audit.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def areas_summary(areas: Iterable[OperatorArea]) -> dict[str, int]:
    """Колко области от кой вид — за отчета и за session log-а."""

    summary = {kind: 0 for kind in KINDS}
    for area in areas:
        if area.kind in summary:
            summary[area.kind] += 1
    return summary
