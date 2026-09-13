from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Iterable, Sequence

from lion_rebar.dxf.geometry_core_v2 import (
    arc_path_from_geometry,
    polyline_path_from_geometry,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.line_chain_v015 import (
    NearGapBridge,
    NearGapEdge,
    audit_unique_one_gap_bridge,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import fingerprint_from_points

Point2D = tuple[float, float]
Segment2D = tuple[Point2D, Point2D]

SUPPORTED_MANUAL_GEOMETRY = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC"}
F006_COMPOSITE_KIND = "F006_SEPARATE_KEYS"


@dataclass(frozen=True)
class ManualGeometrySummary:
    handles: tuple[str, ...]
    entity_types: tuple[str, ...]
    layers: tuple[str, ...]
    points: tuple[Point2D, ...]
    closed: bool
    connected: bool
    branched: bool
    segment_count: int
    segment_lengths_cm: tuple[float, ...]
    turn_angles_deg: tuple[float, ...]
    total_length_cm: float
    canonical_signature: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    physical_connected: bool = True
    component_count: int = 1
    inferred_composite: bool = False
    composite_kind: str | None = None
    inference_note: str | None = None
    near_gap_healed: bool = False
    near_gap_bridge_gap_raw: float | None = None
    near_gap_bridge_handles: tuple[str, str] = field(default_factory=tuple)
    near_gap_bridge_points: tuple[Point2D, Point2D] = field(default_factory=tuple)
    training_eligible: bool = True
    contains_curves: bool = False
    curve_segment_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class ManualGeometryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _CompositeInference:
    loop_points: tuple[Point2D, ...]
    segment_lengths: tuple[float, ...]
    turn_angles: tuple[float, ...]
    signature: str
    component_count: int
    note: str
    fit_score: float = 0.0


def validate_manual_geometry_records(
    records: Iterable[DxfEntityRecord],
    *,
    allowed_handles: Iterable[str],
    protected_handles: Iterable[str] = (),
) -> tuple[DxfEntityRecord, ...]:
    """Fail closed before a UI manual assignment is summarized or saved.

    ``allowed_handles`` must come from the authoritative rebar primitives in
    ``GeometryCandidateContext``.  ``protected_handles`` contains geometry
    proven to belong to a shared position-key legend.  The helper deliberately
    remains separate from :func:`summarize_selected_geometry`, because export
    and audit code may legitimately summarize already-approved historical
    records; this guard is specifically for a new manual UI assignment.
    """

    selected = tuple(records)
    if not selected:
        raise ManualGeometryError(
            "Не са избрани DXF геометрии за инженерно решение."
        )

    unsupported = tuple(
        record
        for record in selected
        if not str(record.handle or "").strip()
        or str(record.entity_type or "").upper() not in SUPPORTED_MANUAL_GEOMETRY
    )
    if unsupported:
        labels = ", ".join(
            f"{record.handle or '—'} ({record.entity_type or '—'})"
            for record in unsupported
        )
        raise ManualGeometryError(
            "Изборът съдържа неподдържани или неидентифицирани DXF обекти: "
            f"{labels}. Избери само LINE, LWPOLYLINE, POLYLINE или ARC."
        )

    allowed = {
        str(handle).strip().upper()
        for handle in allowed_handles
        if str(handle).strip()
    }
    protected = {
        str(handle).strip().upper()
        for handle in protected_handles
        if str(handle).strip()
    }
    selected_by_handle = {
        str(record.handle).strip().upper(): record for record in selected
    }

    protected_selected = tuple(
        selected_by_handle[handle]
        for handle in sorted(selected_by_handle)
        if handle in protected
    )
    if protected_selected:
        labels = ", ".join(
            f"{record.handle} ({record.layer or 'без слой'})"
            for record in protected_selected
        )
        raise ManualGeometryError(
            "Избраната геометрия е заключена като обща геометрия на легендата "
            f"и не може да се присвои на отделна позиция: {labels}."
        )

    foreign = tuple(
        record
        for handle, record in selected_by_handle.items()
        if handle not in allowed
    )
    if foreign:
        labels = ", ".join(
            f"{record.handle} ({record.layer or 'без слой'})"
            for record in foreign
        )
        raise ManualGeometryError(
            "Изборът съдържа обекти извън доказаните армировъчни слоеве: "
            f"{labels}. Маркирай само реалната армировъчна геометрия."
        )

    return selected


def summarize_selected_geometry(
    records: Iterable[DxfEntityRecord],
    *,
    policy: RecognitionPolicy | None = None,
) -> ManualGeometrySummary:
    resolved_policy = policy or RecognitionPolicy()
    selected = [
        record
        for record in records
        if record.handle and record.entity_type.upper() in SUPPORTED_MANUAL_GEOMETRY
    ]
    if not selected:
        raise ManualGeometryError(
            "Не са избрани поддържани DXF геометрии. Избери LINE, "
            "LWPOLYLINE, POLYLINE или ARC."
        )

    segments: list[tuple[Point2D, Point2D, str]] = []
    declared_closed = False
    curve_segment_count = 0
    exact_source_length_total = 0.0
    warnings: list[str] = []
    for record in selected:
        entity_type = record.entity_type.upper()
        if entity_type == "LINE":
            start = _point(record.geometry.get("start"))
            end = _point(record.geometry.get("end"))
            if start is None or end is None or _distance(start, end) <= 1e-9:
                warnings.append(f"{record.handle}: LINE без валиден сегмент.")
                continue
            segments.append((start, end, record.handle))
            exact_source_length_total += _distance(start, end)
            continue

        path = (
            arc_path_from_geometry(record.geometry)
            if entity_type == "ARC"
            else polyline_path_from_geometry(record.geometry)
        )
        if path is None:
            warnings.append(
                f"{record.handle}: крива/полилиния без достатъчно геометрични данни."
            )
            continue
        curve_segment_count += path.curve_count
        exact_source_length_total += path.exact_length_raw
        points = list(path.points)
        if len(points) < 2:
            warnings.append(f"{record.handle}: крива без достатъчно точки.")
            continue
        closed = path.closed
        declared_closed = declared_closed or closed
        for start, end in zip(points, points[1:]):
            if _distance(start, end) > 1e-9:
                segments.append((start, end, record.handle))
        if closed and _distance(points[-1], points[0]) > 1e-9:
            segments.append((points[-1], points[0], record.handle))

    if not segments:
        raise ManualGeometryError("Избраните обекти не съдържат измерими сегменти.")
    if curve_segment_count:
        warnings.append(
            "Избраната геометрия съдържа дъгови сегменти. Общата измерена L "
            "се изчислява по точните дъги; топологията остава отделно и не "
            "превръща автоматично кривите в праволинейна Engine форма."
        )

    raw_segments = [(start, end) for start, end, _handle in segments]
    endpoint_tolerance = max(float(resolved_policy.endpoint_tolerance_raw), 1e-6)
    (
        ordered_points,
        physical_connected,
        closed,
        branched,
        ordered_edge_indices,
    ) = _order_segments(
        raw_segments,
        tolerance=endpoint_tolerance,
    )
    component_count = _component_count(raw_segments, tolerance=endpoint_tolerance)
    near_gap_bridge: NearGapBridge | None = None
    if (
        resolved_policy.near_gap_healing_enabled
        and not physical_connected
        and not branched
        and float(resolved_policy.near_gap_max_raw) > endpoint_tolerance
        and len(selected) == len(raw_segments)
        and all(record.entity_type.upper() == "LINE" for record in selected)
    ):
        record_by_handle = {record.handle: record for record in selected}
        near_gap_edges = tuple(
            NearGapEdge(
                handle=handle,
                layer=record_by_handle[handle].layer,
                start=start,
                end=end,
            )
            for start, end, handle in segments
            if handle in record_by_handle
        )
        near_gap_bridge = audit_unique_one_gap_bridge(
            near_gap_edges,
            strict_tolerance_raw=endpoint_tolerance,
            max_gap_raw=float(resolved_policy.near_gap_max_raw),
        )
        if near_gap_bridge is not None:
            (
                healed_points,
                healed_connected,
                healed_closed,
                healed_branched,
                healed_edge_indices,
            ) = _order_segments(
                raw_segments,
                tolerance=float(resolved_policy.near_gap_max_raw),
            )
            if healed_connected and not healed_branched:
                ordered_points = healed_points
                closed = healed_closed
                branched = False
                ordered_edge_indices = healed_edge_indices
            else:
                near_gap_bridge = None

    if declared_closed and physical_connected and ordered_points:
        closed = True
        if _distance(ordered_points[0], ordered_points[-1]) > endpoint_tolerance:
            ordered_points = tuple(ordered_points) + (ordered_points[0],)

    composite = None
    if near_gap_bridge is None and (not physical_connected or not closed or branched):
        composite = _infer_f006_composite(
            raw_segments,
            tolerance=endpoint_tolerance,
        )

    inferred_composite = composite is not None
    composite_kind = None
    inference_note = None
    logical_connected = physical_connected or near_gap_bridge is not None
    if composite is not None:
        ordered_points = composite.loop_points
        logical_connected = True
        closed = True
        branched = False
        component_count = composite.component_count
        composite_kind = F006_COMPOSITE_KIND
        inference_note = composite.note
        warnings.append(composite.note)
    else:
        if near_gap_bridge is not None:
            warnings.append(
                "Една чертожна празнина е свързана контролирано само за ръчно "
                f"задаване: {near_gap_bridge.gap_raw:.3f} между "
                f"{near_gap_bridge.edge_handles[0]} и "
                f"{near_gap_bridge.edge_handles[1]}. Геометрията не е физически "
                "свързана и не е подходяща за автоматично обучение."
            )
        elif not physical_connected:
            warnings.append("Избраната геометрия има повече от един несвързан компонент.")
        if branched:
            warnings.append("Избраната геометрия е разклонена и не е една армировъчна форма.")

    lengths: tuple[float, ...]
    turns: tuple[float, ...]
    signature: str | None
    if composite is not None:
        lengths = composite.segment_lengths
        turns = composite.turn_angles
        signature = composite.signature
    elif logical_connected and not branched and len(ordered_points) >= 2:
        if near_gap_bridge is not None:
            # Keep the physical DXF measurements auditable.  The bridged points
            # are used only for topology/angle matching, never to invent length.
            lengths = tuple(
                _distance(*raw_segments[index])
                for index in ordered_edge_indices
            )
        else:
            lengths = tuple(
                _distance(left, right)
                for left, right in zip(ordered_points, ordered_points[1:])
                if _distance(left, right) > 1e-9
            )
        fingerprint = fingerprint_from_points(
            ordered_points,
            closed=closed,
            policy=resolved_policy,
        )
        if fingerprint is not None:
            turns = tuple(fingerprint.turn_angles_deg)
            signature = fingerprint.canonical_signature
        else:
            turns = _turns_from_points(ordered_points, closed=closed)
            signature = None
    else:
        lengths = tuple(_distance(start, end) for start, end, _handle in segments)
        turns = ()
        signature = None

    return ManualGeometrySummary(
        handles=tuple(sorted({record.handle for record in selected if record.handle})),
        entity_types=tuple(sorted({record.entity_type.upper() for record in selected})),
        layers=tuple(sorted({record.layer for record in selected if record.layer})),
        points=tuple(ordered_points),
        closed=bool(closed),
        connected=bool(logical_connected),
        branched=bool(branched),
        segment_count=len(lengths),
        segment_lengths_cm=tuple(round(value, 6) for value in lengths),
        turn_angles_deg=tuple(round(value, 3) for value in turns),
        total_length_cm=round(exact_source_length_total, 6),
        canonical_signature=signature,
        warnings=tuple(warnings),
        physical_connected=bool(physical_connected),
        component_count=int(component_count),
        inferred_composite=bool(inferred_composite),
        composite_kind=composite_kind,
        inference_note=inference_note,
        near_gap_healed=near_gap_bridge is not None,
        near_gap_bridge_gap_raw=(
            near_gap_bridge.gap_raw if near_gap_bridge is not None else None
        ),
        near_gap_bridge_handles=(
            near_gap_bridge.edge_handles if near_gap_bridge is not None else ()
        ),
        near_gap_bridge_points=(
            near_gap_bridge.points if near_gap_bridge is not None else ()
        ),
        training_eligible=near_gap_bridge is None,
        contains_curves=curve_segment_count > 0,
        curve_segment_count=curve_segment_count,
    )


def _nonzero_number(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and abs(number) > 1e-12



def infer_f006_composite_segments(
    segments: Sequence[Segment2D],
    *,
    tolerance: float,
) -> _CompositeInference | None:
    """Public, read-only F006 inference used by automatic candidate generation.

    The function is intentionally conservative and returns a result only for the
    exact engineering topology already accepted by manual assignment: four cage
    sides forming a rectangle plus two short, parallel keys.  It never modifies
    MASTER data and never approves a link.
    """

    return _infer_f006_composite(segments, tolerance=tolerance)

def _infer_f006_composite(
    segments: Sequence[Segment2D],
    *,
    tolerance: float,
) -> _CompositeInference | None:
    """Recognise a manually selected stirrup as rectangle + two separate keys.

    Many structural DXFs draw F006 as six independent LINE objects: four sides of
    the cage and two short keys/hooks that intentionally do not share endpoints
    with the cage.  This is a composite engineering symbol, not a single Euler
    path.  Manual assignment may accept it only when the pattern is strict.
    """

    if len(segments) != 6:
        return None

    # F006 is frequently drawn as six independent LINE objects:
    # four straight portions of the cage and two diagonal keys.  The straight
    # portions usually stop before the theoretical corner because the bend is
    # represented symbolically.  Consequently the four cage lines do *not*
    # form a literal closed graph and cannot be recognised only by endpoint
    # coincidence.
    relaxed_tolerance = max(2.5, tolerance * 4.0)
    best: tuple[float, _CompositeInference] | None = None

    for loop_indexes in combinations(range(6), 4):
        loop_segments = [segments[index] for index in loop_indexes]
        rectangle = _infer_gapped_rectangle(loop_segments, tolerance=relaxed_tolerance)
        if rectangle is None:
            continue
        loop_points, cage_fit_error = rectangle

        extra_indexes = [index for index in range(6) if index not in loop_indexes]
        keys = [segments[index] for index in extra_indexes]
        key_match = _keys_fit_rectangle(keys, loop_points)
        if key_match is None:
            continue
        key_lengths, key_corner_distance = key_match
        # Use the measured straight LINE lengths, not the theoretical distances
        # between extended corner intersections.  This preserves the dimensions
        # actually encoded by the designer (for example 34+64+34+64+20+20).
        loop_lengths = _ordered_rectangle_segment_lengths(loop_segments, loop_points)
        if loop_lengths is None:
            continue
        turns = _turns_from_points(loop_points, closed=True)
        all_lengths = tuple(loop_lengths) + tuple(key_lengths)
        normalized = ",".join(
            f"{value / max(sum(all_lengths), 1e-9):.5f}" for value in all_lengths
        )
        signature = (
            f"C|F006_COMPOSITE|n=6|r={normalized}|"
            + "a="
            + ",".join(f"{value:.2f}" for value in turns)
        )
        components = _component_count(segments, tolerance=tolerance)
        score = key_corner_distance + cage_fit_error + _rectangle_error(loop_points)
        inference = _CompositeInference(
            loop_points=tuple(loop_points),
            segment_lengths=all_lengths,
            turn_angles=tuple(turns),
            signature=signature,
            component_count=components,
            note=(
                "Разпозната е съставна геометрия F006: затворен контур и два "
                "отделени ключа. DXF обектите остават физически разделени, но могат "
                "да бъдат зададени като едно стреме след визуално потвърждение."
            ),
            fit_score=float(score),
        )
        if best is None or score < best[0]:
            best = (score, inference)

    return best[1] if best is not None else None


def _infer_gapped_rectangle(
    segments: Sequence[Segment2D],
    *,
    tolerance: float,
) -> tuple[tuple[Point2D, ...], float] | None:
    """Infer a rectangle from four separated straight portions.

    A structural stirrup is often drawn with a gap at every bend.  We therefore
    compare the supporting infinite lines instead of requiring endpoints to
    touch.  The function remains conservative: exactly two parallel pairs are
    required, the pairs must be close to perpendicular, opposite measured
    lengths must agree, and every endpoint must be reasonably close to its
    theoretical corner.
    """

    if len(segments) != 4:
        return None

    directions = [_unit_direction(segment) for segment in segments]
    if any(direction is None for direction in directions):
        return None
    directions = [direction for direction in directions if direction is not None]

    pairings = (
        ((0, 1), (2, 3)),
        ((0, 2), (1, 3)),
        ((0, 3), (1, 2)),
    )
    best: tuple[float, tuple[Point2D, ...]] | None = None
    for first_pair, second_pair in pairings:
        if not _parallel(directions[first_pair[0]], directions[first_pair[1]], tolerance_deg=12.0):
            continue
        if not _parallel(directions[second_pair[0]], directions[second_pair[1]], tolerance_deg=12.0):
            continue
        first_axis = directions[first_pair[0]]
        second_axis = directions[second_pair[0]]
        perpendicular_error = abs(90.0 - _acute_angle_deg(first_axis, second_axis))
        if perpendicular_error > 15.0:
            continue

        first_lengths = [_segment_length(segments[index]) for index in first_pair]
        second_lengths = [_segment_length(segments[index]) for index in second_pair]
        if not _similar(first_lengths[0], first_lengths[1], ratio=0.20, absolute=3.0):
            continue
        if not _similar(second_lengths[0], second_lengths[1], ratio=0.20, absolute=3.0):
            continue

        intersections: dict[tuple[int, int], Point2D] = {}
        valid = True
        for first_index in first_pair:
            for second_index in second_pair:
                point = _line_intersection(segments[first_index], segments[second_index])
                if point is None:
                    valid = False
                    break
                intersections[(first_index, second_index)] = point
            if not valid:
                break
        if not valid:
            continue

        corners = tuple(intersections.values())
        ordered = _order_corners(corners)
        if len(ordered) != 4:
            continue
        loop_points = tuple(ordered) + (ordered[0],)
        if not _rectangle_like(loop_points):
            continue

        # Every straight portion may stop short of a corner, but it must lie on
        # the corresponding side and cover most of it.  Gaps up to 25% of the
        # short side (maximum 12 cm) are accepted to account for symbolic bends.
        theoretical_lengths = [
            _distance(left, right) for left, right in zip(loop_points, loop_points[1:])
        ]
        short_side = min(theoretical_lengths)
        max_corner_gap = max(4.0, min(12.0, short_side * 0.25))
        endpoint_error = 0.0
        for segment in segments:
            matching_side = _matching_rectangle_side(segment, loop_points)
            if matching_side is None:
                valid = False
                break
            side_start, side_end = matching_side
            gap = _segment_to_side_corner_gap(segment, side_start, side_end)
            if gap > max_corner_gap * 2.0:
                valid = False
                break
            endpoint_error += gap
        if not valid:
            continue

        score = perpendicular_error + endpoint_error + _rectangle_error(loop_points)
        if best is None or score < best[0]:
            best = (score, loop_points)

    if best is None:
        return None
    return best[1], best[0]


def _ordered_rectangle_segment_lengths(
    segments: Sequence[Segment2D],
    loop_points: Sequence[Point2D],
) -> tuple[float, ...] | None:
    ordered: list[float] = []
    unused = set(range(len(segments)))
    for side_start, side_end in zip(loop_points, loop_points[1:]):
        side_direction = _unit_direction((side_start, side_end))
        if side_direction is None:
            return None
        candidates = [
            index
            for index in unused
            if (
                (direction := _unit_direction(segments[index])) is not None
                and _parallel(side_direction, direction, tolerance_deg=12.0)
                and _distance_point_to_line(_segment_midpoint(segments[index]), side_start, side_end)
                <= 3.5
            )
        ]
        if not candidates:
            return None
        index = min(
            candidates,
            key=lambda value: _distance_point_to_line(
                _segment_midpoint(segments[value]), side_start, side_end
            ),
        )
        unused.remove(index)
        ordered.append(_segment_length(segments[index]))
    return tuple(ordered) if not unused else None


def _canonical_closed_loop(points: Sequence[Point2D], *, tolerance: float) -> tuple[Point2D, ...]:
    work = list(points)
    if not work:
        return ()
    if _distance(work[0], work[-1]) > tolerance:
        work.append(work[0])
    # Snap the final point to the first for stable output.
    work[-1] = work[0]
    return tuple(work)


def _rectangle_like(points: Sequence[Point2D]) -> bool:
    if len(points) != 5 or _distance(points[0], points[-1]) > 1e-6:
        return False
    lengths = [_distance(left, right) for left, right in zip(points, points[1:])]
    if min(lengths, default=0.0) <= 1e-6:
        return False
    turns = _turns_from_points(points, closed=True)
    if len(turns) != 4 or any(abs(abs(value) - 90.0) > 15.0 for value in turns):
        return False
    if not _similar(lengths[0], lengths[2], ratio=0.18, absolute=2.5):
        return False
    if not _similar(lengths[1], lengths[3], ratio=0.18, absolute=2.5):
        return False
    diagonal_a = _distance(points[0], points[2])
    diagonal_b = _distance(points[1], points[3])
    if not _similar(diagonal_a, diagonal_b, ratio=0.12, absolute=3.0):
        return False
    return abs(_polygon_area(points[:-1])) > 1e-6


def _rectangle_error(points: Sequence[Point2D]) -> float:
    turns = _turns_from_points(points, closed=True)
    angle_error = sum(abs(abs(value) - 90.0) for value in turns)
    lengths = [_distance(left, right) for left, right in zip(points, points[1:])]
    opposite_error = abs(lengths[0] - lengths[2]) + abs(lengths[1] - lengths[3])
    return angle_error + opposite_error * 0.05


def _keys_fit_rectangle(
    keys: Sequence[Segment2D],
    loop_points: Sequence[Point2D],
) -> tuple[tuple[float, float], float] | None:
    if len(keys) != 2:
        return None
    lengths = tuple(_distance(start, end) for start, end in keys)
    if min(lengths) <= 1e-6:
        return None
    if not _similar(lengths[0], lengths[1], ratio=0.25, absolute=3.0):
        return None

    key_directions = [_unit_direction(segment) for segment in keys]
    if any(direction is None for direction in key_directions):
        return None
    if not _parallel(key_directions[0], key_directions[1], tolerance_deg=18.0):
        return None

    corners = tuple(loop_points[:-1])
    corner_matches: list[tuple[float, int]] = []
    for segment, length in zip(keys, lengths):
        distances = [
            min(
                _distance(segment[0], corner),
                _distance(segment[1], corner),
                _distance(_segment_midpoint(segment), corner),
            )
            for corner in corners
        ]
        best_distance = min(distances)
        best_corner = distances.index(best_distance)
        allowed = max(8.0, min(24.0, length * 1.25))
        if best_distance > allowed:
            return None
        corner_matches.append((best_distance, best_corner))

    # Real stirrup symbols often draw both overlapping keys at the same corner.
    # Two distinct corners are also valid, therefore no uniqueness constraint is
    # imposed.  The parallelism and proximity checks above prevent arbitrary
    # nearby lines from being accepted as keys.
    return lengths, sum(value[0] for value in corner_matches)


def _segment_length(segment: Segment2D) -> float:
    return _distance(segment[0], segment[1])


def _segment_midpoint(segment: Segment2D) -> Point2D:
    return (
        (segment[0][0] + segment[1][0]) * 0.5,
        (segment[0][1] + segment[1][1]) * 0.5,
    )


def _unit_direction(segment: Segment2D) -> Point2D | None:
    dx = segment[1][0] - segment[0][0]
    dy = segment[1][1] - segment[0][1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    return dx / length, dy / length


def _parallel(left: Point2D, right: Point2D, *, tolerance_deg: float) -> bool:
    return _acute_angle_deg(left, right) <= tolerance_deg


def _acute_angle_deg(left: Point2D, right: Point2D) -> float:
    dot = max(-1.0, min(1.0, left[0] * right[0] + left[1] * right[1]))
    angle = math.degrees(math.acos(abs(dot)))
    return min(angle, 180.0 - angle)


def _line_intersection(left: Segment2D, right: Segment2D) -> Point2D | None:
    x1, y1 = left[0]
    x2, y2 = left[1]
    x3, y3 = right[0]
    x4, y4 = right[1]
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) <= 1e-9:
        return None
    determinant_left = x1 * y2 - y1 * x2
    determinant_right = x3 * y4 - y3 * x4
    x = (
        determinant_left * (x3 - x4)
        - (x1 - x2) * determinant_right
    ) / denominator
    y = (
        determinant_left * (y3 - y4)
        - (y1 - y2) * determinant_right
    ) / denominator
    return float(x), float(y)


def _order_corners(points: Sequence[Point2D]) -> tuple[Point2D, ...]:
    unique: list[Point2D] = []
    for point in points:
        if not any(_distance(point, existing) <= 1e-6 for existing in unique):
            unique.append(point)
    if len(unique) != 4:
        return ()
    center = (
        sum(point[0] for point in unique) / 4.0,
        sum(point[1] for point in unique) / 4.0,
    )
    ordered = sorted(unique, key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]))
    # Ensure clockwise/counter-clockwise order starts at a deterministic corner.
    start = min(range(4), key=lambda index: (ordered[index][1], ordered[index][0]))
    return tuple(ordered[start:] + ordered[:start])


def _matching_rectangle_side(
    segment: Segment2D,
    loop_points: Sequence[Point2D],
) -> tuple[Point2D, Point2D] | None:
    direction = _unit_direction(segment)
    if direction is None:
        return None
    midpoint = _segment_midpoint(segment)
    matches: list[tuple[float, Point2D, Point2D]] = []
    for side_start, side_end in zip(loop_points, loop_points[1:]):
        side_direction = _unit_direction((side_start, side_end))
        if side_direction is None or not _parallel(direction, side_direction, tolerance_deg=12.0):
            continue
        distance = _distance_point_to_line(midpoint, side_start, side_end)
        matches.append((distance, side_start, side_end))
    if not matches:
        return None
    distance, side_start, side_end = min(matches, key=lambda item: item[0])
    return (side_start, side_end) if distance <= 3.5 else None


def _distance_point_to_line(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return _distance(point, start)
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / length


def _segment_to_side_corner_gap(
    segment: Segment2D,
    side_start: Point2D,
    side_end: Point2D,
) -> float:
    # Match endpoints to theoretical corners in the cheaper of the two
    # orientations.  The result is the sum of the two corner gaps.
    direct = _distance(segment[0], side_start) + _distance(segment[1], side_end)
    reverse = _distance(segment[1], side_start) + _distance(segment[0], side_end)
    return min(direct, reverse)


def _similar(left: float, right: float, *, ratio: float, absolute: float) -> bool:
    difference = abs(float(left) - float(right))
    return difference <= max(absolute, ratio * max(abs(float(left)), abs(float(right)), 1.0))


def _polygon_area(points: Sequence[Point2D]) -> float:
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, tuple(points[1:]) + (points[0],))
    )


def _component_count(segments: Sequence[Segment2D], *, tolerance: float) -> int:
    if not segments:
        return 0
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(segments))}
    for left_index, left in enumerate(segments):
        for right_index in range(left_index + 1, len(segments)):
            right = segments[right_index]
            if any(
                _distance(left_point, right_point) <= tolerance
                for left_point in left
                for right_point in right
            ):
                adjacency[left_index].add(right_index)
                adjacency[right_index].add(left_index)
    remaining = set(adjacency)
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            for neighbour in adjacency[current]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
    return count


def _order_segments(
    segments: Sequence[Segment2D],
    *,
    tolerance: float,
) -> tuple[tuple[Point2D, ...], bool, bool, bool, tuple[int, ...]]:
    nodes: list[Point2D] = []
    edge_nodes: list[tuple[int, int]] = []

    def node_for(point: Point2D) -> int:
        for index, existing in enumerate(nodes):
            if _distance(point, existing) <= tolerance:
                return index
        nodes.append(point)
        return len(nodes) - 1

    adjacency: dict[int, list[int]] = {}
    for edge_index, (start, end) in enumerate(segments):
        left = node_for(start)
        right = node_for(end)
        edge_nodes.append((left, right))
        adjacency.setdefault(left, []).append(edge_index)
        adjacency.setdefault(right, []).append(edge_index)

    if not edge_nodes:
        return (), False, False, False, ()

    visited_edges: set[int] = set()
    stack = [0]
    while stack:
        edge_index = stack.pop()
        if edge_index in visited_edges:
            continue
        visited_edges.add(edge_index)
        left, right = edge_nodes[edge_index]
        for node in (left, right):
            stack.extend(adjacency.get(node, ()))
    connected = len(visited_edges) == len(edge_nodes)
    degrees = {node: len(edges) for node, edges in adjacency.items()}
    branched = any(degree > 2 for degree in degrees.values())
    degree_one = [node for node, degree in degrees.items() if degree == 1]
    closed = connected and not branched and not degree_one and all(
        degree == 2 for degree in degrees.values()
    )

    if not connected or branched:
        return tuple(nodes), connected, closed, branched, ()
    if not closed and len(degree_one) != 2:
        return tuple(nodes), False, False, False, ()

    start_node = min(degree_one) if degree_one else min(adjacency)
    current_node = start_node
    used: set[int] = set()
    edge_order: list[int] = []
    ordered: list[Point2D] = [nodes[current_node]]

    while len(used) < len(edge_nodes):
        options = [edge for edge in adjacency[current_node] if edge not in used]
        if not options:
            break
        edge = min(options)
        used.add(edge)
        edge_order.append(edge)
        left, right = edge_nodes[edge]
        next_node = right if left == current_node else left
        ordered.append(nodes[next_node])
        current_node = next_node
        if closed and current_node == start_node and len(used) == len(edge_nodes):
            break

    if len(used) != len(edge_nodes):
        return tuple(ordered), False, closed, False, tuple(edge_order)
    if closed and _distance(ordered[0], ordered[-1]) > tolerance:
        ordered.append(ordered[0])
    return tuple(ordered), True, closed, False, tuple(edge_order)


def _turns_from_points(points: Sequence[Point2D], *, closed: bool) -> tuple[float, ...]:
    work = list(points)
    if closed and work and _distance(work[0], work[-1]) > 1e-9:
        work.append(work[0])
    vectors: list[Point2D] = []
    for start, end in zip(work, work[1:]):
        vector = (end[0] - start[0], end[1] - start[1])
        if math.hypot(*vector) > 1e-9:
            vectors.append(vector)
    count = len(vectors) if closed else max(0, len(vectors) - 1)
    result: list[float] = []
    for index in range(count):
        left = vectors[index]
        right = vectors[(index + 1) % len(vectors)]
        cross = left[0] * right[1] - left[1] * right[0]
        dot = left[0] * right[0] + left[1] * right[1]
        result.append(math.degrees(math.atan2(cross, dot)))
    return tuple(result)


def _point(value) -> Point2D | None:
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _distance(left: Point2D, right: Point2D) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])
