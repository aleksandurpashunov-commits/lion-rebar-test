from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
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

    definitions = tuple(shape_definitions)
    tests = tuple(control_tests)
    layer_aliases = _normalized_aliases(policy.stirrup_length_group_layer_aliases)
    master_codes = _normalized_aliases(policy.stirrup_length_group_master_codes)
    declared = float(parsed.length_cm)
    length_tolerance = float(policy.stirrup_arc_length_tolerance_cm)
    search_distance = float(policy.stirrup_length_search_distance_raw)
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
        if distance > search_distance:
            continue
        points = primitive.recognition_points or primitive.points
        master = match_master_shapes(
            points,
            definitions,
            policy=policy,
            control_tests=tests,
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
            )
        )

    matches.sort(
        key=lambda value: (
            value.distance_raw,
            value.primitive.handle,
        )
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
