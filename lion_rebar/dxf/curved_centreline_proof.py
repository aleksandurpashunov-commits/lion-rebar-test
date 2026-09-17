"""Производствените размери на F003/F006 от една точна извита ос.

Доказателството живееше в ``batch_geometry_report._single_open_lwpolyline_topology``
заедно с проверките на реда (рамка, конфликти, един кандидат). Изнесено е
тук на 2026-09-17, за да го ползва и правилото за стремената в рамката
(``stirrup_group_resolver``) без кръгов импорт: там няма ред, а копия на
стремето в рамката на елемента.

Портата (``export/auto_gate._single_open_lwpolyline_topology_is_owned``)
остава отделна и доказва същото наново от записаното доказателство.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from lion_rebar.dxf.geometry_core_v2 import (
    GeometryRepresentation,
    PathElementKind,
    polyline_path_from_geometry,
    semantic_path_from_polyline,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.vertical_shape_resolver import snap
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    parameters_from_segments,
)


def curved_centreline_production_proof(
    entity: DxfEntityRecord | None,
    shape_code: str,
    declared_length_cm: float,
) -> dict[str, Any] | None:
    """Derive F003/F006 production sizes from one exact curved centreline.

    This is deliberately narrower than generic curve normalization.  The
    proof requires one open LWPOLYLINE, a fixed alternating LINE/ARC
    topology, tangent joins, one bend radius, the shape-specific symmetries
    and a MASTER total that closes against the declared production length.
    """

    shape_code = str(shape_code or "").strip().upper()
    if shape_code not in {"F003", "F006"}:
        return None
    if entity is None or entity.entity_type.upper() != "LWPOLYLINE":
        return None
    path = polyline_path_from_geometry(entity.geometry or {})
    if path is None or path.closed:
        return None
    semantic = semantic_path_from_polyline(path)
    if (
        semantic.representation is not GeometryRepresentation.CURVED_CENTERLINE
        or not semantic.safe_for_linear_matching
        or semantic.unresolved_arc_count != 0
    ):
        return None

    expected_kinds = (
        ("LINE", "ARC", "LINE", "ARC", "LINE")
        if shape_code == "F003"
        else (
            "LINE", "ARC", "LINE", "ARC", "LINE", "ARC",
            "LINE", "ARC", "LINE", "ARC", "LINE",
        )
    )
    kinds = tuple(element.kind.value for element in path.elements)
    if kinds != expected_kinds:
        return None
    lines = tuple(
        float(element.length_raw)
        for element in path.elements
        if element.kind is PathElementKind.LINE
    )
    arcs = tuple(
        element for element in path.elements if element.kind is PathElementKind.ARC
    )
    expected_arc_count = 2 if shape_code == "F003" else 5
    if len(arcs) != expected_arc_count or semantic.collapsed_arc_count != len(arcs):
        return None
    radii = tuple(float(element.radius_raw or 0.0) for element in arcs)
    sweeps = tuple(float(element.sweep_deg or 0.0) for element in arcs)
    if (
        any(not math.isfinite(value) or value <= 0.0 for value in (*lines, *radii))
        or any(not math.isfinite(value) or abs(value) <= 1e-9 for value in sweeps)
        or len({1 if value > 0.0 else -1 for value in sweeps}) != 1
    ):
        return None
    radius = sum(radii) / len(radii)
    radius_tolerance = max(0.05, radius * 0.02)
    if any(abs(value - radius) > radius_tolerance for value in radii):
        return None

    tangent_deviations: list[float] = []
    for index in range(1, len(path.elements), 2):
        previous = path.elements[index - 1]
        arc = path.elements[index]
        following = path.elements[index + 1]
        if (
            previous.end_tangent is None
            or arc.start_tangent is None
            or arc.end_tangent is None
            or following.start_tangent is None
        ):
            return None
        tangent_deviations.extend(
            (
                _vector_angle_deg(previous.end_tangent, arc.start_tangent),
                _vector_angle_deg(arc.end_tangent, following.start_tangent),
            )
        )
    if any(value > 2.0 for value in tangent_deviations):
        return None

    if shape_code == "F003":
        if (
            len(lines) != 3
            or any(abs(abs(value) - 90.0) > 0.1 for value in sweeps)
        ):
            return None
        raw_segments = (lines[0] + radius, lines[1] + 2.0 * radius, lines[2] + radius)
        canonical = tuple(snap(value) for value in raw_segments)
        if abs(canonical[0] - canonical[2]) > 0.11:
            return None
    else:
        if (
            len(lines) != 6
            or any(abs(abs(value) - 90.0) > 0.1 for value in sweeps[1:4])
            or not all(90.0 < abs(value) < 180.0 for value in (sweeps[0], sweeps[-1]))
            or abs(abs(sweeps[0]) + abs(sweeps[-1]) - 270.0) > 0.2
            or not semantic.hooked_closure
            or semantic.hooked_closure_gap_raw is None
            or abs(float(semantic.hooked_closure_gap_raw) - 2.0 * radius) > 0.11
        ):
            return None
        raw_segments = (
            lines[0],
            lines[1] + 2.0 * radius,
            lines[2] + 2.0 * radius,
            lines[3] + 2.0 * radius,
            lines[4] + 2.0 * radius,
            lines[5],
        )
        canonical = tuple(snap(value) for value in raw_segments)
        if (
            abs(canonical[0] - canonical[5]) > 0.11
            or abs(canonical[1] - canonical[3]) > 0.11
            or abs(canonical[2] - canonical[4]) > 0.11
        ):
            return None

    try:
        declared = float(declared_length_cm)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(declared) or declared <= 0.0:
        return None
    try:
        parameters_from_segments(
            shape_code,
            canonical,
            production_length_cm=declared,
            turn_angles_deg=(90.0,) * (len(canonical) - 1),
        )
    except (MasterDimensionError, TypeError, ValueError):
        return None
    return {
        "segments": canonical,
        "evidence": {
            "geometry_handle": str(entity.handle),
            "geometry_entity_type": "LWPOLYLINE",
            "path_closed": False,
            "element_kinds": list(kinds),
            "line_lengths_raw": list(lines),
            "arc_radii_raw": list(radii),
            "arc_sweeps_deg": list(sweeps),
            "tangent_deviations_deg": tangent_deviations,
            "hooked_closure_gap_raw": (
                float(semantic.hooked_closure_gap_raw)
                if semantic.hooked_closure_gap_raw is not None
                else None
            ),
        },
    }


def _vector_angle_deg(left: Sequence[float], right: Sequence[float]) -> float:
    left_length = math.hypot(float(left[0]), float(left[1]))
    right_length = math.hypot(float(right[0]), float(right[1]))
    if left_length <= 1e-9 or right_length <= 1e-9:
        return math.inf
    cosine = (
        float(left[0]) * float(right[0])
        + float(left[1]) * float(right[1])
    ) / (left_length * right_length)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
