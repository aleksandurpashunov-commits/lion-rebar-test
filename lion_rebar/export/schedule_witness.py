# -*- coding: utf-8 -*-
"""Начертаната ведомост като ВТОРИ СВИДЕТЕЛ на позицията.

ЗАЩО СЪЩЕСТВУВА
---------------
``dxf/drawn_schedule.py`` чете ведомостта, начертана в самия чертеж, и сверява
всеки ред със собствената контрола на проектанта (брой × единична = обща).
Размерите по скицата се приемат само когато сборът им затваря единичната
дължина. До 22.10.2026 четецът не беше закачен към пътя — нито един резултат не
идваше от него.

Решение на собственика, 22.10.2026 (вариант „Б“):

* **свидетел** — до всяка позиция се записва какво казва ведомостта; когато
  ведомостта ПРОТИВОРЕЧИ на изнасяния вектор, автоматичното одобрение спира;
* **права форма** — когато чертежът няма прът за надписа, редът във
  ведомостта е еднозначен и размерите му са потвърдени, ведомостта дава права
  форма.

Измерено на ``h01-1001`` (единственият чертеж с начертана ведомост): от 94
одобрени нито една не противоречи; от 58 спрени ведомостта дава размери на 4.
След закачането: 94 → 97 (``4N8x1550``, ``6N8x570``, ``2*3N14x655``).

На 07.10.2026 собственикът беше отбелязал за ``4N8x1550`` форма F001 и
„подобен тип позиции без мастър форма, нека останат за преглед“. На 22.10.2026
изрично: „Днешното Б важи и отменя бележката от 07.10: правата форма от
ведомостта се одобрява автоматично при тези условия.“

ПРАВИЛАТА
---------
* Редът се намира по **Ø и единична дължина** — двете числа, които надписът и
  ведомостта казват независимо. Бройката НЕ участва: във ведомостта е общата
  бройка на марката, а надписите често са няколко за една марка.
* **Нула или два реда е отказ**, не „най-близкият“. Два реда с един и същ Ø и
  дължина са две различни марки — коя е на надписа, не се знае.
* Сравняват се размерите като множество (подредбата по скицата не е подредбата
  по пръта), с допуска ``РАЗМЕРЕН_ДОПУСК_CM`` на четеца.
* Права форма се дава САМО при: няма кандидат в чертежа, един ред, размери
  ``ПОТВЪРДЕНИ``, точно ЕДИН размер и той е единичната дължина. Всичко друго
  остава за оператора.
* Писачът в анализа не е последната дума: портата доказва наново от записания
  ред (``evidence_is_owned``), за да не може разширяване на писача само по себе
  си да разшири автоматиката.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.drawn_schedule import РАЗМЕРЕН_ДОПУСК_CM, DrawnSchedule

#: Полето на реда в отчета.
WITNESS_FIELD = "schedule_witness"

#: Източникът на производствените размери, когато ведомостта дава правата форма.
STRAIGHT_SOURCE = "DRAWN_SCHEDULE_STRAIGHT"

#: Как връзката обявява, че няма DXF геометрия и откъде идва позицията.
GEOMETRY_PROVENANCE = "NONE_DRAWN_SCHEDULE_ROW"

#: Причината в портата, когато ведомостта противоречи на изнасяния вектор.
CONTRADICTS = "SCHEDULE_CONTRADICTS"

#: Кодът на правата форма в заключената MASTER библиотека.
STRAIGHT_SHAPE_CODE = "F001"

ЕДИН_РЕД = "ЕДИН РЕД"
НЯМА_РЕД = "НЯМА РЕД"
НЕЕДНОЗНАЧНО = "НЕЕДНОЗНАЧНО"
ПОТВЪРДЕНИ = "ПОТВЪРДЕНИ"


def _число(стойност: Any) -> float | None:
    try:
        число = float(стойност)
    except (TypeError, ValueError):
        return None
    return число if math.isfinite(число) else None


def _равни(a: float, b: float) -> bool:
    return abs(a - b) <= РАЗМЕРЕН_ДОПУСК_CM


def same_dimensions(първи: Sequence[float], втори: Sequence[float]) -> bool:
    """Еднакви ли са два набора размери като МНОЖЕСТВА, с допуска на четеца."""

    a = sorted(float(v) for v in първи)
    b = sorted(float(v) for v in втори)
    return len(a) == len(b) and all(_равни(x, y) for x, y in zip(a, b))


def witness_for(row: Mapping[str, Any], schedule: DrawnSchedule) -> dict[str, Any] | None:
    """Какво казва ведомостта за ТОЗИ надпис. ``None`` — надписът няма Ø/L."""

    диаметър = _число(row.get("diameter_mm"))
    дължина = _число(row.get("declared_length_cm"))
    if диаметър is None or дължина is None or дължина <= 0:
        return None
    редове = [
        ред
        for ред in schedule.rows
        if _равни(float(ред.diameter_mm), диаметър)
        and _равни(float(ред.single_length_cm), дължина)
    ]
    if not редове:
        return {"status": НЯМА_РЕД}
    if len(редове) > 1:
        return {
            "status": НЕЕДНОЗНАЧНО,
            "marks": [ред.mark for ред in редове],
        }
    ред = редове[0]
    return {
        "status": ЕДИН_РЕД,
        "mark": ред.mark,
        "diameter_mm": float(ред.diameter_mm),
        "single_length_cm": float(ред.single_length_cm),
        "pcs": int(ред.pcs),
        "dimensions_status": ред.dimensions_status,
        "shape_dimensions": [float(v) for v in ред.shape_dimensions],
        "handles": list(ред.sample_handles),
    }


def confirmed_dimensions(witness: Mapping[str, Any] | None) -> tuple[float, ...] | None:
    """Размерите от ведомостта, когато могат да свидетелстват — иначе ``None``."""

    if not isinstance(witness, Mapping) or witness.get("status") != ЕДИН_РЕД:
        return None
    if witness.get("dimensions_status") != ПОТВЪРДЕНИ:
        return None
    размери = [_число(v) for v in (witness.get("shape_dimensions") or [])]
    if not размери or any(v is None or v <= 0 for v in размери):
        return None
    return tuple(float(v) for v in размери)  # type: ignore[arg-type]


def contradicts(witness: Mapping[str, Any] | None, segments: Iterable[Any]) -> bool:
    """Противоречи ли ведомостта на изнасяния вектор."""

    размери = confirmed_dimensions(witness)
    if размери is None:
        return False
    вектор = [_число(v) for v in segments]
    if not вектор or any(v is None for v in вектор):
        return False
    return not same_dimensions(размери, вектор)  # type: ignore[arg-type]


def straight_length(row: Mapping[str, Any], witness: Mapping[str, Any] | None) -> float | None:
    """Дължината на правата форма от ведомостта, или ``None`` при отказ."""

    if [h for h in (row.get("candidate_handles") or []) if str(h or "").strip()]:
        return None  # чертежът има прът — свидетелства той, не ведомостта
    размери = confirmed_dimensions(witness)
    if размери is None or len(размери) != 1:
        return None
    дължина = _число(row.get("declared_length_cm"))
    if дължина is None or not _равни(размери[0], дължина):
        return None
    return float(размери[0])


def straight_evidence(row: Mapping[str, Any], witness: Mapping[str, Any]) -> dict[str, Any] | None:
    """Доказателството за правата форма, което анализът записва на реда."""

    дължина = straight_length(row, witness)
    if дължина is None:
        return None
    return {
        "source": STRAIGHT_SOURCE,
        "shape_code": STRAIGHT_SHAPE_CODE,
        "segments": [дължина],
        "schedule_mark": witness.get("mark"),
        "schedule_handles": list(witness.get("handles") or []),
        "diameter_mm": witness.get("diameter_mm"),
        "single_length_cm": witness.get("single_length_cm"),
    }


def evidence_is_owned(row: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    """Портата: доказателството наново, САМО от записания ред.

    Иска същите условия като писача и съгласие между доказателството,
    свидетеля и реда — Ø, единична дължина, единствения размер и формата.
    """

    witness = row.get(WITNESS_FIELD)
    дължина = straight_length(row, witness)
    if дължина is None:
        return False
    if str(row.get("master_shape_code") or "").strip().upper() != STRAIGHT_SHAPE_CODE:
        return False
    сегменти = [_число(v) for v in (evidence.get("segments") or [])]
    if len(сегменти) != 1 or сегменти[0] is None or not _равни(сегменти[0], дължина):
        return False
    if str(evidence.get("schedule_mark") or "") != str(witness.get("mark") or ""):
        return False
    for поле in ("diameter_mm", "single_length_cm"):
        a, b = _число(evidence.get(поле)), _число(witness.get(поле))
        if a is None or b is None or not _равни(a, b):
            return False
    return bool(evidence.get("schedule_handles"))
