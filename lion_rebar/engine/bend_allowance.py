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
* недостиг до 8 cm при F006 (затворено стреме) -> приема се;
* недостиг до 2.1 cm при F009 (отворено стреме с четири огъвки) -> приема се;
* недостиг над тавана за формата -> отказ (сгрешени размери);
* НАДВИШАВАНЕ -> отказ при всяка форма (не е добавка, а грешка).

Таваните са в ``BEND_ALLOWANCE_LIMITS_CM``; форма извън него няма добавка.
"""
from __future__ import annotations

from typing import Iterable

#: Максимална добавка за огъване при затворено стреме.
#: Измерена в реален чертеж: при 43 от 60 стремета точно 5 cm,
#: единични случаи 4 и 6.
BEND_ALLOWANCE_LIMIT_CM = 8.0

#: Добавка при ОТВОРЕНОТО стреме с четири огъвки (F009).
#:
#: Собственик, 19.09.2026, за борда ``1180N8x190``: „трябва да има как да се
#: зададе този механизъм да е 8/79/15/79/8, а не за 1 сантиметър да не …
#: защото по чертеж си е така, но нашата програма не дава."
#:
#: Основанието е физиката, не изключение: F009 се огъва ЧЕТИРИ пъти на 90°,
#: точно както затвореното стреме, и дължината, която проектантът надписва,
#: включва същата добавка. Правилото „при отворените форми сборът Е
#: дължината" важи за правия прът и за формите с една огъвка, не за
#: четирикратно огънатото стреме.
#:
#: Границата е нарочно МНОГО по-тясна от 8 cm на F006: измерен е един реален
#: случай — 8+79+15+79+8 = 189 при надписано L=190, тоест 1 cm при четири
#: огъвки. До повече измерени случаи 2.1 cm приема него с резерв: заедно с
#: симетричния допуск за закръгляне (0.51) минава недостиг до 2.61 cm, а
#: размер, сгрешен с 3 cm, се отказва. Същата стойност MASTER формулата
#: вече ползва като допуск за отклонение при F006.
OPEN_STIRRUP_BEND_ALLOWANCE_LIMIT_CM = 2.1

#: Форми, при които добавката е допустима, и нейният таван за всяка.
#: При останалите отворени форми сборът на сегментите Е производствената
#: дължина и разлика няма да бъде приета.
BEND_ALLOWANCE_LIMITS_CM: dict[str, float] = {
    "F006": BEND_ALLOWANCE_LIMIT_CM,
    "F009": OPEN_STIRRUP_BEND_ALLOWANCE_LIMIT_CM,
}

#: Запазено име за обратна съвместимост на вносителите.
BEND_ALLOWANCE_SHAPES = frozenset(BEND_ALLOWANCE_LIMITS_CM)


def allowance_for(shape_code: str | None) -> float:
    """Допустимият НЕДОСТИГ за дадена форма."""

    code = str(shape_code or "").strip().upper()
    return BEND_ALLOWANCE_LIMITS_CM.get(code, 0.0)


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
