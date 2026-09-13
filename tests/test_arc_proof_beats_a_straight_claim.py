"""A measured bend arc outranks any claim that the bar is straight (rc44).

Real shaibi bars "43N8x37" and seven siblings, project ``s-7``. Each declares
``[21, 8, 8]`` -- a 21 cm leg with two 8 cm hooks, satisfying the engine's
F007 control equation 2*8 + 21 = 37 -- and each carries a genuine ARC of
radius 4.8 sweeping 135 degrees.

The matcher nevertheless typed them F001 "ПРАВ ПРЪТ", because its candidate
was ``PXG_<own parent>_2_0``: ONE straight polyline out of the bar's own
exploded geometry. Matching a single straight leg of a hooked bar is not a
match on the bar, but the score came back strong, so the contradiction rule
downgraded the row and left the declaration unused.

``_apply_proxy_declared_shape_auto_promotion`` already documents the physics
for the weak case -- "a straight bar cannot have structural bends". Strength
does not change physics: a positive structural arc count rules out F001
whatever the geometric score. Every other contradiction still goes to a human
with both claims on the record.
"""
from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import (
    _PROXY_SHAPE_CONTRADICTION_CODE,
    _apply_proxy_declared_shape_auto_promotion,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.proxy_adapter import PROXY_DECLARED_SHAPE_CODE

_SHAPES = (
    SimpleNamespace(code="F001", name="ПРАВ ПРЪТ"),
    SimpleNamespace(code="F006", name="СТРЕМЕ"),
    SimpleNamespace(code="F007", name="S / C-ОБРАЗНА ФОРМА"),
)


def _annotation(shape: str, segments: list[float], arcs: int) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="PXANN_7A1565",
        entity_type="BLOCK_ATTR_TEXT",
        layer="arm_shaibi",
        text="43N8x37",
        geometry={"insert": [0.0, 0.0, 0.0]},
        raw={
            "declared_shape_source": PROXY_DECLARED_SHAPE_CODE,
            "declared_shape_code": shape,
            "declared_shape_segments_cm": segments,
            "declared_shape_confidence": "HIGH",
            "declared_shape_bend_arc_count": arcs,
            "parent_proxy_handle": "7A1565",
        },
    )


def _row(master: str, status: str) -> dict:
    return {
        "annotation_handle": "PXANN_7A1565",
        "annotation_text": "43N8x37",
        "declared_length_cm": 37.0,
        "master_shape_code": master,
        "batch_status": status,
        "candidate_handles": ["PXG_7A1565_2_0"],
        "review_reason_codes": [],
        "matcher_blocker_codes": [],
    }


def _promote(row: dict, annotation: DxfEntityRecord) -> int:
    return _apply_proxy_declared_shape_auto_promotion(
        [row],
        annotations_by_handle={"PXANN_7A1565": annotation},
        shape_definitions=_SHAPES,
    )


def test_a_strong_straight_match_loses_to_a_measured_bend() -> None:
    """The real case: one straight sub-piece scored strong, the bar has an arc."""

    row = _row("F001", "СИЛЕН ПО ГЕОМЕТРИЯ")

    assert _promote(row, _annotation("F007", [8.0, 21.0, 8.0], 1)) == 1
    assert row["master_shape_code"] == "F007"
    assert row["production_segments_cm"] == [8.0, 21.0, 8.0]


def test_a_strong_match_on_a_different_BENT_family_still_goes_to_a_human() -> None:
    """Only the straight claim is settled by physics. Two bent families
    disagreeing is a real question and keeps its manual review."""

    row = _row("F006", "СИЛЕН ПО ГЕОМЕТРИЯ")

    assert _promote(row, _annotation("F007", [8.0, 21.0, 8.0], 1)) == 0
    assert row["master_shape_code"] == "F006"
    assert _PROXY_SHAPE_CONTRADICTION_CODE in row["review_reason_codes"]
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"


def test_without_a_measured_arc_the_straight_match_stands() -> None:
    """No arc, no physics argument -- the geometric match is not overruled."""

    row = _row("F001", "СИЛЕН ПО ГЕОМЕТРИЯ")

    assert _promote(row, _annotation("F007", [8.0, 21.0, 8.0], 0)) == 0
    assert row["master_shape_code"] == "F001"
