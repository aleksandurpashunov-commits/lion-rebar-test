# -*- coding: utf-8 -*-
"""Прав прът, начертан с друга дължина: изнася се по надписа, в жълто.

ЗАЩО СЪЩЕСТВУВА
---------------
Собственик, 08.11.2026, „Греди +6.50": правите пръти ``3N25x680``,
``2N8x680``, ``3N25x300``, ``3N20x800``, ``2N12x300``, ``24N12x350``,
``29N12x1100`` и ``20N12x1100`` са начертани с 5–100 cm различно от надписа и
той потвърди надписа при всички. Указание: „Добре е да стои с предупреждение,
че не е изчертано вярно, но да излиза просто в Excel с жълто, за проверка, и
отстрани точно какво му е." И на 09.11.2026: „за всички разлики (отклонения от
чертежа), като да има обосновка за това защо е в жълто."

Измерено преди правилото (корпус от 11 чертежа + SS-1 + „Греди +6.50"):
от спрените прави пръти около 25 спират САМО на дължината — един кандидат,
без спор за собственост, без друга причина. Още над 50 спират на спорна
собственост или близък съперник; те НЕ влизат тук.

ПРАВИЛАТА
---------
Надписът е производственият авторитет за правия прът — така и досега Excel
взима ``declared_length_cm``. Тук се решава само КОГА отклонението на
чертежа не е причина за ръчен преглед. Всички наведнъж:

* формата е F001, кандидатът е един прав участък, никой друг доказател не е
  инсталирал вектор;
* редът е спрян (ръчен преглед / риск) и единствената причина на matcher-а е
  дължината (``LENGTH_MISMATCH`` или нищо), без причини за преглед и без
  флагове за качество на данните;
* няма спор за собственост, конфликт на MASTER или двусмислена форма;
* кандидатът е ясно пред съперниците си — и по оценка, и по
  ``matcher_evidence`` праговете — или няма съперници;
* Ø от слоя не противоречи (слой без Ø е допустим, както при „СИЛЕН ПО
  ГЕОМЕТРИЯ"); ведомостта, ако я има, не противоречи;
* до пръта няма отпечатана кота, равна на начертаната дължина — тогава
  чертежът е оразмерен и разликата е недочертан участък или противоречие
  (Тест-002, „21N10x340/20": начертан 320, коти 320 и 20, ведомост
  „20 + 320");
* отклонението е над допуска на закръгляването — иначе позицията вече е силна.

Обосновката пътува с позицията до Excel (``deviation_note``) — и за ръчно
потвърдените прави пръти, защото отклонението на чертежа е факт и за тях.
Портата доказва наново от записания ред (``evidence_is_owned``).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from lion_rebar.dxf.matcher_evidence import competing_alternative_count

#: Източникът на производствения вектор.
SOURCE = "LABEL_OVER_DRAWING_STRAIGHT"

#: Полето на реда и на одобрената връзка с обосновката.
FIELD = "drawing_deviation"

#: Под това разликата е закръгляне, не отклонение (``printed_dimension_guard``).
ДОПУСК_CM = 0.5

#: Същите прагове за ясна преднина като в ``matcher_evidence._separation_band``.
ПРАГ_ОЦЕНКА = 0.04
ПРАГ_РАЗСТОЯНИЕ = 8.0

_СПРЕНИ = frozenset({"ЗА РЪЧЕН ПРЕГЛЕД", "РИСК"})
_ДОПУСТИМИ_БЛОКЕРИ = frozenset({"LENGTH_MISMATCH"})


def _число(стойност: Any) -> float | None:
    if isinstance(стойност, bool):
        return None
    try:
        число = float(стойност)
    except (TypeError, ValueError):
        return None
    return число if math.isfinite(число) else None


def _списък(стойност: Any) -> list[str]:
    if isinstance(стойност, (list, tuple)):
        return [str(х).strip().upper() for х in стойност if str(х).strip()]
    return [част.strip().upper() for част in str(стойност or "").split(",") if част.strip()]


def _ясна_преднина(row: Mapping[str, Any]) -> bool:
    if competing_alternative_count(row) == 0:
        return True
    оценка = _число(row.get("candidate_score_gap"))
    разстояние = _число(row.get("candidate_distance_gap_raw"))
    if оценка is None or оценка <= 0:
        # Съперник с по-висока оценка: кой прът е на надписа, не е ясно.
        return False
    return оценка >= ПРАГ_ОЦЕНКА or (разстояние is not None and разстояние >= ПРАГ_РАЗСТОЯНИЕ)


def obosnovka(начертан: float, обявен: float) -> str:
    """Текстът, който стои до жълтия ред."""

    разлика = начертан - обявен
    посока = "по-къс" if разлика < 0 else "по-дълъг"
    return (
        f"Начертан {начертан:g} cm, надписан {обявен:g} cm — прътът е "
        f"начертан {abs(разлика):g} cm {посока}. Изнесено по надписа; "
        "чертежът не е в мащаб за този прът — за проверка."
    )


def deviation(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Отклонението, ако редът отговаря на ВСИЧКИ условия; иначе ``None``."""

    if str(row.get("annotation_class") or "") != "ACTIONABLE":
        return None
    if str(row.get("master_shape_code") or "").strip().upper() != "F001":
        return None
    if str(row.get("batch_status") or "") not in _СПРЕНИ:
        return None
    if not [х for х in (row.get("candidate_handles") or ()) if str(х)]:
        return None
    if _число(row.get("segment_count")) not in (None, 1.0):
        return None
    if row.get("production_segments_cm"):
        return None
    if (row.get("global_conflict") or row.get("owner_conflict")
            or row.get("master_conflict") or row.get("master_ambiguous_shape_codes")):
        return None
    if not set(_списък(row.get("matcher_blocker_codes"))) <= _ДОПУСТИМИ_БЛОКЕРИ:
        return None
    if _списък(row.get("review_reason_codes")) or _списък(row.get("data_quality_flags")):
        return None
    if str(row.get("contextual_rule_code") or "").strip():
        return None
    if row.get("diameter_layer_match") is False and row.get("layer_diameter_mm") is not None:
        return None
    свидетел = row.get("schedule_witness")
    if isinstance(свидетел, Mapping) and str(свидетел.get("status") or "") == "SCHEDULE_CONTRADICTS":
        return None
    if not _ясна_преднина(row):
        return None
    обявен = _число(row.get("declared_length_cm"))
    начертан = _число(row.get("effective_geometry_length_cm"))
    if обявен is None or начертан is None or обявен <= 0 or начертан <= 0:
        return None
    if abs(начертан - обявен) <= ДОПУСК_CM:
        return None
    # Чертежът ОРАЗМЕРЯВА начертаната дължина: тогава разликата не е мащаб, а
    # недочертан участък или истинско противоречие. Измерено на Тест-002:
    # „21N10x340/20" е начертан 320, до него стоят коти 320 и 20, а
    # ведомостта казва „20 + 320" — прът с кука, не прав 340. „15N10x690/15"
    # е начертан 700 с кота 700. И двата остават за оператора; нито едно от
    # осемте потвърждения на собственика в „Греди +6.50" не носи такава кота.
    if any(abs(кота - начертан) <= ДОПУСК_CM
           for кота in (_число(х) for х in (row.get("nearby_dimensions_cm") or ()))
           if кота is not None):
        return None
    начертан = round(начертан, 1)
    return {
        "drawn_cm": начертан,
        "declared_cm": обявен,
        "difference_cm": round(начертан - обявен, 1),
        "note": obosnovka(начертан, обявен),
    }


def evidence_for(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Доказателството за производствения вектор ``[L]``, или ``None``."""

    отклонение = deviation(row)
    if отклонение is None:
        return None
    return {
        "shape_code": "F001",
        "segments": [отклонение["declared_cm"]],
        "source": SOURCE,
        "confidence": "LABEL",
        FIELD: отклонение,
    }


def evidence_is_owned(row: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    """Портата: условията наново от реда, без да вярва на записа."""

    # Писачът не мени нищо друго по реда, затова условията се проверяват
    # върху същия ред, само без вектора, който самият той е сложил.
    проба = {**dict(row), "production_segments_cm": None}
    наново = evidence_for(проба)
    if наново is None:
        return False
    записани = [_число(х) for х in (evidence.get("segments") or ())]
    return записани == наново["segments"] and list(row.get("production_segments_cm") or ()) == наново["segments"]


def _начертана_дължина(link: Mapping[str, Any], производствена: float | None) -> float | None:
    """Начертаната дължина в cm за одобрената връзка.

    Инженерното решение записва в ``effective_geometry_length_cm``
    производствената дължина — затова там отклонението изчезва. Новите
    решения носят ``drawn_geometry_length_cm``. За вече записаните суровата
    ``geometry_length_raw`` се приема само ако е в cm — проверено срещу
    записаната ``raw_numeric_difference`` на същата връзка; иначе не се гадае.
    """

    начертана = _число(link.get("drawn_geometry_length_cm"))
    if начертана is not None:
        return начертана
    if str(link.get("approved_by") or "") != "OWNER_ENGINEER_DECISION":
        return _число(link.get("effective_geometry_length_cm"))
    сурова = _число(link.get("geometry_length_raw"))
    разлика = _число(link.get("raw_numeric_difference"))
    обявена = _число(link.get("declared_length_cm"))
    if сурова is None or разлика is None or обявена is None:
        return None
    if abs(abs(сурова - обявена) - abs(разлика)) > 0.01:
        return None
    return сурова


def deviation_note(link: Mapping[str, Any]) -> str:
    """Обосновката за жълтия ред от одобрената връзка — и за ръчно решените.

    Само за прав прът с начертана геометрия: при огънатите форми символът
    нарочно не е в мащаб и разликата не е отклонение.
    """

    записано = link.get(FIELD)
    if isinstance(записано, Mapping) and записано.get("note"):
        return str(записано["note"])
    if str(link.get("master_shape_code") or "").strip().upper() != "F001":
        return ""
    if not [х for х in (link.get("geometry_handles") or ()) if str(х)]:
        return ""
    производствена = _число(link.get("production_length_cm")) or _число(link.get("declared_length_cm"))
    начертан = _начертана_дължина(link, производствена)
    if производствена is None or начертан is None or начертан <= 0:
        return ""
    if abs(начертан - производствена) <= ДОПУСК_CM:
        return ""
    return obosnovka(round(начертан, 1), производствена)
