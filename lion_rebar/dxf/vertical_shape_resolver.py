"""Извеждане на MASTER форма и производствени сегменти от котировки.

Предназначение: чертежи на вертикални елементи (колони, шайби, ядра), при
които проектантът изписва размерите на стремената като ``DIMENSION``
котировки на отделен слой, а не като свободен текст.

ПРИНЦИП
-------
Общата дължина от надписа е АВТОРИТЕТ. Котировките казват кои размери са
налице; последният сегмент се ИЗЧИСЛЯВА от уравнението, за да е точен.
Така в описа влиза ``210``, а не измереното ``209.8``.

Уравнения (изведени от реален чертеж, не предположени):

  затворено стреме   ``2*(широчина + височина) + 2*кука + добавка = L``
  отворени форми     ``сума(сегменти) = L``

Добавката за огъване е измерена: при 43 от 60 стремета разликата е точно
5.0 cm; при единични случаи 4 или 6. Затова се приемат три стойности с
предпочитание към 5.

FAIL-CLOSED
-----------
Ако уравнението не се затваря с наличните котировки, функцията връща
``None``. Формата остава неопределена и позицията отива при оператора.
Никога не се гадае по диаметър и дължина.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Sequence

from lion_rebar.engine.bend_allowance import segments_match_total

#: Радиус, в който котировка се смята за принадлежаща на позицията.
DIMENSION_RADIUS = 260.0

#: Праг за етикет на група ("Надл.арм.", "Есове"). Измерено на реален
#: чертеж: верните етикети са на 22-79 единици, чуждите на 222-239.
LABEL_GATE = 120.0

#: Етикет отстъпва пред стреме само ако е по-далеч от това разстояние И
#: стремето е доказано с три измерени котировки.
LABEL_OVERRIDE_DISTANCE = 100.0

#: Добавки за огъване при затворено стреме, по ред на предпочитание.
STIRRUP_ALLOWANCES = (5.0, 6.0, 4.0)

#: Толеранс на уравнението след закръгляне на котировките.
EXACT_TOLERANCE = 0.05

#: Минимален смислен производствен размер в cm.
MIN_SEGMENT_CM = 4.0

#: Колко котировки най-много се разглеждат около една позиция.
MAX_CANDIDATE_DIMENSIONS = 14


def snap(value: float) -> float:
    """Привежда измерена котировка към производствен размер.

    ``DIMENSION`` дава реално измерено число (209.8, 29.8, 25.2).
    Производственият размер е кръгъл.
    """

    nearest = round(value)
    if abs(value - nearest) <= 0.4:
        return float(nearest)
    return round(value * 2) / 2


@dataclass(frozen=True)
class ShapeEvidence:
    """Изведена форма с доказателството зад нея."""

    shape_code: str
    segments: tuple[float, ...]
    source: str
    confidence: str
    allowance: float = 0.0
    measured_count: int = 0
    computed_count: int = 0
    #: Посоките на начертаните прави участъци, когато формата е изведена от
    #: самата геометрия, а не от котировки. Пази се, за да може одобряващият
    #: гейт да ПРЕизведе формата от измереното, вместо да вярва на кода --
    #: виж ``proxy_adapter._read_declared_three_leg_shape``.
    leg_directions_deg: tuple[float, ...] = ()
    #: Ъгълът на редукцията при F002, изведен от надписаните числа
    #: (``proxy_adapter.reduction_offset_angle_deg``), а не от начертаната
    #: посока -- за да излезе отместването точно както го е дал проектантът.
    reduction_angle_deg: float | None = None

    def to_dict(self) -> dict:
        return {
            "shape_code": self.shape_code,
            "segments": list(self.segments),
            "source": self.source,
            "confidence": self.confidence,
            "bend_allowance_cm": self.allowance,
            "measured_dimensions": self.measured_count,
            "computed_dimensions": self.computed_count,
        }


@dataclass
class _Candidates:
    values: list[float] = field(default_factory=list)
    distance: dict[float, float] = field(default_factory=dict)


def collect_dimensions(point, dimensions, radius: float = DIMENSION_RADIUS) -> _Candidates:
    """Събира близките котировки, закръглени до производствени размери."""

    near: list[tuple[float, float]] = []
    for record in dimensions:
        measurement = record.get("measurement") if isinstance(record, dict) else None
        position = record.get("point") if isinstance(record, dict) else None
        if not measurement or not position:
            continue
        distance = math.hypot(position[0] - point[0], position[1] - point[1])
        if distance <= radius:
            near.append((distance, snap(float(measurement))))
    near.sort()
    result = _Candidates()
    for distance, value in near:
        if value < MIN_SEGMENT_CM:
            continue
        if value not in result.distance:
            result.distance[value] = distance
        if value not in result.values:
            result.values.append(value)
        if len(result.values) >= MAX_CANDIDATE_DIMENSIONS:
            break
    return result


#: Максимално разминаване между сбора на НАДПИСАНИТЕ размери и общата
#: дължина, при което позицията се приема. Разликата идва от огъвките и е
#: отговорност на проектанта, не наша. Над този праг позицията отива при
#: оператора -- вероятно котировките са от съседна фигура.
BEND_DISCREPANCY_LIMIT_CM = 8.0


def infer_preferred_gap(gaps) -> float | None:
    """Обичайната добавка за огъване, измерена от този чертеж."""

    import collections

    counter = collections.Counter(
        round(float(value), 1) for value in gaps if 0 <= float(value) <= 12)
    if not counter:
        return None
    value, occurrences = counter.most_common(1)[0]
    return value if occurrences >= 3 else None


def infer_preferred_hook(rows_dimensions) -> float | None:
    """Извежда обичайната кука на проектанта от самия чертеж.

    Не е статистика върху чужди проекти: това е конвенция В ТОЗИ файл.
    Взима се най-често срещаният малък размер (4-20 cm) около стремената.
    Ползва се САМО за разчистване на двусмислие, никога за измисляне на
    стойност, която я няма в чертежа.
    """

    import collections

    counter = collections.Counter()
    for value in rows_dimensions:
        if 4.0 <= value <= 20.0:
            counter[value] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _read_stirrup(values, distance_of, length_cm, preferred_hook=None,
                  preferred_gap=None):
    """Чете НАДПИСАНИТЕ размери на стреме: две страни и кука.

    Product Owner (2026-08-25): описва се каквото пише в чертежа, дори
    сборът с огъвките да не дава точно общата дължина. Затова размерите се
    ЧЕТАТ по близост, а уравнението служи само за проверка.
    """

    if len(values) < 2:
        return None
    nearest = sorted(values, key=lambda v: distance_of.get(v, DIMENSION_RADIUS))
    # Куката е най-малкият от близките размери; страните са останалите два.
    best = None
    # Прозорецът за страните е по-широк от този за куката.
    # Причината е в разположението, не в типа елемент: дългата страна се
    # коти ПОД контура, докато надписът стои встрани, затова разстоянието
    # расте с дължината на стремето. Куката обратно се коти до ъгъла и е
    # винаги близо. Реален случай, проверен в чертежа: 1x(25N8x395) е
    # стреме 165 x 20 с кука 10; котата 165 е дванадесета по близост.
    for hook in nearest[:5]:
        sides = [v for v in nearest[:12] if v != hook and v > hook]
        if len(sides) < 1:
            continue
        for width in sides:
            for height in sides:
                if height > width:
                    continue
                total = 2 * (width + height) + 2 * hook
                gap = length_cm - total
                if not 0 <= gap <= BEND_DISCREPANCY_LIMIT_CM:
                    continue
                # Подредба на кандидатите:
                #   1) най-малка кука -- тя е най-късият размер по дефиниция;
                #   2) най-близки котировки -- надписаните до контура са
                #      верните, съседните фигури са по-далеч.
                #
                # По-ранна версия предпочиташе комбинации с ТРИ различни
                # числа. Това е грешно: квадратното стреме 20x20 има само
                # две, а изкуственото 25x14 има три и печелеше. Реален
                # случай: 50N8x105 излизаше 25 x 14 вместо 20 x 20.
                gap_rank = 0 if (preferred_gap is not None
                                 and abs(gap - preferred_gap) <= 0.05) else 1
                score = (
                    gap_rank,
                    hook,
                    distance_of.get(hook, DIMENSION_RADIUS)
                    + distance_of.get(width, DIMENSION_RADIUS)
                    + distance_of.get(height, DIMENSION_RADIUS),
                )
                if best is None or score < best[0]:
                    best = (score, [width, height, hook], gap)
    return best


def _printed_pair_with_bend_allowance(values, distance_of, length_cm: float):
    """Двата НАДПИСАНИ размера при F004, когато остатъкът е добавка за огъване.

    Досега вторият размер се изчисляваше като ``L - първия`` и в описа
    влизаше рамо, каквото в чертежа няма: при ``28N12x455`` се получаваше
    440 + 15, докато проектантът е начертал 440 и 13. Собственик,
    21.09.2026: „задавайки правото и куката останалото да остава за
    огъвката“.

    Остатъкът се приема само по правилото за закръгляне
    (``engine.bend_allowance``), не по плосък таван.

    ОТКАЗ ПРИ ДВЕ СЪВПАДЕНИЯ: ако две различни двойки надписани размери
    минават правилото и по-близката не е ЯВНО по-близка, не се връща нищо —
    позицията отива при оператора. Близост не е принадлежност.
    """

    qualified: list[tuple[float, float, float]] = []
    for first, second in itertools.permutations(values, 2):
        if first <= second:
            continue
        total = first + second
        if abs(total - length_cm) <= EXACT_TOLERANCE:
            continue  # точното затваряне вече е намерено по друг път
        if not segments_match_total("F004", total, length_cm,
                                    tolerance=EXACT_TOLERANCE):
            continue
        span = (distance_of.get(first, DIMENSION_RADIUS)
                + distance_of.get(second, DIMENSION_RADIUS))
        qualified.append((span, first, second))
    if not qualified:
        return None
    qualified.sort()
    if len(qualified) > 1 and qualified[1][0] < qualified[0][0] * 2:
        return None
    span, first, second = qualified[0]
    return first, second, span


def derive_shape(point, dimensions, length_cm: float,
                 expected_shape: str | None = None,
                 preferred_hook: float | None = None,
                 preferred_gap: float | None = None) -> ShapeEvidence | None:
    """Извежда производствените размери от котировките.

    ПРИНЦИП (Product Owner, 2026-08-25): в описа влиза това, което е
    НАДПИСАНО в чертежа. Уравнението не избира размерите -- то проверява
    дали намерените са правдоподобни. Разлика до 8 cm се приема като
    добавка за огъване; над това позицията отива при оператора.

    ``expected_shape`` ограничава търсенето до вече доказаната от matcher-а
    форма: задачата е „намери размерите“, а не „познай формата“.
    """

    if not length_cm or length_cm <= 0:
        return None
    candidates = collect_dimensions(point, dimensions)
    values = candidates.values
    if not values:
        return None
    distance_of = candidates.distance
    wanted = str(expected_shape or "").strip().upper()

    def measured(items) -> int:
        return len({v for v in items if v in distance_of})

    # ---- затворено стреме: размерите се ЧЕТАТ ----
    if wanted in ("", "F006"):
        read = _read_stirrup(values, distance_of, length_cm, preferred_hook,
                             preferred_gap)
        if read is not None:
            _score, segments, gap = read
            return ShapeEvidence(
                shape_code="F006",
                segments=tuple(segments),
                source="НАДПИСАНИ КОТИРОВКИ",
                confidence="HIGH" if measured(segments) >= 3 else "MEDIUM",
                allowance=round(gap, 1),
                measured_count=measured(segments),
                computed_count=3 - measured(segments),
            )
        if wanted == "F006":
            return None

    # ---- отворени форми: сумата на надписаните дава дължината ----
    solutions = []
    for leg in values:
        middle = snap(length_cm - 2 * leg)
        if middle > leg and abs(2 * leg + middle - length_cm) <= EXACT_TOLERANCE:
            solutions.append((3 - measured([leg, middle]),
                              distance_of.get(leg, DIMENSION_RADIUS),
                              "F003", (leg, middle, leg)))
    for middle in values:
        leg = snap((length_cm - middle) / 2)
        if 0 < leg < middle and abs(2 * leg + middle - length_cm) <= EXACT_TOLERANCE:
            solutions.append((3 - measured([leg, middle]),
                              distance_of.get(middle, DIMENSION_RADIUS),
                              "F003", (leg, middle, leg)))
    for first in values:
        second = snap(length_cm - first)
        if second > 0 and second != first and abs(first + second - length_cm) <= EXACT_TOLERANCE:
            solutions.append((3 - measured([first, second]),
                              distance_of.get(first, DIMENSION_RADIUS) + 25,
                              "F004", (first, second)))
    # Само когато кодът НАКРАЯ ще е F004. При ``wanted`` F002 и F007 долният
    # ред преозначава намереното (``code = wanted``), а F002 няма добавка за
    # огъване: двойката би спечелила подредбата и после щеше да падне на
    # MASTER проверката — позиция, която днес минава, щеше да се откаже.
    if wanted not in ("F002", "F007"):
        printed_pair = _printed_pair_with_bend_allowance(values, distance_of, length_cm)
        if printed_pair is not None:
            first, second, span = printed_pair
            solutions.append((3 - measured([first, second]), span, "F004",
                              (first, second)))
    if wanted:
        allowed = {"F007": {"F003", "F007"}, "F002": {"F004", "F002"}}.get(wanted, {wanted})
        solutions = [item for item in solutions if item[2] in allowed]
    if not solutions:
        return None
    solutions.sort(key=lambda item: (item[0], item[1]))
    best = solutions[0]
    code = wanted if wanted in ("F007", "F002") else best[2]
    return ShapeEvidence(
        shape_code=code,
        segments=tuple(best[3]),
        source="НАДПИСАНИ КОТИРОВКИ",
        confidence="HIGH" if best[0] <= 1 else "MEDIUM",
        measured_count=3 - best[0],
        computed_count=best[0],
    )


def label_kind(text: str) -> str | None:
    """Разпознава етикет на група в чертежа на вертикални елементи."""

    normalized = " ".join(str(text or "").split()).replace("%%U", "").upper()
    if "НАДЛ" in normalized or "ВЕРТ.АРМИРОВКА" in normalized:
        return "LONGITUDINAL"
    if "ЕСОВЕ" in normalized:
        return "S_HOOK"
    if "СТРЕМЕНА" in normalized:
        return "STIRRUP"
    return None


def nearest_label(point, labels) -> tuple[str | None, float]:
    best_kind, best_distance = None, math.inf
    for item in labels:
        position = item.get("point")
        if not position:
            continue
        distance = math.hypot(position[0] - point[0], position[1] - point[1])
        if distance < best_distance:
            best_distance = distance
            best_kind = label_kind(item.get("text"))
    if best_kind is None or best_distance > LABEL_GATE:
        return None, best_distance
    return best_kind, best_distance


def resolve_vertical_shape(point, length_cm, labels, dimensions,
                           *, preferred_hook: float | None = None,
                           preferred_gap: float | None = None) -> ShapeEvidence | None:
    """Определя формата, комбинирайки етикет и котировки.

    Приоритет: етикетът е авторитет за надлъжната армировка и есовете;
    отстъпва само пред стреме, доказано с три измерени котировки и точно
    уравнение (реален случай: 2184 бр. Ø8 L=110 е стреме 22x20, а стоеше
    на границата на радиуса от етикет "Надл.арм.").
    """

    kind, distance = nearest_label(point, labels)
    # Конвенциите на проектанта се предават и тук: без тях обвивката работи
    # с по-малко информация от самия резолвер и връща по-слаб резултат.
    evidence = derive_shape(point, dimensions, length_cm,
                            preferred_hook=preferred_hook,
                            preferred_gap=preferred_gap)

    if kind == "LONGITUDINAL":
        if (evidence is not None and evidence.shape_code == "F006"
                and evidence.confidence == "HIGH"
                and distance > LABEL_OVERRIDE_DISTANCE):
            return evidence
        return ShapeEvidence("F001", (), f"ЕТИКЕТ НАДЛ.АРМ. (d={distance:.0f})", "HIGH")
    if kind == "S_HOOK":
        segments = evidence.segments[:3] if evidence and len(evidence.segments) >= 3 else ()
        return ShapeEvidence(
            "F007", segments,
            f"ЕТИКЕТ ЕСОВЕ (d={distance:.0f})",
            "HIGH" if segments else "MEDIUM")
    if kind == "STIRRUP" and evidence is not None and evidence.shape_code == "F006":
        return evidence
    return evidence


# ---------------------------------------------------------------- принадлежност

#: Максимално отстояние на опорна точка от връх на контура, за да се смята
#: котата за НЕГОВА. Измерено в реален чертеж: собствените коти лежат на
#: 0.0-1.5 единици от върха, чуждите са на десетки.
OWNERSHIP_TOLERANCE = 3.0


def _vertices(entity) -> list[tuple[float, float]]:
    geometry = getattr(entity, "geometry", None) or {}
    points = geometry.get("points") or []
    return [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]


def _closest_vertex_distance(point, vertices) -> float:
    if not vertices:
        return math.inf
    return min(math.hypot(point[0] - x, point[1] - y) for x, y in vertices)


def dimensions_owned_by_contours(entities) -> dict[str, list[float]]:
    """Кои котировки принадлежат на кой контур.

    ПРИНАДЛЕЖНОСТ, НЕ БЛИЗОСТ. Котировката носи двете точки, между които
    мери. Ако и двете лежат върху върховете на един контур, тя описва
    ИМЕННО него — това е връзка, начертана от проектанта, а не преценка по
    разстояние.

    Измерено в реален чертеж: опорните точки на собствените коти са на
    0.0 единици от върха; чуждите са на десетки. Няма сива зона.
    """

    contours = {
        str(entity.handle): _vertices(entity)
        for entity in entities
        if getattr(entity, "entity_type", "") == "LWPOLYLINE"
        and _vertices(entity)
    }
    owned: dict[str, list[float]] = {}
    for entity in entities:
        if getattr(entity, "entity_type", "") != "DIMENSION":
            continue
        geometry = getattr(entity, "geometry", None) or {}
        measurement = geometry.get("measurement")
        first, second = geometry.get("defpoint2"), geometry.get("defpoint3")
        if not measurement or not first or not second:
            continue
        best_handle, best_score = None, math.inf
        for handle, vertices in contours.items():
            score = (_closest_vertex_distance(first, vertices)
                     + _closest_vertex_distance(second, vertices))
            if score < best_score:
                best_handle, best_score = handle, score
        if best_handle is not None and best_score <= OWNERSHIP_TOLERANCE:
            owned.setdefault(best_handle, []).append(snap(float(measurement)))
    return owned


def strict_unique_dimension_records_by_contours(
    entities,
    *,
    ownership_tolerance: float = OWNERSHIP_TOLERANCE,
    ambiguity_margin: float = OWNERSHIP_TOLERANCE,
) -> dict[str, list[dict[str, float | str | None]]]:
    """Return DIMENSION records only when one contour owns both endpoints.

    This stricter allocator is intentionally separate from the historical
    ``dimensions_owned_by_contours`` API, whose established callers keep their
    existing behaviour.  It is used by safety-critical recognition paths that
    must reject overlapping contours: the best contour has to pass the normal
    endpoint-distance gate, and *each endpoint* must have the same unique
    owner. Every endpoint runner-up must be farther away by more than one full
    ownership tolerance. Equal, near-equal and shared-vertex candidates
    therefore fail-closed instead of being assigned by iteration order.
    """

    contours = {
        str(entity.handle): _vertices(entity)
        for entity in entities
        if getattr(entity, "entity_type", "") == "LWPOLYLINE"
        and _vertices(entity)
    }
    owned: dict[str, list[dict[str, float | str | None]]] = {}
    for entity in entities:
        if getattr(entity, "entity_type", "") != "DIMENSION":
            continue
        geometry = getattr(entity, "geometry", None) or {}
        measurement = geometry.get("measurement")
        first, second = geometry.get("defpoint2"), geometry.get("defpoint3")
        if not measurement or not first or not second:
            continue
        first_ranked = sorted(
            (
                (_closest_vertex_distance(first, vertices), handle)
                for handle, vertices in contours.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
        second_ranked = sorted(
            (
                (_closest_vertex_distance(second, vertices), handle)
                for handle, vertices in contours.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
        if not first_ranked or not second_ranked:
            continue

        first_score, first_handle = first_ranked[0]
        second_score, second_handle = second_ranked[0]
        if first_handle != second_handle:
            continue
        if (
            first_score > float(ownership_tolerance)
            or second_score > float(ownership_tolerance)
            or first_score + second_score > float(ownership_tolerance)
        ):
            continue

        first_runner = first_ranked[1][0] if len(first_ranked) > 1 else None
        second_runner = second_ranked[1][0] if len(second_ranked) > 1 else None
        first_margin = (
            first_runner - first_score if first_runner is not None else None
        )
        second_margin = (
            second_runner - second_score if second_runner is not None else None
        )
        if (
            first_margin is not None
            and first_margin <= float(ambiguity_margin)
        ) or (
            second_margin is not None
            and second_margin <= float(ambiguity_margin)
        ):
            continue

        owned.setdefault(first_handle, []).append(
            {
                "handle": str(getattr(entity, "handle", "")),
                "measurement_cm": snap(float(measurement)),
                "ownership_score_raw": float(first_score + second_score),
                "first_endpoint_score_raw": float(first_score),
                "second_endpoint_score_raw": float(second_score),
                "first_runner_up_score_raw": (
                    float(first_runner) if first_runner is not None else None
                ),
                "second_runner_up_score_raw": (
                    float(second_runner) if second_runner is not None else None
                ),
                "first_ownership_margin_raw": (
                    float(first_margin) if first_margin is not None else None
                ),
                "second_ownership_margin_raw": (
                    float(second_margin) if second_margin is not None else None
                ),
            }
        )
    return owned


def read_owned_stirrup(values: Sequence[float],
                       preferred_hook: float | None = None) -> list[float] | None:
    """Чете размерите на стреме от СОБСТВЕНИТЕ му котировки.

    Не комбинира различни контури. Повече от три различни стойности са
    двусмислие и остават за преглед; проверката срещу общата дължина се
    прави от production gate-а, който познава надписа на позицията.

    Правило за подредба: най-голямата стойност е широчината, най-малката е
    куката, средната е височината. При две стойности стремето е квадратно.
    """

    unique = sorted({value for value in values if value >= MIN_SEGMENT_CM},
                    reverse=True)
    if len(unique) == 3:
        return [unique[0], unique[1], unique[2]]
    if len(unique) == 2:
        width, hook = unique
        if preferred_hook is not None and hook != preferred_hook:
            return None
        return [width, width, hook]
    return None
