from __future__ import annotations

import math

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import match_master_shapes
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


def _segment(
    index: int,
    code: str,
    direction: str,
    bend: float | None = None,
    bend_direction: str = "LEFT",
    **values,
) -> SegmentDefinition:
    return SegmentDefinition(
        index=index,
        length_code=code,
        drawing_direction=direction,
        bend_after_deg=bend,
        bend_direction=bend_direction,
        **values,
    )


def test_projection_xy_turn_pair_is_parametric_in_detailed_matcher():
    shape = ShapeDefinition(
        code="REDUCTION",
        name="PARAMETRIC REDUCTION",
        state="APPROVED",
        mirror_allowed=True,
        segments=[
            _segment(1, "L1", "RIGHT"),
            _segment(
                2,
                "L2",
                "UP_RIGHT",
                geometry_method="PROJECTIONS_XY",
                projection_x_code="DX2",
                projection_y_code="DY2",
            ),
            _segment(3, "L3", "RIGHT"),
        ],
    )
    angle = math.radians(30.0)
    points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0 + 50.0 * math.cos(angle), 50.0 * math.sin(angle)),
        (220.0, 25.0),
    )

    match = match_master_shapes(
        points,
        [shape],
        policy=RecognitionPolicy(),
    )

    assert match is not None
    assert match.shape_code == "REDUCTION"
    assert match.max_angle_error_deg < 1e-6


def _rounded_open_stirrup() -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ST1",
        entity_type="LWPOLYLINE",
        layer="Stremena",
        geometry={
            "points": [
                [11.1590072210, 11.9078989661, 0.0],
                [4.3251418444, 19.2084663422, 0.6801808649],
                [0.0, 17.5, 0.0],
                [0.0, 2.5, math.tan(math.radians(90.0) / 4.0)],
                [2.5, 0.0, 0.0],
                [47.5, 0.0, math.tan(math.radians(90.0) / 4.0)],
                [50.0, 2.5, 0.0],
                [50.0, 17.5, math.tan(math.radians(90.0) / 4.0)],
                [47.5, 20.0, 0.0],
                [2.5, 20.0, 0.6563080394],
                [0.6748581559, 15.7915336538, 0.0],
                [7.5087235328, 8.4909662777, 0.0],
            ],
            "closed": False,
        },
    )


def _closed_hooked_master() -> ShapeDefinition:
    return ShapeDefinition(
        code="STIRRUP",
        name="CLOSED HOOKED STIRRUP",
        state="APPROVED",
        closed=True,
        symmetric=True,
        mirror_allowed=True,
        segments=[
            _segment(1, "H", "UP_RIGHT", 135.0),
            _segment(2, "W", "LEFT", 90.0),
            _segment(3, "V", "DOWN", 90.0),
            _segment(4, "W", "RIGHT", 90.0),
            _segment(5, "V", "UP", 135.0),
            _segment(6, "H", "DOWN_LEFT"),
        ],
    )


def test_open_terminal_hooks_are_reviewable_against_closed_master():
    annotation = DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text="1N8x179",
        geometry={"insert": [20.0, -8.0, 0.0], "height": 2.5},
    )
    geometry = _rounded_open_stirrup()

    analysis = find_geometry_candidates(
        annotation,
        [annotation, geometry],
        shape_definitions=[_closed_hooked_master()],
    )

    assert analysis.candidates
    candidate = analysis.candidates[0]
    assert candidate.master_shape_code == "STIRRUP"
    assert candidate.segment_count == 6
    assert candidate.hooked_closure is True
    assert candidate.hooked_closure_gap_raw is not None
    assert "HOOKED_CLOSURE" in candidate.review_reason_codes
    assert "РЪЧНА ПРОВЕРКА" in candidate.confidence
    assert candidate.normalized_bend_count == 5
