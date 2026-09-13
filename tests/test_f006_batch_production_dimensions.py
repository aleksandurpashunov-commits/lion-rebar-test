from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import _apply_f006_printed_production_dimensions
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import ShapeDefinition


def test_batch_f006_uses_printed_master_equation_and_keeps_raw_dxf_for_audit() -> None:
    entities = [
        DxfEntityRecord("R1", "LINE", "ARM_Bars", {"start": [100, 500, 0], "end": [194, 500, 0]}),
        DxfEntityRecord("R2", "LINE", "ARM_Bars", {"start": [194, 500, 0], "end": [194, 534, 0]}),
        DxfEntityRecord("R3", "LINE", "ARM_Bars", {"start": [194, 534, 0], "end": [100, 534, 0]}),
        DxfEntityRecord("R4", "LINE", "ARM_Bars", {"start": [100, 534, 0], "end": [100, 500, 0]}),
        DxfEntityRecord("H1", "LINE", "ARM_Bars", {"start": [180, 534, 0], "end": [194, 548, 0]}),
        DxfEntityRecord("H2", "LINE", "ARM_Bars", {"start": [194, 525, 0], "end": [180, 511, 0]}),
        DxfEntityRecord("DW", "TEXT", "koti", {"insert": [147, 492, 0], "rotation": 0}, "50"),
        DxfEntityRecord("DH", "TEXT", "koti", {"insert": [92, 517, 0], "rotation": 90}, "20"),
        DxfEntityRecord("DK", "TEXT", "koti", {"insert": [198, 541, 0], "rotation": 45}, "10"),
    ]
    row = {
        "annotation_handle": "T1",
        "annotation_text": "19N8x160",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 160.0,
        "master_shape_code": "F006",
        "candidate_handles": ["R1", "R2", "R3", "R4", "H1", "H2"],
        "geometry_length_raw": 296.0,
        "effective_geometry_length_cm": 296.0,
        "raw_numeric_difference": 136.0,
        "numeric_difference": 0.0,
        "canonical_segment_lengths_cm": [34.0, 94.0, 34.0, 94.0, 20.0, 20.0],
        "candidate_notes": [],
        "analysis_warnings": [],
        "review_reason_codes": [],
        "batch_status": "СИЛНО СТРЕМЕ F006",
    }

    _apply_f006_printed_production_dimensions(
        [row],
        entities=entities,
        shape_definitions=(ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED"),),
    )

    assert row["canonical_segment_lengths_cm"] == [10.0, 50.0, 20.0, 50.0, 20.0, 10.0]
    assert row["effective_geometry_length_cm"] == 160.0
    assert row["raw_numeric_difference"] == 136.0
    assert row["numeric_difference"] == 0.0
    assert row["f006_printed_dimensions_resolved"] is True
    assert "2×(50+20+10)" in row["contextual_rule_note"]


def test_batch_f006_keeps_two_centimetre_designer_rounding_visible() -> None:
    entities = [
        DxfEntityRecord("R1", "LINE", "ARM_Bars", {"start": [0, 0, 0], "end": [52, 0, 0]}),
        DxfEntityRecord("R2", "LINE", "ARM_Bars", {"start": [52, 0, 0], "end": [52, 34, 0]}),
        DxfEntityRecord("R3", "LINE", "ARM_Bars", {"start": [52, 34, 0], "end": [0, 34, 0]}),
        DxfEntityRecord("R4", "LINE", "ARM_Bars", {"start": [0, 34, 0], "end": [0, 0, 0]}),
        DxfEntityRecord("H1", "LINE", "ARM_Bars", {"start": [42, 34, 0], "end": [52, 44, 0]}),
        DxfEntityRecord("H2", "LINE", "ARM_Bars", {"start": [52, 24, 0], "end": [42, 14, 0]}),
        DxfEntityRecord("DW", "TEXT", "koti", {"insert": [26, -8, 0], "rotation": 0}, "29"),
        DxfEntityRecord("DH", "TEXT", "koti", {"insert": [-8, 17, 0], "rotation": 90}, "20"),
        DxfEntityRecord("DK", "TEXT", "koti", {"insert": [56, 40, 0], "rotation": 45}, "10"),
    ]
    row = {
        "annotation_handle": "T1",
        "annotation_text": "60N8x120",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 120.0,
        "master_shape_code": "F006",
        "candidate_handles": ["R1", "R2", "R3", "R4", "H1", "H2"],
        "geometry_length_raw": 212.0,
        "raw_numeric_difference": 92.0,
        "candidate_notes": [],
        "analysis_warnings": [],
        "review_reason_codes": [],
        "batch_status": "СИЛНО СТРЕМЕ F006",
    }

    _apply_f006_printed_production_dimensions(
        [row],
        entities=entities,
        shape_definitions=(ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED"),),
    )

    assert row["canonical_segment_lengths_cm"] == [10.0, 29.0, 20.0, 29.0, 20.0, 10.0]
    assert row["effective_geometry_length_cm"] == 118.0
    assert row["numeric_difference"] == 2.0
    assert "проектантско закръгляне" in row["contextual_rule_note"]
