"""A superseded matcher attempt must not keep a real position out (rc44).

Owner-reported 2026-09-13 from project ``s-7``, wall Ш 3-25/600: position 2,
"17N12x415 (общо 34)", never reached the specification. The owner confirmed
from the drawing that it is a real production position -- the section itself
dimensions it 30 | 355 | 30 = 415 -- so 34 bars of Ø12 x 415 cm, about 125 kg,
were missing from the Excel.

The shape was never in doubt. The annotation's own proxy geometry is a clean
60 / 710 / 60 U at the sheet's 2-units-per-cm scale, and the promotion had
already written the right answer:

    master_shape_code        F003
    production_segments_cm   [30.0, 355.0, 30.0]
    evidence source          PROXY_ARC_VERIFIED_DECLARED_SEGMENTS

What kept it out was an EARLIER, unrelated attempt. The matcher had latched
onto a distant foreign candidate (7A533E / 7A533F) and tried to derive
segments from its printed A-E dimensions (50 + 365). That attempt hit an
ownership conflict, so ``_block_authoritative_segment_evidence`` fail-closed:
it deleted the segments and raised ``production_export_blocked`` with reason
``MATCHER_DIMENSION_EVIDENCE_INVALID``.

Failing closed there is right. Leaving the flag up afterwards is not: the
promotion then supplied segments from a DIFFERENT source, independently
re-proved by ``auto_gate``. The flag still described the discarded matcher
evidence, and ``batch_auto_output`` turned it into an export blocker for a
position that had nothing wrong with it.
"""
from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import (
    _apply_proxy_declared_shape_auto_promotion,
    _block_authoritative_segment_evidence,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.proxy_adapter import PROXY_DECLARED_SHAPE_CODE

_SHAPES = (SimpleNamespace(code="F003", name="ПРЪТ С ДВА ВИНКЕЛА"),)


def _annotation() -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="PXANN_789D5D",
        entity_type="BLOCK_ATTR_TEXT",
        layer="arm_shaibi",
        text="34N12x415",
        geometry={"insert": [0.0, 0.0, 0.0]},
        raw={
            "declared_shape_source": PROXY_DECLARED_SHAPE_CODE,
            "declared_shape_code": "F003",
            "declared_shape_segments_cm": [30.0, 355.0, 30.0],
            "declared_shape_confidence": "HIGH",
            "declared_shape_bend_arc_count": 0,
            "declared_shape_leg_directions_deg": [90.0, 0.0, 90.0],
            "parent_proxy_handle": "789D5D",
        },
    )


def _row() -> dict:
    """The real row, with the matcher's failed A-E attempt already recorded."""

    row = {
        "annotation_handle": "PXANN_789D5D",
        "annotation_text": "34N12x415",
        "declared_length_cm": 415.0,
        "master_shape_code": "F004",
        "candidate_handles": ["7A533E", "7A533F"],
        "review_reason_codes": ["FAR_ASSOCIATION"],
        "matcher_blocker_codes": ["GEOMETRY_OWNERSHIP_CONFLICT", "FAR_ASSOCIATION"],
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "production_segments_cm": [50.0, 365.0],
        "production_segment_evidence": {"source": "MATCHER_LOCAL_A_E_CANONICAL"},
    }
    _block_authoritative_segment_evidence(row, "MATCHER_DIMENSION_EVIDENCE_INVALID")
    return row


def _promote(row: dict) -> int:
    return _apply_proxy_declared_shape_auto_promotion(
        [row],
        annotations_by_handle={"PXANN_789D5D": _annotation()},
        shape_definitions=_SHAPES,
    )


def test_the_declared_shape_still_replaces_the_discarded_matcher_attempt() -> None:
    row = _row()

    assert _promote(row) == 1
    assert row["master_shape_code"] == "F003"
    assert row["production_segments_cm"] == [30.0, 355.0, 30.0]


def test_the_stale_export_block_is_lifted() -> None:
    """This is the 125 kg: the flag described segments that no longer exist."""

    row = _row()
    assert row["production_export_blocked"] is True

    _promote(row)

    assert not row.get("production_export_blocked")
    assert not row.get("production_export_block_reason")
    assert "MATCHER_DIMENSION_EVIDENCE_INVALID" not in row["review_reason_codes"]


def test_the_genuine_far_association_reason_is_left_alone() -> None:
    """Only the superseded matcher complaint is cleared. Everything the row
    legitimately carries stays on the record for the operator to see."""

    row = _row()

    _promote(row)

    assert "FAR_ASSOCIATION" in row["review_reason_codes"]
    assert "GEOMETRY_OWNERSHIP_CONFLICT" in row["matcher_blocker_codes"]


def test_a_row_the_promotion_cannot_prove_keeps_its_block() -> None:
    """Fail-closed stays fail-closed: without a promotable declaration there
    is nothing to supersede the matcher, so the block must survive."""

    row = _row()
    annotation = _annotation()
    annotation.raw["declared_shape_segments_cm"] = [30.0, 355.0, 40.0]

    applied = _apply_proxy_declared_shape_auto_promotion(
        [row],
        annotations_by_handle={"PXANN_789D5D": annotation},
        shape_definitions=_SHAPES,
    )

    assert applied == 0
    assert row["production_export_blocked"] is True
    assert row["production_export_block_reason"] == "MATCHER_DIMENSION_EVIDENCE_INVALID"
