from __future__ import annotations

import math
import re
from typing import Any, Mapping

from lion_rebar.dxf.local_detail_resolver import local_detail_shape_code
from lion_rebar.dxf.proxy_adapter import three_leg_shape_from_directions
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    parameters_from_segments,
)
from lion_rebar.review_center_model import (
    F006_STRONG_STATUS,
    GEOMETRY_STRONG_STATUS,
    STRONG_STATUS,
)


AUTO_EXPORT_BATCH_STATUSES = {
    STRONG_STATUS,
    GEOMETRY_STRONG_STATUS,
    F006_STRONG_STATUS,
    # Compatibility with pre-v3 reports which persisted the confidence label
    # rather than the canonical batch status.
    "СИЛЕН КАНДИДАТ",
}
AUTO_EXPORT_MATCHER_DISPOSITIONS = {"AUTO", "CONTROL"}

#: Owner-requested 2026-09-12 ("целим програмата ни да е професионална с
#: минимално инженерно вмешателство"): the arm_koloni/arm_shaibi proxy
#: dialect proves production segments WITHOUT a geometric candidate --
#: the designer's own declared breakdown, with the shape FAMILY proven
#: independently by real bend-arc geometry (see proxy_adapter.py's
#: PROXY_DECLARED_SHAPE_CODE; 165/165 arc-count correlation on real data,
#: zero exceptions). Admitted here so such rows can clear AUTO without a
#: geometric match, exactly like the ``::BJ-FI-L`` block-derived dialect
#: already does -- and re-validated below, independently of whoever wrote
#: the evidence, by ``_proxy_arc_verified_evidence_is_owned``.
_PROXY_ARC_VERIFIED_SOURCE = "PROXY_ARC_VERIFIED_DECLARED_SEGMENTS"

#: „Форма от котите до надписа" (собственик, 19.09.2026). Позиция без
#: геометричен кандидат, чийто локален детайл е обявен за стреме, получава
#: участъците си от НАДПИСАНИТЕ коти на този детайл — или от спецификация
#: ``стр.: N8x166`` със същия Ø и същата дължина. Писателят е
#: ``batch_geometry_report._apply_printed_detail_dimension_segments``;
#: проверителят по-долу преизвежда прочита от записания ред, включително
#: изчисления размер, и не вярва на кода на източника.
_PRINTED_DETAIL_DIMENSION_SOURCE = "LOCAL_DETAIL_PRINTED_DIMENSIONS"
_PRINTED_SPECIFICATION_DIMENSION_SOURCE = "STIRRUP_SPECIFICATION_PRINTED_DIMENSIONS"
_PRINTED_REPEATED_LABEL_DIMENSION_SOURCE = "REPEATED_LABEL_PRINTED_DIMENSIONS"
#: Пренесеният прочит иска СЪЩИЯ надпис, затова тук се сверяват и двата
#: текста; ``STIRRUP_SPECIFICATION`` иска само Ø и дължина, защото
#: спецификацията няма брой и надписът ѝ по определение е различен.
_PRINTED_DECLARATION_SOURCES = frozenset(
    {
        _PRINTED_SPECIFICATION_DIMENSION_SOURCE,
        _PRINTED_REPEATED_LABEL_DIMENSION_SOURCE,
    }
)

_PRODUCTION_SEGMENT_EVIDENCE_SOURCES = frozenset(
    {
        "F007_CONTEXTUAL_MASTER_CANONICAL",
        "STRICT_LOCAL_DIMENSIONS",
        "MATCHER_LOCAL_A_E_CANONICAL",
        "F006_ROUNDED_LOCAL_PRINTED",
        "F006_PRINTED_MASTER_EXACT",
        "F006_OWNED_CONTOUR_DIMENSIONS",
        "SINGLE_OPEN_LWPOLYLINE_TOPOLOGY",
        _PRINTED_DETAIL_DIMENSION_SOURCE,
        _PRINTED_SPECIFICATION_DIMENSION_SOURCE,
        _PRINTED_REPEATED_LABEL_DIMENSION_SOURCE,
        _PROXY_ARC_VERIFIED_SOURCE,
    }
)

#: Куката/крачето на стреме е най-късият му размер. Диапазонът е този на
#: ``vertical_shape_resolver.infer_preferred_hook`` и се проверява тук
#: наново, за да не може промяна в политиката да разшири АВТОМАТИКАТА.
_PRINTED_DETAIL_HOOK_MIN_CM = 4.0
_PRINTED_DETAIL_HOOK_MAX_CM = 20.0
#: Измерено: верните локални надписи стоят на 0-28 чертожни единици от своя
#: надпис, чуждите — на стотици (``local_detail_resolver``).
_PRINTED_DETAIL_LABEL_MAX_DISTANCE_RAW = 95.0
#: Надписаните размери са кръгли производствени числа: уравнението затваря
#: точно или позицията остава при оператора.
_PRINTED_DETAIL_CLOSURE_TOLERANCE_CM = 0.05

#: Bent families the bend-arc signal can prove. A positive structural arc
#: count proves the bar IS bent; it cannot, on its own, separate a bent
#: family from a straight one, so the two directions are gated separately
#: below.
_PROXY_AUTO_BENT_SHAPES = frozenset({"F003", "F004", "F006", "F007"})
#: The declared breakdown must close the declared total. Real data closes
#: exactly; this allows only ordinary rounding in the designer's own text.
_PROXY_AUTO_TOTAL_TOLERANCE_CM = 0.51
_F007_CONTEXTUAL_RULE = "F007_ESOVE_CURVED_CENTERLINE"
_MATCHER_DIMENSION_STRATEGIES = frozenset(
    {
        "A_E_DIRECT_SEGMENTS",
        "A_E_MASTER_PARAMETER_FORMULA",
        "A_E_TOTAL_MINUS_PRINTED",
        "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS",
    }
)


def validate_production_segment_evidence(row: Mapping[str, Any]) -> bool:
    """Validate the complete, persisted proof for production segments.

    The proof is deliberately self-contained because this predicate is used
    both by the AUTO classification gate and by the batch-export loader.  A
    truthy mapping is never evidence on its own: its shape, segment vector,
    MASTER total and source-specific ownership chain must all agree with the
    effective report row.
    """

    evidence = row.get("production_segment_evidence")
    if not isinstance(evidence, dict):
        return False
    source = str(evidence.get("source") or "").strip().upper()
    if source not in _PRODUCTION_SEGMENT_EVIDENCE_SOURCES:
        return False

    effective_shape = str(row.get("master_shape_code") or "").strip().upper()
    evidence_shape = str(evidence.get("shape_code") or "").strip().upper()
    if not effective_shape or evidence_shape != effective_shape:
        return False
    production_raw = row.get("production_segments_cm")
    evidence_raw = evidence.get("segments")
    if not isinstance(production_raw, (list, tuple)) or not isinstance(
        evidence_raw, (list, tuple)
    ):
        return False
    if list(evidence_raw) != list(production_raw):
        return False
    segments = _finite_positive_vector(production_raw)
    if segments is None:
        return False

    total_raw = (
        row.get("production_length_cm")
        if row.get("production_length_cm") is not None
        else row.get("declared_length_cm")
    )
    try:
        total = float(total_raw)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(total) or total <= 0:
        return False
    turns = _finite_vector(
        evidence.get("turn_angles_deg")
        or row.get("canonical_turn_angles_deg")
        or (90.0, 90.0)
    )
    if turns is None or (effective_shape == "F007" and len(turns) != 2):
        return False
    try:
        parameters_from_segments(
            effective_shape,
            segments,
            production_length_cm=total,
            turn_angles_deg=turns,
        )
    except (MasterDimensionError, TypeError, ValueError):
        return False

    if source == "F007_CONTEXTUAL_MASTER_CANONICAL":
        return _contextual_f007_evidence_is_owned(row, evidence, segments)
    if source == "STRICT_LOCAL_DIMENSIONS":
        return (
            effective_shape == "F007"
            and _strict_dimension_evidence_is_owned(row, evidence, segments)
        )
    if source == "MATCHER_LOCAL_A_E_CANONICAL":
        return _matcher_local_a_e_evidence_is_owned(row, evidence, segments)
    if source in {"F006_ROUNDED_LOCAL_PRINTED", "F006_PRINTED_MASTER_EXACT"}:
        return _f006_exact_evidence_is_owned(row, evidence, segments, source)
    if source == "F006_OWNED_CONTOUR_DIMENSIONS":
        return _f006_owned_contour_evidence_is_owned(row, evidence, segments)
    if source == "SINGLE_OPEN_LWPOLYLINE_TOPOLOGY":
        return _single_open_lwpolyline_topology_is_owned(row, evidence, segments)
    if source in {
        _PRINTED_DETAIL_DIMENSION_SOURCE,
        *_PRINTED_DECLARATION_SOURCES,
    }:
        return _printed_detail_dimension_evidence_is_owned(
            row, evidence, segments, total, source
        )
    if source == _PROXY_ARC_VERIFIED_SOURCE:
        return _proxy_arc_verified_evidence_is_owned(row, evidence, segments, total)
    return False


#: ``PXG_<parent>_<index>_0`` -- exploded proxy geometry, named after the
#: proxy that owns it. ``PXANN_<parent>`` is that same parent's annotation,
#: so a row's own geometry is identifiable from its handle alone.
_PROXY_ANNOTATION_HANDLE_PREFIX = "PXANN_"
_PROXY_GEOMETRY_OWNER_RE = re.compile(r"^PXG_([^_]+)_\d+_\d+$")


def _proxy_candidates_include_own_geometry(row: Mapping[str, Any]) -> bool:
    """Whether a competing geometric claim is about THIS annotation's own
    exploded proxy geometry, rather than some unrelated entity.

    A distant or contested match on foreign geometry says nothing about a
    declaration measured entirely on the annotation's own proxy -- see
    tests/test_proxy_foreign_candidate_does_not_veto.py. A claim on our OWN
    geometry is a genuine contradiction and still blocks.

    Fail-closed: if the owner cannot be established, the claim is treated as
    if it were ours and the row stays manual.
    """

    handle = str(row.get("annotation_handle") or "")
    if not handle.startswith(_PROXY_ANNOTATION_HANDLE_PREFIX):
        return True
    parent = handle[len(_PROXY_ANNOTATION_HANDLE_PREFIX) :]
    if not parent:
        return True
    candidates = [
        str(value) for value in (row.get("candidate_handles") or []) if str(value)
    ]
    if not candidates:
        return False
    for candidate in candidates:
        match = _PROXY_GEOMETRY_OWNER_RE.match(candidate)
        if match is not None and match.group(1) == parent:
            return True
    return False


def _proxy_arc_verified_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
    total: float,
) -> bool:
    """Re-prove an arm_koloni/arm_shaibi declared-shape row from scratch.

    Deliberately stricter than the suggestion path that produced it, and
    deliberately independent of it: everything checked here is re-read
    from the row and the persisted evidence, so a future change to the
    writer cannot widen what AUTO accepts.

    Four independent conditions, all fail-closed:

    * the row really is one of this dialect's own annotations;
    * the shape resolver was HIGH confidence -- MEDIUM stays manual;
    * no competing geometric claim (FAR_ASSOCIATION or an ownership
      conflict means the generic matcher disagrees, so a human decides);
    * the bend-arc count agrees with the shape family, AND the declared
      breakdown closes the declared total.

    F002 (straight bar with a diameter reduction) is deliberately NOT
    auto-approved: it shares "zero bend arcs" with F001 while carrying a
    three-part breakdown whose middle value is a reduction rather than a
    length, and that exact distinction has already produced one real
    misclassification (owner-caught, 2026-09-11).
    """

    if not str(row.get("annotation_handle") or "").startswith("PXANN_"):
        return False
    if str(evidence.get("confidence") or "").strip().upper() != "HIGH":
        return False

    reasons = {
        str(value).strip().upper() for value in (row.get("review_reason_codes") or [])
    }
    blockers = {
        str(value).strip().upper() for value in (row.get("matcher_blocker_codes") or [])
    }
    competing_claim = (
        "FAR_ASSOCIATION" in reasons
        or "FAR_ASSOCIATION" in blockers
        or "GEOMETRY_OWNERSHIP_CONFLICT" in blockers
        or bool(row.get("geometry_owner_conflict"))
        # Set together with geometry_owner_conflict when one DXF entity is
        # the first candidate of several annotations -- a candidate claim,
        # despite the name.
        or bool(row.get("global_conflict"))
    )
    if competing_claim and _proxy_candidates_include_own_geometry(row):
        return False
    # MASTER-shape ambiguity is genuine uncertainty about which shape applies,
    # not a claim about a candidate, so ownership cannot clear it.
    if row.get("master_conflict") or "AMBIGUOUS_MASTER" in reasons | blockers:
        return False

    shape = str(row.get("master_shape_code") or "").strip().upper()
    arc_count = evidence.get("bend_arc_count")
    if not isinstance(arc_count, int) or isinstance(arc_count, bool) or arc_count < 0:
        return False
    if arc_count > 0:
        if shape not in _PROXY_AUTO_BENT_SHAPES:
            return False
    elif shape == "F001":
        if len(segments) != 1:
            return False
    else:
        # Zero ARCs is not proof of a straight bar in this dialect -- the
        # bends are drawn as sharp polyline corners. A three-part shape is
        # admitted only if the SHAPE CODE can be re-derived here from the
        # recorded leg directions, so a wrong code on the row cannot pass.
        declared = _finite_positive_vector(evidence.get("declared_segments_cm"))
        directions = _finite_vector(evidence.get("leg_directions_deg"))
        if declared is None or directions is None:
            return False
        if three_leg_shape_from_directions(directions, declared) != shape:
            return False
        if tuple(segments) != declared:
            return False

    return abs(math.fsum(segments) - total) <= _PROXY_AUTO_TOTAL_TOLERANCE_CM


def _topology_snap(value: float) -> float:
    nearest = round(value)
    if abs(value - nearest) <= 0.4:
        return float(nearest)
    return round(value * 2.0) / 2.0


def _single_open_lwpolyline_topology_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
) -> bool:
    """Revalidate the self-contained exact curved-centreline proof."""

    shape = str(row.get("master_shape_code") or "").strip().upper()
    if (
        shape not in {"F003", "F006"}
        or str(row.get("frame_category_code") or "").strip().upper()
        != "VERTICALS"
        or bool(row.get("global_conflict"))
        or bool(row.get("geometry_owner_conflict"))
        or bool(row.get("ownership_conflict"))
        or bool(row.get("ownership_rejected"))
        or bool(row.get("near_gap_healed"))
        or bool(row.get("master_ambiguous_shape_codes"))
        or not _canonical_vector_matches(row, segments)
        or evidence.get("path_closed") is not False
        or str(evidence.get("geometry_entity_type") or "").upper()
        != "LWPOLYLINE"
    ):
        return False
    candidates = tuple(
        str(value) for value in (row.get("candidate_handles") or ()) if str(value)
    )
    evidence_candidates = tuple(
        str(value) for value in (evidence.get("candidate_handles") or ()) if str(value)
    )
    geometry_handle = str(evidence.get("geometry_handle") or "")
    if (
        len(candidates) != 1
        or evidence_candidates != candidates
        or geometry_handle != candidates[0]
    ):
        return False

    expected_kinds = (
        ("LINE", "ARC", "LINE", "ARC", "LINE")
        if shape == "F003"
        else (
            "LINE", "ARC", "LINE", "ARC", "LINE", "ARC",
            "LINE", "ARC", "LINE", "ARC", "LINE",
        )
    )
    kinds = tuple(str(value).upper() for value in (evidence.get("element_kinds") or ()))
    lines = _finite_positive_vector(evidence.get("line_lengths_raw"))
    radii = _finite_positive_vector(evidence.get("arc_radii_raw"))
    sweeps = _finite_vector(evidence.get("arc_sweeps_deg"))
    tangencies = _finite_vector(evidence.get("tangent_deviations_deg"))
    expected_arcs = 2 if shape == "F003" else 5
    if (
        kinds != expected_kinds
        or lines is None
        or radii is None
        or sweeps is None
        or tangencies is None
        or len(radii) != expected_arcs
        or len(sweeps) != expected_arcs
        or len(tangencies) != expected_arcs * 2
        or any(value < 0.0 or value > 2.0 for value in tangencies)
        or any(abs(value) <= 1e-9 for value in sweeps)
        or len({1 if value > 0.0 else -1 for value in sweeps}) != 1
    ):
        return False
    radius = sum(radii) / len(radii)
    radius_tolerance = max(0.05, radius * 0.02)
    if any(abs(value - radius) > radius_tolerance for value in radii):
        return False

    if shape == "F003":
        if (
            len(lines) != 3
            or len(segments) != 3
            or any(abs(abs(value) - 90.0) > 0.1 for value in sweeps)
        ):
            return False
        raw = (lines[0] + radius, lines[1] + 2.0 * radius, lines[2] + radius)
        derived = tuple(_topology_snap(value) for value in raw)
        return derived == segments and abs(segments[0] - segments[2]) <= 0.11

    gap = _finite_positive_scalar(evidence.get("hooked_closure_gap_raw"))
    if (
        len(lines) != 6
        or len(segments) != 6
        or any(abs(abs(value) - 90.0) > 0.1 for value in sweeps[1:4])
        or not all(90.0 < abs(value) < 180.0 for value in (sweeps[0], sweeps[-1]))
        or abs(abs(sweeps[0]) + abs(sweeps[-1]) - 270.0) > 0.2
        or gap is None
        or abs(gap - 2.0 * radius) > 0.11
        or str(row.get("f006_production_status") or "").strip().upper()
        != "F006_PRODUCTION_DERIVED"
        or str(row.get("f006_production_reason") or "").strip().upper()
        != "SINGLE_OPEN_LWPOLYLINE_TOPOLOGY"
    ):
        return False
    raw = (
        lines[0],
        lines[1] + 2.0 * radius,
        lines[2] + 2.0 * radius,
        lines[3] + 2.0 * radius,
        lines[4] + 2.0 * radius,
        lines[5],
    )
    derived = tuple(_topology_snap(value) for value in raw)
    return (
        derived == segments
        and abs(segments[0] - segments[5]) <= 0.11
        and abs(segments[1] - segments[3]) <= 0.11
        and abs(segments[2] - segments[4]) <= 0.11
    )


def _canonical_vector_matches(
    row: Mapping[str, Any],
    segments: tuple[float, ...],
) -> bool:
    return _finite_positive_vector(
        row.get("canonical_segment_lengths_cm")
    ) == segments


def _matcher_local_a_e_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
) -> bool:
    """Validate the matcher-owned A-E proof persisted by the batch report."""

    if row.get("dimension_evidence_applied") is not True:
        return False
    strategy = str(row.get("dimension_resolution_strategy") or "").strip().upper()
    if (
        strategy not in _MATCHER_DIMENSION_STRATEGIES
        or str(evidence.get("dimension_resolution_strategy") or "").strip().upper()
        != strategy
        or not _canonical_vector_matches(row, segments)
    ):
        return False

    row_dimensions = tuple(
        str(value)
        for value in (row.get("dimension_text_handles") or [])
        if str(value)
    )
    evidence_dimensions = tuple(
        str(value)
        for value in (evidence.get("dimension_handles") or [])
        if str(value)
    )
    if (
        not row_dimensions
        or len(set(row_dimensions)) != len(row_dimensions)
        or evidence_dimensions != row_dimensions
    ):
        return False

    row_candidates = tuple(
        str(value)
        for value in (row.get("candidate_handles") or [])
        if str(value)
    )
    evidence_candidates = tuple(
        str(value)
        for value in (evidence.get("candidate_handles") or [])
        if str(value)
    )
    if not row_candidates or evidence_candidates != row_candidates:
        return False

    measurements = _dimension_measurements(evidence, evidence_dimensions)
    if measurements is None:
        return False
    total_difference = _finite_nonnegative_scalar(
        evidence.get("dimension_total_difference_cm")
    )
    row_difference = _finite_nonnegative_scalar(
        row.get("dimension_total_difference_cm")
    )
    return (
        total_difference is not None
        and row_difference is not None
        and total_difference <= 0.51
        and abs(total_difference - row_difference) <= 1e-6
    )


def _f006_exact_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
    source: str,
) -> bool:
    if (
        len(segments) != 6
        or str(row.get("master_shape_code") or "").strip().upper() != "F006"
        or str(row.get("f006_production_status") or "").strip().upper()
        != "F006_PRODUCTION_EXACT"
        or row.get("f006_printed_dimensions_resolved") is not True
        or not _canonical_vector_matches(row, segments)
    ):
        return False

    row_candidates = tuple(
        str(value)
        for value in (row.get("candidate_handles") or [])
        if str(value)
    )
    evidence_candidates = tuple(
        str(value)
        for value in (evidence.get("candidate_handles") or [])
        if str(value)
    )
    if not row_candidates or evidence_candidates != row_candidates:
        return False

    if source == "F006_PRINTED_MASTER_EXACT":
        return (
            str(row.get("contextual_rule_code") or "").strip().upper()
            == "F006_PRINTED_MASTER_EQUATION"
        )

    if "ЗАКРЪГЛЕНО СТРЕМЕ" not in str(row.get("candidate_basis") or "").upper():
        return False
    row_dimensions = {
        str(value)
        for value in (row.get("local_f006_printed_dimension_handles") or [])
        if str(value)
    }
    evidence_dimensions = tuple(
        str(value)
        for value in (evidence.get("dimension_handles") or [])
        if str(value)
    )
    measurements = _dimension_measurements(evidence, evidence_dimensions)
    if (
        not row_dimensions
        or not evidence_dimensions
        or len(set(evidence_dimensions)) != len(evidence_dimensions)
        or set(evidence_dimensions) != row_dimensions
        or measurements is None
    ):
        return False
    required = {round(value, 3) for value in segments[:3]}
    measured = {round(value, 3) for value in measurements.values()}
    return required == measured


def _f006_owned_contour_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
) -> bool:
    if (
        len(segments) != 6
        or str(row.get("master_shape_code") or "").strip().upper() != "F006"
        or str(row.get("f006_production_status") or "").strip().upper()
        != "F006_PRODUCTION_DERIVED"
        or str(row.get("f006_production_reason") or "").strip().upper()
        != "OWNED_DIMENSIONS"
        or not _canonical_vector_matches(row, segments)
    ):
        return False
    owners = {
        str(value)
        for value in (evidence.get("owner_geometry_handles") or [])
        if str(value)
    }
    candidates = {
        str(value)
        for value in (row.get("candidate_handles") or [])
        if str(value)
    }
    dimensions = tuple(
        str(value)
        for value in (evidence.get("dimension_handles") or [])
        if str(value)
    )
    measurements = _dimension_measurements(evidence, dimensions)
    if (
        len(owners) != 1
        or not owners.issubset(candidates)
        or not dimensions
        or len(set(dimensions)) != len(dimensions)
        or measurements is None
    ):
        return False
    hook, side_a, side_b = segments[0], segments[1], segments[2]
    required = {round(value, 3) for value in (hook, side_a, side_b)}
    measured = {round(value, 3) for value in measurements.values()}
    return required == measured


def _printed_detail_dimension_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
    total: float,
    source: str,
) -> bool:
    """Преизвежда „форма от котите до надписа" от записания ред.

    Умишлено по-строго от писателя и независимо от него: тук не се вярва
    нито на кода на източника, нито на политиката. Проверява се

    * че надписът до детайла наистина обявява СТРЕМЕ — текстът се пуска
      наново през ``local_detail_shape_code``;
    * че позицията все още е без геометричен кандидат (правилото важи само
      за такива; появил се кандидат прави доказателството остаряло);
    * че всяка надписана кота има свой handle и измерена стойност, и че
      надписаните числа са ТОЧНО тези, от които са сглобени участъците;
    * че прочитът е един от двата допустими — две коти с ЕДИН изчислен
      размер (F006) или пет коти в симетричен набор без изчислени (F010);
    * че куката е най-късият размер и е в измерения диапазон;
    * че уравнението затваря декларираната дължина точно.

    При спецификация се иска и Ø и дължината на самата спецификация да
    съвпадат с тези на позицията: това е връзката, която проектантът е
    написал, и без нея прочитът е чужд.
    """

    shape_code = str(row.get("master_shape_code") or "").strip().upper()
    if shape_code not in {"F006", "F010"}:
        return False
    if local_detail_shape_code(str(evidence.get("label_text") or "")) != "F006":
        return False
    label_distance = _finite_nonnegative_scalar(evidence.get("label_distance_raw"))
    if label_distance is None or label_distance > _PRINTED_DETAIL_LABEL_MAX_DISTANCE_RAW:
        return False
    if [str(value) for value in (row.get("candidate_handles") or []) if str(value)]:
        return False
    if [str(value) for value in (evidence.get("candidate_handles") or []) if str(value)]:
        return False
    if not _canonical_vector_matches(row, segments):
        return False

    dimensions = tuple(
        str(value) for value in (evidence.get("dimension_handles") or []) if str(value)
    )
    measurements = _dimension_measurements(evidence, dimensions)
    if (
        not dimensions
        or len(set(dimensions)) != len(dimensions)
        or measurements is None
    ):
        return False
    printed = _finite_positive_vector(evidence.get("printed_values_cm"))
    if printed is None or sorted(printed) != sorted(measurements.values()):
        return False

    computed_raw = evidence.get("computed_value_cm")
    if shape_code == "F006":
        if len(segments) != 6 or len(printed) != 2:
            return False
        hook, side_a, side_b = segments[0], segments[1], segments[2]
        if segments != (hook, side_a, side_b, side_a, side_b, hook):
            return False
        computed = _finite_positive_scalar(computed_raw)
        if computed is None:
            return False
        sizes = sorted((hook, side_a, side_b))
        if sorted([*printed, computed]) != sizes:
            return False
        if hook != sizes[0]:
            return False
        if not _PRINTED_DETAIL_HOOK_MIN_CM <= hook <= _PRINTED_DETAIL_HOOK_MAX_CM:
            return False
    else:
        if len(segments) != 5 or len(printed) != 5 or computed_raw is not None:
            return False
        end, leg, middle = segments[0], segments[1], segments[2]
        if segments != (end, leg, middle, leg, end) or end > leg:
            return False
        if sorted(printed) != sorted(segments):
            return False
    if abs(math.fsum(segments) - total) > _PRINTED_DETAIL_CLOSURE_TOLERANCE_CM:
        return False

    if source not in _PRINTED_DECLARATION_SOURCES:
        return True
    specification = str(evidence.get("specification_handle") or "")
    if not specification or specification == str(row.get("annotation_handle") or ""):
        return False
    if source == _PRINTED_REPEATED_LABEL_DIMENSION_SOURCE:
        # Пренесен прочит: същият надпис, а собствените коти на позицията не
        # бива да противоречат на пренесените участъци.
        declared_text = " ".join(
            str(evidence.get("specification_text") or "").split()
        ).upper()
        row_text = " ".join(str(row.get("annotation_text") or "").split()).upper()
        if not declared_text or declared_text != row_text:
            return False
        own_printed = evidence.get("own_printed_values_cm")
        if not isinstance(own_printed, (list, tuple)):
            return False
        for value in own_printed:
            own_value = _finite_positive_scalar(value)
            if own_value is None or not any(
                abs(own_value - segment) <= 0.001 for segment in segments
            ):
                return False
    specification_diameter = _finite_positive_scalar(
        evidence.get("specification_diameter_mm")
    )
    specification_length = _finite_positive_scalar(
        evidence.get("specification_length_cm")
    )
    row_diameter = _finite_positive_scalar(row.get("diameter_mm"))
    row_length = _finite_positive_scalar(row.get("declared_length_cm"))
    if (
        specification_diameter is None
        or specification_length is None
        or row_diameter is None
        or row_length is None
    ):
        return False
    return (
        abs(specification_diameter - row_diameter) <= 0.001
        and abs(specification_length - row_length) <= 0.001
    )


def _dimension_measurements(
    evidence: Mapping[str, Any],
    dimension_handles: tuple[str, ...],
) -> dict[str, float] | None:
    if not dimension_handles:
        return None
    raw = evidence.get("dimension_measurements_cm")
    if not isinstance(raw, list) or len(raw) != len(dimension_handles):
        return None
    result: dict[str, float] = {}
    for item in raw:
        if not isinstance(item, dict):
            return None
        handle = str(item.get("handle") or "")
        value = _finite_positive_scalar(item.get("measurement_cm"))
        if handle not in dimension_handles or handle in result or value is None:
            return None
        result[handle] = value
    return result if set(result) == set(dimension_handles) else None


def _contextual_f007_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
) -> bool:
    row_rule = str(row.get("contextual_rule_code") or "").strip().upper()
    evidence_rule = str(evidence.get("contextual_rule_code") or "").strip().upper()
    canonical_raw = row.get("canonical_segment_lengths_cm")
    return (
        len(segments) == 3
        and row_rule == _F007_CONTEXTUAL_RULE
        and evidence_rule == _F007_CONTEXTUAL_RULE
        and isinstance(canonical_raw, (list, tuple))
        and list(canonical_raw) == list(evidence.get("segments") or [])
        and _finite_positive_vector(canonical_raw) == segments
    )


def _strict_dimension_evidence_is_owned(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segments: tuple[float, ...],
) -> bool:
    if len(segments) != 3:
        return False
    row_owners = {
        str(value)
        for value in (row.get("candidate_handles") or [])
        if str(value)
    }
    evidence_owners = {
        str(value)
        for value in (evidence.get("owner_geometry_handles") or [])
        if str(value)
    }
    if not evidence_owners or not evidence_owners.issubset(row_owners):
        return False

    dimension_handles = [
        str(value)
        for value in (evidence.get("dimension_handles") or [])
        if str(value)
    ]
    if not dimension_handles or len(set(dimension_handles)) != len(dimension_handles):
        return False
    measurements_raw = evidence.get("dimension_measurements_cm")
    if not isinstance(measurements_raw, list) or len(measurements_raw) != len(
        dimension_handles
    ):
        return False
    measurements: dict[str, float] = {}
    for item in measurements_raw:
        if not isinstance(item, dict):
            return False
        handle = str(item.get("handle") or "")
        value = _finite_positive_scalar(item.get("measurement_cm"))
        if handle not in dimension_handles or value is None or handle in measurements:
            return False
        measurements[handle] = value
    if set(measurements) != set(dimension_handles):
        return False

    bindings = evidence.get("segment_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(segments):
        return False
    seen_indexes: set[int] = set()
    used_handles: set[str] = set()
    for item in bindings:
        if not isinstance(item, dict):
            return False
        index = item.get("segment_index")
        if isinstance(index, bool) or not isinstance(index, int):
            return False
        handle = str(item.get("dimension_handle") or "")
        measured = _finite_positive_scalar(item.get("measurement_cm"))
        if (
            index < 1
            or index > len(segments)
            or index in seen_indexes
            or handle not in measurements
            or measured is None
            or measured != measurements[handle]
            or abs(measured - segments[index - 1]) > 0.11
        ):
            return False
        seen_indexes.add(index)
        used_handles.add(handle)
    return (
        seen_indexes == set(range(1, len(segments) + 1))
        and used_handles == set(dimension_handles)
    )


def _finite_positive_vector(values: Any) -> tuple[float, ...] | None:
    if not isinstance(values, (list, tuple)):
        return None
    result: list[float] = []
    for value in values:
        parsed = _finite_positive_scalar(value)
        if parsed is None:
            return None
        result.append(parsed)
    return tuple(result)


def _finite_vector(values: Any) -> tuple[float, ...] | None:
    if not isinstance(values, (list, tuple)):
        return None
    result: list[float] = []
    for value in values:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        result.append(parsed)
    return tuple(result)


def _finite_positive_scalar(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _finite_nonnegative_scalar(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def is_deterministic_production_source(row: Mapping[str, Any]) -> bool:
    """Return whether structured source data plus proven dimensions is
    deterministic.

    ``BJ-FI-L`` rows carry named block attributes instead of free-form OCR/text
    parsing. They may pass the production gate even when the geometry matcher
    remains MANUAL, but only after the production dimensions have been proven.

    ``PXANN_`` rows (arm_koloni/arm_shaibi) are the same class of source for
    the same reason: count, diameter and length come from the designer's own
    structured proxy text fragments, not from guessing at free-form text, and
    the shape family is proven independently by real bend-arc geometry. They
    likewise stay ineligible until ``validate_production_segment_evidence``
    re-proves the dimensions -- which, for this dialect, also re-checks
    confidence, matcher agreement and the arc/shape/total relationship (see
    ``_proxy_arc_verified_evidence_is_owned``).
    """

    handle = str(row.get("annotation_handle") or "")
    if "::BJ-FI-L" in handle:
        return validate_production_segment_evidence(row)
    if not handle.startswith("PXANN_"):
        return False
    # The proxy bypass is earned by the DECLARED breakdown, not by the handle.
    # A row whose segments came from the geometry matcher has no such
    # provenance and must face the ordinary status/disposition gate -- see
    # tests/test_proxy_structured_source_scope.py for the real row that was
    # shipped as a bent F003 while its own declaration proved it straight.
    evidence = row.get("production_segment_evidence")
    if not isinstance(evidence, Mapping):
        return False
    if str(evidence.get("source") or "") != _PROXY_ARC_VERIFIED_SOURCE:
        return False
    return validate_production_segment_evidence(row)


def auto_batch_status_passes(row: Mapping[str, Any]) -> bool:
    return is_deterministic_production_source(row) or (
        str(row.get("batch_status") or "") in AUTO_EXPORT_BATCH_STATUSES
    )


def auto_matcher_disposition_passes(row: Mapping[str, Any]) -> bool:
    return is_deterministic_production_source(row) or (
        str(row.get("matcher_disposition") or "")
        in AUTO_EXPORT_MATCHER_DISPOSITIONS
    )


def auto_classification_gate_passes(row: Mapping[str, Any]) -> bool:
    """Shared AUTO classification predicate used by production and final gates."""

    return auto_batch_status_passes(row) and auto_matcher_disposition_passes(row)


def auto_gate_eligibility_mode(row: Mapping[str, Any]) -> str:
    if is_deterministic_production_source(row):
        return "DETERMINISTIC_BLOCK_DERIVED_DIMENSIONS"
    if auto_classification_gate_passes(row):
        return "STATUS_AND_MATCHER"
    return "INELIGIBLE"
