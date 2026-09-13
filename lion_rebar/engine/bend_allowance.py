"""Добавка за огъване — ЕДИН източник на истина.

ПРАВИЛО (Product Owner, 2026-08-25)
-----------------------------------
В описа влиза това, което е НАДПИСАНО в чертежа. При затворено стреме
проектантът изписва страните, а общата дължина включва добавка за
огъвките. Затова сборът на страните е по-МАЛЪК от надписаната дължина:

    56N8x115 -> стреме 25 x 20, кука 10 -> 2*(25+20)+2*10 = 110 при L=115

Разликата е отговорност на проектанта и не е повод за отказ.

ЗАЩО Е ОТДЕЛЕН МОДУЛ
--------------------
Същата проверка съществуваше на ПЕТ различни места: диалога за инженерно
решение, пътя за запис, MASTER формулата, обвивката към Engine-а и Excel
експорта. Поправянето им едно по едно означаваше, че операторът удря
следваща преграда след всеки ъпдейт — седем поредни версии за един и същ
случай.

Всяко място вика ``segments_match_total``. Ново място задължително също.

ГРАНИЦИ (асиметрични, умишлено)
-------------------------------
* недостиг до 8 cm при F006 -> приема се (добавка за огъване);
* недостиг над 8 cm -> отказ (сгрешени размери);
* НАДВИШАВАНЕ -> отказ при всяка форма (не е добавка, а грешка).
"""
from __future__ import annotations

from typing import Iterable

#: Максимална добавка за огъване при затворено стреме.
#: Измерена в реален чертеж: при 43 от 60 стремета точно 5 cm,
#: единични случаи 4 и 6.
BEND_ALLOWANCE_LIMIT_CM = 8.0

#: Форми, при които добавката е допустима. Само затвореното стреме:
#: при отворените форми сборът на сегментите Е производствената дължина.
BEND_ALLOWANCE_SHAPES = frozenset({"F006"})


def allowance_for(shape_code: str | None) -> float:
    """Допустимият НЕДОСТИГ за дадена форма."""

    code = str(shape_code or "").strip().upper()
    return BEND_ALLOWANCE_LIMIT_CM if code in BEND_ALLOWANCE_SHAPES else 0.0


def segments_match_total(
    shape_code: str | None,
    segments_total: float,
    production_total: float,
    *,
    tolerance: float = 0.51,
) -> bool:
    """Приемлив ли е сборът на сегментите спрямо производствената дължина.

    ``tolerance`` е симетричният допуск за закръгляне; добавката за огъване
    се прилага ОТГОРЕ на него и само в посока недостиг.
    """

    try:
        difference = float(segments_total) - float(production_total)
    except (TypeError, ValueError):
        return False
    if difference > tolerance:
        return False
    return -(tolerance + allowance_for(shape_code)) <= difference


def explain_mismatch(
    shape_code: str | None,
    segments_total: float,
    production_total: float,
    *,
    tolerance: float = 0.51,
) -> str:
    """Човешко обяснение защо сборът не е приемлив. Празно = приемлив е."""

    if segments_match_total(shape_code, segments_total, production_total,
                            tolerance=tolerance):
        return ""
    difference = float(segments_total) - float(production_total)
    if difference > tolerance:
        return (
            f"Сборът на сегментите ({float(segments_total):g} cm) НАДВИШАВА "
            f"общата дължина ({float(production_total):g} cm) с "
            f"{difference:g} cm."
        )
    allowance = allowance_for(shape_code)
    if allowance:
        return (
            f"Сборът на сегментите ({float(segments_total):g} cm) е по-малък от "
            f"общата дължина ({float(production_total):g} cm) с {-difference:g} cm "
            f"— повече от допустимата добавка за огъване ({allowance:g} cm)."
        )
    return (
        f"Сборът на сегментите ({float(segments_total):g} cm) не съвпада с "
        f"общата дължина ({float(production_total):g} cm)."
    )


def sum_of(values: Iterable) -> float:
    total = 0.0
    for value in values or ():
        try:
            total += float(value)
        except (TypeError, ValueError):
            continue
    return total
