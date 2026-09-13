from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import _apply_beam_engineering_shape_law
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import ShapeDefinition


def _entities() -> list[DxfEntityRecord]:
    return [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "0", {"insert": [100, 0, 0]}, "АРМИРОВКА ГРЕДИ"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "2N8x435"),
    ]


def test_stair_shape_inside_beam_frame_is_normalized_to_f004_at_90_degrees() -> None:
    row = {
        "annotation_handle": "T1",
        "annotation_text": "2N8x435",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 435.0,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "master_shape_code": "F013",
        "master_shape_name": "СТЪЛБИЩНО РАМО 2",
        "master_conflict": False,
        "master_ambiguous_shape_codes": [],
        "canonical_segment_lengths_cm": [405.0, 30.0],
        "canonical_turn_angles_deg": [29.0],
        "candidate_notes": [],
        "analysis_warnings": [],
        "review_reason_codes": [],
    }
    entities = _entities()
    _apply_beam_engineering_shape_law(
        [row],
        annotations_by_handle={"T1": entities[-1]},
        entities=entities,
        shape_definitions=(
            ShapeDefinition(code="F004", name="ПРЪТ С ЕДИН ВИНКЕЛ", state="LOCKED"),
            ShapeDefinition(code="F013", name="СТЪЛБИЩНО РАМО 2", state="LOCKED"),
        ),
        policy=RecognitionPolicy(),
    )

    assert row["master_shape_code"] == "F004"
    assert row["beam_original_master_shape_code"] == "F013"
    assert row["canonical_turn_angles_deg"] == [90.0]
    assert row["contextual_rule_code"] == "BEAM_ENGINEERING_90_F004"


def test_beam_stair_shape_is_blocked_when_printed_segments_do_not_close_total() -> None:
    row = {
        "annotation_handle": "T1",
        "annotation_text": "2N8x435",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 435.0,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "master_shape_code": "F013",
        "canonical_segment_lengths_cm": [390.0, 30.0],
        "candidate_notes": [],
        "review_reason_codes": [],
    }
    entities = _entities()
    _apply_beam_engineering_shape_law(
        [row],
        annotations_by_handle={"T1": entities[-1]},
        entities=entities,
        shape_definitions=(
            ShapeDefinition(code="F004", name="ПРЪТ С ЕДИН ВИНКЕЛ", state="LOCKED"),
            ShapeDefinition(code="F013", name="СТЪЛБИЩНО РАМО 2", state="LOCKED"),
        ),
        policy=RecognitionPolicy(),
    )

    assert row["master_shape_code"] == "F013"
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert "BEAM_STAIR_SHAPE_FORBIDDEN" in row["review_reason_codes"]
