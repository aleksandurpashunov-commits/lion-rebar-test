"""Разногласия — постоянен изход след пакетния анализ.

ЗАЩО. Собственик, 23.09.2026: „по-скоро се питам дали няма нещо градивно, което
да ни помогне за в бъдеще“. Всичко открито през тази седмица дойде от сверка с
нещо, което чертежът ВЕЧЕ казва — добавката за огъване, надписаните коти срещу
измерените участъци, ведомостта на проектанта, двата еднакви кандидата. Нито
едно не дойде от измислено правило, и всяко отне по ден ръчна работа.

Тук същото се прави машинно, при всеки анализ, за чертежа пред оператора.

ПРАВИЛОТО, КОЕТО СИ ПЛАТИХМЕ. Разногласие е това, което програмата НЕ МОЖЕ да
обясни. Първата редакция на инструмента в ``tools/razminavaniya.py`` сверяваше
с плосък допуск ±0,51 и закръгляне до 5, обяви 2547 находки — и всичките се
оказаха F006 стремета, които програмата обяснява с документираната си добавка.
Мярката беше по-груба от програмата и преоткриваше вече взети решения. Затова
всяка проверка тук пита самата програма: ``segments_match_total`` за дължините,
дефиницията на формата за броя участъци.

КОИ ПРОВЕРКИ ВЛИЗАТ. Само трите, измерени на 23.09.2026 върху 29 151 позиции от
125 проекта със **100% съгласуваност** — тоест всяка находка се обяснява с една
и съща хипотеза, а не с разпиляна шума:

===  ====================  =======  ==============
код  проверка              обхват   съгласуваност
===  ====================  =======  ==============
R2   коти ↔ измерено          2161            100%
R4   спор за геометрия         283            100%
R5   брой участъци             135            100%
===  ====================  =======  ==============

``R1`` (74%) и ``R6`` (67%) НЕ влизат: разпределението на разликите им е
разпиляно, тоест те сочат дефекти, не правило, и още нямат едно обяснение.
``R3`` е 0 находки в целия корпус. И трите остават в инструмента, където
корпусът се мери наведнъж.

ТОЗИ МОДУЛ НЕ МЕНИ ИНЖЕНЕРЕН РЕЗУЛТАТ. Само чете готовите редове на отчета;
нито една позиция не сменя статуса, размерите или одобрението си заради него.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from lion_rebar.engine.bend_allowance import segments_match_total
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.export.engine_shape_parameters import expected_segment_codes

SCHEMA = "lion-rebar-disagreement-report/v1"

#: Под колко cm разминаването с най-близката кота е шум от обхождането, а не
#: участък без своя кота. Мерено е в cm, тоест 0,5 cm.
_DIMENSION_NOISE_CM = 0.5

#: Точното съвпадение с кота — под него нищо не се брои за разминаване.
#: Същият праг, с който ``production_segment_precision`` реже до 0,1 mm.
_DIMENSION_EXACT_CM = 0.011


@dataclass(frozen=True)
class Disagreement:
    """Една находка: коя проверка се е обадила, за коя позиция и защо."""

    check: str
    kind: str
    explanation: str
    annotation_handle: str
    annotation_text: str
    batch_status: str


def _число(value: Any) -> float | None:
    try:
        число = float(value)
    except (TypeError, ValueError):
        return None
    return число if math.isfinite(число) else None


def _вектор(value: Any) -> list[float]:
    изход: list[float] = []
    for item in value or ():
        число = _число(item)
        if число is not None:
            изход.append(число)
    return изход


def r2_printed_dimensions(
    row: Mapping[str, Any],
    shapes: Mapping[str, ShapeDefinition],
) -> tuple[str, str] | None:
    """Всеки производствен участък съвпада ли с НАДПИСАНА до него кота.

    Проектантът е написал котите; програмата е измерила участъците. Когато
    двете се разминават с по-малко от половин сантиметър, това е измереното,
    изместило надписаното — точно случаят „50 · 16,9706 · 60“ при надписани
    50/17/60. Когато участъкът няма никаква своя кота, източникът е един.
    """

    сегменти = _вектор(row.get("production_segments_cm"))
    коти = _вектор(row.get("nearby_dimensions_cm"))
    if len(сегменти) < 2 or not коти:
        return None
    без_кота = 0
    шум = 0
    for стойност in сегменти:
        най_близка = min(коти, key=lambda кота: abs(кота - стойност))
        разлика = abs(най_близка - стойност)
        if разлика <= _DIMENSION_EXACT_CM:
            continue
        if разлика <= _DIMENSION_NOISE_CM:
            шум += 1
        else:
            без_кота += 1
    if шум and not без_кота:
        return (
            "ИЗМЕРЕНО_ВМЕСТО_НАДПИСАНО",
            f"{шум} участъка се разминават с котата под 0,5 cm",
        )
    if без_кота:
        return ("УЧАСТЪК_БЕЗ_КОТА", f"{без_кота} участъка нямат своя кота")
    return None


def r4_geometry_dispute(
    row: Mapping[str, Any],
    shapes: Mapping[str, ShapeDefinition],
) -> tuple[str, str] | None:
    """Една и съща геометрия, поискана от повече от един надпис.

    Това е случаят от 23.09 (`г.а. 4N14x310` и `д.а. 4N16x310` върху един
    обект). Отчита се, защото операторът трябва да го види — не се решава тук.
    """

    if not row.get("global_conflict"):
        return None
    брой = len([дръжка for дръжка in (row.get("candidate_handles") or ()) if str(дръжка)])
    return ("СПОР_ЗА_ГЕОМЕТРИЯ", f"глобален конфликт при {брой or 1} кандидата")


def r5_segment_count(
    row: Mapping[str, Any],
    shapes: Mapping[str, ShapeDefinition],
) -> tuple[str, str] | None:
    """Броят производствени участъци срещу това, което формата ОЧАКВА.

    Очакваното не се изброява тук: чете се от дефиницията на формата в
    активния engine (``expected_segment_codes``). Затова важи и за формите,
    които заключеният MASTER модул не познава — родените от случай.
    """

    код = str(row.get("master_shape_code") or "").strip().upper()
    сегменти = _вектор(row.get("production_segments_cm"))
    if not код or not сегменти:
        return None
    форма = shapes.get(код)
    if форма is None:
        return None
    очаквани = len(expected_segment_codes(форма))
    if not очаквани or len(сегменти) == очаквани:
        return None
    return ("БРОЙ_УЧАСТЪЦИ", f"{код} очаква {очаквани}, а са {len(сегменти)}")


def r3_total_vs_declared(
    row: Mapping[str, Any],
    shapes: Mapping[str, ShapeDefinition],
) -> tuple[str, str] | None:
    """Сборът на участъците срещу обявената дължина — по правилата на програмата.

    НЕ влиза в постоянния изход: в целия корпус от 29 151 позиции дава нула
    находки. Стои тук, защото е най-силната проверка, която имаме, и трябва да
    се обади веднага щом някога проговори. ``build_disagreement_report`` я
    включва само когато викащият изрично я поиска.
    """

    сегменти = _вектор(row.get("production_segments_cm"))
    обявена = _число(row.get("production_length_cm")) or _число(
        row.get("declared_length_cm")
    )
    if len(сегменти) < 2 or обявена is None or обявена <= 0:
        return None
    код = str(row.get("master_shape_code") or "").strip().upper() or None
    сбор = math.fsum(сегменти)
    if segments_match_total(код, сбор, обявена):
        return None
    разлика = сбор - обявена
    if разлика > 0:
        return ("СБОРЪТ_НАДВИШАВА", f"сборът е с {разлика:.2f} cm повече")
    return ("СБОРЪТ_НЕ_ЗАТВАРЯ", f"недостиг {-разлика:.2f} cm, необяснен")


Проверка = Callable[[Mapping[str, Any], Mapping[str, ShapeDefinition]], "tuple[str, str] | None"]

#: Проверките в постоянния изход, по реда, в който се показват.
CHECKS: dict[str, Проверка] = {
    "R2 коти↔измерено": r2_printed_dimensions,
    "R4 спор за геометрия": r4_geometry_dispute,
    "R5 брой участъци": r5_segment_count,
}

#: Проверка, която още не се е обаждала в корпуса. Вкарва се по избор.
OPTIONAL_CHECKS: dict[str, Проверка] = {
    "R3 сбор↔надпис": r3_total_vs_declared,
}


def _shape_index(
    shape_definitions: Iterable[ShapeDefinition] | None,
) -> dict[str, ShapeDefinition]:
    индекс: dict[str, ShapeDefinition] = {}
    for форма in shape_definitions or ():
        код = str(getattr(форма, "code", "") or "").strip().upper()
        if код and код not in индекс:
            индекс[код] = форма
    return индекс


def disagreements_for_row(
    row: Mapping[str, Any],
    *,
    shapes: Mapping[str, ShapeDefinition],
    checks: Mapping[str, Проверка] | None = None,
) -> list[Disagreement]:
    """Всички находки за един ред. Проверките НЕ се изключват една друга."""

    изход: list[Disagreement] = []
    for име, проверка in (checks or CHECKS).items():
        резултат = проверка(row, shapes)
        if резултат is None:
            continue
        вид, обяснение = резултат
        изход.append(
            Disagreement(
                check=име,
                kind=вид,
                explanation=обяснение,
                annotation_handle=str(row.get("annotation_handle") or ""),
                annotation_text=str(row.get("annotation_text") or ""),
                batch_status=str(row.get("batch_status") or ""),
            )
        )
    return изход


def build_disagreement_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    shape_definitions: Iterable[ShapeDefinition] | None = None,
    include_optional: bool = False,
) -> dict[str, Any]:
    """Отчетът за един чертеж: три числа на проверка плюс самите находки.

    Мерят се само редовете ``ACTIONABLE`` — истинските позиции с брой, Ø и
    дължина. Контекстните надписи нямат производствени размери и биха раздули
    знаменателя, без да носят източник за сверка.
    """

    проверки = dict(CHECKS)
    if include_optional:
        проверки.update(OPTIONAL_CHECKS)
    shapes = _shape_index(shape_definitions)

    находки: list[Disagreement] = []
    видове: dict[str, Counter] = {име: Counter() for име in проверки}
    обхват = 0
    засегнати = 0
    for ред in rows:
        if str(ред.get("annotation_class") or "") != "ACTIONABLE":
            continue
        обхват += 1
        редови = disagreements_for_row(ред, shapes=shapes, checks=проверки)
        if редови:
            засегнати += 1
        for находка in редови:
            видове[находка.check][находка.kind] += 1
        находки.extend(редови)

    по_проверка: dict[str, dict[str, Any]] = {}
    for име in проверки:
        брояч = видове[име]
        сбор = sum(брояч.values())
        водещ, дял = ("—", 0.0)
        if сбор:
            водещ, брой = брояч.most_common(1)[0]
            дял = брой / сбор
        по_проверка[име] = {
            "findings": сбор,
            "consistency": round(дял, 4),
            "dominant_kind": водещ,
            "kinds": dict(брояч),
        }

    return {
        "schema": SCHEMA,
        "scope_positions": обхват,
        "affected_positions": засегнати,
        "checks": по_проверка,
        "findings": [
            {
                "check": н.check,
                "kind": н.kind,
                "explanation": н.explanation,
                "annotation_handle": н.annotation_handle,
                "annotation_text": н.annotation_text,
                "batch_status": н.batch_status,
            }
            for н in находки
        ],
    }


def disagreement_summary_lines(report: Mapping[str, Any]) -> list[str]:
    """Редовете за README_BG.txt — същите три числа, на човешки език."""

    обхват = int(report.get("scope_positions") or 0)
    засегнати = int(report.get("affected_positions") or 0)
    редове = [
        "Разногласия (два независими източника в чертежа не се съгласяват)",
        f"  позиции за действие: {обхват}; с поне едно разногласие: {засегнати}",
    ]
    for име, данни in (report.get("checks") or {}).items():
        брой = int(данни.get("findings") or 0)
        if not брой:
            редове.append(f"  {име}: няма находки")
            continue
        редове.append(
            f"  {име}: {брой} находки, "
            f"{данни.get('consistency', 0.0) * 100:.0f}% с едно обяснение "
            f"({данни.get('dominant_kind')})"
        )
    редове.append(
        "  Постоянно разногласие = правило, което още не знаем. "
        "Разпиляно = дефект."
    )
    return редове
