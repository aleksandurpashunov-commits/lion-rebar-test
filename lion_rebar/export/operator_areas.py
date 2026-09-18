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
           всички позиции вътре, и над доказаната автоматична връзка
           (собственик, 2026-09-14; виж ``_freeze_beam_assignments``).
``GROUP``  заглавие на шайба („Ш 3-25/600") или на колонна група
           („К23, К24 (35/35) - 2бр.") -> дава груповия етикет, по който
           описът се подрежда блок по блок.

ПРАВИЛА (като при другите операторски пътища, освен рамката за греда)
----------------------------------------------------------------------
  * областта изисква ТОЧНО ЕДНО заглавие вътре — нула или две е отказ, не
    догадка;
  * рамката за греда бие и доказаната връзка, но само вътре в
    правоъгълника, и главният прозорец показва преди записа какво мести.
    До 2026-09-14 попълваше само празното — това пазеше от невнимателна
    рамка, но правеше невъзможно поправянето на грешно свързана позиция;
  * груповата рамка НЕ отменя автоматичния групов етикет — попълва само
    празното;
  * нова рамка СЕ ОГЪВА по вече начертаните съседни: тя не взима нищо,
    което друго заглавие вече е взело (виж ``bend_around_existing``);
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

#: Правоъгълник в координатите на DXF-а: ``(x0, y0, x1, y1)``.
Box = tuple[float, float, float, float]


class OperatorAreaError(ValueError):
    """Невалидна операторска област — отказва се, не се записва."""


def region_contains(
    box: Box,
    bent_around: Sequence[Box],
    x: float,
    y: float,
) -> bool:
    """Точката е в рамката и НЕ е в нито една, по която тя се е огънала."""

    if not (box[0] <= float(x) <= box[2] and box[1] <= float(y) <= box[3]):
        return False
    return not any(
        other[0] <= float(x) <= other[2] and other[1] <= float(y) <= other[3]
        for other in bent_around
    )


def bend_around_existing(
    box: Box,
    areas: Iterable["OperatorArea"],
    *,
    kind: str,
    title_handle: str,
) -> tuple[Box, ...]:
    """Съседните области, по които новата рамка се огъва.

    Собственик, 2026-09-17: „съгласен съм да прилепват към съседни
    съществуващи, т.е. да се огъват по нея“.

    Измерено на 2026-09-17 в „Вертикали до +8.90“: чертежът е минал през
    състояние, в което две зони са на 1,3 единици една от друга (Стб.Ядро
    B.3 и „Тип 2c -215x25“). При такова разстояние правоъгълник с два клика
    не може да ги раздели — операторът или мине в съседната зона, или
    остави свои елементи навън. Затова новата рамка се чертае щедро и се
    огъва по вече начертаните: тя не взима нищо, което друго заглавие вече
    е взело.

    Огъването се смята ВЕДНЪЖ — при чертането — и се записва в самата
    област. Не се преизчислява при четене, защото решението е операторско:
    оттегли ли се после съседната, тази рамка запазва изрезката си и не се
    разширява тихо върху освободеното.

    Област със СЪЩОТО заглавие не е съседна, а пречертаване: тя се слива,
    както досега (``area_for_point``, случаят „Гр. B.13 25/50“ от
    2026-09-14). Различен вид (``BEAM`` срещу ``GROUP``) също не е съсед —
    двата вида нарочно се застъпват.
    """

    result: list[Box] = []
    for area in areas:
        if area.kind != str(kind):
            continue
        if str(area.title_handle) == str(title_handle):
            continue
        other: Box = (area.x0, area.y0, area.x1, area.y1)
        if other[2] <= box[0] or box[2] <= other[0]:
            continue
        if other[3] <= box[1] or box[3] <= other[1]:
            continue
        result.append(other)
    return tuple(sorted(result))


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
    #: Съседните области, по които рамката се е огънала при чертането.
    #: Празно при по-старите записи — тогава рамката е чист правоъгълник.
    bent_around: tuple[Box, ...] = ()

    def contains(self, x: float, y: float) -> bool:
        return region_contains(
            (self.x0, self.y0, self.x1, self.y1), self.bent_around, x, y
        )

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
            "bent_around": [list(other) for other in self.bent_around],
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
    # Огъването се решава ВЕДНЪЖ — тук, при чертането — и влиза в записа,
    # за да не се преизчислява при всяко четене (виж bend_around_existing).
    bent_around = bend_around_existing(
        ordered,
        load_operator_areas(project_root, identity),
        kind=str(kind),
        title_handle=str(title_handle),
    )
    if any(
        other[0] <= ordered[0]
        and other[1] <= ordered[1]
        and ordered[2] <= other[2]
        and ordered[3] <= other[3]
        for other in bent_around
    ):
        raise OperatorAreaError(
            "Рамката е изцяло в друга начертана рамка — след огъването по нея "
            "не остава нищо. Начертай я извън чуждата рамка."
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
        bent_around=bent_around,
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
                bent_around=_read_bent_around(entry.get("bent_around")),
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


def _read_bent_around(value: Any) -> tuple[Box, ...]:
    """Изрезките от записа. Повреден ред се пропуска, а не се гадае.

    Пропусната изрезка би разширила рамката върху чужда територия, затова
    се чете строго: четири крайни числа в правилен ред, иначе редът пада.
    """

    if not isinstance(value, (list, tuple)):
        return ()
    result: list[Box] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(number) for number in item)
        except (TypeError, ValueError):
            continue
        if x0 >= x1 or y0 >= y1:
            continue
        result.append((x0, y0, x1, y1))
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

    Две припокриващи се области са отказ САМО когато сочат различни
    заглавия — тогава наистина не се знае чия е позицията.

    Сочат ли едно и също заглавие, спор няма и отговорът е един. Това не е
    измислен случай: собственикът начертава рамка, тя не обхваща всичко,
    той чертае втора малко по-широка върху същата греда. Реален случай в
    ``s-9`` на 2026-09-14 — две области за „Гр. B.13 25/50" се припокриваха
    и се отменяха взаимно, а шест позиции оставаха без греда и блокираха
    експорта. Операторът няма как да се досети, че причината е в това.
    """

    matches = [
        area
        for area in areas
        if area.kind == kind
        and area.contains(x, y)
        and (frame_index is None or area.frame_index == int(frame_index))
    ]
    if not matches:
        return None
    # Сверява се и handle, и текстът: разминаване между тях е повреден запис,
    # а не съгласие. Отказът е по-евтин от мълчаливо погрешна греда.
    if len({(area.title_handle, area.title_text) for area in matches}) != 1:
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
