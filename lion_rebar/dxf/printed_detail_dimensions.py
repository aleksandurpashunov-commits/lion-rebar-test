from __future__ import annotations

"""Форма и производствени участъци от надписаните коти на локалния детайл.

ЗАЩО
----
Този проектант чертае стремето СХЕМАТИЧНО (не в мащаб), а размерите му
надписва като отделни текстове до самата фигура: ``165`` до рамото, ``10``
до куката. Позицията носи пълния надпис (``11N8x390``), а до нея стои
префиксът ``стр.:``. Matcher-ът отказва такава фигура — начертаната дължина
не отговаря на L (Закон 6) — затова позицията оставаше „НЯМА КАНДИДАТ" с
празен панел, макар размерите ѝ да са в чертежа.

ПРИНЦИП
-------
Общата дължина от надписа е АВТОРИТЕТ. Надписаните коти казват кои размери
са налице; най-много ЕДИН размер се изчислява от уравнението — същият
принцип, по който работи ``vertical_shape_resolver``.

Семейството на формата НЕ се гадае по числата: то идва от вече доказания
локален детайл (``local_detail_resolver`` свързва префикса ``стр.:``
еднозначно с надписа и дава F006). Принадлежността на котите също е
доказана там — най-близък собственик с изричен отстъп — затова съседното
``20`` на есовете остава при есовете, а не влиза в стремето.

ДВА ПРОЧИТА, ИЗМЕРЕНИ В „Сграда бета\\Плоча_кота 14.90"
--------------------------------------------------------
* ДВЕ коти → затворено стреме **F006**. Третият размер се изчислява от
  ``L = 2*(кука + страна1 + страна2)``, а куката е най-късият от трите.
  ``11N8x390`` (×3): надписани 165 и 10 → ``[10, 165, 20, 165, 20, 10]``
  = 390. Съвпада с решението на собственика от 19.09.2026.
  ``стр.: N8x166`` (щурците): надписани 40 и 33 → ``[10, 40, 33, 40, 33, 10]``
  = 166, тук изчисленият размер е самата кука.
* ПЕТ коти в симетричен набор ``{x:2, y:2, z:1}`` → отворено стреме **F010**
  ``[x, y, z, y, x]``. Крайните участъци са по-късата двойка: краче/кука.
  ``60N8x182``: надписани 7, 7, 79, 79, 10 → ``[7, 79, 10, 79, 7]`` = 182.
  Редът е проверен срещу НАЧЕРТАНАТА фигура: двете ``7`` са долните крачета
  (18 единици), ``79`` са рамената (154/156), ``10`` е горната връзка (14).

Защо F010, а не F008 (столче): двете форми приемат един и същ петсегментен
набор и по размери не се различават. Разделя ги префиксът — ``стр.`` значи
стреме, а не столче — и точно затова правилото изисква доказан детайл
``стр.``, вместо да избира форма по числата.

ОТКАЗ, НЕ НАГЛАСЯНЕ
-------------------
Бордът ``1180N8x190`` надписва 8, 8, 79, 79, 15 = **189** при L=190. Не се
затваря → отказ с обяснение. Дали куката е 8.5, или надписът е закръглен,
решава само собственикът (той я реши като 8.5 на 19.09.2026); програмата не
„оправя" един участък, за да излезе сборът.

Същата позиция показва и защо ПЕТ коти никога не се свеждат до две:
подмножеството ``{8, 79}`` затваря F006 — ``2*(8+79+8) = 190`` — и би
обявило ГРЕШНА затворена форма на отворено стреме. Затова броят на
надписаните коти е част от прочита, а не само сборът им.
"""

import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from lion_rebar.dxf.models import DxfEntityRecord

#: Надписана кота: цяло или дробно число, само по себе си. ``s=20`` (стъпка)
#: и ``~ L ~`` умишлено НЕ са коти.
_PRINTED_NUMBER_RE = re.compile(r"^(\d{1,4})(?:[.,](\d{1,2}))?$")

#: Точност на уравнението. Надписаните размери са кръгли производствени
#: числа; разлика над това не е закръгляне, а друга фигура.
CLOSURE_TOLERANCE_CM = 0.05

#: Минимален смислен производствен участък (както в ``vertical_shape_resolver``).
MIN_SEGMENT_CM = 4.0

#: До това разминаване отказът се обяснява с числата (бордът: 189 срещу 190).
#: Над него котите просто не са на тази позиция и няма какво да се докладва.
NEAR_MISS_REPORT_LIMIT_CM = 3.0


@dataclass(frozen=True)
class PrintedDimension:
    """Една надписана кота, с handle-а си за проследимост."""

    handle: str
    value_cm: float


@dataclass(frozen=True)
class PrintedShape:
    """Прочетена форма с участъците и доказателството зад тях."""

    shape_code: str
    segments: tuple[float, ...]
    printed_values_cm: tuple[float, ...]
    computed_value_cm: float | None
    reading: str

    @property
    def total_cm(self) -> float:
        return math.fsum(self.segments)


def printed_dimension_value(text: str) -> float | None:
    """Стойността на надписана кота или ``None``, ако текстът не е кота."""

    value = " ".join(str(text or "").split())
    value = value.replace("%%U", "").replace("%%u", "").strip()
    if not _PRINTED_NUMBER_RE.match(value):
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:  # pragma: no cover - регулярният израз вече го пази
        return None


def printed_dimensions_for_detail(
    dimension_handles: Sequence[str],
    entity_map: Mapping[str, DxfEntityRecord],
    *,
    layer_aliases: Sequence[str],
) -> tuple[PrintedDimension, ...]:
    """Надписаните коти на детайла, четени от собствените му текстове.

    Приемат се само ``TEXT``/``MTEXT`` на слоевете за спецификации: там
    проектантът надписва размерите. ``DIMENSION`` обект носи ИЗМЕРЕНА
    стойност от схематичния чертеж и има свой, отделен път
    (``vertical_shape_resolver``).
    """

    allowed = {str(alias).strip().upper() for alias in layer_aliases if str(alias).strip()}
    result: list[PrintedDimension] = []
    for handle in dimension_handles:
        record = entity_map.get(str(handle))
        if record is None or str(record.entity_type or "").upper() not in {"TEXT", "MTEXT"}:
            continue
        if str(record.layer or "").strip().upper() not in allowed:
            continue
        value = printed_dimension_value(record.text or "")
        if value is None or value < MIN_SEGMENT_CM:
            continue
        result.append(PrintedDimension(handle=str(handle), value_cm=value))
    return tuple(sorted(result, key=lambda item: (item.value_cm, item.handle)))


def shape_from_printed_dimensions(
    values_cm: Sequence[float],
    declared_length_cm: float,
    *,
    hook_min_cm: float,
    hook_max_cm: float,
) -> PrintedShape | None:
    """Прочита формата от надписаните коти или отказва.

    Без нагласяне: приема се само точното затваряне на производственото
    уравнение при точния брой коти.
    """

    total = float(declared_length_cm or 0.0)
    if not math.isfinite(total) or total <= 0:
        return None
    values = tuple(float(value) for value in values_cm)
    if any(not math.isfinite(value) or value < MIN_SEGMENT_CM for value in values):
        return None
    if len(values) == 2:
        return _closed_stirrup_from_two(values, total, hook_min_cm, hook_max_cm)
    if len(values) == 5:
        return _open_stirrup_from_five(values, total)
    return None


def _closed_stirrup_from_two(
    values: tuple[float, ...],
    total: float,
    hook_min_cm: float,
    hook_max_cm: float,
) -> PrintedShape | None:
    """F006 от две надписани коти; третият размер е от уравнението."""

    first, second = sorted(values)
    computed = total / 2.0 - first - second
    if computed < MIN_SEGMENT_CM:
        return None
    sizes = sorted((first, second, computed))
    hook, side_b, side_a = sizes
    if not hook_min_cm <= hook <= hook_max_cm:
        return None
    segments = (hook, side_a, side_b, side_a, side_b, hook)
    if abs(math.fsum(segments) - total) > CLOSURE_TOLERANCE_CM:
        return None
    return PrintedShape(
        shape_code="F006",
        segments=segments,
        printed_values_cm=tuple(sorted(values)),
        computed_value_cm=computed,
        reading=(
            f"две надписани коти {first:g} и {second:g}; третият размер "
            f"{computed:g} cm идва от уравнението L=2×(кука+страна+страна)"
        ),
    )


def _open_stirrup_from_five(
    values: tuple[float, ...],
    total: float,
) -> PrintedShape | None:
    """F010 от пет надписани коти в симетричен набор."""

    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    pairs = sorted(value for value, number in counts.items() if number >= 2)
    singles = sorted(value for value, number in counts.items() if number % 2 == 1)
    if len(pairs) == 1 and counts[pairs[0]] == 4 and len(singles) == 1:
        # Четири еднакви и една различна: крайните и рамената са с един размер.
        end, leg = pairs[0], pairs[0]
        middle = singles[0]
    elif len(pairs) == 2 and all(counts[value] == 2 for value in pairs) and len(singles) == 1:
        end, leg = pairs[0], pairs[1]
        middle = singles[0]
    else:
        return None
    segments = (end, leg, middle, leg, end)
    if abs(math.fsum(segments) - total) > CLOSURE_TOLERANCE_CM:
        return None
    return PrintedShape(
        shape_code="F010",
        segments=segments,
        printed_values_cm=tuple(sorted(values)),
        computed_value_cm=None,
        reading=(
            f"пет надписани коти в симетричен набор {end:g}+{leg:g}+{middle:g}"
            f"+{leg:g}+{end:g}; крайните участъци са по-късата двойка"
        ),
    )


def near_miss_note(
    values_cm: Sequence[float],
    declared_length_cm: float,
) -> str | None:
    """Обяснение, когато котите почти затварят, но не съвпадат.

    Реален случай (бордът ``1180N8x190``): 8+79+15+79+8 = 189 при L=190.
    Операторът трябва да вижда разликата, за да реши, вместо да гадае защо
    позицията е отказана.
    """

    total = float(declared_length_cm or 0.0)
    values = tuple(float(value) for value in values_cm)
    if not math.isfinite(total) or total <= 0 or len(values) != 5:
        return None
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    pairs = sorted(value for value, number in counts.items() if number >= 2)
    singles = sorted(value for value, number in counts.items() if number % 2 == 1)
    if not pairs or len(singles) != 1:
        return None
    end = pairs[0]
    leg = pairs[-1] if len(pairs) == 2 else pairs[0]
    middle = singles[0]
    printed_total = math.fsum((end, leg, middle, leg, end))
    difference = printed_total - total
    if abs(difference) <= CLOSURE_TOLERANCE_CM or abs(difference) > NEAR_MISS_REPORT_LIMIT_CM:
        return None
    return (
        "НАДПИСАНИТЕ КОТИ НЕ ЗАТВАРЯТ: "
        f"{end:g}+{leg:g}+{middle:g}+{leg:g}+{end:g} = {printed_total:g} cm "
        f"срещу L={total:g} cm (разлика {difference:+g} cm). Кой участък е "
        "закръглен, е инженерно решение — формата остава неопределена."
    )
