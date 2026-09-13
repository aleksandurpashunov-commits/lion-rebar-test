"""P1-4 (търсене и preview на MASTER форми) и P1-5 (segment refresh).

P1-5 — ЗАЩО СЪЩЕСТВУВА
----------------------
В диалога за инженерно решение сегментите се предзареждат от текущия
кандидат. При смяна на MASTER формата полето остава непроменено, затова
новата форма може да наследи чужди сегменти от предишната. Пример:
позиция е предложена като F003 със сегменти ``650 + 50``; инженерът я
сменя на F006 и полето все още показва ``650 + 50``, което не е валидно
стреме.

Правилото тук е нарочно консервативно: при смяна на форма сегментите се
ИЗЧИСТВАТ, освен когато новата форма приема точно същия брой сегменти и
сборът им съвпада с производствената дължина. Никога не се преизчисляват
наум и не се допълват с измислени стойности.

Модулът е без Qt, за да е тестваем без UI среда.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

TOTAL_TOLERANCE_CM = 0.51


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


def _fold(value: Any) -> str:
    """Нормализация за търсене: главни, без диакритика, без пунктуация."""

    text = unicodedata.normalize("NFKD", _text(value).upper())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^0-9A-ZА-Я ]+", " ", text).strip()


# --------------------------------------------------------------- P1-4


@dataclass(frozen=True)
class ShapeChoice:
    code: str
    name: str
    segment_count: int
    active: bool
    state: str
    preview: str

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name,
            "segment_count": self.segment_count, "active": self.active,
            "state": self.state, "preview": self.preview,
        }


def _segment_count(shape: Any) -> int:
    recognition = getattr(shape, "recognition", None)
    for source in (recognition, shape):
        if source is None:
            continue
        value = getattr(source, "segment_count", None)
        if value is None and isinstance(source, Mapping):
            value = source.get("segment_count")
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return 0


def shape_preview(code: str, segment_count: int) -> str:
    """Текстова скица на формата — достатъчна за разпознаване в списък."""

    previews = {
        "F001": "───────────",
        "F002": "──────╲─────",
        "F003": "│___________│",
        "F004": "│___________",
        "F005": "╰───────────╯",
        "F006": "▭ (затворено стреме)",
        "F007": "╭─╮ ЕСО",
    }
    if code in previews:
        return previews[code]
    if segment_count <= 1:
        return "───────────"
    if segment_count == 2:
        return "│___________"
    if segment_count == 3:
        return "│___________│"
    return f"◺ {segment_count} сегмента"


def available_shapes(engine: Any, *, include_retired: bool = False) -> list[ShapeChoice]:
    """ВСИЧКИ активни MASTER форми от Engine-а — динамично, без hardcode.

    Няма таван F008: списъкът идва от engine.shapes.
    """

    choices: list[ShapeChoice] = []
    for shape in getattr(engine, "shapes", ()) or ():
        state = _text(getattr(shape, "state", "")).upper()
        active = bool(getattr(shape, "active", False)) and state != "RETIRED"
        if not active and not include_retired:
            continue
        code = _text(getattr(shape, "code", "")).upper()
        if not code:
            continue
        count = _segment_count(shape)
        choices.append(ShapeChoice(
            code=code, name=_text(getattr(shape, "name", "")),
            segment_count=count, active=active, state=state or "ACTIVE",
            preview=shape_preview(code, count)))
    choices.sort(key=lambda item: item.code)
    return choices


def search_shapes(choices: Sequence[ShapeChoice], query: str) -> list[ShapeChoice]:
    """Търсене по F-код или по име; празна заявка връща всичко."""

    needle = _fold(query)
    if not needle:
        return list(choices)
    exact = [c for c in choices if _fold(c.code) == needle]
    if exact:
        return exact
    tokens = needle.split()
    result = []
    for choice in choices:
        blob = f"{_fold(choice.code)} {_fold(choice.name)}"
        if all(token in blob for token in tokens):
            result.append(choice)
    return result


# --------------------------------------------------------------- P1-5


@dataclass(frozen=True)
class SegmentRefresh:
    segments: tuple[float, ...]
    cleared: bool
    reason: str

    def to_dict(self) -> dict:
        return {"segments": list(self.segments), "cleared": self.cleared,
                "reason": self.reason}


def _numbers(values: Iterable[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values or ():
        try:
            out.append(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def refresh_segments_for_shape(
    *,
    previous_shape_code: str,
    new_shape_code: str,
    current_segments: Iterable[Any],
    production_length_cm: Any,
    new_shape_segment_count: int = 0,
) -> SegmentRefresh:
    """Определя какво да стане със сегментите при смяна на MASTER форма.

    Правила:
      * същата форма -> нищо не се пипа;
      * F001 без сегменти -> сегментът е самата дължина;
      * различна форма и несъвпадащ брой сегменти -> ИЗЧИСТВАНЕ;
      * различна форма, съвпадащ брой, но сборът не отговаря на L ->
        ИЗЧИСТВАНЕ;
      * различна форма, съвпадащ брой и коректен сбор -> запазване.
    """

    previous = _text(previous_shape_code).upper()
    new = _text(new_shape_code).upper()
    segments = _numbers(current_segments)
    try:
        length = float(str(production_length_cm).replace(",", "."))
    except (TypeError, ValueError):
        length = 0.0

    if new and previous == new:
        return SegmentRefresh(segments, False, "SAME_SHAPE")
    if new == "F001":
        if length > 0:
            return SegmentRefresh((length,), False, "F001_SINGLE_SEGMENT")
        return SegmentRefresh((), True, "F001_WITHOUT_LENGTH")
    if not segments:
        return SegmentRefresh((), True, "NO_SEGMENTS_TO_CARRY")
    if new_shape_segment_count and len(segments) != new_shape_segment_count:
        return SegmentRefresh(
            (), True,
            f"SEGMENT_COUNT_MISMATCH:{len(segments)}!={new_shape_segment_count}")
    if length > 0 and abs(sum(segments) - length) > TOTAL_TOLERANCE_CM:
        return SegmentRefresh(
            (), True,
            f"TOTAL_MISMATCH:{sum(segments):g}!={length:g}")
    if not new_shape_segment_count:
        # Не знаем контракта на новата форма -> не пренасяме наследени стойности.
        return SegmentRefresh((), True, "UNKNOWN_TARGET_SEGMENT_COUNT")
    return SegmentRefresh(segments, False, "CARRIED_OVER_VERIFIED")
