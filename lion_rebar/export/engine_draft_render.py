"""Рисунката на формата, както я прави САМИЯТ редактор на форми.

ЗАЩО. Собственик, 23.09.2026, показвайки OPEN_ENGINE редактора: „много добре
ги рисува и много лесно“. И преди това, за стълбищните рамена в описа: „имаш
мастър форма и тя не е такава — намери я в Енджина“.

Заключеният ``OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html`` носи ДВА рисувача:

* ``geometry()`` — по една описана рисунка за F001..F008 плюс отделните
  ``f006SvgFromUploadedV023`` и ``f009SvgFromLocked``. Пренесени са в
  ``master_engine_v031`` и не се пипат.
* ``renderDraftDrawing()`` — ОБЩИЯТ рисувач за произволна верига от участъци.
  С него редакторът рисува всичко от F010 нататък. Него пренася този модул.

Разликата, която се вижда с просто око: общият рисувач слага размерната линия
**УСПОРЕДНО на участъка**, със завъртян надпис. Затова наклонените рамена се
четат. Дотук описът мереше ПРОЕКЦИЯТА на наклонения участък по осите — оттам
идваха и смачканите надписи, и силуетите, които собственикът не прие.

ПРЕНОСЪТ Е БУКВАЛЕН. Имената на функциите отговарят едно към едно на
оригинала (``physicalPoints``, ``fitDraft``, ``chooseDimensionSign``,
``draftDimensionSvg``, ``fmtDraft``, ``separateOverlappingEnds``). Числата —
мащабът 64/54, отстъпът 2..18, отместването на надписа 2.2, прагът 0.2 при
избора на страна — са на редактора, не са избрани тук. Стиловете също.

Сверено: рисувачът в ``OPEN_ENGINE_v0.3.2_MASTER_F018_CANDIDATE.html`` е БАЙТ
ПО БАЙТ същият, тоест няма по-нова редакция, която да се пропуска.

РАДИУС НА ОГЪВКА. ``SegmentDefinition.radius_cm`` и полето
``РАДИУС_ОГЪВКА_СМ`` в данните съществуват, но в целия публикуван engine са
``null`` — редакторът няма поле за тях. Затова тук огъвката е чуп, както е в
оригинала. Когато редакторът получи полето, закръглянето се добавя и на двете
места заедно.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition

#: Изгледът на редактора: ``<svg id="draftDrawing" viewBox="0 0 100 100">``.
VIEW_BOX = "0 0 100 100"

#: ``fitDraft``: вписва фигурата в 64 на 54 единици около центъра (50, 50).
_FIT_WIDTH = 64.0
_FIT_HEIGHT = 54.0

#: ``draftDimensionSvg``: границите на отстъпа и отместването на надписа.
_OFFSET_MIN = 2.0
_OFFSET_MAX = 18.0
_TEXT_LIFT = 2.2

#: ``chooseDimensionSign``: под този праг посоката „навън“ не значи нищо и
#: страните се редуват.
_OUTWARD_EPSILON = 0.2

_LEFT_WORDS = frozenset({"LEFT", "НАЛЯВО"})
_HIDDEN_WORDS = frozenset({"HIDDEN", "СКРИЙ"})

#: ``headingDegrees`` — само четирите основни посоки, както в редактора.
_INITIAL_HEADINGS = {
    "RIGHT": 0.0, "НАДЯСНО": 0.0,
    "UP": 90.0, "НАГОРЕ": 90.0,
    "LEFT": 180.0, "НАЛЯВО": 180.0,
    "DOWN": -90.0, "НАДОЛУ": -90.0,
}

#: ``dimensionPositionOptions`` — и на двата езика, защото данните носят и
#: български („АВТО“, „ОТГОРЕ“), и английски („AUTO“) стойности.
_POSITIONS = {
    "AUTO": "AUTO", "АВТО": "AUTO",
    "TOP": "TOP", "ОТГОРЕ": "TOP",
    "BOTTOM": "BOTTOM", "ОТДОЛУ": "BOTTOM",
    "LEFT": "LEFT", "ВЛЯВО": "LEFT",
    "RIGHT": "RIGHT", "ВДЯСНО": "RIGHT",
    "HIDDEN": "HIDDEN", "СКРИЙ": "HIDDEN",
}


@dataclass(frozen=True)
class DraftSegment:
    """Един участък така, както рисувачът има нужда от него."""

    length_cm: float
    bend_after_deg: float | None = None
    bend_direction: str | None = None
    dimension_position: str = "AUTO"
    dimension_offset: float = 6.0

    @property
    def turns_left(self) -> bool:
        return str(self.bend_direction or "").strip().upper() in _LEFT_WORDS

    @property
    def position(self) -> str:
        return _POSITIONS.get(str(self.dimension_position or "AUTO").strip().upper(), "AUTO")


def initial_heading(value: str | None) -> float:
    """``headingDegrees`` — началната посока на първия участък."""

    return _INITIAL_HEADINGS.get(str(value or "RIGHT").strip().upper(), 0.0)


def segments_from_shape(
    shape: ShapeDefinition,
    lengths: Sequence[float],
) -> tuple[DraftSegment, ...]:
    """Участъците на формата с ПОДАДЕНИТЕ производствени дължини.

    Редът и завоите идват от дефиницията; дължините — от одобрения размер на
    конкретната позиция, не от контролния тест.
    """

    definitions: list[SegmentDefinition] = sorted(
        shape.segments, key=lambda item: int(item.index)
    )
    values = [float(value) for value in lengths]
    if len(definitions) != len(values):
        raise ValueError(
            f"{shape.code}: рисувачът очаква {len(definitions)} участъка, "
            f"получени са {len(values)}."
        )
    return tuple(
        DraftSegment(
            length_cm=value,
            bend_after_deg=item.bend_after_deg,
            bend_direction=item.bend_direction,
            dimension_position=item.dimension_position,
            dimension_offset=item.dimension_offset,
        )
        for item, value in zip(definitions, values)
    )


def physical_points(
    segments: Sequence[DraftSegment],
    *,
    initial_direction: str | None = "RIGHT",
) -> list[tuple[float, float]]:
    """``physicalPoints`` — върховете на веригата.

    Пази се уговорката на редактора: завоят важи ПОСЛЕ участъка и последният
    участък няма завой. „НАЛЯВО“ е плюс, тоест обратно на часовниковата
    стрелка. Схематичната посока на сегмента (``drawing_direction``) НЕ се
    ползва — редакторът не я чете и рисунката му е еталонът.
    """

    heading = initial_heading(initial_direction)
    points: list[tuple[float, float]] = [(0.0, 0.0)]
    последен = len(segments) - 1
    for index, segment in enumerate(segments):
        radians = math.radians(heading)
        x, y = points[-1]
        length = float(segment.length_cm or 0.0)
        points.append((x + length * math.cos(radians), y + length * math.sin(radians)))
        if index < последен:
            знак = 1.0 if segment.turns_left else -1.0
            heading += знак * float(segment.bend_after_deg or 0.0)
    return points


def separate_overlapping_ends(points: list[tuple[float, float]]) -> bool:
    """``separateOverlappingEnds`` — отмества последния участък, ако лежи
    върху първия.

    Само графично: реалните дължини не се менят. В редактора е отметка
    „Разделяй застъпващи се крайни сегменти“.
    """

    if len(points) < 4:
        return False
    first_start, first_end = points[0], points[1]
    last_start, last_end = points[-2], points[-1]
    v1 = (first_end[0] - first_start[0], first_end[1] - first_start[1])
    v2 = (last_end[0] - last_start[0], last_end[1] - last_start[1])
    len1 = math.hypot(*v1)
    len2 = math.hypot(*v2)
    if len1 < 1e-8 or len2 < 1e-8:
        return False
    cross = (v1[0] * v2[1] - v1[1] * v2[0]) / (len1 * len2)
    if abs(cross) > 0.001:
        return False

    def разстояние(point: tuple[float, float]) -> float:
        return abs(
            v1[0] * (point[1] - first_start[1]) - v1[1] * (point[0] - first_start[0])
        ) / len1

    tolerance = max(len1, len2) * 0.001 + 1e-7
    if разстояние(last_start) > tolerance or разстояние(last_end) > tolerance:
        return False
    unit = (v1[0] / len1, v1[1] / len1)

    def проекция(point: tuple[float, float]) -> float:
        return (point[0] - first_start[0]) * unit[0] + (point[1] - first_start[1]) * unit[1]

    последна = sorted((проекция(last_start), проекция(last_end)))
    overlap = min(len1, последна[1]) - max(0.0, последна[0])
    if overlap <= tolerance:
        return False

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    normal = (-unit[1], unit[0])
    centroid = (
        math.fsum(p[0] for p in points) / len(points),
        math.fsum(p[1] for p in points) / len(points),
    )
    first_mid = ((first_start[0] + first_end[0]) / 2, (first_start[1] + first_end[1]) / 2)
    outward = (first_mid[0] - centroid[0]) * normal[0] + (first_mid[1] - centroid[1]) * normal[1]
    if outward < 0:
        normal = (-normal[0], -normal[1])
    shift = min(4.0, max(1.5, span * 0.04))
    points[-2] = (last_start[0] + normal[0] * shift, last_start[1] + normal[1] * shift)
    points[-1] = (last_end[0] + normal[0] * shift, last_end[1] + normal[1] * shift)
    return True


def fit_draft(points: Sequence[tuple[float, float]]):
    """``fitDraft`` — вписва фигурата и обръща Y за екранните координати."""

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width = max(max(xs) - min(xs), 1.0)
    height = max(max(ys) - min(ys), 1.0)
    scale = min(_FIT_WIDTH / width, _FIT_HEIGHT / height)
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    return lambda point: (
        50 + (point[0] - center_x) * scale,
        50 - (point[1] - center_y) * scale,
    )


def choose_dimension_sign(
    position: str,
    normal: tuple[float, float],
    mid: tuple[float, float],
    center: tuple[float, float],
    index: int,
) -> float:
    """``chooseDimensionSign`` — от коя страна на участъка стои размерът."""

    if position == "TOP":
        return 1.0 if normal[1] <= 0 else -1.0
    if position == "BOTTOM":
        return 1.0 if normal[1] >= 0 else -1.0
    if position == "LEFT":
        return 1.0 if normal[0] <= 0 else -1.0
    if position == "RIGHT":
        return 1.0 if normal[0] >= 0 else -1.0
    outward = (mid[0] - center[0]) * normal[0] + (mid[1] - center[1]) * normal[1]
    if abs(outward) < _OUTWARD_EPSILON:
        return -1.0 if index % 2 == 0 else 1.0
    return 1.0 if outward >= 0 else -1.0


def fmt_draft(value: float) -> str:
    """``fmtDraft`` — цяло число без нули, иначе до два знака."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(number):
        return "0"
    if abs(number - round(number)) < 1e-8:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def draft_dimension_svg(
    start: tuple[float, float],
    end: tuple[float, float],
    segment: DraftSegment,
    index: int,
    center: tuple[float, float],
) -> str:
    """``draftDimensionSvg`` — размерът, УСПОРЕДЕН на участъка."""

    if segment.position == "HIDDEN":
        return ""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-7:
        return ""
    normal = (-dy / length, dx / length)
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    sign = choose_dimension_sign(segment.position, normal, mid, center, index)
    try:
        заявен = float(segment.dimension_offset)
    except (TypeError, ValueError):
        заявен = 6.0
    if not math.isfinite(заявен) or заявен == 0:
        заявен = 6.0
    offset = min(_OFFSET_MAX, max(_OFFSET_MIN, заявен))
    nx, ny = normal[0] * sign, normal[1] * sign
    a = (start[0] + nx * offset, start[1] + ny * offset)
    b = (end[0] + nx * offset, end[1] + ny * offset)
    text_point = (mid[0] + nx * (offset + _TEXT_LIFT), mid[1] + ny * (offset + _TEXT_LIFT))
    rotation = math.degrees(math.atan2(dy, dx))
    if rotation > 90:
        rotation -= 180
    if rotation < -90:
        rotation += 180
    надпис = f"{fmt_draft(segment.length_cm)} cm"
    return (
        f'<g class="draft-dimension" data-segment="{index + 1}">'
        f'<line class="draft-extension" x1="{start[0]:g}" y1="{start[1]:g}" '
        f'x2="{a[0]:g}" y2="{a[1]:g}"/>'
        f'<line class="draft-extension" x1="{end[0]:g}" y1="{end[1]:g}" '
        f'x2="{b[0]:g}" y2="{b[1]:g}"/>'
        f'<line class="draft-dimension-line" x1="{a[0]:g}" y1="{a[1]:g}" '
        f'x2="{b[0]:g}" y2="{b[1]:g}"/>'
        f'<text class="draft-dimension-text" x="{text_point[0]:g}" y="{text_point[1]:g}" '
        'text-anchor="middle" dominant-baseline="central" '
        f'transform="rotate({rotation:g} {text_point[0]:g} {text_point[1]:g})">{надпис}</text>'
        "</g>"
    )


#: Стиловете и стрелките са дословно тези на редактора.
_DEFS = (
    "<defs><style>"
    ".safe-zone{fill:none;stroke:#cbd5e1;stroke-width:.45;stroke-dasharray:2 2}"
    ".draft-rebar{fill:none;stroke:#172033;stroke-width:1.05;stroke-linecap:round;"
    "stroke-linejoin:round}"
    ".draft-dimension-line{stroke:#2f6698;stroke-width:.4;"
    "marker-start:url(#draftArrowStart);marker-end:url(#draftArrowEnd)}"
    ".draft-extension{stroke:#7f96ad;stroke-width:.3;stroke-dasharray:1 1}"
    ".draft-dimension-text{fill:#244f7d;stroke:#fff;stroke-width:1.2;paint-order:stroke;"
    "font-size:2.65px;font-weight:800}"
    ".draft-point{fill:#c78a2a;stroke:#fff;stroke-width:.7}"
    "</style>"
    '<marker id="draftArrowStart" viewBox="0 0 10 10" refX="5" refY="5" '
    'markerWidth="2.5" markerHeight="2.5" orient="auto-start-reverse">'
    '<path d="M 9 1 L 1 5 L 9 9" fill="none" stroke="#2f6698" stroke-width="1.4"/></marker>'
    '<marker id="draftArrowEnd" viewBox="0 0 10 10" refX="5" refY="5" '
    'markerWidth="2.5" markerHeight="2.5" orient="auto">'
    '<path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#2f6698" stroke-width="1.4"/></marker>'
    "</defs>"
)


def render_draft_svg(
    segments: Sequence[DraftSegment],
    *,
    initial_direction: str | None = "RIGHT",
    separate_overlap: bool = False,
    include_safe_zone: bool = False,
    include_end_points: bool = False,
) -> str:
    """``renderDraftDrawing`` — цялата рисунка.

    ``include_safe_zone`` и ``include_end_points`` са изключени по
    подразбиране: пунктираната рамка и оранжевите точки са помощни за
    редактора, а в описа рисунката стои сама в клетка.
    """

    if not segments:
        raise ValueError("Формата няма нито един участък.")
    points = physical_points(segments, initial_direction=initial_direction)
    if separate_overlap:
        separate_overlapping_ends(points)
    tx = fit_draft(points)
    screen = [tx(point) for point in points]
    center = (
        math.fsum(p[0] for p in screen) / len(screen),
        math.fsum(p[1] for p in screen) / len(screen),
    )
    svg = _DEFS
    if include_safe_zone:
        svg += '<rect class="safe-zone" x="10" y="12" width="80" height="76" rx="1"/>'
    polyline = " ".join(f"{p[0]:g},{p[1]:g}" for p in screen)
    svg += f'<polyline class="draft-rebar" points="{polyline}"/>'
    for index, segment in enumerate(segments):
        svg += draft_dimension_svg(screen[index], screen[index + 1], segment, index, center)
    if include_end_points:
        начало, край = screen[0], screen[-1]
        svg += (
            f'<circle class="draft-point" cx="{начало[0]:g}" cy="{начало[1]:g}" r="1.15"/>'
            f'<circle class="draft-point" cx="{край[0]:g}" cy="{край[1]:g}" r="1.15"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEW_BOX}" '
        'preserveAspectRatio="xMidYMid meet" role="img">' + svg + "</svg>"
    )


def render_shape_draft_svg(
    shape: ShapeDefinition,
    lengths: Sequence[float],
    **kwargs,
) -> str:
    """Рисунката на една Engine форма с дадените производствени размери."""

    return render_draft_svg(
        segments_from_shape(shape, lengths),
        initial_direction=shape.initial_direction,
        **kwargs,
    )
