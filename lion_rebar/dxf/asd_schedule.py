"""Ведомостта на AutoCAD Structural Detailing — НЕЗАВИСИМО доказателство.

Чертежите, направени с ASD, носят собствена армировъчна ведомост като ТЕКСТ на
слой ``…Reinforcement tables``.  Заглавията ѝ, подредени по x, са::

    позиция | N/Ø | диаметър | брой | дължина (cm) | Детайл (cm)

а един ред изглежда така (клетките са отделни TEXT/MTEXT обекта)::

    3   N   10   22   315        [малка рисунка с надписи 10 / 295 / 10]

тоест: позиция 3, марка ``N``, Ø10, **22 броя**, обявена дължина 315 cm, а
„Детайл" е МИНИАТЮРНА РИСУНКА на пръта — осева линия от отсечки и дъги плюс
размерни надписи — а не подреден списък от числа.

ЗАЩО Е ВАЖНО.  Това е числото на ПРОЕКТАНТА, а не на програмата.  Позволява
независима проверка на броя, диаметъра, дължината, формата и участъците.

КАКВО ТОЗИ МОДУЛ НЕ ПРАВИ — И НЕ БИВА ДА ПРАВИ
----------------------------------------------
Модулът е САМО ЗА ЧЕТЕНЕ.  Не променя нито една позиция, не одобрява и не
захранва разпознаването.  Ако ведомостта някога влезе във веригата на
разпознаването (кандидати, форми, количества, експорт), тя престава да е
независима отправна точка и проверката „проектант срещу програма" губи смисъл.
Затова изходът му е доказателство за оператора и за отчетите, нищо друго.

КАК СЕ ЧЕТЕ ТАБЛИЦАТА — ПО КООРДИНАТИ, НЕ ПО РЕД НА ДОКУМЕНТА
-------------------------------------------------------------
Предишната редакция вървеше по реда на документа и започваше нов ред, когато
видеше „<цяло число>, N".  Това чупи таблицата на три места:

* марката в колоната ``N/Ø`` не е винаги ``N``.  ASD пише символа Ø чрез
  превключване на шрифта (``\\Fdiam;0\\Fsimplex.shx;``); при превръщане в чист
  текст остава голо ``0``.  Ред с такава марка не се разпознаваше и клетките му
  изтичаха в предишния ред;
* клетките на „Детайл" не са подреден списък — те са размерните надписи на
  рисунката и стоят на различни y;
* редът на документа не е ред на таблицата.

Сега решетката се намира ГЕОМЕТРИЧНО: вертикалните линии на слоя дават
границите на колоните, хоризонталните — границите на редовете.  За граница се
приема само линия, която покрива почти цялата височина (съответно ширина) на
таблицата, така че рисунките в „Детайл" не се бъркат с рамка.  Всяка клетка се
отнася към (колона, ред) по своята опорна точка и носи произхода си: handle, x,
y, коя колона и защо.  Ред, който не се чете уверено, се връща като ``UNPARSED``
с причина — отказът е по-добър от догадката.

Колоната „Детайл" се връща като ``detail_labels`` (сурови стойности и позиции).
Подреден ``detail_cm`` се пълни само когато рисунката го доказва: отсечките се
навързват във верига през дъгите на огъвките и всеки надпис се свързва с точно
една отсечка (еднозначно съответствие + надписът е паралелен на своята
отсечка).  Иначе ``detail_cm`` остава празен и ``detail_reason`` казва защо.
Пример от ``d-2``: позиция 41 има 5 нарисувани участъка, но само 3 надписа
(20/20/14, сбор 54 при обявена дължина 88) — двата края на скобата са без
размер, затова подреждане не се твърди.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

SCHEDULE_SCHEMA = "lion-rebar-asd-schedule/v1"

#: Коя редакция на четенето е произвела резултата — за отчетите.
SCHEDULE_READER = "grid-coordinates/v2"

#: Слоят с ведомостта. Търси се по СЪДЪРЖАНИЕ на името, защото ASD слага
#: езиков суфикс, но „Reinforcement tables" е постоянно.
LAYER_MARKERS = ("REINFORCEMENT TABLES", "REINFORCEMENT TABLE")

#: Заглавията, които потвърждават, че това е точно тази ведомост.
HEADER_WORDS = ("позиция", "диаметър", "дължина", "детайл", "брой")

#: Заглавие на колона → роля.  Търси се по съдържание, защото ASD добавя
#: мерната единица („дължина (cm)").  Редът има значение: по-специфичното
#: („диаметър") се пробва преди по-общото.
COLUMN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("position", ("позиция",)),
    ("diameter", ("диаметър",)),
    ("length", ("дължина",)),
    ("detail", ("детайл",)),
    ("count", ("брой",)),
    ("mark", ("n/ø", "n/o", "n/")),
)

#: Задължителните роли.  Без тях не е ясно кой стълб какво е и се отказва.
REQUIRED_ROLES = ("position", "diameter", "count", "length")

#: Какво може да стои в колоната ``N/Ø``.  ``N`` е нервурана, символът Ø е
#: гладка армировка — ASD пише Ø чрез шрифта ``diam``, чийто глиф „0" изглежда
#: като Ø; ``plain_text()`` маха превключването на шрифта и оставя „0".
#: Значението на марката е проектна конвенция и НЕ се тълкува тук — връща се
#: суровата стойност и вид.
MARK_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("N", ("n",)),
    ("Ø", ("ø", "0", "o", "⌀", "⌀", "%%c")),
)

_INTEGER = re.compile(r"^\d{1,5}$")
_DECIMAL = re.compile(r"^\d{1,6}(?:[.,]\d+)?$")

#: Допуски.  Геометричният допуск е в единиците на чертежа и е нарочно малък:
#: краищата на отсечките и дъгите в ASD съвпадат точно.
_JOIN_TOL = 1.0
#: Колко градуса разминаване между надписа и неговата отсечка се приема.
_ANGLE_TOL_DEG = 10.0
#: Каква част от височината (ширината) на таблицата трябва да покрие една
#: линия, за да е граница на колона (ред), а не част от рисунка.
_GRID_COVERAGE = 0.8
#: Най-много участъци, за които се търси еднозначно съответствие с надписите.
#: Над това броят на разместванията става неразумен и се отказва.
_MAX_MATCH_SEGMENTS = 8


# --------------------------------------------------------------------------- #
# Опорна точка на текста
# --------------------------------------------------------------------------- #
def _anchor(record: Any) -> tuple[float, float, str] | None:
    """Центърът на текста плюс с какво е изчислен.

    ``importer`` пише ``visual_anchor``, но го смята като че ли ``insert`` е
    долният ляв край.  За MTEXT на ASD ``attachment_point`` е 5 (среда-център),
    тоест ``insert`` Е центърът, а ``visual_anchor`` е изместен с половин
    надпис.  За табличните клетки това е половин колона и половин ред — точно
    толкова, колкото стига да сложи клетка в чужд ред.  Затова тук опорната
    точка се смята от ``attachment_point`` (MTEXT), съответно от
    ``halign``/``valign`` и ``align_point`` (TEXT).
    """

    geometry = getattr(record, "geometry", None) or {}
    kind = str(getattr(record, "entity_type", "") or "").upper()
    insert = geometry.get("insert")
    width = _float(geometry.get("estimated_text_width"), 0.0)
    height = _float(geometry.get("estimated_text_height"), 0.0)

    if kind == "MTEXT":
        if not _is_point(insert):
            return None
        attachment = int(_float(geometry.get("attachment_point"), 1.0) or 1)
        attachment = min(9, max(1, attachment))
        column = (attachment - 1) % 3
        row = (attachment - 1) // 3
        x = float(insert[0]) + (width / 2.0, 0.0, -width / 2.0)[column]
        y = float(insert[1]) + (-height / 2.0, 0.0, height / 2.0)[row]
        return x, y, f"MTEXT attachment_point={attachment}"

    align_point = geometry.get("align_point")
    halign = int(_float(geometry.get("halign"), 0.0) or 0)
    valign = int(_float(geometry.get("valign"), 0.0) or 0)
    if _is_point(align_point) and (halign or valign):
        return (
            float(align_point[0]),
            float(align_point[1]),
            f"TEXT align_point halign={halign} valign={valign}",
        )
    if not _is_point(insert):
        return None
    return (
        float(insert[0]) + width / 2.0,
        float(insert[1]) + height / 2.0,
        "TEXT insert + половин надпис",
    )


def _is_point(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 2


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _number(text: str) -> float | None:
    text = str(text or "").strip().replace(",", ".")
    if not _DECIMAL.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Данни
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Cell:
    """Една клетка с произхода си — за да е проверимо всяко число."""

    handle: str
    text: str
    x: float
    y: float
    rotation: float = 0.0
    column_index: int = -1
    column_role: str = ""
    row_index: int = -1
    anchor_basis: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "text": self.text,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "rotation": round(self.rotation, 3),
            "column_index": self.column_index,
            "column_role": self.column_role,
            "row_index": self.row_index,
            "anchor_basis": self.anchor_basis,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DetailLabel:
    """Размерен надпис от колоната „Детайл" — сурова стойност и място."""

    handle: str
    text: str
    value_cm: float | None
    x: float
    y: float
    rotation: float
    segment_index: int | None = None
    segment_handle: str = ""
    match_distance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "text": self.text,
            "value_cm": self.value_cm,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "rotation": round(self.rotation, 3),
            "segment_index": self.segment_index,
            "segment_handle": self.segment_handle,
            "match_distance": (
                None if self.match_distance is None else round(self.match_distance, 3)
            ),
        }


@dataclass(frozen=True)
class ScheduleRow:
    """Един ред от ведомостта на проектанта.

    ``detail_cm`` е подреден списък САМО когато рисунката го доказва; иначе е
    празен и причината е в ``detail_reason``.  ``detail_labels`` са суровите
    надписи и винаги са налични.
    """

    position: int
    diameter_mm: float
    count: int
    length_cm: float
    detail_cm: tuple[float, ...] = ()
    mark_raw: str = ""
    mark_kind: str = ""
    detail_labels: tuple[DetailLabel, ...] = ()
    detail_ordered: bool = False
    detail_reason: str = ""
    detail_segment_count: int = 0
    detail_bend_count: int = 0
    row_index: int = -1
    band: tuple[float, float] = (0.0, 0.0)
    cells: tuple[Cell, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def detail_total_cm(self) -> float:
        return round(sum(self.detail_cm), 3)

    @property
    def detail_label_total_cm(self) -> float:
        """Сборът на ВСИЧКИ надписи, дори когато не са подредени."""

        return round(
            sum(l.value_cm for l in self.detail_labels if l.value_cm is not None), 3
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "diameter_mm": self.diameter_mm,
            "count": self.count,
            "length_cm": self.length_cm,
            "detail_cm": list(self.detail_cm),
            "detail_total_cm": self.detail_total_cm,
            "mark_raw": self.mark_raw,
            "mark_kind": self.mark_kind,
            "detail_labels": [l.to_dict() for l in self.detail_labels],
            "detail_label_total_cm": self.detail_label_total_cm,
            "detail_ordered": self.detail_ordered,
            "detail_reason": self.detail_reason,
            "detail_segment_count": self.detail_segment_count,
            "detail_bend_count": self.detail_bend_count,
            "detail_length_delta_cm": (
                round(self.detail_label_total_cm - self.length_cm, 3)
                if self.detail_labels
                else None
            ),
            "row_index": self.row_index,
            "band": [round(v, 3) for v in self.band],
            "cells": [c.to_dict() for c in self.cells],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class UnparsedRow:
    """Ред, който не е прочетен уверено — с причината, не с догадка."""

    row_index: int
    band: tuple[float, float]
    reason: str
    cells: tuple[Cell, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "UNPARSED",
            "row_index": self.row_index,
            "band": [round(v, 3) for v in self.band],
            "reason": self.reason,
            "cells": [c.to_dict() for c in self.cells],
        }


@dataclass
class ScheduleGrid:
    """Намерената решетка — границите, по които е разчетена таблицата."""

    column_bounds: tuple[float, ...] = ()
    row_bounds: tuple[float, ...] = ()
    header_row_index: int = -1
    roles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_bounds": [round(v, 3) for v in self.column_bounds],
            "row_bounds": [round(v, 3) for v in self.row_bounds],
            "column_count": max(0, len(self.column_bounds) - 1),
            "row_count": max(0, len(self.row_bounds) - 1),
            "header_row_index": self.header_row_index,
            "roles": list(self.roles),
        }


@dataclass
class Schedule:
    rows: list[ScheduleRow] = field(default_factory=list)
    layer: str = ""
    header_seen: bool = False
    skipped_cells: int = 0
    unparsed: list[UnparsedRow] = field(default_factory=list)
    grid: ScheduleGrid = field(default_factory=ScheduleGrid)
    outside_grid: list[Cell] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEDULE_SCHEMA,
            "reader": SCHEDULE_READER,
            "read_only": True,
            "layer": self.layer,
            "header_seen": self.header_seen,
            "row_count": len(self.rows),
            "unparsed_count": len(self.unparsed),
            "skipped_cells": self.skipped_cells,
            "grid": self.grid.to_dict(),
            "rows": [r.to_dict() for r in self.rows],
            "unparsed": [u.to_dict() for u in self.unparsed],
            "outside_grid": [c.to_dict() for c in self.outside_grid],
            "notes": list(self.notes),
        }

    def by_position(self) -> dict[int, ScheduleRow]:
        return {r.position: r for r in self.rows}

    def total_pieces(self) -> int:
        return sum(r.count for r in self.rows)


# --------------------------------------------------------------------------- #
# Решетката
# --------------------------------------------------------------------------- #
def _merge_spans(spans: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    ordered = sorted(spans)
    merged: list[list[float]] = []
    for low, high in ordered:
        if merged and low <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return [(a, b) for a, b in merged]


def _grid_bounds(records: Sequence[Any]) -> tuple[list[float], list[float], set[float], set[float]]:
    """Границите на колоните и редовете от линиите на таблицата.

    Хоризонталните чертички на таблицата в ASD са начупени на по една отсечка
    за клетка, затова покритието се смята върху обединението на отсечките със
    същата координата, а не върху най-дългата от тях.
    """

    vertical: dict[float, list[tuple[float, float]]] = {}
    horizontal: dict[float, list[tuple[float, float]]] = {}
    for record in records:
        if str(getattr(record, "entity_type", "") or "").upper() != "LINE":
            continue
        geometry = getattr(record, "geometry", None) or {}
        start, end = geometry.get("start"), geometry.get("end")
        if not (_is_point(start) and _is_point(end)):
            continue
        x0, y0 = float(start[0]), float(start[1])
        x1, y1 = float(end[0]), float(end[1])
        if abs(x0 - x1) <= 1e-6 and abs(y0 - y1) > 1e-6:
            vertical.setdefault(round(x0, 2), []).append((min(y0, y1), max(y0, y1)))
        elif abs(y0 - y1) <= 1e-6 and abs(x0 - x1) > 1e-6:
            horizontal.setdefault(round(y0, 2), []).append((min(x0, x1), max(x0, x1)))

    def coverage(groups: dict[float, list[tuple[float, float]]]) -> dict[float, float]:
        return {
            key: sum(b - a for a, b in _merge_spans(spans, _JOIN_TOL))
            for key, spans in groups.items()
        }

    vcov = coverage(vertical)
    hcov = coverage(horizontal)
    if not vcov or not hcov:
        return [], [], set(vertical), set(horizontal)
    height = max(vcov.values())
    width = max(hcov.values())
    columns = sorted(x for x, c in vcov.items() if c >= _GRID_COVERAGE * height)
    rows = sorted(y for y, c in hcov.items() if c >= _GRID_COVERAGE * width)
    return columns, rows, set(vertical), set(horizontal)


def _band_index(value: float, bounds: Sequence[float]) -> int:
    for index in range(len(bounds) - 1):
        if bounds[index] - _JOIN_TOL <= value <= bounds[index + 1] + _JOIN_TOL:
            return index
    return -1


# --------------------------------------------------------------------------- #
# Рисунката в колоната „Детайл"
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Segment:
    handle: str
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def angle(self) -> float:
        return math.degrees(
            math.atan2(self.end[1] - self.start[1], self.end[0] - self.start[0])
        ) % 180.0


def _arc_endpoints(geometry: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    center = geometry.get("center")
    radius = _float(geometry.get("radius"))
    if not _is_point(center) or radius is None:
        return None
    points = []
    for key in ("start_angle", "end_angle"):
        angle = _float(geometry.get(key))
        if angle is None:
            return None
        radians = math.radians(angle)
        points.append(
            (
                float(center[0]) + radius * math.cos(radians),
                float(center[1]) + radius * math.sin(radians),
            )
        )
    return points[0], points[1]


def _point_to_segment(point: tuple[float, float], segment: _Segment) -> float:
    ax, ay = segment.start
    bx, by = segment.end
    dx, dy = bx - ax, by - ay
    squared = dx * dx + dy * dy
    if squared <= 0.0:
        return math.dist(point, segment.start)
    t = ((point[0] - ax) * dx + (point[1] - ay) * dy) / squared
    t = max(0.0, min(1.0, t))
    return math.dist(point, (ax + t * dx, ay + t * dy))


def _chain(segments: Sequence[_Segment], arcs: Sequence[tuple[tuple[float, float], tuple[float, float]]]) -> list[int] | None:
    """Подрежда отсечките във верига през дъгите на огъвките.

    Връща ``None``, ако веригата не е еднозначна (разклонение, затворен контур
    или несвързани части) — такава рисунка не доказва ред.
    """

    count = len(segments)
    if count == 0:
        return None
    if count == 1:
        return [0]
    neighbours: dict[int, set[int]] = {i: set() for i in range(count)}
    ends = [(s.start, s.end) for s in segments]
    for a in range(count):
        for b in range(a + 1, count):
            linked = min(
                math.dist(p, q) for p in ends[a] for q in ends[b]
            ) <= _JOIN_TOL
            if not linked:
                for first, second in arcs:
                    near_a_first = min(math.dist(first, p) for p in ends[a])
                    near_b_second = min(math.dist(second, p) for p in ends[b])
                    near_a_second = min(math.dist(second, p) for p in ends[a])
                    near_b_first = min(math.dist(first, p) for p in ends[b])
                    if (near_a_first <= _JOIN_TOL and near_b_second <= _JOIN_TOL) or (
                        near_a_second <= _JOIN_TOL and near_b_first <= _JOIN_TOL
                    ):
                        linked = True
                        break
            if linked:
                neighbours[a].add(b)
                neighbours[b].add(a)
    if any(len(neighbours[i]) > 2 for i in range(count)):
        return None
    ends_of_chain = [i for i in range(count) if len(neighbours[i]) <= 1]
    if len(ends_of_chain) != 2:
        return None
    order = [ends_of_chain[0]]
    previous, current = None, ends_of_chain[0]
    while True:
        following = [n for n in neighbours[current] if n != previous]
        if not following:
            break
        previous, current = current, following[0]
        order.append(current)
    if len(order) != count:
        return None
    return order


def _angles_agree(rotation: float, segment: _Segment) -> bool:
    difference = abs((rotation % 180.0) - segment.angle)
    return min(difference, 180.0 - difference) <= _ANGLE_TOL_DEG


def _match_labels(
    segments: Sequence[_Segment],
    order: Sequence[int],
    labels: Sequence[DetailLabel],
) -> tuple[list[DetailLabel], str]:
    """Еднозначно съответствие надпис ↔ участък.

    Изисква: (1) толкова надписа, колкото участъка; (2) всеки надпис е
    паралелен на своя участък; (3) избира се съответствието с най-малка обща
    разстояние, за да не се разчита само на „най-близкото".
    """

    count = len(segments)
    if count > _MAX_MATCH_SEGMENTS:
        return [], f"твърде много участъци за еднозначно съответствие ({count})"
    if len(labels) != count:
        return [], (
            f"{count} нарисувани участъка срещу {len(labels)} надписа — "
            "част от участъците са без размер"
        )
    if any(label.value_cm is None for label in labels):
        return [], "нечислов надпис в колоната „Детайл“"
    best: tuple[float, tuple[int, ...]] | None = None
    for permutation in itertools.permutations(range(count)):
        total = 0.0
        acceptable = True
        for label_index, segment_index in enumerate(permutation):
            label = labels[label_index]
            segment = segments[segment_index]
            if not _angles_agree(label.rotation, segment):
                acceptable = False
                break
            total += _point_to_segment((label.x, label.y), segment)
        if acceptable and (best is None or total < best[0]):
            best = (total, permutation)
    if best is None:
        return [], "надпис, който не е паралелен на никой участък"
    assignment = {
        segment_index: label_index for label_index, segment_index in enumerate(best[1])
    }
    ordered: list[DetailLabel] = []
    for position, segment_index in enumerate(order):
        label = labels[assignment[segment_index]]
        segment = segments[segment_index]
        ordered.append(
            DetailLabel(
                handle=label.handle,
                text=label.text,
                value_cm=label.value_cm,
                x=label.x,
                y=label.y,
                rotation=label.rotation,
                segment_index=position,
                segment_handle=segment.handle,
                match_distance=_point_to_segment((label.x, label.y), segment),
            )
        )
    return ordered, ""


# --------------------------------------------------------------------------- #
# Четене
# --------------------------------------------------------------------------- #
def has_schedule(entities: Iterable[Any]) -> bool:
    """Има ли изобщо такъв слой в чертежа."""

    for record in entities:
        layer = str(getattr(record, "layer", "") or "").upper()
        if any(marker in layer for marker in LAYER_MARKERS):
            return True
    return False


def _classify_mark(text: str) -> str:
    lowered = str(text or "").strip().lower()
    for kind, aliases in MARK_ALIASES:
        if lowered in aliases:
            return kind
    return ""


def read_schedule(entities: Iterable[Any]) -> Schedule:
    """Чете ведомостта по координати. При липса на слой или решетка връща празно.

    Отказът е по-добър от догадката: без заглавия не е ясно кой стълб какво е,
    а без решетка не е ясно кой ред докъде стига.
    """

    records: list[Any] = []
    layer_name = ""
    for record in entities:
        layer = str(getattr(record, "layer", "") or "")
        if not any(marker in layer.upper() for marker in LAYER_MARKERS):
            continue
        layer_name = layer_name or layer
        records.append(record)

    schedule = Schedule(layer=layer_name)
    if not records:
        schedule.notes.append("Няма слой с ведомост.")
        return schedule

    texts: list[tuple[Any, str, float, float, float, str]] = []
    for record in records:
        if str(getattr(record, "entity_type", "") or "").upper() not in ("TEXT", "MTEXT"):
            continue
        text = str(getattr(record, "text", "") or "").strip()
        if not text:
            continue
        anchor = _anchor(record)
        if anchor is None:
            schedule.skipped_cells += 1
            continue
        rotation = _float((getattr(record, "geometry", None) or {}).get("rotation"), 0.0) or 0.0
        texts.append((record, text, anchor[0], anchor[1], rotation, anchor[2]))

    if not texts:
        schedule.notes.append("Слоят няма текстови клетки.")
        return schedule

    columns, rows, _, _ = _grid_bounds(records)
    schedule.grid = ScheduleGrid(column_bounds=tuple(columns), row_bounds=tuple(rows))
    if len(columns) < 3 or len(rows) < 2:
        schedule.skipped_cells += len(texts)
        schedule.notes.append(
            "Не е намерена решетка от линии (колони %d, редове %d) — не се чете."
            % (len(columns), len(rows))
        )
        return schedule

    # клетките по (ред, колона)
    grid_cells: dict[tuple[int, int], list[Cell]] = {}
    for record, text, x, y, rotation, basis in texts:
        column_index = _band_index(x, columns)
        row_index = _band_index(y, rows)
        cell = Cell(
            handle=str(getattr(record, "handle", "") or ""),
            text=text,
            x=x,
            y=y,
            rotation=rotation,
            column_index=column_index,
            row_index=row_index,
            anchor_basis=basis,
            reason=(
                "опорната точка попада в колона [%.2f; %.2f] и ред [%.2f; %.2f]"
                % (
                    columns[column_index],
                    columns[column_index + 1],
                    rows[row_index],
                    rows[row_index + 1],
                )
                if column_index >= 0 and row_index >= 0
                else "опорната точка е извън решетката"
            ),
        )
        if column_index < 0 or row_index < 0:
            schedule.outside_grid.append(cell)
            schedule.skipped_cells += 1
            continue
        grid_cells.setdefault((row_index, column_index), []).append(cell)

    # заглавията: най-горният ред с поне три от познатите думи
    roles: list[str] = [""] * (len(columns) - 1)
    header_index = -1
    for row_index in range(len(rows) - 2, -1, -1):
        found: dict[int, str] = {}
        for column_index in range(len(columns) - 1):
            for cell in grid_cells.get((row_index, column_index), []):
                lowered = cell.text.lower()
                for role, keywords in COLUMN_KEYWORDS:
                    if any(keyword in lowered for keyword in keywords):
                        found.setdefault(column_index, role)
                        break
        named = sum(
            1
            for word in HEADER_WORDS
            for column_index in found
            if any(
                word in cell.text.lower()
                for cell in grid_cells.get((row_index, column_index), [])
            )
        )
        if len(found) >= 3 and named >= 3:
            header_index = row_index
            for column_index, role in found.items():
                roles[column_index] = role
            break

    schedule.header_seen = header_index >= 0 and all(
        role in roles for role in REQUIRED_ROLES
    )
    schedule.grid = ScheduleGrid(
        column_bounds=tuple(columns),
        row_bounds=tuple(rows),
        header_row_index=header_index,
        roles=tuple(roles),
    )
    if not schedule.header_seen:
        schedule.skipped_cells += sum(len(v) for v in grid_cells.values())
        missing = [role for role in REQUIRED_ROLES if role not in roles]
        schedule.notes.append(
            "Заглавията не са разпознати (липсват роли: %s) — не се чете."
            % (", ".join(missing) or "неизвестно")
        )
        return schedule

    # Ролята на колоната се знае едва след заглавията; клетките се
    # пре-подпечатват, за да носи всяка произхода си докрай.
    grid_cells = {
        key: [replace(cell, column_role=roles[key[1]]) for cell in cells]
        for key, cells in grid_cells.items()
    }

    role_to_column = {role: index for index, role in enumerate(roles) if role}
    detail_column = role_to_column.get("detail")

    # рисунките и дъгите по редове — за колоната „Детайл"
    segments_by_row: dict[int, list[_Segment]] = {}
    arcs_by_row: dict[int, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    if detail_column is not None:
        x_low, x_high = columns[detail_column], columns[detail_column + 1]
        for record in records:
            kind = str(getattr(record, "entity_type", "") or "").upper()
            geometry = getattr(record, "geometry", None) or {}
            if kind == "LINE":
                start, end = geometry.get("start"), geometry.get("end")
                if not (_is_point(start) and _is_point(end)):
                    continue
                a = (float(start[0]), float(start[1]))
                b = (float(end[0]), float(end[1]))
                # рамката на таблицата не е част от рисунката
                if _on_grid(a, b, columns, rows):
                    continue
                middle = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                if not x_low <= middle[0] <= x_high:
                    continue
                row_index = _band_index(middle[1], rows)
                if row_index < 0:
                    continue
                segments_by_row.setdefault(row_index, []).append(
                    _Segment(str(getattr(record, "handle", "") or ""), a, b)
                )
            elif kind == "ARC":
                ends = _arc_endpoints(geometry)
                if ends is None:
                    continue
                middle = (
                    (ends[0][0] + ends[1][0]) / 2.0,
                    (ends[0][1] + ends[1][1]) / 2.0,
                )
                if not x_low <= middle[0] <= x_high:
                    continue
                row_index = _band_index(middle[1], rows)
                if row_index < 0:
                    continue
                arcs_by_row.setdefault(row_index, []).append(ends)

    for row_index in range(len(rows) - 2, -1, -1):
        if row_index == header_index:
            continue
        cells_here = {
            column_index: grid_cells.get((row_index, column_index), [])
            for column_index in range(len(columns) - 1)
        }
        if not any(cells_here.values()):
            continue
        band = (rows[row_index], rows[row_index + 1])
        all_cells = tuple(
            cell for column in sorted(cells_here) for cell in cells_here[column]
        )

        def single(role: str) -> tuple[Cell | None, str]:
            column_index = role_to_column.get(role)
            if column_index is None:
                return None, f"липсва колона „{role}“"
            values = cells_here.get(column_index) or []
            if not values:
                return None, f"празна клетка „{role}“"
            if len(values) > 1:
                return None, (
                    "колоната „%s“ има %d стойности (%s) — редът не се чете"
                    % (
                        role,
                        len(values),
                        ", ".join(f"{c.text}@{c.handle}" for c in values),
                    )
                )
            return values[0], ""

        problems: list[str] = []
        position_cell, problem = single("position")
        if problem:
            problems.append(problem)
        diameter_cell, problem = single("diameter")
        if problem:
            problems.append(problem)
        count_cell, problem = single("count")
        if problem:
            problems.append(problem)
        length_cell, problem = single("length")
        if problem:
            problems.append(problem)

        position = None
        if position_cell is not None:
            if _INTEGER.match(position_cell.text):
                position = int(position_cell.text)
            else:
                problems.append(
                    f"позицията „{position_cell.text}“ не е цяло число"
                )
        diameter = _number(diameter_cell.text) if diameter_cell is not None else None
        if diameter_cell is not None and diameter is None:
            problems.append(f"диаметърът „{diameter_cell.text}“ не е число")
        count = None
        if count_cell is not None:
            if _INTEGER.match(count_cell.text):
                count = int(count_cell.text)
            else:
                problems.append(f"броят „{count_cell.text}“ не е цяло число")
        length = _number(length_cell.text) if length_cell is not None else None
        if length_cell is not None and length is None:
            problems.append(f"дължината „{length_cell.text}“ не е число")

        if problems:
            schedule.unparsed.append(
                UnparsedRow(
                    row_index=row_index,
                    band=band,
                    reason="; ".join(problems),
                    cells=all_cells,
                )
            )
            continue

        notes: list[str] = []
        mark_raw = ""
        mark_kind = ""
        mark_column = role_to_column.get("mark")
        if mark_column is not None:
            mark_cells = cells_here.get(mark_column) or []
            if len(mark_cells) == 1:
                mark_raw = mark_cells[0].text
                mark_kind = _classify_mark(mark_raw)
                if not mark_kind:
                    # Марката не влияе на брой, дължина и форма: непозната
                    # стойност се отбелязва, но не отменя целия ред.
                    mark_kind = "НЕИЗВЕСТНА"
                    notes.append(
                        "непозната марка в колоната „N/Ø“: %r" % mark_raw
                    )
                elif mark_kind == "Ø" and mark_raw.strip() == "0":
                    notes.append(
                        "марката е символът Ø, записан чрез превключване на "
                        "шрифта (\\Fdiam;0); в чист текст остава „0“"
                    )
            elif len(mark_cells) > 1:
                notes.append(
                    "колоната „N/Ø“ има %d стойности — марката не се твърди"
                    % len(mark_cells)
                )

        detail_labels: tuple[DetailLabel, ...] = ()
        detail_cm: tuple[float, ...] = ()
        detail_ordered = False
        detail_reason = ""
        segments = segments_by_row.get(row_index, [])
        arcs = arcs_by_row.get(row_index, [])
        if detail_column is not None:
            raw_labels = [
                DetailLabel(
                    handle=cell.handle,
                    text=cell.text,
                    value_cm=_number(cell.text),
                    x=cell.x,
                    y=cell.y,
                    rotation=cell.rotation,
                )
                for cell in (cells_here.get(detail_column) or [])
            ]
            detail_labels = tuple(raw_labels)
            if not raw_labels:
                detail_reason = "няма надписи в колоната „Детайл“"
            elif not segments:
                detail_reason = (
                    "няма нарисувана осева линия — надписите не могат да се подредят"
                )
            else:
                order = _chain(segments, arcs)
                if order is None:
                    detail_reason = (
                        "участъците не се навързват в еднозначна верига (%d отсечки, "
                        "%d дъги)" % (len(segments), len(arcs))
                    )
                else:
                    matched, reason = _match_labels(segments, order, raw_labels)
                    if matched:
                        detail_labels = tuple(matched)
                        detail_cm = tuple(
                            float(l.value_cm) for l in matched if l.value_cm is not None
                        )
                        detail_ordered = True
                        detail_reason = (
                            "всеки от %d участъка има точно един паралелен надпис"
                            % len(segments)
                        )
                    else:
                        detail_reason = reason

        schedule.rows.append(
            ScheduleRow(
                position=position or 0,
                diameter_mm=float(diameter),
                count=int(count),
                length_cm=float(length),
                detail_cm=detail_cm,
                mark_raw=mark_raw,
                mark_kind=mark_kind,
                detail_labels=detail_labels,
                detail_ordered=detail_ordered,
                detail_reason=detail_reason,
                detail_segment_count=len(segments),
                detail_bend_count=len(arcs),
                row_index=row_index,
                band=band,
                cells=all_cells,
                notes=tuple(notes),
            )
        )

    schedule.rows.sort(key=lambda r: r.position)
    return schedule


def _on_grid(
    a: tuple[float, float],
    b: tuple[float, float],
    columns: Sequence[float],
    rows: Sequence[float],
) -> bool:
    """Линията ли е част от рамката на таблицата."""

    if abs(a[0] - b[0]) <= 1e-6:
        return any(abs(a[0] - x) <= _JOIN_TOL for x in columns)
    if abs(a[1] - b[1]) <= 1e-6:
        return any(abs(a[1] - y) <= _JOIN_TOL for y in rows)
    return False


# --------------------------------------------------------------------------- #
# Сверяване
# --------------------------------------------------------------------------- #
def _program_index(rows: Sequence[dict[str, Any]]) -> tuple[
    dict[tuple[float, float], dict[str, Any]], list[dict[str, Any]]
]:
    """Групира извлечените позиции по (диаметър, дължина) плюс отпадналите."""

    grouped: dict[tuple[float, float], dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []
    for row in rows:
        annotation_class = str(row.get("annotation_class") or "")
        diameter = row.get("diameter_mm")
        length = row.get("declared_length_cm")
        count = row.get("annotation_count")
        if annotation_class != "ACTIONABLE" or not diameter or not length or not count:
            excluded.append(
                {
                    "annotation_handle": str(row.get("annotation_handle") or ""),
                    "annotation_text": str(row.get("annotation_text") or "").strip(),
                    "annotation_class": annotation_class,
                    "diameter_mm": diameter,
                    "declared_length_cm": length,
                    "annotation_count": count,
                    "reason": (
                        "клас %s" % annotation_class
                        if annotation_class != "ACTIONABLE"
                        else "липсва диаметър, дължина или брой"
                    ),
                }
            )
            continue
        key = (round(float(diameter), 1), round(float(length), 1))
        bucket = grouped.setdefault(
            key,
            {
                "count": 0,
                "annotations": [],
                "handles": [],
                "shape_codes": set(),
                "segment_counts": set(),
                "segments": [],
            },
        )
        bucket["count"] += int(count)
        bucket["annotations"].append(str(row.get("annotation_text") or "").strip())
        bucket["handles"].append(str(row.get("annotation_handle") or ""))
        if row.get("master_shape_code"):
            bucket["shape_codes"].add(str(row.get("master_shape_code")))
        if row.get("segment_count"):
            bucket["segment_counts"].add(int(row.get("segment_count") or 0))
        canonical = row.get("canonical_segment_lengths_cm") or []
        if canonical:
            values = tuple(round(float(v), 1) for v in canonical)
            if values not in bucket["segments"]:
                bucket["segments"].append(values)
    return grouped, excluded


def compare_per_field(
    schedule: Schedule,
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Сверява ведомостта с извлечените позиции ПО ПОЛЕТА, поотделно.

    Сравняват се пет различни неща и всяко има собствена присъда: ``count``,
    ``diameter``, ``length``, ``shape`` и ``segments``.  Едно общо „съвпада"
    върху двойката (диаметър, дължина) би скрило, че формата и участъците не са
    сравнявани; затова обща присъда не се дава.

    Групирането е по (диаметър, дължина), защото извлечените позиции не носят
    номер на позиция от ведомостта.  Това е ограничение, а не съответствие: два
    различни номера с еднакви диаметър и дължина попадат в една група.
    """

    grouped, excluded = _program_index(rows)
    from_schedule: dict[tuple[float, float], dict[str, Any]] = {}
    for row in schedule.rows:
        key = (round(float(row.diameter_mm), 1), round(float(row.length_cm), 1))
        bucket = from_schedule.setdefault(
            key, {"count": 0, "positions": [], "segments": [], "unordered": []}
        )
        bucket["count"] += row.count
        bucket["positions"].append(row.position)
        if row.detail_ordered:
            bucket["segments"].append(tuple(round(v, 1) for v in row.detail_cm))
        else:
            bucket["unordered"].append(
                {
                    "position": row.position,
                    "reason": row.detail_reason,
                    "labels": [l.text for l in row.detail_labels],
                    "drawn_segments": row.detail_segment_count,
                    "drawn_bends": row.detail_bend_count,
                }
            )
    all_keys = sorted(set(grouped) | set(from_schedule))

    count_rows: list[dict[str, Any]] = []
    length_rows: list[dict[str, Any]] = []
    shape_rows: list[dict[str, Any]] = []
    segment_rows: list[dict[str, Any]] = []
    for key in all_keys:
        mine = from_schedule.get(key)
        theirs = grouped.get(key)
        positions = sorted(mine["positions"]) if mine else []

        # 1) наличие на двойката (диаметър, дължина)
        if mine and theirs:
            length_verdict = "И В ДВЕТЕ"
        elif mine:
            length_verdict = "САМО ВЪВ ВЕДОМОСТТА"
        else:
            length_verdict = "САМО В ПРОГРАМАТА"
        length_rows.append(
            {
                "diameter_mm": key[0],
                "length_cm": key[1],
                "positions": positions,
                "verdict": length_verdict,
            }
        )

        # 2) брой
        schedule_count = mine["count"] if mine else None
        program_count = theirs["count"] if theirs else None
        if schedule_count is None:
            count_verdict = "САМО В ПРОГРАМАТА"
        elif program_count is None:
            count_verdict = "САМО ВЪВ ВЕДОМОСТТА"
        elif schedule_count == program_count:
            count_verdict = "БРОЯТ СЪВПАДА"
        else:
            count_verdict = "РАЗЛИЧЕН БРОЙ"
        count_rows.append(
            {
                "diameter_mm": key[0],
                "length_cm": key[1],
                "positions": positions,
                "count_schedule": schedule_count,
                "count_program": program_count,
                "difference": (
                    None
                    if schedule_count is None or program_count is None
                    else program_count - schedule_count
                ),
                "annotations": (theirs["annotations"][:4] if theirs else []),
                "verdict": count_verdict,
                "compared_fields": ["count"],
            }
        )

        # 3) форма: брой нарисувани участъци и огъвки срещу MASTER формата
        drawn = None
        bends = None
        if mine:
            drawn = sorted(
                {
                    row.detail_segment_count
                    for row in schedule.rows
                    if (round(row.diameter_mm, 1), round(row.length_cm, 1)) == key
                }
            )
            bends = sorted(
                {
                    row.detail_bend_count
                    for row in schedule.rows
                    if (round(row.diameter_mm, 1), round(row.length_cm, 1)) == key
                }
            )
        program_shapes = sorted(theirs["shape_codes"]) if theirs else []
        program_segments = sorted(theirs["segment_counts"]) if theirs else []
        if not mine or not theirs or not drawn or not program_segments:
            shape_verdict = "НЕСРАВНИМО"
            shape_reason = (
                "няма данни от едната страна"
                if (not mine or not theirs)
                else "програмата не дава брой участъци"
            )
        elif drawn == program_segments:
            shape_verdict = "БРОЯТ УЧАСТЪЦИ СЪВПАДА"
            shape_reason = ""
        else:
            shape_verdict = "РАЗЛИЧНА ФОРМА"
            shape_reason = (
                "ведомостта рисува %s участъка и %s огъвки, програмата — %s"
                % (drawn, bends, program_segments)
            )
        shape_rows.append(
            {
                "diameter_mm": key[0],
                "length_cm": key[1],
                "positions": positions,
                "schedule_drawn_segments": drawn,
                "schedule_drawn_bends": bends,
                "program_shape_codes": program_shapes,
                "program_segment_counts": program_segments,
                "verdict": shape_verdict,
                "reason": shape_reason,
                "compared_fields": ["shape"],
            }
        )

        # 4) участъци
        schedule_segments = mine["segments"] if mine else []
        program_segment_lists = theirs["segments"] if theirs else []
        if not schedule_segments or not program_segment_lists:
            segment_verdict = "НЕСРАВНИМО"
            segment_reason = (
                "ведомостта не дава подредени участъци"
                if not schedule_segments
                else "програмата не дава участъци"
            )
        else:
            matched = any(
                sorted(a) == sorted(b)
                for a in schedule_segments
                for b in program_segment_lists
            )
            segment_verdict = (
                "УЧАСТЪЦИТЕ СЪВПАДАТ" if matched else "РАЗЛИЧНИ УЧАСТЪЦИ"
            )
            segment_reason = "" if matched else "сравнени като множества, без ред"
        segment_rows.append(
            {
                "diameter_mm": key[0],
                "length_cm": key[1],
                "positions": positions,
                "schedule_segments": [list(v) for v in schedule_segments],
                "schedule_unordered": (mine["unordered"] if mine else []),
                "program_segments": [list(v) for v in program_segment_lists],
                "verdict": segment_verdict,
                "reason": segment_reason,
                "compared_fields": ["segments"],
            }
        )

    # 5) диаметри
    schedule_diameters = sorted({key[0] for key in from_schedule})
    program_diameters = sorted({key[0] for key in grouped})
    diameter_rows = []
    for diameter in sorted(set(schedule_diameters) | set(program_diameters)):
        in_schedule = diameter in schedule_diameters
        in_program = diameter in program_diameters
        diameter_rows.append(
            {
                "diameter_mm": diameter,
                "pieces_schedule": sum(
                    b["count"] for k, b in from_schedule.items() if k[0] == diameter
                )
                or None,
                "pieces_program": sum(
                    b["count"] for k, b in grouped.items() if k[0] == diameter
                )
                or None,
                "verdict": (
                    "И В ДВЕТЕ"
                    if in_schedule and in_program
                    else ("САМО ВЪВ ВЕДОМОСТТА" if in_schedule else "САМО В ПРОГРАМАТА")
                ),
                "compared_fields": ["diameter"],
            }
        )

    def tally(items: Sequence[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in items:
            result[item["verdict"]] = result.get(item["verdict"], 0) + 1
        return result

    return {
        "schema": SCHEDULE_SCHEMA + "/compare-per-field",
        "reader": SCHEDULE_READER,
        "grouping": "(диаметър, дължина) — ведомостта не носи номер на позиция "
        "към извлечените позиции",
        "schedule_rows": len(schedule.rows),
        "schedule_unparsed": len(schedule.unparsed),
        "schedule_pieces": schedule.total_pieces(),
        "program_pieces": sum(b["count"] for b in grouped.values()),
        "program_excluded": excluded,
        "fields": {
            "count": {"rows": count_rows, "tally": tally(count_rows)},
            "diameter": {"rows": diameter_rows, "tally": tally(diameter_rows)},
            "length": {"rows": length_rows, "tally": tally(length_rows)},
            "shape": {"rows": shape_rows, "tally": tally(shape_rows)},
            "segments": {"rows": segment_rows, "tally": tally(segment_rows)},
        },
    }


def compare_with_rows(
    schedule: Schedule,
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Сверява ведомостта с извлечените позиции — само отчита, не променя.

    Запазеното поведение: групира по (диаметър, дължина) и отчита броя.
    Присъдата на всеки ред вече казва КАКВО е сравнено („БРОЯТ СЪВПАДА"), а не
    само „съвпада", и носи ``compared_fields``.  Пълното сравнение по полета е
    в ``per_field`` (и в :func:`compare_per_field`).
    """

    per_field = compare_per_field(schedule, rows)
    count_rows = per_field["fields"]["count"]["rows"]

    result_rows = []
    matched = different = only_program = only_schedule = 0
    for row in count_rows:
        entry = {
            "diameter_mm": row["diameter_mm"],
            "length_cm": row["length_cm"],
            "positions": row["positions"],
            "count_schedule": row["count_schedule"],
            "count_program": row["count_program"],
            "annotations": row["annotations"],
            "verdict": row["verdict"],
            "compared_fields": ["count"],
        }
        if row["verdict"] == "САМО В ПРОГРАМАТА":
            only_program += 1
        elif row["verdict"] == "САМО ВЪВ ВЕДОМОСТТА":
            only_schedule += 1
        elif row["verdict"] == "БРОЯТ СЪВПАДА":
            matched += 1
        else:
            different += 1
            schedule_count = row["count_schedule"]
            entry["ratio"] = (
                round(row["count_program"] / schedule_count, 3)
                if schedule_count
                else None
            )
        result_rows.append(entry)

    return {
        "schema": SCHEDULE_SCHEMA + "/compare",
        "reader": SCHEDULE_READER,
        "compared_fields": ["count"],
        "schedule_rows": len(schedule.rows),
        "schedule_unparsed": len(schedule.unparsed),
        "schedule_pieces": schedule.total_pieces(),
        "matched": matched,
        "different": different,
        "only_program": only_program,
        "only_schedule": only_schedule,
        "rows": result_rows,
        "per_field": per_field,
    }
