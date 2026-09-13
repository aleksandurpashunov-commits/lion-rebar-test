from __future__ import annotations

from copy import deepcopy

import pytest

import lion_rebar.dxf.batch_geometry_report as batch_geometry_report
from lion_rebar.dxf.batch_geometry_report import (
    _apply_borderline_curved_f006_owned_dimension_recovery,
    _apply_matcher_decisions,
)
from lion_rebar.dxf.geometry_core_v2 import (
    polyline_path_from_geometry,
    recover_borderline_f006_semantic_path,
    semantic_path_from_polyline,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex
from lion_rebar.ui.review_center import _shape_summary_identity
from lion_rebar.dxf.vertical_shape_resolver import (
    strict_unique_dimension_records_by_contours,
)


_H3_BULGES = (
    0.0,
    -0.6801808649388219,
    0.0,
    -0.4142135623726688,
    0.0,
    -0.414213562372669,
    0.0,
    -0.4142135623735212,
    0.0,
    -0.6563080393584722,
    0.0,
    0.0,
)

# Exact 8BCB coordinates from the H-3 import. The other three affected
# contours are translations with the same hooks/radii and a different width;
# indices 5..8 are the translated left side of that rectangle.
_H3_BASE_POINTS = (
    (8273.419695766908, 1434.188124246964),
    (8280.548252431361, 1441.488694161479),
    (8284.873395779554, 1439.780227223247),
    (8284.873395779554, 1424.780222009704),
    (8282.373394909233, 1422.280221140459),
    (8197.373363616796, 1422.280221140692),
    (8194.873362747172, 1424.780222009936),
    (8194.873362747172, 1439.780227223480),
    (8197.373363615863, 1442.280228092724),
    (8282.373394909233, 1442.280228092491),
    (8284.198537388036, 1438.071760285249),
    (8277.364669635112, 1430.771190370502),
)


def _f006() -> ShapeDefinition:
    codes = ("L1", "L2", "L3", "L2", "L3", "L1")
    bends = (135.0, 90.0, 90.0, 90.0, 135.0, None)
    return ShapeDefinition(
        code="F006",
        name="LOCKED CLOSED STIRRUP",
        state="LOCKED",
        locked=True,
        active=True,
        closed=True,
        symmetric=True,
        mirror_allowed=True,
        recognition={"segment_count": 6, "bend_count": 5},
        segments=[
            SegmentDefinition(
                index=index,
                length_code=code,
                bend_after_deg=bend,
                bend_direction="LEFT" if bend is not None else "NONE",
            )
            for index, (code, bend) in enumerate(zip(codes, bends), start=1)
        ],
    )


def _contour(handle: str, width: float, *, layer: str = "Stremena") -> DxfEntityRecord:
    left_shift = width - 90.0
    points = [list(point) for point in _H3_BASE_POINTS]
    for index in (5, 6, 7, 8):
        points[index][0] -= left_shift
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer=layer,
        geometry={
            "points": [
                [point[0], point[1], _H3_BULGES[index]]
                for index, point in enumerate(points)
            ],
            "bulges": list(_H3_BULGES),
            "closed": False,
            "curve_segment_count": 5,
        },
    )


def _dimension(
    handle: str,
    measurement: float,
    first: list[float],
    second: list[float],
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="DIMENSION",
        layer="koti",
        geometry={
            "measurement": measurement,
            "defpoint2": list(first),
            "defpoint3": list(second),
            "text_midpoint": list(first),
        },
    )


def _owned_dimensions(
    contour: DxfEntityRecord,
    width: float,
    *,
    duplicate_height: bool = False,
    extra_value: float | None = None,
) -> list[DxfEntityRecord]:
    points = contour.geometry["points"]
    dimensions = [
        _dimension(f"D-{contour.handle}-W", width, points[4], points[5]),
        _dimension(f"D-{contour.handle}-H", 20.0, points[6], points[7]),
        _dimension(f"D-{contour.handle}-K", 10.0, points[0], points[1]),
    ]
    if duplicate_height:
        dimensions.append(
            _dimension(f"D-{contour.handle}-H2", 20.0, points[8], points[9])
        )
    if extra_value is not None:
        dimensions.append(
            _dimension(
                f"D-{contour.handle}-X",
                extra_value,
                points[2],
                points[3],
            )
        )
    return dimensions


def _row(contour: DxfEntityRecord, declared: float, count: int) -> dict:
    path = polyline_path_from_geometry(contour.geometry)
    assert path is not None
    semantic = semantic_path_from_polyline(path)
    return {
        "annotation_handle": f"A-{contour.handle}",
        "annotation_text": f"{count}N8x{declared:g}",
        "annotation_class": "ACTIONABLE",
        "annotation_count": count,
        "diameter_mm": 8.0,
        "declared_length_cm": declared,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "analysis_status": "ИМА КАНДИДАТИ ЗА РЪЧНА ПРОВЕРКА",
        "approved_in_project": False,
        "candidate_handles": [contour.handle],
        "candidate_layer": contour.layer,
        "segment_count": len(semantic.points) - 1,
        "geometry_length_raw": path.exact_length_raw,
        "effective_geometry_length_cm": path.exact_length_raw,
        "raw_numeric_difference": abs(path.exact_length_raw - declared),
        "numeric_difference": abs(path.exact_length_raw - declared),
        "distance_raw": 12.0,
        "centroid_distance_raw": 20.0,
        "candidate_confidence": "НЕРАЗРЕШЕНА ДЪГОВА ГЕОМЕТРИЯ — РЪЧНА ПРОВЕРКА",
        "candidate_basis": "ЕДИНИЧНА ПОЛИЛИНИЯ",
        "candidate_score": 0.1,
        "candidate_score_gap": 0.5,
        "candidate_distance_gap_raw": 60.0,
        "candidate_notes": [],
        "analysis_warnings": [],
        "alternative_candidates": [],
        "master_shape_code": None,
        "master_ambiguous_shape_codes": [],
        "master_conflict": False,
        "global_conflict": False,
        "geometry_owner_conflict": False,
        "review_reason_codes": [
            "UNRESOLVED_CURVE_GEOMETRY",
            "CURVED_LAYER_ALIAS",
        ],
        "source_curve_count": semantic.source_curve_count,
        "normalized_bend_count": semantic.collapsed_arc_count,
        "unresolved_curve_count": semantic.unresolved_arc_count,
        "source_arc_radii_raw": list(semantic.arc_radii_raw),
        "hooked_closure": False,
        "canonical_segment_lengths_cm": [],
        "canonical_turn_angles_deg": [],
    }


@pytest.mark.parametrize(
    ("handle", "width", "declared", "count", "duplicate_height"),
    (
        ("7B4B", 145.0, 350.0, 36, False),
        ("875F", 90.0, 245.0, 108, False),
        ("8BCB", 90.0, 245.0, 36, False),
        ("8C7E", 55.0, 175.0, 36, True),
    ),
)
def test_all_four_h3_borderline_contours_recover_only_with_complete_proof(
    handle: str,
    width: float,
    declared: float,
    count: int,
    duplicate_height: bool,
) -> None:
    contour = _contour(handle, width)
    path = polyline_path_from_geometry(contour.geometry)
    assert path is not None
    generic = semantic_path_from_polyline(path)
    assert generic.collapsed_arc_count == 4
    assert generic.unresolved_arc_count == 1
    strict = recover_borderline_f006_semantic_path(path)
    assert strict is not None
    assert strict.offset_residual_ratio == pytest.approx(0.030179969887, rel=1e-8)

    entities = [
        contour,
        *_owned_dimensions(
            contour,
            width,
            duplicate_height=duplicate_height,
        ),
    ]
    row = _row(contour, declared, count)
    shape = _f006()
    policy = RecognitionPolicy()
    recovered = _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=entities,
        shape_definitions=[shape],
        control_tests=[],
        policy=policy,
        shape_index=EngineShapeIndex.from_shapes([shape]),
    )

    assert recovered == 1
    assert row["master_shape_code"] == "F006"
    assert row["unresolved_curve_count"] == 0
    assert row["normalized_bend_count"] == 5
    assert row["production_segments_cm"] == [
        10.0,
        20.0,
        width,
        20.0,
        width,
        10.0,
    ]
    assert row["effective_geometry_length_cm"] == declared
    assert row["numeric_difference"] == 0.0
    assert row["raw_numeric_difference"] > 0.0
    assert row["f006_production_status"] == "F006_PRODUCTION_DERIVED"
    assert row["contextual_rule_code"] == (
        "F006_BORDERLINE_CURVE_OWNED_DIMENSIONS"
    )
    assert "UNRESOLVED_CURVE_GEOMETRY" not in row["review_reason_codes"]
    assert row["f006_borderline_curve_recovery"]["dimension_handles"]

    _apply_matcher_decisions(
        [row],
        shape_definitions=[shape],
        policy=policy,
    )
    assert row["matcher_disposition"] == "CONTROL"
    assert row["matcher_blocker_codes"] == []


def test_residual_above_3_25_percent_remains_unresolved() -> None:
    contour = _contour("TOO-FAR", 90.0)
    contour.geometry = deepcopy(contour.geometry)
    contour.geometry["bulges"][1] = -0.681
    contour.geometry["points"][1][2] = -0.681
    path = polyline_path_from_geometry(contour.geometry)
    assert path is not None
    generic = semantic_path_from_polyline(path)
    assert generic.unresolved_arc_count == 1
    assert recover_borderline_f006_semantic_path(path) is None

    row = _row(contour, 245.0, 36)
    shape = _f006()
    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=[contour, *_owned_dimensions(contour, 90.0)],
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row["master_shape_code"] is None
    assert "UNRESOLVED_CURVE_GEOMETRY" in row["review_reason_codes"]


@pytest.mark.parametrize("evidence", ("AMBIGUOUS_DIMENSIONS", "MISSING_DIMENSION"))
def test_incomplete_or_ambiguous_contour_dimensions_fail_closed(evidence: str) -> None:
    contour = _contour(evidence, 90.0)
    dimensions = _owned_dimensions(
        contour,
        90.0,
        extra_value=15.0 if evidence == "AMBIGUOUS_DIMENSIONS" else None,
    )
    if evidence == "MISSING_DIMENSION":
        dimensions.pop()
    row = _row(contour, 245.0, 36)
    shape = _f006()

    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=[contour, *dimensions],
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row["master_shape_code"] is None
    assert row.get("production_segments_cm") in (None, [])


def test_non_stirrup_layer_never_uses_h3_exception() -> None:
    contour = _contour("WRONG-LAYER", 90.0, layer="ARM_BARS")
    row = _row(contour, 245.0, 36)
    shape = _f006()

    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=[contour, *_owned_dimensions(contour, 90.0)],
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row["master_shape_code"] is None


@pytest.mark.parametrize("rival_shift_raw", (0.0, 0.25))
def test_overlapping_or_near_tied_contours_never_recover(
    rival_shift_raw: float,
) -> None:
    contour = _contour("OWNER", 90.0)
    rival = _contour("RIVAL", 90.0)
    rival.geometry = deepcopy(rival.geometry)
    for point in rival.geometry["points"]:
        point[0] += rival_shift_raw
    dimensions = _owned_dimensions(contour, 90.0)
    entities = [contour, rival, *dimensions]

    owned = strict_unique_dimension_records_by_contours(entities)
    assert owned.get(contour.handle) in (None, [])
    assert owned.get(rival.handle) in (None, [])

    row = _row(contour, 245.0, 36)
    before = deepcopy(row)
    shape = _f006()
    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=entities,
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row == before


def test_one_shared_dimension_endpoint_is_not_unique_ownership() -> None:
    contour = _contour("OWNER-SHARED", 90.0)
    rival = _contour("RIVAL-SHARED", 90.0)
    rival.geometry = deepcopy(rival.geometry)
    for point in rival.geometry["points"]:
        point[0] += 1000.0
    # One rival vertex exactly shares the width dimension's first endpoint.
    rival.geometry["points"][0][:2] = contour.geometry["points"][4][:2]
    dimensions = _owned_dimensions(contour, 90.0)
    entities = [contour, rival, *dimensions]

    owned = strict_unique_dimension_records_by_contours(entities)
    owner_handles = {
        str(record.get("handle") or "")
        for record in owned.get(contour.handle, ())
    }
    assert f"D-{contour.handle}-W" not in owner_handles
    assert len(owner_handles) == 2

    row = _row(contour, 245.0, 36)
    before = deepcopy(row)
    shape = _f006()
    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=entities,
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row == before


def test_failed_authoritative_install_leaves_row_atomically_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contour = _contour("INSTALL-FAIL", 90.0)
    row = _row(contour, 245.0, 36)
    before = deepcopy(row)
    shape = _f006()
    monkeypatch.setattr(
        batch_geometry_report,
        "_install_authoritative_production_segments",
        lambda *_args, **_kwargs: False,
    )

    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=[contour, *_owned_dimensions(contour, 90.0)],
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row == before


def test_other_hooked_closure_reason_stays_blocked_outside_strict_conjunction() -> None:
    contour = _contour("OTHER-HOOKED", 90.0)
    row = _row(contour, 245.0, 36)
    row["hooked_closure"] = True
    row["review_reason_codes"].append("HOOKED_CLOSURE")
    before = deepcopy(row)
    shape = _f006()

    assert _apply_borderline_curved_f006_owned_dimension_recovery(
        [row],
        entities=[contour, *_owned_dimensions(contour, 90.0)],
        shape_definitions=[shape],
        control_tests=[],
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes([shape]),
    ) == 0
    assert row == before
    assert row["master_shape_code"] is None
    assert row.get("production_segments_cm") in (None, [])


def test_review_center_never_labels_a_suggestion_as_master() -> None:
    assert _shape_summary_identity({"suggested_shape_code": "F006"}) == (
        "ПРЕДЛОЖЕНИЕ",
        "F006",
    )
    assert _shape_summary_identity(
        {"master_shape_code": "F006", "suggested_shape_code": "F007"}
    ) == ("MASTER", "F006")
    assert _shape_summary_identity(
        {"master_ambiguous_shape_codes": ["F003", "F005"]}
    ) == ("MASTER", "F003 / F005")
