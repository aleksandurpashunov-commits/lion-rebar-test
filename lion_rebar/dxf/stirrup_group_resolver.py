from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from lion_rebar.dxf.geometry_candidates import (
    GeometryCandidate,
    GeometryCandidateAnalysis,
    GeometryCandidateContext,
    GeometryPrimitive,
    _annotation_probe_points,
    _distance_point_to_segments,
    find_geometry_candidates,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import (
    ShapeFingerprint,
    fingerprint_from_points,
    match_master_shapes,
)
from lion_rebar.dxf.text_parser import parse_rebar_text
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex

Point2D = tuple[float, float]


@dataclass(frozen=True)
class StirrupLengthGroupResolution:
    code: str
    annotation_handle: str
    declared_count: int
    declared_length_cm: float
    representative_handle: str
    representative_distance_raw: float
    member_handles: tuple[str, ...]
    member_distances_raw: tuple[float, ...]
    exact_arc_lengths_cm: tuple[float, ...]
    probe_annotation: DxfEntityRecord
    analysis: GeometryCandidateAnalysis

    @property
    def candidate(self) -> GeometryCandidate:
        return self.analysis.candidates[0]


@dataclass(frozen=True)
class _LengthMatch:
    primitive: GeometryPrimitive
    distance_raw: float
    center: Point2D
    signature: str
    fingerprint: ShapeFingerprint


# Две копия са една форма, когато участъците им се разминават до допуска за
# повторение (stirrup_group_repeat_length_tolerance_cm) и завоите — до толкова
# градуса. Копията на едно стреме в различни разрези са чертани от един блок;
# 0.1–0.3 cm разлика е чертожен шум, различното стреме се разминава с cm.
_CONGRUENT_TURN_TOLERANCE_DEG = 2.0


def resolve_stirrup_length_group_annotation(
    annotation: DxfEntityRecord,
    entities: Sequence[DxfEntityRecord],
    *,
    shape_definitions: Iterable[ShapeDefinition],
    control_tests: Iterable[Mapping[str, object]],
    policy: RecognitionPolicy,
    context: GeometryCandidateContext,
    shape_index: EngineShapeIndex,
) -> StirrupLengthGroupResolution | None:
    """Resolve one remote curved stirrup by exact arc length or one row.

    The resolver is intentionally independent from generic proximity ranking.
    More than one length-compatible primitive is an ambiguity unless every
    compatible primitive belongs to one repeated row.  No result claims local
    DXF ownership and no result creates an automatic approval.
    """

    definitions = tuple(shape_definitions)
    tests = tuple(control_tests)
    prepared = _parsed_probes(annotation, entities, policy=policy, context=context)
    if prepared is None:
        return None
    parsed, probes = prepared
    search_distance = float(policy.stirrup_length_search_distance_raw)
    matches = _stirrup_length_matches(
        context,
        probes=probes,
        declared=float(parsed.length_cm),
        shape_definitions=definitions,
        control_tests=tests,
        policy=policy,
        shape_index=shape_index,
        admits=lambda _primitive, distance: distance <= search_distance,
    )
    if not matches:
        return None

    if len(matches) == 1:
        selected = (matches[0],)
        code = "STIRRUP_EXACT_ARC_LENGTH_UNIQUE"
    else:
        components = _repeat_components(matches, policy)
        valid = [
            component
            for component in components
            if len(component) >= int(policy.stirrup_group_min_members)
            and _row_is_valid(component, matches, policy)
            and (
                not policy.stirrup_group_require_count_compatibility
                or len(component) <= int(parsed.count)
            )
        ]
        # Every exact-length match inside the narrow resolver radius must be a
        # member of the same proven row.  A second row or a stray exact match is
        # real ambiguity and therefore remains unresolved.
        if len(valid) != 1 or set(valid[0]) != set(range(len(matches))):
            return None
        selected = tuple(matches[index] for index in valid[0])
        code = "STIRRUP_EXACT_ARC_LENGTH_GROUP"

    return _resolution_for_selection(
        annotation,
        entities,
        parsed=parsed,
        selected=selected,
        code=code,
        shape_definitions=definitions,
        control_tests=tests,
        policy=policy,
        context=context,
        shape_index=shape_index,
    )


def resolve_stirrup_operator_area_annotation(
    annotation: DxfEntityRecord,
    entities: Sequence[DxfEntityRecord],
    *,
    area_bounds: tuple[float, float, float, float],
    shape_definitions: Iterable[ShapeDefinition],
    control_tests: Iterable[Mapping[str, object]],
    policy: RecognitionPolicy,
    context: GeometryCandidateContext,
    shape_index: EngineShapeIndex,
) -> StirrupLengthGroupResolution | None:
    """Стремето на надпис, изнесен встрани от разреза, в рамката на елемента.

    Собственик, 2026-09-16, за s-11: надписите на стремената стоят на 1–2,5 m
    от разрезите и най-близкият прът е чужд, затова излизаха „НЯМА КАНДИДАТ“.
    Операторът е оградил елемента с рамка, а вътре стремето е начертано в
    няколко копия — по едно във всеки разрез по височината. Правилото на
    собственика: когато ВСИЧКИ стремена в рамката с дължината на надписа са
    една и съща форма, формата е доказана. Различни форми (15x15 и 15x17 при
    еднаква L) са отказ; нито едно — също.

    Филтрите са същите като при групата по точна дъгова L: слой, извита ос с
    кука, MASTER без двусмислие, дължина в допуска. Сменят се само обхватът
    (рамката вместо радиус) и изискването за подредба (една форма вместо една
    редица). Представителят е проследимост, не изключителна собственост.
    """

    definitions = tuple(shape_definitions)
    tests = tuple(control_tests)
    prepared = _parsed_probes(annotation, entities, policy=policy, context=context)
    if prepared is None:
        return None
    parsed, probes = prepared
    x0, x1 = sorted((float(area_bounds[0]), float(area_bounds[2])))
    y0, y1 = sorted((float(area_bounds[1]), float(area_bounds[3])))

    def inside_area(primitive: GeometryPrimitive, _distance: float) -> bool:
        points = primitive.points
        return bool(points) and all(
            x0 <= float(point[0]) <= x1 and y0 <= float(point[1]) <= y1
            for point in points
        )

    matches = _stirrup_length_matches(
        context,
        probes=probes,
        declared=float(parsed.length_cm),
        shape_definitions=definitions,
        control_tests=tests,
        policy=policy,
        shape_index=shape_index,
        admits=inside_area,
    )
    if not matches:
        return None
    if (
        policy.stirrup_group_require_count_compatibility
        and len(matches) > int(parsed.count)
    ):
        return None
    if not _all_congruent(matches, policy):
        return None
    return _resolution_for_selection(
        annotation,
        entities,
        parsed=parsed,
        selected=tuple(matches),
        code="STIRRUP_OPERATOR_AREA_CONGRUENT",
        shape_definitions=definitions,
        control_tests=tests,
        policy=policy,
        context=context,
        shape_index=shape_index,
        accept_congruent_member=True,
    )


def _parsed_probes(
    annotation: DxfEntityRecord,
    entities: Sequence[DxfEntityRecord],
    *,
    policy: RecognitionPolicy,
    context: GeometryCandidateContext,
):
    if not policy.stirrup_length_group_rule_enabled:
        return None
    parsed = parse_rebar_text(annotation.text or "")
    if (
        parsed.count is None
        or parsed.count < 1
        or parsed.diameter_mm is None
        or parsed.length_cm is None
    ):
        return None
    probes = _annotation_probe_points(
        annotation,
        entities,
        policy,
        leader_entities=context.leaders,
    )
    if not probes:
        return None
    return parsed, probes


def _stirrup_length_matches(
    context: GeometryCandidateContext,
    *,
    probes: Sequence[Point2D],
    declared: float,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, object]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex,
    admits: Callable[[GeometryPrimitive, float], bool],
) -> list[_LengthMatch]:
    layer_aliases = _normalized_aliases(policy.stirrup_length_group_layer_aliases)
    master_codes = _normalized_aliases(policy.stirrup_length_group_master_codes)
    length_tolerance = float(policy.stirrup_arc_length_tolerance_cm)
    matches: list[_LengthMatch] = []

    for primitive in context.primitives:
        if primitive.entity_type.upper() not in {"LWPOLYLINE", "POLYLINE"}:
            continue
        if _normalize_token(primitive.layer) not in layer_aliases:
            continue
        if (
            primitive.source_curve_count < 1
            or primitive.unresolved_curve_count != 0
            or not primitive.hooked_closure
            or abs(float(primitive.length_raw) - declared) > length_tolerance
        ):
            continue
        distance = min(
            _distance_point_to_segments(probe, primitive.segments)
            for probe in probes
        )
        if not admits(primitive, distance):
            continue
        points = primitive.recognition_points or primitive.points
        master = match_master_shapes(
            points,
            shape_definitions,
            policy=policy,
            control_tests=control_tests,
            shape_index=shape_index,
            candidate_closed_override=True,
            implicit_closure=True,
        )
        if (
            master is None
            or master.ambiguous_shape_codes
            or _normalize_token(master.shape_code) not in master_codes
        ):
            continue
        fingerprint = fingerprint_from_points(
            points,
            closed=True,
            implicit_closure=True,
            policy=policy,
        )
        if fingerprint is None:
            continue
        matches.append(
            _LengthMatch(
                primitive=primitive,
                distance_raw=float(distance),
                center=_points_center(points),
                signature=fingerprint.canonical_signature,
                fingerprint=fingerprint,
            )
        )

    matches.sort(
        key=lambda value: (
            value.distance_raw,
            value.primitive.handle,
        )
    )
    return matches


def _all_congruent(matches: Sequence[_LengthMatch], policy: RecognitionPolicy) -> bool:
    tolerance = float(policy.stirrup_group_repeat_length_tolerance_cm)
    reference = matches[0]
    reference_profile = _cyclic_profile(reference)
    for match in matches[1:]:
        if abs(float(match.primitive.length_raw) - float(reference.primitive.length_raw)) > tolerance:
            return False
        if match.fingerprint.segment_count != reference.fingerprint.segment_count:
            return False
        if not _profiles_congruent(reference_profile, _cyclic_profile(match), tolerance):
            return False
    return True


def _cyclic_profile(match: _LengthMatch) -> list[tuple[float, float]]:
    """Затвореният контур като цикъл от (участък, завой след него).

    Ъглите са по абсолютна стойност: огледално копие завива на другата страна,
    а е същото стреме. Отпечатъкът не подрежда копията от едно и също място —
    в s-11 четирите копия на едно стреме излизаха в четири различни подредби.
    """

    lengths = _segment_lengths(match)
    turns = tuple(abs(float(value)) for value in match.fingerprint.turn_angles_deg)
    if len(turns) != len(lengths):
        return []
    return list(zip(lengths, turns))


def _profiles_congruent(
    left: Sequence[tuple[float, float]],
    right: Sequence[tuple[float, float]],
    tolerance: float,
) -> bool:
    count = len(left)
    if count == 0 or count != len(right):
        return False
    # Обратна посока: завоят след участък j е завоят ПРЕДИ него в правата
    # посока — затова ъгълът се взима с едно място по-напред.
    reversed_right = [
        (right[count - 1 - index][0], right[(count - 2 - index) % count][1])
        for index in range(count)
    ]
    for sequence in (list(right), reversed_right):
        for shift in range(count):
            rotated = sequence[shift:] + sequence[:shift]
            if all(
                abs(a_length - b_length) <= tolerance
                and abs(a_turn - b_turn) <= _CONGRUENT_TURN_TOLERANCE_DEG
                for (a_length, a_turn), (b_length, b_turn) in zip(left, rotated)
            ):
                return True
    return False


def _segment_lengths(match: _LengthMatch) -> tuple[float, ...]:
    total = float(match.primitive.length_raw)
    return tuple(float(ratio) * total for ratio in match.fingerprint.length_ratios)


def _resolution_for_selection(
    annotation: DxfEntityRecord,
    entities: Sequence[DxfEntityRecord],
    *,
    parsed,
    selected: Sequence[_LengthMatch],
    code: str,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, object]],
    policy: RecognitionPolicy,
    context: GeometryCandidateContext,
    shape_index: EngineShapeIndex,
    accept_congruent_member: bool = False,
) -> StirrupLengthGroupResolution | None:
    master_codes = _normalized_aliases(policy.stirrup_length_group_master_codes)
    declared = float(parsed.length_cm)
    length_tolerance = float(policy.stirrup_arc_length_tolerance_cm)
    definitions = shape_definitions
    tests = control_tests
    representative = min(
        selected,
        key=lambda value: (value.distance_raw, value.primitive.handle),
    )
    probe = DxfEntityRecord(
        handle=f"{annotation.handle}::STIRRUP_LENGTH_PROBE",
        entity_type="TEXT",
        layer=annotation.layer,
        text=str(annotation.text or ""),
        geometry={
            "insert": [representative.center[0], representative.center[1], 0.0],
            "height": 2.5,
        },
        raw={
            "source": "STIRRUP_LENGTH_GROUP_PROBE",
            "source_annotation_handle": str(annotation.handle),
            "resolution_code": code,
        },
    )
    analysis = find_geometry_candidates(
        probe,
        list(entities),
        max_candidates=8,
        shape_definitions=definitions,
        control_tests=tests,
        policy=policy,
        context=context,
        shape_index=shape_index,
    )
    if not analysis.candidates:
        return None
    candidate = analysis.candidates[0]
    if accept_congruent_member and len(candidate.handles) == 1:
        # При еднакви копия сондата може да върне съседното, начертано почти
        # върху представителя (s-11, Ш.7: на 3,7 единици). То е същото стреме
        # — формата вече е доказана за всички копия — и става представител.
        # Строгото правило за редица от стремена не ползва това облекчение.
        confirmed = next(
            (value for value in selected if value.primitive.handle == candidate.handles[0]),
            None,
        )
        if confirmed is not None:
            representative = confirmed
    if (
        tuple(candidate.handles) != (representative.primitive.handle,)
        or _normalize_token(candidate.master_shape_code) not in master_codes
        or candidate.master_ambiguous_shape_codes
        or abs(float(candidate.geometry_length_raw) - declared) > length_tolerance
    ):
        return None

    ordered_members = tuple(
        sorted(selected, key=lambda value: (value.distance_raw, value.primitive.handle))
    )
    return StirrupLengthGroupResolution(
        code=code,
        annotation_handle=str(annotation.handle),
        declared_count=int(parsed.count),
        declared_length_cm=declared,
        representative_handle=representative.primitive.handle,
        representative_distance_raw=representative.distance_raw,
        member_handles=tuple(value.primitive.handle for value in ordered_members),
        member_distances_raw=tuple(value.distance_raw for value in ordered_members),
        exact_arc_lengths_cm=tuple(
            float(value.primitive.length_raw) for value in ordered_members
        ),
        probe_annotation=probe,
        analysis=analysis,
    )


def _repeat_components(
    matches: Sequence[_LengthMatch],
    policy: RecognitionPolicy,
) -> tuple[tuple[int, ...], ...]:
    maximum_spacing = float(policy.stirrup_group_max_member_spacing_raw)
    length_tolerance = float(policy.stirrup_group_repeat_length_tolerance_cm)
    adjacency = {index: set() for index in range(len(matches))}
    for left_index, left in enumerate(matches):
        for right_index in range(left_index + 1, len(matches)):
            right = matches[right_index]
            if left.signature != right.signature:
                continue
            if abs(left.primitive.length_raw - right.primitive.length_raw) > length_tolerance:
                continue
            if _distance(left.center, right.center) > maximum_spacing:
                continue
            adjacency[left_index].add(right_index)
            adjacency[right_index].add(left_index)

    remaining = set(adjacency)
    components: list[tuple[int, ...]] = []
    while remaining:
        seed = min(remaining)
        stack = [seed]
        component: set[int] = set()
        while stack:
            index = stack.pop()
            if index in component:
                continue
            component.add(index)
            stack.extend(adjacency[index] - component)
        remaining -= component
        components.append(tuple(sorted(component)))
    return tuple(components)


def _row_is_valid(
    component: Sequence[int],
    matches: Sequence[_LengthMatch],
    policy: RecognitionPolicy,
) -> bool:
    if len(component) <= 2:
        return True
    points = [matches[index].center for index in component]
    first, last = max(
        (
            (left, right)
            for left_index, left in enumerate(points)
            for right in points[left_index + 1 :]
        ),
        key=lambda pair: _distance(pair[0], pair[1]),
    )
    span = _distance(first, last)
    if span <= 1e-9:
        return False
    maximum_deviation = float(policy.stirrup_group_max_row_deviation_raw)
    return all(
        _distance_point_to_line(point, first, last) <= maximum_deviation
        for point in points
    )


def _distance_point_to_line(
    point: Point2D,
    start: Point2D,
    end: Point2D,
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = math.hypot(dx, dy)
    if denominator <= 1e-12:
        return _distance(point, start)
    return abs(
        dy * point[0]
        - dx * point[1]
        + end[0] * start[1]
        - end[1] * start[0]
    ) / denominator


def _points_center(points: Sequence[Point2D]) -> Point2D:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _distance(left: Point2D, right: Point2D) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _normalized_aliases(values: Sequence[str]) -> frozenset[str]:
    return frozenset(_normalize_token(value) for value in values if str(value).strip())


def _normalize_token(value: object) -> str:
    return str(value or "").strip().upper()
