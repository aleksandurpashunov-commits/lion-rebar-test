"""Разделител: армировъчна геометрия срещу контури, оси, размери и символи.

Защо съществува: съдържателното правило в ``convention_profile`` обявява слой за
армировъчен, когато достатъчно надписа намират до себе си обект с точно своята
надписана дължина, с контрола при дължина + 13 cm. Измерено на ``d-2`` това
прие ``AutoCAD_Structural_Detailing_Slab contour`` — **74 надписа съвпадат по
дължина, контролата 1**. Плочата не е армировка. Обявяването ѝ влоши проекта:
124 → 105 преминали позиции, анализът от 6,3 s на 128,9 s.

Изводът от измерването: **съвпадението по дължина не разделя**. Един контур на
плоча е начертан с десетки прави отсечки и някои от тях са дълги точно колкото
пише в съседния надпис. Разликата не е в дължината, а в **това как обектът е
свързан**:

* армировъчният прът е **отворен път с два свободни края** — началото и краят му
  не срещат друг обект от същия слой;
* контурът е **затворена верига** — всяка отсечка среща следващата с двата си
  края, защото огражда област.

Затова модулът иска **съвместно** доказателство: и надписната следа (локално
съвпадение по дължина с контрола), и структурната (заявените обекти са отворени
пътища, а не звена от верига). Само едното не стига в никоя от двете посоки.

Модулът НИЩО не решава сам и нищо не променя: той връща присъда, оценка,
измерените признаци и обяснение на български. Няма запис на диск, няма глобално
състояние, няма ``eval``. Присъдите са три и „не знам“ е пълноправна:
``rebar`` / ``not_rebar`` / ``unknown``. При тънко доказателство се връща
``unknown`` — отказът е по-добър от догадката.

Праговете са **измерени** върху 11 проекта, не избрани. Виж докстринга при
всяка константа. Няма имена на файлове, handle-и и фиксирани размери като
изключения: единственото име, което се чете, е кодираният диаметър в името на
слоя (``N12``) — съществуваща конвенция на чертожните диалекти, вече ползвана в
``geometry_candidates._diameter_from_layer``.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.recognition_policy import (
    DEFAULT_RECOGNITION_POLICY,
    RecognitionPolicy,
)
from lion_rebar.dxf.text_parser import looks_like_rebar_text, parse_rebar_text

REBAR_GEOMETRY_EVIDENCE_SCHEMA = "lion-rebar-rebar-geometry-evidence/v1"
EVIDENCE_VERSION = "1.0"

#: Присъди. Три, защото „не знам“ е отговор, а не липса на отговор.
VERDICT_REBAR = "rebar"
VERDICT_NOT_REBAR = "not_rebar"
VERDICT_UNKNOWN = "unknown"

# --------------------------------------------------------------------------- #
# Измерени прагове
#
# Мерено на 2026-09-27 върху 11 проекта (d-1, d-2, s-7…s-11, Тест-002, Тест-003,
# тест-004, тест-006) от ``working/extracted_entities.json``, с еталони:
#   армировка  — d-2 „Surface reinforcement - bars - bottom/top“, s-8/s-9
#                „Arm - N10/N12/N14“, s-9 и тест-006 „ARM_Bars“, s-11 и
#                тест-004 „Stremena“, тест-006 „Arm - N16“, Тест-003 „ARM.1“
#                и „ARM.MR.2“;
#   не е армировка — d-2 „Slab contour“, „found_slab“, „col_wall“, „axes“,
#                „symbol“, „hatch*“, Тест-003 „TEKST NA *“.

#: Минимум надписи с локално съвпадение по дължина. Взето непроменено от
#: измереното правило в ``convention_profile`` (истинските слоеве дават 4–170,
#: шумът 1–2). Тук НЕ се вдига и НЕ се пада: новото е структурната проверка,
#: която маха погрешните приемания, а не по-строга бройка.
MIN_LENGTH_MATCHES = 5

#: Превес над контролата при дължина + 13 cm — също непроменен.
MIN_CONTROL_RATIO = 4.0

#: Дял на ЗАЯВЕНИТЕ обекти (тези, които някой надпис е намерил по своята
#: дължина), които са свързани с двата си края или са сами по себе си затворени.
#: Това е признакът, който разделя. Измерено: при петнайсетте еталонни
#: армировъчни слоя дялът е **0,00** (най-лошият — Тест-002 „s-Reinf“ — 0,01);
#: при ``Slab contour`` е **1,00** (всичките 148 заявени отсечки са звена от
#: веригата на контура), при Тест-003 „SHAIBI“ (затворени правоъгълници) — също
#: 1,00. Прагът е сложен близо до армировъчната страна, защото погрешното
#: приемане е по-скъпо от пропуска.
MAX_CLAIMED_BOUND_SHARE = 0.25

#: Кодиран диаметър в името на слоя: по-ниска бройка се приема САМО когато
#: името и надписите се потвърждават взаимно и контролата е нула. Измерено:
#: единственият слой, който това добавя, е тест-006 „Arm - N16“ (3 надписа, 3 от
#: 3 съгласни по Ø16, контрола 0). В целия корпус слоеве с кодиран диаметър са
#: само „Arm - N10/N12/N14/N16“ в s-8, s-9 и тест-006 — всичките армировъчни,
#: тоест тази врата не отваря нито едно погрешно приемане.
CODED_DIAMETER_MIN_MATCHES = 3

#: Под толкова заявени обекта слоят не се обявява за НЕ-армировъчен, а остава
#: ``unknown``: един затворен обект не доказва, че слоят е контур. Измерено:
#: ``Slab contour`` има 148 заявени обекта — отказът там е върху много; Тест-003
#: „SHAIBI“ има 1 и остава „не знам“. И в двата случая слоят не се приема.
MIN_CLAIMED_FOR_REFUSAL = 3

#: Дължини под това не се броят за геометрия — както в ``convention_profile``.
MIN_OBJECT_LENGTH_RAW = 5.0

#: Допуск при сравнение на дължини — 1% или поне 1 cm, както в правилото.
LENGTH_TOLERANCE = 0.01

#: Контролата против случайно съвпадение — същата стойност, същият смисъл.
CONTROL_SHIFT_CM = 13.0

#: Кодираният диаметър в името на слоя. Същата конвенция като
#: ``geometry_candidates._diameter_from_layer`` — едно място по-малко за
#: разминаване, ако диалектът се промени.
_LAYER_DIAMETER_RE = re.compile(r"(?:^|[^A-Z0-9])N\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Резултат


@dataclass(frozen=True)
class ObjectEvidence:
    """Присъда за ЕДИН обект: отворен път ли е, който надпис го е заявил."""

    handle: str
    layer: str
    entity_type: str
    length_raw: float
    segment_count: int
    self_closed: bool
    free_end_count: int
    claiming_annotations: tuple[str, ...]
    verdict: str
    why: str

    @property
    def bound_both_ends(self) -> bool:
        return self.self_closed or self.free_end_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "layer": self.layer,
            "entity_type": self.entity_type,
            "length_raw": round(float(self.length_raw), 3),
            "segment_count": self.segment_count,
            "self_closed": self.self_closed,
            "free_end_count": self.free_end_count,
            "claiming_annotations": list(self.claiming_annotations),
            "verdict": self.verdict,
            "why": self.why,
        }


@dataclass(frozen=True)
class LayerEvidence:
    """Присъда за един слой заедно с измереното, което я е дало."""

    layer: str
    verdict: str
    score: float
    signals: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    gates: dict[str, bool] = field(default_factory=dict)
    sample_handles: tuple[str, ...] = ()
    objects: tuple[ObjectEvidence, ...] = ()

    @property
    def is_rebar(self) -> bool:
        return self.verdict == VERDICT_REBAR

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "verdict": self.verdict,
            "score": round(float(self.score), 3),
            "signals": dict(self.signals),
            "gates": dict(self.gates),
            "why": self.why,
            "sample_handles": list(self.sample_handles),
        }


@dataclass(frozen=True)
class RebarGeometryEvidence:
    """Всичко измерено за един чертеж. Само данни — нищо не се изпълнява."""

    schema: str
    version: str
    layers: dict[str, LayerEvidence]
    annotations_with_length: int
    linear_objects: int
    endpoint_tolerance_raw: float
    search_radius_raw: float

    def verdict(self, layer: str) -> str:
        evidence = self.layers.get(str(layer or ""))
        return evidence.verdict if evidence else VERDICT_UNKNOWN

    def rebar_layers(self) -> tuple[str, ...]:
        return tuple(sorted(
            name for name, ev in self.layers.items() if ev.verdict == VERDICT_REBAR
        ))

    def refused_layers(self) -> tuple[str, ...]:
        """Слоевете с надписна следа, но структурно отказани — екранът на
        оператора има нужда точно от тях: намерена е дължина, но обектите са
        затворена верига."""

        return tuple(sorted(
            name for name, ev in self.layers.items() if ev.verdict == VERDICT_NOT_REBAR
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "annotations_with_length": self.annotations_with_length,
            "linear_objects": self.linear_objects,
            "endpoint_tolerance_raw": self.endpoint_tolerance_raw,
            "search_radius_raw": self.search_radius_raw,
            "layers": {name: ev.to_dict() for name, ev in sorted(self.layers.items())},
        }


# --------------------------------------------------------------------------- #
# Вход: и ``DxfEntityRecord``, и обикновен речник от ``extracted_entities.json``


def _attr(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _polyline_points(geometry: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Върховете на линеен обект, както екстракторът ги пази.

    Същият прочит като в ``convention_profile._polyline_points`` — дължината,
    срещу която меря, трябва да е ТАЗИ, която правилото сравнява с надписа.
    """

    for key in ("points", "vertices", "polyline_points"):
        raw = geometry.get(key)
        if isinstance(raw, list) and len(raw) >= 2:
            points = [p for p in (_point(v) for v in raw) if p is not None]
            if len(points) >= 2:
                return points
    start = _point(geometry.get("start"))
    end = _point(geometry.get("end"))
    if start is not None and end is not None:
        return [start, end]
    return []


def _path_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _lengths_match(measured: float, target: float) -> bool:
    return abs(measured - target) <= max(1.0, target * LENGTH_TOLERANCE)


def diameter_from_layer_name(layer: str) -> float | None:
    """Кодираният диаметър в името на слоя, или ``None``."""

    match = _LAYER_DIAMETER_RE.search(str(layer or ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Измерване


@dataclass
class _Object:
    index: int
    layer: str
    handle: str
    entity_type: str
    points: list[tuple[float, float]]
    length: float
    self_closed: bool
    claimants: list[str] = field(default_factory=list)

    @property
    def segment_count(self) -> int:
        return max(0, len(self.points) - 1)


def _collect_objects(records: Iterable[Any]) -> list[_Object]:
    objects: list[_Object] = []
    for record in records:
        geometry = _attr(record, "geometry") or {}
        if not isinstance(geometry, Mapping):
            continue
        points = _polyline_points(geometry)
        if len(points) < 2:
            continue
        length = _path_length(points)
        if length <= MIN_OBJECT_LENGTH_RAW:
            continue
        objects.append(_Object(
            index=len(objects),
            layer=str(_attr(record, "layer") or ""),
            handle=str(_attr(record, "handle") or ""),
            entity_type=str(_attr(record, "entity_type") or ""),
            points=points,
            length=length,
            self_closed=bool(geometry.get("closed")),
        ))
    return objects


def _collect_annotations(records: Iterable[Any]) -> list[dict[str, Any]]:
    """Надписите с прочетена дължина — същият прочит като в правилото."""

    annotations: list[dict[str, Any]] = []
    for record in records:
        text = str(_attr(record, "text") or "").strip()
        if not text or not looks_like_rebar_text(text):
            continue
        parsed = parse_rebar_text(text)
        length = getattr(parsed, "length_cm", None)
        geometry = _attr(record, "geometry") or {}
        if not isinstance(geometry, Mapping):
            continue
        point = _point(geometry.get("insert") or geometry.get("position"))
        if not length or point is None:
            continue
        annotations.append({
            "handle": str(_attr(record, "handle") or ""),
            "length": float(length),
            "diameter": getattr(parsed, "diameter_mm", None),
            "point": point,
        })
    return annotations


def _length_match_pass(
    objects: Sequence[_Object],
    annotations: Sequence[Mapping[str, Any]],
    radius: float,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    """Локалното съвпадение по дължина, както го мери ``convention_profile``.

    Повтаря се тук нарочно: разделителят трябва да е независим модул и да не
    променя поведението на профила. Страничният резултат е важният — кой надпис
    е заявил кой обект (``_Object.claimants``), защото структурната проверка е
    за ТЕЗИ обекти, не за целия слой.
    """

    cell = max(100.0, radius)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for obj in objects:
        for x, y in obj.points:
            grid[(int(x // cell), int(y // cell))].append(obj.index)

    hits: dict[str, int] = defaultdict(int)
    control: dict[str, int] = defaultdict(int)
    diameter_agree: dict[str, int] = defaultdict(int)
    diameter_clash: dict[str, int] = defaultdict(int)
    for annotation in annotations:
        point = annotation["point"]
        target = float(annotation["length"])
        control_target = target + CONTROL_SHIFT_CM
        gx, gy = int(point[0] // cell), int(point[1] // cell)
        nearby: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nearby.update(grid.get((gx + dx, gy + dy), ()))
        matched: set[str] = set()
        matched_control: set[str] = set()
        for index in nearby:
            obj = objects[index]
            if min(math.dist(point, vertex) for vertex in obj.points) > radius:
                continue
            if _lengths_match(obj.length, target):
                matched.add(obj.layer)
                obj.claimants.append(annotation["handle"])
            elif _lengths_match(obj.length, control_target):
                matched_control.add(obj.layer)
        for layer in matched:
            hits[layer] += 1
            coded = diameter_from_layer_name(layer)
            printed = annotation.get("diameter")
            if coded is not None and printed:
                if abs(coded - float(printed)) <= 0.01:
                    diameter_agree[layer] += 1
                else:
                    diameter_clash[layer] += 1
        for layer in matched_control:
            control[layer] += 1
    return dict(hits), dict(control), dict(diameter_agree), dict(diameter_clash)


def _free_end_counts(
    objects: Sequence[_Object],
    indexes: Sequence[int],
    tolerance: float,
) -> dict[int, int]:
    """Колко от двата края на обекта не срещат друг обект от същия слой.

    Търси се в 3×3 клетки около края, за да не се губи съвпадение заради
    границата на решетката. Затвореният сам по себе си обект няма свободни краища
    по определение — така една затворена ламела не минава за прът.
    """

    step = max(tolerance, 1e-9)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    ends: list[tuple[int, tuple[float, float]]] = []
    for index in indexes:
        obj = objects[index]
        for vertex in (obj.points[0], obj.points[-1]):
            buckets[(round(vertex[0] / step), round(vertex[1] / step))].append(len(ends))
            ends.append((index, vertex))

    free: dict[int, int] = {index: 0 for index in indexes}
    for position, (index, vertex) in enumerate(ends):
        if objects[index].self_closed:
            continue
        cx, cy = round(vertex[0] / step), round(vertex[1] / step)
        touched = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in buckets.get((cx + dx, cy + dy), ()):
                    if other == position:
                        continue
                    other_index, other_vertex = ends[other]
                    if other_index == index:
                        continue
                    if math.dist(vertex, other_vertex) <= tolerance:
                        touched = True
                        break
                if touched:
                    break
            if touched:
                break
        if not touched:
            free[index] += 1
    return free


def _object_verdict(
    obj: _Object,
    free_ends: int,
    max_segments: int,
) -> tuple[str, str]:
    """Присъда за един обект по неговите СОБСТВЕНИ признаци."""

    if not obj.claimants:
        return VERDICT_UNKNOWN, "никой надпис не намира своята дължина в този обект"
    if obj.self_closed:
        return VERDICT_NOT_REBAR, "затворен контур — прът има два свободни края"
    if free_ends == 0:
        return (
            VERDICT_NOT_REBAR,
            "и двата края срещат друг обект от слоя — звено от верига, не прът",
        )
    if obj.segment_count > max_segments:
        return (
            VERDICT_NOT_REBAR,
            f"{obj.segment_count} отсечки — над {max_segments} за една форма",
        )
    return (
        VERDICT_REBAR,
        f"отворен път с {free_ends} свободен(и) край(а), заявен от "
        f"{len(set(obj.claimants))} надпис(а) с точно тази дължина",
    )


def _share(part: int, whole: int) -> float:
    return (part / whole) if whole else 0.0


def evaluate_rebar_geometry(
    records: Sequence[Any],
    *,
    policy: RecognitionPolicy | None = None,
    search_radius_raw: float | None = None,
    endpoint_tolerance_raw: float | None = None,
) -> RebarGeometryEvidence:
    """Мери всеки слой на чертежа и връща присъда, оценка и доказателство.

    ``records`` са обектите на чертежа — или ``DxfEntityRecord``, или речниците
    от ``working/extracted_entities.json``. Функцията не пише, не чете от диск и
    не пипа нищо извън върнатата стойност.

    Присъдата е по съвместно доказателство:

    1. **надписна следа** — поне ``MIN_LENGTH_MATCHES`` надписа намират до себе
       си обект от слоя с точно своята надписана дължина, с превес над
       контролата при дължина + 13 cm; и
    2. **структурна следа** — заявените обекти са отворени пътища със свободен
       край, а не звена от затворена верига.

    Падне ли (2) при силно (1), присъдата е ``not_rebar`` — това е случаят
    ``Slab contour``. Липсва ли (1), присъдата е ``unknown``: слоят може да е
    армировъчен, но чертежът не го доказва.
    """

    resolved = policy or DEFAULT_RECOGNITION_POLICY
    radius = float(
        search_radius_raw
        if search_radius_raw is not None
        else getattr(resolved, "max_search_distance_raw", 90.0) or 90.0
    )
    tolerance = float(
        endpoint_tolerance_raw
        if endpoint_tolerance_raw is not None
        else getattr(resolved, "endpoint_tolerance_raw", 0.75) or 0.75
    )
    max_segments = int(getattr(resolved, "max_component_segments", 16) or 16)

    objects = _collect_objects(records)
    annotations = _collect_annotations(records)
    hits, control, diameter_agree, diameter_clash = _length_match_pass(
        objects, annotations, radius
    )

    by_layer: dict[str, list[int]] = defaultdict(list)
    for obj in objects:
        by_layer[obj.layer].append(obj.index)

    layers: dict[str, LayerEvidence] = {}
    for layer, indexes in by_layer.items():
        matches = int(hits.get(layer, 0))
        if matches <= 0:
            # Слой без нито едно съвпадение не се отчита: няма доказателство в
            # никоя посока и екранът на оператора не бива да се пълни с шум.
            continue
        layers[layer] = _judge_layer(
            layer=layer,
            objects=objects,
            indexes=indexes,
            matches=matches,
            control_matches=int(control.get(layer, 0)),
            diameter_agree=int(diameter_agree.get(layer, 0)),
            diameter_clash=int(diameter_clash.get(layer, 0)),
            tolerance=tolerance,
            max_segments=max_segments,
        )

    return RebarGeometryEvidence(
        schema=REBAR_GEOMETRY_EVIDENCE_SCHEMA,
        version=EVIDENCE_VERSION,
        layers=layers,
        annotations_with_length=len(annotations),
        linear_objects=len(objects),
        endpoint_tolerance_raw=tolerance,
        search_radius_raw=radius,
    )


def _judge_layer(
    *,
    layer: str,
    objects: Sequence[_Object],
    indexes: Sequence[int],
    matches: int,
    control_matches: int,
    diameter_agree: int,
    diameter_clash: int,
    tolerance: float,
    max_segments: int,
) -> LayerEvidence:
    free = _free_end_counts(objects, indexes, tolerance)
    claimed = [index for index in indexes if objects[index].claimants]

    object_evidence: list[ObjectEvidence] = []
    for index in claimed:
        obj = objects[index]
        verdict, why = _object_verdict(obj, free[index], max_segments)
        object_evidence.append(ObjectEvidence(
            handle=obj.handle,
            layer=obj.layer,
            entity_type=obj.entity_type,
            length_raw=obj.length,
            segment_count=obj.segment_count,
            self_closed=obj.self_closed,
            free_end_count=free[index],
            claiming_annotations=tuple(sorted(set(obj.claimants))),
            verdict=verdict,
            why=why,
        ))

    bound = sum(1 for ev in object_evidence if ev.bound_both_ends)
    self_closed = sum(1 for ev in object_evidence if ev.self_closed)
    too_many_segments = sum(
        1 for ev in object_evidence if ev.segment_count > max_segments
    )
    # Присъдата за слоя е сбор от присъдите за обектите му: слой не бива да е
    # „армировка“, докато заявените му обекти един по един са отказани.
    refused = sum(1 for ev in object_evidence if ev.verdict == VERDICT_NOT_REBAR)
    layer_bound = sum(
        1 for index in indexes if objects[index].self_closed or free[index] == 0
    )
    control_ratio = matches / max(1, control_matches)
    coded_diameter = diameter_from_layer_name(layer)

    signals: dict[str, Any] = {
        "length_matches": matches,
        "control_matches": control_matches,
        "control_ratio": round(control_ratio, 2),
        "layer_objects": len(indexes),
        "claimed_objects": len(object_evidence),
        "claimed_bound_share": round(_share(bound, len(object_evidence)), 3),
        "claimed_refused_share": round(_share(refused, len(object_evidence)), 3),
        "claimed_self_closed_share": round(
            _share(self_closed, len(object_evidence)), 3
        ),
        "claimed_over_segment_share": round(
            _share(too_many_segments, len(object_evidence)), 3
        ),
        "claimed_free_endpoint_share": round(_share(
            sum(ev.free_end_count for ev in object_evidence),
            2 * len(object_evidence),
        ), 3),
        "layer_bound_share": round(_share(layer_bound, len(indexes)), 3),
        "claimed_segment_count_max": max(
            (ev.segment_count for ev in object_evidence), default=0
        ),
        "distinct_claimants_max": max(
            (len(ev.claiming_annotations) for ev in object_evidence), default=0
        ),
        "coded_diameter_mm": coded_diameter,
        "coded_diameter_agreements": diameter_agree,
        "coded_diameter_clashes": diameter_clash,
    }

    claimed_bound_share = _share(bound, len(object_evidence))
    claimed_refused_share = _share(refused, len(object_evidence))
    open_path = (
        bool(object_evidence) and claimed_refused_share <= MAX_CLAIMED_BOUND_SHARE
    )
    coded_support = (
        coded_diameter is not None
        and diameter_clash == 0
        and diameter_agree >= CODED_DIAMETER_MIN_MATCHES
        and control_matches == 0
        and matches >= CODED_DIAMETER_MIN_MATCHES
    )
    annotation_trace = (
        matches >= MIN_LENGTH_MATCHES and control_ratio >= MIN_CONTROL_RATIO
    ) or coded_support

    gates = {
        "annotation_trace": annotation_trace,
        "open_path": open_path,
        "coded_diameter_support": coded_support,
    }

    # Оценката е „най-слабото звено“: всяко рамо е нормирано спрямо своя праг и
    # отрязано в [0, 1]. Присъдата обаче се взема от портите, не от оценката —
    # висока оценка НЕ отменя паднала порта.
    open_leg = max(0.0, 1.0 - claimed_refused_share / MAX_CLAIMED_BOUND_SHARE)
    score = min(
        min(1.0, matches / (2.0 * MIN_LENGTH_MATCHES)),
        min(1.0, control_ratio / (2.0 * MIN_CONTROL_RATIO)),
        open_leg,
    )
    if coded_support:
        # Двойно кодиране (име на слоя + Ø в надписа) при нулева контрола е
        # независимо доказателство; оценката не бива да пада само защото
        # бройката е малка.
        score = max(score, 0.6 * min(1.0, open_leg))

    if annotation_trace and open_path:
        verdict = VERDICT_REBAR
        why = (
            f"{matches} надписа намират до себе си обект от слоя с точно своята "
            f"надписана дължина (контрола {control_matches}); от "
            f"{len(object_evidence)} заявени обекта "
            f"{len(object_evidence) - bound} са отворени пътища със свободен край"
        )
        if coded_diameter is not None and diameter_agree:
            why += (
                f"; името кодира Ø{coded_diameter:g} и {diameter_agree} надписа "
                f"го потвърждават"
            )
    elif open_path or len(object_evidence) < MIN_CLAIMED_FOR_REFUSAL:
        verdict = VERDICT_UNKNOWN
        причини = []
        if matches < MIN_LENGTH_MATCHES:
            причини.append(
                f"само {matches} надписа намират дължината си тук "
                f"(под {MIN_LENGTH_MATCHES})"
            )
        if control_ratio < MIN_CONTROL_RATIO:
            причини.append(
                f"контролата дава {control_matches} — превесът "
                f"{control_ratio:.1f}x не убеждава"
            )
        if not open_path:
            причини.append(
                f"само {len(object_evidence)} заявени обекта — под "
                f"{MIN_CLAIMED_FOR_REFUSAL}, не стигат и за отказ"
            )
        why = "тънко доказателство: " + ", ".join(причини or ["няма заявени обекти"])
    else:
        verdict = VERDICT_NOT_REBAR
        части = []
        if self_closed:
            части.append(f"{self_closed} са затворени контури")
        if bound - self_closed > 0:
            части.append(
                f"{bound - self_closed} са свързани с двата си края — звена от "
                f"верига, не прът"
            )
        if too_many_segments:
            части.append(
                f"{too_many_segments} имат над {max_segments} отсечки за една форма"
            )
        why = (
            f"{matches} надписа съвпадат по дължина, но от {len(object_evidence)} "
            f"заявени обекта " + ", ".join(части) +
            " — съвпадението по дължина не прави контура армировка"
        )

    samples = tuple(
        ev.handle for ev in object_evidence[:3] if ev.handle
    )
    return LayerEvidence(
        layer=layer,
        verdict=verdict,
        score=float(score),
        signals=signals,
        why=why,
        gates=gates,
        sample_handles=samples,
        objects=tuple(object_evidence),
    )


def classify_layer(
    records: Sequence[Any],
    layer: str,
    *,
    policy: RecognitionPolicy | None = None,
    search_radius_raw: float | None = None,
    endpoint_tolerance_raw: float | None = None,
) -> LayerEvidence:
    """Присъдата за ЕДИН слой. Мери целия чертеж, защото признакът е връзка
    между надпис и геометрия, а не свойство на слоя сам по себе си."""

    evidence = evaluate_rebar_geometry(
        records,
        policy=policy,
        search_radius_raw=search_radius_raw,
        endpoint_tolerance_raw=endpoint_tolerance_raw,
    )
    name = str(layer or "")
    found = evidence.layers.get(name)
    if found is not None:
        return found
    return LayerEvidence(
        layer=name,
        verdict=VERDICT_UNKNOWN,
        score=0.0,
        signals={"length_matches": 0},
        why="нито един надпис не намира своята дължина в този слой",
        gates={"annotation_trace": False, "open_path": False,
               "coded_diameter_support": False},
    )


__all__ = [
    "CODED_DIAMETER_MIN_MATCHES",
    "EVIDENCE_VERSION",
    "LayerEvidence",
    "MAX_CLAIMED_BOUND_SHARE",
    "MIN_CLAIMED_FOR_REFUSAL",
    "MIN_CONTROL_RATIO",
    "MIN_LENGTH_MATCHES",
    "ObjectEvidence",
    "REBAR_GEOMETRY_EVIDENCE_SCHEMA",
    "RebarGeometryEvidence",
    "VERDICT_NOT_REBAR",
    "VERDICT_REBAR",
    "VERDICT_UNKNOWN",
    "classify_layer",
    "diameter_from_layer_name",
    "evaluate_rebar_geometry",
]
