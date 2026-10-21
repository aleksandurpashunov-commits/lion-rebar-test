from __future__ import annotations

import itertools
import math
import os
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import ShapeDefinition


Point2D = tuple[float, float]

_NUMERIC_DIMENSION_RE = re.compile(
    r"\s*(\d+(?:[.,]\d+)?)\s*(?:CM)?\s*",
    flags=re.IGNORECASE,
)
_DIMENSION_ENDPOINT_OWNERSHIP_TOLERANCE_RAW = 2.0
# Линейна кота мери по своята ос. Кота, чиято ос е почти перпендикулярна на
# участъка, мери отместване или ширина, не дължината му — а в ENGINE
# участъците са преки дължини. s-11, ключ 4a: котата 4 на редукция 1:10 лежи с
# опорните си точки върху пръта и ставаше L2 (146 / 4 / 320) редом с верния
# 110 / 40 / 320, при което еднозначността правилно отказваше. Прагът е широк
# нарочно: проекция на наклонен участък (до 45°) остава допустима.
#
# Прилага се САМО по изрична заявка (легендна геометрия). В локалното
# съпоставяне отказът при двусмислие е част от защитата: премахнато грешно
# тълкуване може да остави единствено друго грешно. Измерено в s-11: глобално
# приложено, правилото правеше хоризонталния прът 7622 (742 cm) „кандидат" на
# 36N8x295 и двете позиции губеха доказаните си размери.
_MAX_DIMENSION_AXIS_OFFSET_DEG = 60.0


@dataclass(frozen=True)
class DimensionEvidence:
    """One strict A-E interpretation of printed dimensions around a bar.

    ``segment_lengths_cm`` follows the actual DXF traversal supplied in
    ``points``.  The resolver does not approve a bar or alter MASTER data; it
    only returns evidence when printed values reproduce the declared total.
    """

    shape_code: str
    segment_lengths_cm: tuple[float, ...]
    text_handles: tuple[str, ...]
    score: float
    total_difference_cm: float
    strategy: str


@dataclass(frozen=True)
class _NumericDimension:
    handle: str
    value_cm: float
    point: Point2D
    rotation_deg: float | None
    # Възможните оси, по които DIMENSION мери; None — не е кота или оста не
    # може да се установи (тогава няма ограничение, както преди).
    measurement_axes_deg: tuple[float, ...] | None = None


#: Допуск при проверката на ДОСМЕТНАТА стойност срещу начертания участък.
#: Отпечатаната стойност е доказателство и не се проверява така — тя стои над
#: чертежа. Досметнатата е аритметика и иска независимо потвърждение.
#: Границата поема разширяването през радиуса на огъване и неточността на
#: чертежа; над нея резолверът отказва, вместо да предположи.
#: Избран по данни (rezultat/etap_6/DOPUSK.json): най-строгата граница, която
#: пуска законния случай (начертано 66, произведено 61 — разширяване през
#: радиуса на огъване) и спира всички спорни случаи на d-2. Допускът е
#: АБСОЛЮТЕН: разширяването през радиуса е от порядъка на самия радиус и не
#: зависи от дължината на участъка. Относителен допуск би направил дългия
#: участък прекалено позволителен — точно там, където грешката е най-скъпа.
_INFERRED_GEOMETRY_TOLERANCE_CM = 6.0


def _inferred_tolerance_cm() -> float:
    """Границата, с четене от средата при всяко извикване.

    Диагностичен ключ `LION_REBAR_INFERRED_TOLERANCE_CM`: служи само за да се
    измери кое пада заради границата и кое заради друго. Не е настройка за
    собственика.
    """

    сурово = os.environ.get("LION_REBAR_INFERRED_TOLERANCE_CM")
    if сурово:
        try:
            стойност = float(сурово)
        except ValueError:
            return _INFERRED_GEOMETRY_TOLERANCE_CM
        if стойност >= 0:
            return стойност
    return _INFERRED_GEOMETRY_TOLERANCE_CM


def _inferred_value_matches_drawing(
    value: float,
    governed_indices: tuple[int, ...],
    raw_segment_lengths: tuple[float, ...],
) -> bool:
    """Потвърждава ли начертаното една досметната стойност.

    Досметнатата стойност описва конкретни участъци от полилинията. Ако нито
    един от тях не е с такава начертана дължина, стойността не е размер на този
    прът, а остатък от аритметика.
    """

    for index in governed_indices:
        if index < 0 or index >= len(raw_segment_lengths):
            continue
        drawn = raw_segment_lengths[index]
        if abs(drawn - value) <= _inferred_tolerance_cm():
            return True
    return False


def dimension_evidence_for_shape(
    points: Sequence[Point2D],
    shape: ShapeDefinition,
    entities: Iterable[DxfEntityRecord],
    *,
    declared_length_cm: float,
    excluded_handles: Iterable[str] = (),
    allowed_handles: Iterable[str] | None = None,
    maximum_total_difference_cm: float = 0.51,
    maximum_text_distance_raw: float = 60.0,
    maximum_orientation_difference_deg: float = 25.0,
    exclude_cross_axis_dimensions: bool = False,
    annotation_layer: str = "",
) -> tuple[DimensionEvidence, ...]:
    """Resolve nearby numeric A-E values against one MASTER parameter formula.

    The association is deliberately conservative:

    * only pure numeric TEXT/MTEXT/DIMENSION values are considered;
    * every independent MASTER length code needs its own distinct text owner;
    * text orientation may be parallel or perpendicular to a governed bar
      segment, while distance and segment projection still have to agree;
    * the expanded segment formula must reproduce the annotation total.

    Both drawing directions are tried because bar traversal direction is not an
    engineering identity.  Closed shapes are excluded from this first A-E
    stage; their hook/side formulas already have dedicated handling.
    """

    clean_points = tuple((float(point[0]), float(point[1])) for point in points)
    segments = tuple(zip(clean_points, clean_points[1:]))
    shape_segments = tuple(sorted(shape.segments, key=lambda item: item.index))
    if (
        shape.closed
        or len(segments) < 2
        or len(segments) > 5
        or len(shape_segments) != len(segments)
        or declared_length_cm <= 0.0
    ):
        return ()

    excluded = {str(value) for value in excluded_handles if str(value)}
    allowed = (
        {str(value) for value in allowed_handles if str(value)}
        if allowed_handles is not None
        else None
    )
    numeric = _nearby_numeric_dimensions(
        entities,
        segments,
        excluded_handles=excluded,
        allowed_handles=allowed,
        maximum_text_distance_raw=maximum_text_distance_raw,
        annotation_layer=annotation_layer,
    )
    if not numeric:
        return ()
    raw_segment_lengths = tuple(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in segments
    )

    code_by_shape_position = tuple(
        str(segment.length_code or f"S{position + 1}").strip().upper()
        for position, segment in enumerate(shape_segments)
    )
    parameter_codes = tuple(dict.fromkeys(code_by_shape_position))
    if not parameter_codes:
        return ()

    solutions: list[DimensionEvidence] = []
    for reversed_direction in (False, True):
        geometry_index_for_shape = tuple(
            (
                len(segments) - 1 - shape_position
                if reversed_direction
                else shape_position
            )
            for shape_position in range(len(shape_segments))
        )
        governed_geometry_indices = {
            code: tuple(
                geometry_index_for_shape[position]
                for position, value in enumerate(code_by_shape_position)
                if value == code
            )
            for code in parameter_codes
        }

        candidates_by_code: dict[str, tuple[tuple[float, _NumericDimension], ...]] = {}
        for code in parameter_codes:
            ranked: list[tuple[float, _NumericDimension]] = []
            for item in numeric:
                association_costs = []
                for geometry_index in governed_geometry_indices[code]:
                    left, right = segments[geometry_index]
                    distance, projection = _point_segment_distance(item.point, left, right)
                    if distance > maximum_text_distance_raw:
                        continue
                    if exclude_cross_axis_dimensions and not _dimension_can_measure_segment(
                        item, left, right
                    ):
                        continue
                    angle_cost = 0.0
                    if item.rotation_deg is not None:
                        segment_angle = math.degrees(
                            math.atan2(right[1] - left[1], right[0] - left[0])
                        ) % 180.0
                        parallel_difference = _orientation_difference(
                            item.rotation_deg,
                            segment_angle,
                        )
                        perpendicular_difference = _orientation_difference(
                            item.rotation_deg,
                            (segment_angle + 90.0) % 180.0,
                        )
                        # Structural drawings use both conventions: dimension
                        # text can follow the measured segment or the dimension
                        # line perpendicular to it. Exact parallel and exact
                        # perpendicular evidence are equally strong; spatial
                        # distance/projection and the final unique-total gate
                        # decide ownership.
                        angle_cost = min(
                            parallel_difference,
                            perpendicular_difference,
                        )
                        if angle_cost > maximum_orientation_difference_deg:
                            continue
                    projection_penalty = 0.0
                    if projection < 0.0:
                        projection_penalty = abs(projection) * 50.0
                    elif projection > 1.0:
                        projection_penalty = abs(projection - 1.0) * 50.0
                    association_costs.append(
                        distance + angle_cost * 1.5 + projection_penalty
                    )
                if association_costs:
                    ranked.append((min(association_costs), item))
            ranked.sort(key=lambda value: (value[0], value[1].handle))
            # A bounded shortlist prevents dimension-rich structural drawings
            # from turning a local association into an unbounded permutation.
            candidates_by_code[code] = tuple(ranked[:6])

        def append_assignment(
            assigned_codes: Sequence[str],
            assignment: Sequence[tuple[float, _NumericDimension]],
            *,
            inferred_values: dict[str, float] | None = None,
            strategy: str,
        ) -> None:
            handles = tuple(item.handle for _cost, item in assignment)
            if len(set(handles)) != len(handles):
                return
            values_by_code = {
                code: item.value_cm
                for code, (_cost, item) in zip(assigned_codes, assignment)
            }
            values_by_code.update(inferred_values or {})
            if any(code not in values_by_code for code in parameter_codes):
                return
            # Досметнатата стойност не е доказателство — иска потвърждение от
            # начертаната геометрия на участъка, който описва. Без него
            # резолверът отказва: измисленият вектор е по-лош от липсващия.
            for code, value in (inferred_values or {}).items():
                if not _inferred_value_matches_drawing(
                    float(value),
                    governed_geometry_indices.get(code, ()),
                    raw_segment_lengths,
                ):
                    return
            geometry_values = [0.0] * len(segments)
            for shape_position, code in enumerate(code_by_shape_position):
                geometry_values[geometry_index_for_shape[shape_position]] = values_by_code[code]
            total_difference = abs(sum(geometry_values) - float(declared_length_cm))
            if total_difference > maximum_total_difference_cm:
                return
            # When several total-minus-one-parameter equations close exactly,
            # prefer the interpretation whose *printed* values also reproduce
            # the straight DXF segments they govern. This is not a geometry
            # fallback: the total still has to be proven by distinct printed
            # owners, and the bonus only separates otherwise competing exact
            # assignments. It is what makes 90 + residual 20 + 50 win over
            # silently discarding the printed 90 or 50 on an almost-to-scale U.
            exact_geometry_matches = 0
            for code, (_cost, item) in zip(assigned_codes, assignment):
                governed = governed_geometry_indices.get(code, ())
                if any(
                    abs(raw_segment_lengths[index] - item.value_cm) <= 0.51
                    for index in governed
                ):
                    exact_geometry_matches += 1
            score = (
                sum(cost for cost, _item in assignment)
                + total_difference * 20.0
                - exact_geometry_matches * 10.0
            )
            solutions.append(
                DimensionEvidence(
                    shape_code=shape.code.strip().upper(),
                    segment_lengths_cm=tuple(geometry_values),
                    text_handles=handles,
                    score=score,
                    total_difference_cm=total_difference,
                    strategy=strategy,
                )
            )

        if all(candidates_by_code[code] for code in parameter_codes):
            candidate_lists = tuple(candidates_by_code[code] for code in parameter_codes)
            for assignment in itertools.product(*candidate_lists):
                append_assignment(
                    parameter_codes,
                    assignment,
                    strategy=(
                        "A_E_DIRECT_SEGMENTS"
                        if len(parameter_codes) == len(segments)
                        else "A_E_MASTER_PARAMETER_FORMULA"
                    ),
                )

        # Common project notation prints the main straight part but omits one
        # hook/arm.  Infer exactly one missing MASTER parameter from
        # Δ = declared total - all printed contributions.  The multiplicity of
        # a repeated length code (for example L1 + L2 + L1) is respected.
        for missing_code in parameter_codes:
            assigned_codes = tuple(code for code in parameter_codes if code != missing_code)
            if not assigned_codes or any(not candidates_by_code[code] for code in assigned_codes):
                continue
            candidate_lists = tuple(candidates_by_code[code] for code in assigned_codes)
            missing_multiplicity = code_by_shape_position.count(missing_code)
            for assignment in itertools.product(*candidate_lists):
                handles = tuple(item.handle for _cost, item in assignment)
                if len(set(handles)) != len(handles):
                    continue
                printed_contribution = sum(
                    item.value_cm * code_by_shape_position.count(code)
                    for code, (_cost, item) in zip(assigned_codes, assignment)
                )
                remainder = float(declared_length_cm) - printed_contribution
                inferred = remainder / max(missing_multiplicity, 1)
                if inferred <= 0.0:
                    continue
                append_assignment(
                    assigned_codes,
                    assignment,
                    inferred_values={missing_code: inferred},
                    strategy="A_E_TOTAL_MINUS_PRINTED",
                )

        # Locked two-vinkel rule: retain a main-only symmetric candidate when
        # the two drawn end arms are demonstrably alike, then let the normal
        # evidence score rank it.  Nearby rows may place unrelated arm texts
        # inside the generic radius, so their mere presence must not suppress
        # this candidate.  Do not apply it to arbitrary three-segment forms.
        if (
            shape.code.strip().upper() == "F003"
            and len(segments) == 3
        ):
            raw_lengths = tuple(
                math.hypot(right[0] - left[0], right[1] - left[1])
                for left, right in segments
            )
            shape_main_position = 1
            geometry_main_index = geometry_index_for_shape[shape_main_position]
            end_indices = tuple(index for index in range(3) if index != geometry_main_index)
            ends_similar = abs(raw_lengths[end_indices[0]] - raw_lengths[end_indices[1]]) <= max(
                5.0,
                0.10 * max(raw_lengths[end_indices[0]], raw_lengths[end_indices[1]], 1.0),
            )
            main_dominant = raw_lengths[geometry_main_index] > 2.0 * max(
                raw_lengths[end_indices[0]],
                raw_lengths[end_indices[1]],
            )
            main_code = code_by_shape_position[shape_main_position]
            if ends_similar and main_dominant and candidates_by_code.get(main_code):
                end_codes = tuple(
                    code_by_shape_position[position]
                    for position in (0, 2)
                )
                for assignment in itertools.product(candidates_by_code[main_code]):
                    main_value = assignment[0][1].value_cm
                    remainder = float(declared_length_cm) - main_value
                    if remainder <= 0.0:
                        continue
                    inferred = remainder / 2.0
                    append_assignment(
                        (main_code,),
                        assignment,
                        inferred_values={end_codes[0]: inferred, end_codes[1]: inferred},
                        strategy="A_E_DELTA_SPLIT_TWO_EQUAL_BENDS",
                    )

    unique: dict[tuple[tuple[float, ...], tuple[str, ...]], DimensionEvidence] = {}
    for item in solutions:
        forward = tuple(round(value, 3) for value in item.segment_lengths_cm)
        reverse = tuple(reversed(forward))
        canonical_lengths = min(forward, reverse)
        key = canonical_lengths, tuple(sorted(item.text_handles))
        previous = unique.get(key)
        if previous is None or item.score < previous.score:
            unique[key] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.score,
                tuple(round(value, 6) for value in item.segment_lengths_cm),
                item.text_handles,
            ),
        )
    )


def points_with_segment_lengths(
    points: Sequence[Point2D],
    segment_lengths_cm: Sequence[float],
) -> tuple[Point2D, ...] | None:
    """Preserve DXF directions while replacing schematic lengths with A-E."""

    clean_points = tuple((float(point[0]), float(point[1])) for point in points)
    lengths = tuple(float(value) for value in segment_lengths_cm)
    if len(clean_points) != len(lengths) + 1 or any(value <= 0.0 for value in lengths):
        return None
    result: list[Point2D] = [clean_points[0]]
    for (left, right), length in zip(zip(clean_points, clean_points[1:]), lengths):
        dx = right[0] - left[0]
        dy = right[1] - left[1]
        raw_length = math.hypot(dx, dy)
        if raw_length <= 1e-9:
            return None
        start = result[-1]
        result.append(
            (
                start[0] + dx / raw_length * length,
                start[1] + dy / raw_length * length,
            )
        )
    return tuple(result)


def _nearby_numeric_dimensions(
    entities: Iterable[DxfEntityRecord],
    segments: Sequence[tuple[Point2D, Point2D]],
    *,
    excluded_handles: set[str],
    allowed_handles: set[str] | None,
    maximum_text_distance_raw: float,
    annotation_layer: str = "",
) -> tuple[_NumericDimension, ...]:
    # СВОБОДЕН ТЕКСТ с голо число на СЪЩИЯ слой, на който стои позиционният
    # надпис, е част от надписната система, не размер на пръта. ASD пише
    # описанието на разпределението като два MTEXT-а — брой и надпис — със
    # съседни handle-и. Измерено на d-2: точно това дава [601, 9] вместо
    # 15 + 595 при поз. 9, и още три позиции (24, 25, 35).
    #
    # Правилото важи САМО за свободен текст. Обект DIMENSION е размер по
    # устройство: носи измерена стойност и геометрия, която го връзва с онова,
    # което мери. Контрапример, който наложи това стеснение: `Тест-003`,
    # позиция `69F9B3`, където истинските DIMENSION обекти `69F9B1` и `69F9B2`
    # стоят на слой `Text ARM.2` — същия, на който е и надписът.
    #
    # Слоят се познава по съдържание (носи обработвания надпис), не по име.
    надписен_слой = str(annotation_layer or "").strip().casefold()
    rows: list[_NumericDimension] = []
    for record in entities:
        handle = str(record.handle)
        if handle in excluded_handles:
            continue
        if (
            надписен_слой
            and str(record.entity_type or "").strip().upper() != "DIMENSION"
            and str(record.layer or "").strip().casefold() == надписен_слой
            # Изричното разрешение на оператора стои над правилото.
            and not (allowed_handles is not None and handle in allowed_handles)
        ):
            continue
        if allowed_handles is not None and handle not in allowed_handles:
            continue
        if record.entity_type.upper() not in {"TEXT", "MTEXT", "DIMENSION"}:
            continue
        match = _NUMERIC_DIMENSION_RE.fullmatch(str(record.text or ""))
        measured_value = None
        if match is not None:
            measured_value = float(match.group(1).replace(",", "."))
        elif record.entity_type.upper() == "DIMENSION":
            geometry = record.geometry or {}
            endpoints: list[Point2D] = []
            for key in ("defpoint2", "defpoint3"):
                raw_point = geometry.get(key)
                if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                    endpoints = []
                    break
                try:
                    endpoints.append((float(raw_point[0]), float(raw_point[1])))
                except (TypeError, ValueError):
                    endpoints = []
                    break
            if len(endpoints) != 2 or any(
                min(
                    _point_segment_distance(endpoint, left, right)[0]
                    for left, right in segments
                )
                > _DIMENSION_ENDPOINT_OWNERSHIP_TOLERANCE_RAW
                for endpoint in endpoints
            ):
                # A blank DIMENSION carries no readable value owner.  Its
                # measured value is admissible only when both extension
                # witnesses land on this exact candidate contour; a nearby
                # text midpoint alone is not ownership.
                continue
            raw_measurement = geometry.get("measurement")
            if isinstance(raw_measurement, bool):
                continue
            try:
                parsed_measurement = float(raw_measurement)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(parsed_measurement) or parsed_measurement <= 0.0:
                continue
            nearest = round(parsed_measurement)
            measured_value = (
                float(nearest)
                if abs(parsed_measurement - nearest) <= 0.4
                else round(parsed_measurement * 2.0) / 2.0
            )
        if measured_value is None:
            continue
        point = _entity_text_anchor(record)
        if point is None:
            continue
        nearest = min(
            _point_segment_distance(point, left, right)[0]
            for left, right in segments
        )
        if nearest > maximum_text_distance_raw:
            continue
        value = float(measured_value)
        if not math.isfinite(value) or value <= 0.0:
            continue
        rotation = _entity_text_rotation(record)
        rows.append(
            _NumericDimension(
                handle=str(record.handle),
                value_cm=value,
                point=point,
                rotation_deg=rotation,
                measurement_axes_deg=(
                    _dimension_measurement_axes(record)
                    if record.entity_type.upper() == "DIMENSION"
                    else None
                ),
            )
        )
    return tuple(rows)


def _dimension_can_measure_segment(
    item: _NumericDimension,
    left: Point2D,
    right: Point2D,
) -> bool:
    if not item.measurement_axes_deg:
        return True
    segment_angle = math.degrees(
        math.atan2(right[1] - left[1], right[0] - left[0])
    ) % 180.0
    return any(
        _orientation_difference(axis, segment_angle) <= _MAX_DIMENSION_AXIS_OFFSET_DEG
        for axis in item.measurement_axes_deg
    )


def _dimension_measurement_axes(record: DxfEntityRecord) -> tuple[float, ...] | None:
    """Осите (в градуси, mod 180), по които една линейна кота може да мери.

    Първо от самата кота: линията от ``defpoint3`` до ``defpoint`` е
    удължителната линия, а оста на измерване е перпендикулярна на нея.
    Приема се само ако проекцията на опорните точки върху тази ос дава
    измерената стойност. Иначе — от мярката: ``m = |v|·|cos(φ − θ)|`` дава
    двете възможни оси. Без достатъчно данни — ``None``, без ограничение.
    """

    geometry = record.geometry or {}
    first = _point_from(geometry.get("defpoint2"))
    second = _point_from(geometry.get("defpoint3"))
    raw_measurement = geometry.get("measurement")
    if first is None or second is None or isinstance(raw_measurement, bool):
        return None
    try:
        measurement = float(raw_measurement)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(measurement) or measurement <= 0.0:
        return None
    vx, vy = second[0] - first[0], second[1] - first[1]
    span = math.hypot(vx, vy)
    if span <= 1e-9:
        return None
    line_point = _point_from(geometry.get("defpoint"))
    if line_point is not None:
        ex, ey = line_point[0] - second[0], line_point[1] - second[1]
        if math.hypot(ex, ey) > 1e-6:
            axis = (math.degrees(math.atan2(ey, ex)) + 90.0) % 180.0
            radians = math.radians(axis)
            projected = abs(vx * math.cos(radians) + vy * math.sin(radians))
            if abs(projected - measurement) <= 0.5:
                return (axis,)
    if measurement > span + 0.5:
        return None
    phi = math.degrees(math.atan2(vy, vx))
    delta = math.degrees(math.acos(min(1.0, measurement / span)))
    return tuple(sorted({(phi + delta) % 180.0, (phi - delta) % 180.0}))


def _point_from(value: object) -> Point2D | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(coordinate) for coordinate in point) else None


def _entity_text_anchor(record: DxfEntityRecord) -> Point2D | None:
    geometry = record.geometry or {}
    for key in ("visual_anchor", "insert", "text_midpoint", "defpoint"):
        value = geometry.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return float(value[0]), float(value[1])
    return None


def _entity_text_rotation(record: DxfEntityRecord) -> float | None:
    geometry = record.geometry or {}
    raw = geometry.get("rotation")
    if raw is not None:
        try:
            return float(raw) % 180.0
        except (TypeError, ValueError):
            return None
    if record.entity_type.upper() == "DIMENSION":
        left = geometry.get("defpoint2")
        right = geometry.get("defpoint3")
        if (
            isinstance(left, (list, tuple))
            and isinstance(right, (list, tuple))
            and len(left) >= 2
            and len(right) >= 2
        ):
            return math.degrees(
                math.atan2(float(right[1]) - float(left[1]), float(right[0]) - float(left[0]))
            ) % 180.0
    return None


def _point_segment_distance(
    point: Point2D,
    left: Point2D,
    right: Point2D,
) -> tuple[float, float]:
    vx, vy = right[0] - left[0], right[1] - left[1]
    wx, wy = point[0] - left[0], point[1] - left[1]
    denominator = vx * vx + vy * vy
    if denominator <= 1e-12:
        return math.hypot(wx, wy), 0.0
    projection = (wx * vx + wy * vy) / denominator
    clamped = min(1.0, max(0.0, projection))
    nearest = (left[0] + clamped * vx, left[1] + clamped * vy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1]), projection


def _orientation_difference(left: float, right: float) -> float:
    difference = abs((float(left) - float(right)) % 180.0)
    return min(difference, 180.0 - difference)
