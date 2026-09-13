from __future__ import annotations

"""DXF path geometry with explicit curve and representation semantics.

The matcher historically read every LWPOLYLINE vertex as a sharp straight
corner.  That is incorrect for DXF ``bulge`` values: a common rounded 90-degree
bend is stored as ``LINE -> ARC -> LINE`` and its two arc chord endpoints look
like two extra 45-degree corners.

This module keeps the source path losslessly, derives exact circular-arc
properties, and exposes a conservative semantic linearization.  Only a
geometrically proven tangent connector arc is collapsed to the intersection of
its two straight tangent runs.  Endpoint hooks, non-tangent arcs and suspected
stroked outlines remain explicit and are never silently flattened.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Any

Point2D = tuple[float, float]
Segment2D = tuple[float, float, float, float]

_EPSILON = 1e-9


class PathElementKind(str, Enum):
    LINE = "LINE"
    ARC = "ARC"


class GeometryRepresentation(str, Enum):
    CENTERLINE = "CENTERLINE"
    CURVED_CENTERLINE = "CURVED_CENTERLINE"
    STROKED_OUTLINE = "STROKED_OUTLINE"
    AMBIGUOUS_CLOSED_PATH = "AMBIGUOUS_CLOSED_PATH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PolylineVertex:
    point: Point2D
    bulge: float = 0.0


@dataclass(frozen=True)
class PathElement:
    kind: PathElementKind
    start: Point2D
    end: Point2D
    length_raw: float
    bulge: float = 0.0
    center: Point2D | None = None
    radius_raw: float | None = None
    sweep_deg: float | None = None
    start_tangent: Point2D | None = None
    end_tangent: Point2D | None = None

    @property
    def is_curve(self) -> bool:
        return self.kind is PathElementKind.ARC


@dataclass(frozen=True)
class PolylinePath:
    vertices: tuple[PolylineVertex, ...]
    elements: tuple[PathElement, ...]
    closed: bool

    @property
    def points(self) -> tuple[Point2D, ...]:
        points = tuple(vertex.point for vertex in self.vertices)
        if self.closed and points and not _points_close(points[0], points[-1]):
            return points + (points[0],)
        return points

    @property
    def curve_count(self) -> int:
        return sum(element.is_curve for element in self.elements)

    @property
    def exact_length_raw(self) -> float:
        return sum(element.length_raw for element in self.elements)

    @property
    def arc_radii_raw(self) -> tuple[float, ...]:
        return tuple(
            float(element.radius_raw)
            for element in self.elements
            if element.radius_raw is not None
        )


@dataclass(frozen=True)
class RepresentationAssessment:
    representation: GeometryRepresentation
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SemanticPath:
    """A matcher-safe path plus audit evidence about curve handling."""

    points: tuple[Point2D, ...]
    representation: GeometryRepresentation
    source_curve_count: int
    collapsed_arc_count: int
    unresolved_arc_count: int
    semantic_length_raw: float
    exact_curve_length_raw: float
    arc_radii_raw: tuple[float, ...]
    safe_for_linear_matching: bool
    hooked_closure: bool
    hooked_closure_gap_raw: float | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class BorderlineF006SemanticRecovery:
    """Strict evidence for one narrowly recoverable rounded F006 contour.

    This is deliberately separate from :func:`semantic_path_from_polyline`.
    The generic 3% tangent/offset law remains unchanged for every other DXF
    curve. A caller may use this result only together with contour-owned
    dimensions and a successful run of the normal MASTER matcher.
    """

    points: tuple[Point2D, ...]
    missing_arc_index: int
    offset_residual_ratio: float
    radius_relative_spread: float
    hooked_closure_gap_raw: float


def polyline_path_from_geometry(
    geometry: Mapping[str, Any],
) -> PolylinePath | None:
    """Parse current and legacy imported polyline geometry.

    Current LWPOLYLINE records store ``[x, y, bulge]`` vertices.  POLYLINE
    records historically stored ``[x, y, z]`` plus a separate ``bulges`` list,
    so the explicit list takes precedence whenever it is present.
    """

    raw_points = geometry.get("points")
    if not isinstance(raw_points, (list, tuple)):
        return None

    explicit_bulges = geometry.get("bulges")
    has_explicit_bulges = isinstance(explicit_bulges, (list, tuple))
    vertices: list[PolylineVertex] = []
    for index, raw in enumerate(raw_points):
        point = _point(raw)
        if point is None:
            continue
        if has_explicit_bulges and index < len(explicit_bulges):
            bulge = _finite_float(explicit_bulges[index]) or 0.0
        elif isinstance(raw, (list, tuple)) and len(raw) >= 3:
            # For LWPOLYLINE the third item is bulge.  A legacy POLYLINE has
            # explicit ``bulges`` above, so its Z coordinate is never confused
            # with a curve.
            bulge = _finite_float(raw[2]) or 0.0
        else:
            bulge = 0.0
        vertices.append(PolylineVertex(point=point, bulge=bulge))

    if len(vertices) < 2:
        return None
    closed = bool(geometry.get("closed"))
    if closed and _points_close(vertices[0].point, vertices[-1].point):
        vertices.pop()
    if len(vertices) < 2:
        return None

    edge_count = len(vertices) if closed else len(vertices) - 1
    elements: list[PathElement] = []
    for index in range(edge_count):
        left = vertices[index]
        right = vertices[(index + 1) % len(vertices)]
        if _points_close(left.point, right.point):
            continue
        element = element_from_bulge(left.point, right.point, left.bulge)
        if element is not None:
            elements.append(element)
    if not elements:
        return None
    return PolylinePath(
        vertices=tuple(vertices),
        elements=tuple(elements),
        closed=closed,
    )


def arc_path_from_geometry(
    geometry: Mapping[str, Any],
) -> PolylinePath | None:
    """Build an exact open path from one standalone DXF ARC entity.

    DXF ARC angles are counter-clockwise in degrees.  The path deliberately
    stays an unresolved curve for MASTER topology matching, but its exact
    centreline length and tessellated proximity geometry are available to the
    candidate engine instead of silently dropping the entity.
    """

    center = _point(geometry.get("center"))
    radius = _finite_float(geometry.get("radius"))
    start_deg = _finite_float(geometry.get("start_angle"))
    end_deg = _finite_float(geometry.get("end_angle"))
    if (
        center is None
        or radius is None
        or radius <= _EPSILON
        or start_deg is None
        or end_deg is None
    ):
        return None

    sweep_deg = (end_deg - start_deg) % 360.0
    if sweep_deg <= 1e-9 or sweep_deg >= 360.0 - 1e-9:
        return None
    start_rad = math.radians(start_deg)
    end_rad = start_rad + math.radians(sweep_deg)
    start = (
        center[0] + radius * math.cos(start_rad),
        center[1] + radius * math.sin(start_rad),
    )
    end = (
        center[0] + radius * math.cos(end_rad),
        center[1] + radius * math.sin(end_rad),
    )
    sweep_rad = math.radians(sweep_deg)
    bulge = math.tan(sweep_rad / 4.0)
    element = PathElement(
        kind=PathElementKind.ARC,
        start=start,
        end=end,
        length_raw=radius * sweep_rad,
        bulge=bulge,
        center=center,
        radius_raw=radius,
        sweep_deg=sweep_deg,
        start_tangent=(-math.sin(start_rad), math.cos(start_rad)),
        end_tangent=(-math.sin(end_rad), math.cos(end_rad)),
    )
    return PolylinePath(
        vertices=(PolylineVertex(start, bulge), PolylineVertex(end, 0.0)),
        elements=(element,),
        closed=False,
    )


def element_from_bulge(
    start: Point2D,
    end: Point2D,
    bulge: float,
) -> PathElement | None:
    """Build a line or exact circular arc from one DXF bulge edge."""

    chord_x = float(end[0]) - float(start[0])
    chord_y = float(end[1]) - float(start[1])
    chord = math.hypot(chord_x, chord_y)
    if chord <= _EPSILON:
        return None

    bulge = float(bulge)
    direction = (chord_x / chord, chord_y / chord)
    if not math.isfinite(bulge) or abs(bulge) <= 1e-12:
        return PathElement(
            kind=PathElementKind.LINE,
            start=start,
            end=end,
            length_raw=chord,
            start_tangent=direction,
            end_tangent=direction,
        )

    sweep_rad = 4.0 * math.atan(bulge)
    sin_half = math.sin(abs(sweep_rad) / 2.0)
    if abs(sin_half) <= _EPSILON:
        return None
    radius = chord / (2.0 * sin_half)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    signed_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    left_normal = (-direction[1], direction[0])
    center = (
        midpoint[0] + left_normal[0] * signed_offset,
        midpoint[1] + left_normal[1] * signed_offset,
    )
    orientation = 1.0 if sweep_rad > 0.0 else -1.0
    start_radius = _unit((start[0] - center[0], start[1] - center[1]))
    end_radius = _unit((end[0] - center[0], end[1] - center[1]))
    if start_radius is None or end_radius is None:
        return None
    start_tangent = (
        -orientation * start_radius[1],
        orientation * start_radius[0],
    )
    end_tangent = (
        -orientation * end_radius[1],
        orientation * end_radius[0],
    )
    return PathElement(
        kind=PathElementKind.ARC,
        start=start,
        end=end,
        length_raw=abs(radius * sweep_rad),
        bulge=bulge,
        center=center,
        radius_raw=radius,
        sweep_deg=math.degrees(sweep_rad),
        start_tangent=start_tangent,
        end_tangent=end_tangent,
    )


def classify_path_representation(path: PolylinePath) -> RepresentationAssessment:
    """Conservatively distinguish a single path from a drawn stroke envelope.

    An open LWPOLYLINE with bulges is a curved centreline, not an outline.  A
    closed path is intrinsically ambiguous: only a very narrow, area-bearing
    envelope is labelled as a stroked outline.  Normal closed stirrups are kept
    as ambiguous centreline-capable paths instead of being rejected.
    """

    if not path.elements:
        return RepresentationAssessment(
            GeometryRepresentation.UNKNOWN,
            0.0,
            ("EMPTY_PATH",),
        )
    if not path.closed:
        if path.curve_count:
            return RepresentationAssessment(
                GeometryRepresentation.CURVED_CENTERLINE,
                0.99,
                ("OPEN_SINGLE_PATH", "DXF_BULGE_ARCS"),
            )
        return RepresentationAssessment(
            GeometryRepresentation.CENTERLINE,
            0.99,
            ("OPEN_SINGLE_PATH",),
        )

    outline_ratio = _closed_envelope_width_ratio(path.points)
    if outline_ratio is not None and outline_ratio <= 0.055:
        return RepresentationAssessment(
            GeometryRepresentation.STROKED_OUTLINE,
            0.86,
            ("NARROW_CLOSED_ENVELOPE", f"WIDTH_RATIO={outline_ratio:.5f}"),
        )
    return RepresentationAssessment(
        GeometryRepresentation.AMBIGUOUS_CLOSED_PATH,
        0.60,
        (
            "CLOSED_PATH_CAN_BE_CENTERLINE_OR_OUTLINE",
            *(
                (f"WIDTH_RATIO={outline_ratio:.5f}",)
                if outline_ratio is not None
                else ()
            ),
        ),
    )


def semantic_path_from_polyline(
    path: PolylinePath,
    *,
    tangent_tolerance_deg: float = 2.0,
    preferred_bend_deg: float | None = None,
    bend_tolerance_deg: float = 8.0,
) -> SemanticPath:
    """Collapse proven tangent bend arcs while preserving all other curves."""

    assessment = classify_path_representation(path)
    if assessment.representation is GeometryRepresentation.STROKED_OUTLINE:
        return SemanticPath(
            points=path.points,
            representation=assessment.representation,
            source_curve_count=path.curve_count,
            collapsed_arc_count=0,
            unresolved_arc_count=path.curve_count,
            semantic_length_raw=_polyline_length(path.points),
            exact_curve_length_raw=path.exact_length_raw,
            arc_radii_raw=path.arc_radii_raw,
            safe_for_linear_matching=False,
            hooked_closure=False,
            hooked_closure_gap_raw=None,
            notes=assessment.reasons
            + ("OUTLINE_REQUIRES_EXPLICIT_CENTERLINE_EXTRACTION",),
        )

    connectors = _tangent_arc_connectors(
        path,
        tangent_tolerance_deg=tangent_tolerance_deg,
        preferred_bend_deg=preferred_bend_deg,
        bend_tolerance_deg=bend_tolerance_deg,
    )
    normalized = _points_with_collapsed_connectors(path, connectors)
    collapsed_count = len(connectors)
    unresolved_count = max(0, path.curve_count - collapsed_count)
    safe = unresolved_count == 0
    if not normalized:
        normalized = path.points
        safe = False

    if path.closed and normalized and not _points_close(normalized[0], normalized[-1]):
        normalized = normalized + (normalized[0],)
    representation = assessment.representation
    if path.curve_count and representation is GeometryRepresentation.AMBIGUOUS_CLOSED_PATH:
        # The source remains auditable as closed/ambiguous, but a path whose
        # every arc is a proven fillet has valid centreline bend semantics.
        representation = GeometryRepresentation.CURVED_CENTERLINE

    notes = list(assessment.reasons)
    if collapsed_count:
        notes.append(f"TANGENT_BEND_ARCS_NORMALIZED={collapsed_count}")
    if unresolved_count:
        notes.append(f"UNRESOLVED_ARCS={unresolved_count}")
    hooked_closure_gap = (
        _hooked_closure_gap(normalized)
        if safe and not path.closed
        else None
    )
    hooked_closure = hooked_closure_gap is not None
    if hooked_closure:
        notes.append(f"HOOKED_CLOSURE_GAP={hooked_closure_gap:.5f}")
    return SemanticPath(
        points=normalized,
        representation=representation,
        source_curve_count=path.curve_count,
        collapsed_arc_count=collapsed_count,
        unresolved_arc_count=unresolved_count,
        semantic_length_raw=_polyline_length(normalized),
        exact_curve_length_raw=path.exact_length_raw,
        arc_radii_raw=path.arc_radii_raw,
        safe_for_linear_matching=safe,
        hooked_closure=hooked_closure,
        hooked_closure_gap_raw=hooked_closure_gap,
        notes=tuple(notes),
    )


def recover_borderline_f006_semantic_path(
    path: PolylinePath,
    *,
    tangent_tolerance_deg: float = 2.0,
    bend_tolerance_deg: float = 8.0,
) -> BorderlineF006SemanticRecovery | None:
    """Recover only the measured H-3 rounded-stirrup boundary signature.

    The accepted source must be one open, alternating 6-LINE/5-ARC path with
    the locked F006 bend sequence (135/90/90/90/135), one uniform bend radius,
    and exactly one terminal-hook arc which misses the generic 3% offset gate
    but stays within the measured 3.25% boundary. All remaining four arcs must
    already pass the unchanged generic normalizer.

    This function proves only a matcher-safe semantic path. It does not name a
    MASTER form and it does not prove production dimensions; both decisions
    remain mandatory at the batch layer.
    """

    if path.closed or len(path.elements) != 11:
        return None
    expected_kinds = tuple(
        PathElementKind.LINE if index % 2 == 0 else PathElementKind.ARC
        for index in range(11)
    )
    if tuple(element.kind for element in path.elements) != expected_kinds:
        return None

    semantic = semantic_path_from_polyline(
        path,
        tangent_tolerance_deg=tangent_tolerance_deg,
        preferred_bend_deg=None,
        bend_tolerance_deg=bend_tolerance_deg,
    )
    if (
        semantic.source_curve_count != 5
        or semantic.collapsed_arc_count != 4
        or semantic.unresolved_arc_count != 1
        or semantic.safe_for_linear_matching
    ):
        return None

    arc_indices = tuple(range(1, 10, 2))
    arcs = tuple(path.elements[index] for index in arc_indices)
    radii = tuple(
        float(arc.radius_raw)
        for arc in arcs
        if arc.radius_raw is not None and arc.radius_raw > _EPSILON
    )
    sweeps = tuple(
        float(arc.sweep_deg)
        for arc in arcs
        if arc.sweep_deg is not None and math.isfinite(float(arc.sweep_deg))
    )
    if len(radii) != 5 or len(sweeps) != 5:
        return None
    mean_radius = sum(radii) / len(radii)
    radius_spread = (max(radii) - min(radii)) / mean_radius
    if radius_spread > 0.01:
        return None
    if len({1 if sweep > 0.0 else -1 for sweep in sweeps}) != 1:
        return None
    expected_sweeps = (135.0, 90.0, 90.0, 90.0, 135.0)
    if any(
        abs(abs(observed) - expected) > bend_tolerance_deg
        for observed, expected in zip(sweeps, expected_sweeps)
    ):
        return None

    connectors = _tangent_arc_connectors(
        path,
        tangent_tolerance_deg=tangent_tolerance_deg,
        preferred_bend_deg=None,
        bend_tolerance_deg=bend_tolerance_deg,
    )
    missing = tuple(index for index in arc_indices if index not in connectors)
    if len(connectors) != 4 or len(missing) != 1:
        return None
    missing_index = missing[0]
    if missing_index not in {arc_indices[0], arc_indices[-1]}:
        return None

    borderline = _borderline_tangent_arc_connector(
        path,
        missing_index,
        tangent_tolerance_deg=tangent_tolerance_deg,
    )
    if borderline is None:
        return None
    corner, residual = borderline
    # Ordinary <=3% cases belong to the generic path above. This branch is
    # only the measured sliver which caused the four H-3 false negatives.
    if residual <= 0.03 or residual > 0.0325:
        return None

    recovered_connectors = dict(connectors)
    recovered_connectors[missing_index] = corner
    points = _points_with_collapsed_connectors(path, recovered_connectors)
    hooked_gap = _hooked_closure_gap(points)
    if hooked_gap is None or len(points) != 7:
        return None
    return BorderlineF006SemanticRecovery(
        points=points,
        missing_arc_index=missing_index,
        offset_residual_ratio=residual,
        radius_relative_spread=radius_spread,
        hooked_closure_gap_raw=hooked_gap,
    )


def path_spatial_segments(
    path: PolylinePath,
    *,
    max_arc_step_deg: float = 10.0,
) -> tuple[Segment2D, ...]:
    """Tessellate curves only for proximity/spatial indexing."""

    rows: list[Segment2D] = []
    for element in path.elements:
        samples = _element_sample_points(element, max_arc_step_deg=max_arc_step_deg)
        rows.extend(
            (left[0], left[1], right[0], right[1])
            for left, right in pairwise(samples)
            if not _points_close(left, right)
        )
    return tuple(rows)


def _tangent_arc_connectors(
    path: PolylinePath,
    *,
    tangent_tolerance_deg: float,
    preferred_bend_deg: float | None,
    bend_tolerance_deg: float,
) -> dict[int, Point2D]:
    elements = path.elements
    connectors: dict[int, Point2D] = {}
    for index, arc in enumerate(elements):
        if not arc.is_curve:
            continue
        if not path.closed and index in {0, len(elements) - 1}:
            continue
        previous = elements[(index - 1) % len(elements)]
        following = elements[(index + 1) % len(elements)]
        if previous.kind is not PathElementKind.LINE or following.kind is not PathElementKind.LINE:
            continue
        if (
            arc.start_tangent is None
            or arc.end_tangent is None
            or arc.sweep_deg is None
            or arc.radius_raw is None
        ):
            continue
        if not 5.0 <= abs(arc.sweep_deg) <= 175.0:
            continue
        if (
            preferred_bend_deg is not None
            and abs(abs(arc.sweep_deg) - abs(preferred_bend_deg))
            > bend_tolerance_deg
        ):
            continue
        previous_direction = _unit(
            (previous.end[0] - previous.start[0], previous.end[1] - previous.start[1])
        )
        following_direction = _unit(
            (following.end[0] - following.start[0], following.end[1] - following.start[1])
        )
        if previous_direction is None or following_direction is None:
            continue
        if (
            _direction_error_deg(previous_direction, arc.start_tangent)
            > tangent_tolerance_deg
            or _direction_error_deg(following_direction, arc.end_tangent)
            > tangent_tolerance_deg
        ):
            continue
        corner = _line_intersection(
            previous.start,
            previous.end,
            following.start,
            following.end,
        )
        if corner is None:
            continue
        before = _dot(
            previous_direction,
            (corner[0] - arc.start[0], corner[1] - arc.start[1]),
        )
        after = _dot(
            following_direction,
            (corner[0] - arc.end[0], corner[1] - arc.end[1]),
        )
        if before <= _EPSILON or after >= -_EPSILON:
            continue
        expected_offset = arc.radius_raw * math.tan(
            math.radians(abs(arc.sweep_deg)) / 2.0
        )
        offset_tolerance = max(1e-5, expected_offset * 0.03)
        if (
            abs(before - expected_offset) > offset_tolerance
            or abs(abs(after) - expected_offset) > offset_tolerance
        ):
            continue
        connectors[index] = corner
    return connectors


def _borderline_tangent_arc_connector(
    path: PolylinePath,
    index: int,
    *,
    tangent_tolerance_deg: float,
) -> tuple[Point2D, float] | None:
    """Return one otherwise-valid connector and its maximum offset residual.

    Unlike the generic normalizer, this helper does not own an acceptance
    threshold. Its sole caller applies the narrow ``(3%, 3.25%]`` H-3 window
    only after the complete F006 topology/radius proof has passed.
    """

    elements = path.elements
    if not 0 < index < len(elements) - 1:
        return None
    arc = elements[index]
    previous = elements[index - 1]
    following = elements[index + 1]
    if (
        arc.kind is not PathElementKind.ARC
        or previous.kind is not PathElementKind.LINE
        or following.kind is not PathElementKind.LINE
        or arc.start_tangent is None
        or arc.end_tangent is None
        or arc.sweep_deg is None
        or arc.radius_raw is None
        or not 5.0 <= abs(arc.sweep_deg) <= 175.0
    ):
        return None
    previous_direction = _unit(
        (previous.end[0] - previous.start[0], previous.end[1] - previous.start[1])
    )
    following_direction = _unit(
        (following.end[0] - following.start[0], following.end[1] - following.start[1])
    )
    if previous_direction is None or following_direction is None:
        return None
    if (
        _direction_error_deg(previous_direction, arc.start_tangent)
        > tangent_tolerance_deg
        or _direction_error_deg(following_direction, arc.end_tangent)
        > tangent_tolerance_deg
    ):
        return None
    corner = _line_intersection(
        previous.start,
        previous.end,
        following.start,
        following.end,
    )
    if corner is None:
        return None
    before = _dot(
        previous_direction,
        (corner[0] - arc.start[0], corner[1] - arc.start[1]),
    )
    after = _dot(
        following_direction,
        (corner[0] - arc.end[0], corner[1] - arc.end[1]),
    )
    if before <= _EPSILON or after >= -_EPSILON:
        return None
    expected_offset = arc.radius_raw * math.tan(
        math.radians(abs(arc.sweep_deg)) / 2.0
    )
    if expected_offset <= _EPSILON:
        return None
    residual = max(
        abs(before - expected_offset),
        abs(abs(after) - expected_offset),
    ) / expected_offset
    if not math.isfinite(residual):
        return None
    return corner, residual


def _points_with_collapsed_connectors(
    path: PolylinePath,
    connectors: Mapping[int, Point2D],
) -> tuple[Point2D, ...]:
    if not path.elements:
        return ()
    if not path.closed:
        output: list[Point2D] = [path.elements[0].start]
        skip_junction_after_arc: set[int] = set()
        for junction in range(1, len(path.elements)):
            previous_index = junction - 1
            current_index = junction
            if current_index in connectors:
                _append_unique(output, connectors[current_index])
                skip_junction_after_arc.add(current_index + 1)
            elif junction in skip_junction_after_arc:
                continue
            else:
                _append_unique(output, path.elements[previous_index].end)
        _append_unique(output, path.elements[-1].end)
        return tuple(output)

    output = []
    count = len(path.elements)
    skipped: set[int] = set()
    for junction in range(count):
        previous_index = (junction - 1) % count
        if junction in connectors:
            _append_unique(output, connectors[junction])
            skipped.add((junction + 1) % count)
        elif junction in skipped:
            continue
        else:
            _append_unique(output, path.elements[previous_index].end)
    if output:
        _append_unique(output, output[0])
    return tuple(output)


def _element_sample_points(
    element: PathElement,
    *,
    max_arc_step_deg: float,
) -> tuple[Point2D, ...]:
    if (
        element.kind is PathElementKind.LINE
        or element.center is None
        or element.radius_raw is None
        or element.sweep_deg is None
    ):
        return element.start, element.end
    sweep = math.radians(element.sweep_deg)
    start_angle = math.atan2(
        element.start[1] - element.center[1],
        element.start[0] - element.center[0],
    )
    steps = max(1, math.ceil(abs(element.sweep_deg) / max(float(max_arc_step_deg), 1.0)))
    points = [
        (
            element.center[0]
            + element.radius_raw * math.cos(start_angle + sweep * index / steps),
            element.center[1]
            + element.radius_raw * math.sin(start_angle + sweep * index / steps),
        )
        for index in range(steps + 1)
    ]
    points[0] = element.start
    points[-1] = element.end
    return tuple(points)


def _closed_envelope_width_ratio(points: Sequence[Point2D]) -> float | None:
    if len(points) < 4:
        return None
    work = list(points)
    if _points_close(work[0], work[-1]):
        work.pop()
    if len(work) < 3:
        return None
    area = abs(
        sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(work, work[1:] + work[:1])
        )
    ) / 2.0
    perimeter = sum(
        math.dist(left, right)
        for left, right in zip(work, work[1:] + work[:1])
    )
    min_x = min(point[0] for point in work)
    max_x = max(point[0] for point in work)
    min_y = min(point[1] for point in work)
    max_y = max(point[1] for point in work)
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    if area <= _EPSILON or perimeter <= _EPSILON or diagonal <= _EPSILON:
        return None
    # For a long thin closed stroke envelope, 2A/P approaches its width.
    equivalent_width = 2.0 * area / perimeter
    return equivalent_width / diagonal


def _hooked_closure_gap(points: Sequence[Point2D]) -> float | None:
    """Detect an open, nearly closed loop with two antiparallel terminal hooks.

    This is topology evidence only.  It allows comparison with a closed MASTER
    shape but must remain review-only because the endpoints are intentionally
    not physically joined.
    """

    if len(points) < 6 or _points_close(points[0], points[-1]):
        return None
    lengths = [
        math.dist(left, right)
        for left, right in pairwise(points)
    ]
    if len(lengths) < 5 or any(length <= _EPSILON for length in lengths):
        return None
    total = sum(lengths)
    gap = math.dist(points[0], points[-1])
    if (
        gap > total * 0.08
        or gap > min(lengths[0], lengths[-1]) * 0.50
    ):
        return None
    first = _unit(
        (points[1][0] - points[0][0], points[1][1] - points[0][1])
    )
    last = _unit(
        (
            points[-1][0] - points[-2][0],
            points[-1][1] - points[-2][1],
        )
    )
    if first is None or last is None:
        return None
    antiparallel_error = abs(180.0 - _direction_error_deg(first, last))
    if antiparallel_error > 12.0:
        return None
    # A hooked loop has a consistent turn sense through its interior bends.
    turns = [
        _signed_turn(points[index - 1], points[index], points[index + 1])
        for index in range(1, len(points) - 1)
    ]
    significant = [turn for turn in turns if abs(turn) >= 5.0]
    if len(significant) < 3:
        return None
    signs = {1 if turn > 0.0 else -1 for turn in significant}
    if len(signs) != 1:
        return None
    return gap


def _line_intersection(
    left_start: Point2D,
    left_end: Point2D,
    right_start: Point2D,
    right_end: Point2D,
) -> Point2D | None:
    left = (left_end[0] - left_start[0], left_end[1] - left_start[1])
    right = (right_end[0] - right_start[0], right_end[1] - right_start[1])
    denominator = left[0] * right[1] - left[1] * right[0]
    if abs(denominator) <= _EPSILON:
        return None
    delta = (right_start[0] - left_start[0], right_start[1] - left_start[1])
    factor = (delta[0] * right[1] - delta[1] * right[0]) / denominator
    return (
        left_start[0] + factor * left[0],
        left_start[1] + factor * left[1],
    )


def _direction_error_deg(left: Point2D, right: Point2D) -> float:
    dot = max(-1.0, min(1.0, _dot(left, right)))
    return math.degrees(math.acos(dot))


def _signed_turn(left: Point2D, middle: Point2D, right: Point2D) -> float:
    first = (
        middle[0] - left[0],
        middle[1] - left[1],
    )
    second = (
        right[0] - middle[0],
        right[1] - middle[1],
    )
    cross = first[0] * second[1] - first[1] * second[0]
    dot = _dot(first, second)
    return math.degrees(math.atan2(cross, dot))


def _polyline_length(points: Sequence[Point2D]) -> float:
    return sum(math.dist(left, right) for left, right in pairwise(points))


def _append_unique(points: list[Point2D], point: Point2D) -> None:
    if not points or not _points_close(points[-1], point):
        points.append(point)


def _unit(vector: Point2D) -> Point2D | None:
    length = math.hypot(vector[0], vector[1])
    if length <= _EPSILON:
        return None
    return vector[0] / length, vector[1] / length


def _dot(left: Point2D, right: Point2D) -> float:
    return left[0] * right[0] + left[1] * right[1]


def _point(value: Any) -> Point2D | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(item) for item in point) else None


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _points_close(
    left: Point2D,
    right: Point2D,
    tolerance: float = 1e-8,
) -> bool:
    return math.dist(left, right) <= tolerance
