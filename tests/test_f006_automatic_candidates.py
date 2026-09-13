from __future__ import annotations

from lion_rebar.dxf.geometry_candidates import (
    F006_CONTEXTUAL_RULE_CODE,
    F006_HOOKED_FAMILY_CONFIDENCE,
    F006_STRONG_CONFIDENCE,
    find_geometry_candidates,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.review_center_model import F006_STRONG_STATUS, f006_bulk_eligible, queue_rows


def _line(handle: str, start: tuple[float, float], end: tuple[float, float]) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer="ARM_Bars",
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
    )


def _annotation(text: str = "11N8x130") -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text=text,
        geometry={"insert": [20.0, -18.0, 0.0], "height": 3.0},
    )


def _f006_shape() -> ShapeDefinition:
    return ShapeDefinition(
        code="F006",
        name="СТРЕМЕ (БИГЛА)",
        category="ГРЕДИ",
        state="LOCKED",
        revision=3,
        active=True,
        closed=True,
    )


def _real_stirrup_lines() -> list[DxfEntityRecord]:
    return [
        _line("TOP", (3.0, 70.0), (37.0, 70.0)),
        _line("BOTTOM", (3.0, 0.0), (37.0, 0.0)),
        _line("LEFT", (0.0, 3.0), (0.0, 67.0)),
        _line("RIGHT", (40.0, 3.0), (40.0, 67.0)),
        _line("KEY1", (0.0, 64.0), (14.1421356, 49.8578644)),
        _line("KEY2", (6.0, 70.0), (20.1421356, 55.8578644)),
    ]


def test_six_separate_lines_become_a_strong_f006_candidate():
    annotation = _annotation()
    entities = [annotation, *_real_stirrup_lines()]

    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=[_f006_shape()],
    )

    assert analysis.candidates
    top = analysis.candidates[0]
    assert set(top.handles) == {"TOP", "BOTTOM", "LEFT", "RIGHT", "KEY1", "KEY2"}
    assert top.master_shape_code == "F006"
    assert top.segment_count == 6
    assert top.confidence == F006_STRONG_CONFIDENCE
    assert top.contextual_rule_code == F006_CONTEXTUAL_RULE_CODE
    assert round(top.geometry_length_raw, 3) == 236.0
    assert round(top.effective_geometry_length_cm or 0.0, 3) == 236.0
    assert round(top.raw_numeric_difference or 0.0, 3) == 106.0
    assert top.numeric_difference == 0.0


def test_random_six_lines_do_not_become_f006():
    annotation = _annotation()
    entities = [
        annotation,
        _line("A", (0.0, 0.0), (10.0, 0.0)),
        _line("B", (30.0, 0.0), (45.0, 0.0)),
        _line("C", (0.0, 30.0), (0.0, 50.0)),
        _line("D", (70.0, 10.0), (75.0, 35.0)),
        _line("E", (100.0, 100.0), (110.0, 108.0)),
        _line("F", (130.0, 80.0), (145.0, 83.0)),
    ]

    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=[_f006_shape()],
    )

    assert not analysis.candidates


def test_hooked_curved_f006_is_strong_family_but_not_composite_auto_candidate():
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_count": 33,
        "diameter_mm": 8,
        "batch_status": "СИЛНО СТРЕМЕ F006",
        "matcher_disposition": "MANUAL",
        "candidate_handles": ["POLY"],
        "segment_count": 6,
        "master_shape_code": "F006",
        "contextual_rule_code": None,
        "candidate_confidence": F006_HOOKED_FAMILY_CONFIDENCE,
        "hooked_closure": True,
        "data_quality_flags": [],
        "global_conflict": False,
        "master_conflict": False,
        "master_ambiguous_shape_codes": [],
    }

    assert f006_bulk_eligible(row) is False


def test_f006_review_queue_and_bulk_gate_are_separate():
    row = {
        "annotation_handle": "ANN",
        "annotation_text": "11N8x130",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 11,
        "diameter_mm": 8,
        "batch_status": F006_STRONG_STATUS,
        "global_conflict": False,
        "candidate_handles": ["TOP", "BOTTOM", "LEFT", "RIGHT", "KEY1", "KEY2"],
        "segment_count": 6,
        "master_shape_code": "F006",
        "contextual_rule_code": F006_CONTEXTUAL_RULE_CODE,
        "effective_geometry_length_cm": 236.0,
        "data_quality_flags": [],
    }

    assert f006_bulk_eligible(row) is True
    assert queue_rows([row], queue_code="F006_STRONG") == [row]

    row["matcher_disposition"] = "MANUAL"
    assert f006_bulk_eligible(row) is False

    row["matcher_disposition"] = "UNKNOWN"
    assert f006_bulk_eligible(row) is False

    row["matcher_disposition"] = "CONTROL"
    assert f006_bulk_eligible(row) is True
