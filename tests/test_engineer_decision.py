from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.engineer_decision import (
    ACTION_CONFIRMATION,
    ACTION_CORRECTION,
    ACTION_OVERRIDE,
    classify_engineer_action,
    engineer_reason_from_problem,
    parse_segment_text,
    save_engineer_decision,
)


def _row() -> dict:
    return {
        "annotation_handle": "A1",
        "annotation_text": "60N8x94",
        "annotation_count": 60,
        "diameter_mm": 8.0,
        "declared_length_cm": 94.0,
        "master_shape_code": "F005",
        "master_shape_name": "ФИБА",
        "candidate_handles": ["G1", "G2", "G3"],
        "candidate_layer": "ARM_Bars",
        "candidate_basis": "MATCHER",
        "canonical_segment_lengths_cm": [40.0, 14.0, 40.0],
        "canonical_turn_angles_deg": [-90.0, -90.0],
    }


def test_segment_parser_accepts_common_engineering_notation() -> None:
    assert parse_segment_text("40 + 14 + 40") == (40.0, 14.0, 40.0)
    assert parse_segment_text("40/14/40") == (40.0, 14.0, 40.0)
    assert parse_segment_text("4,6 + 10") == (4.6, 10.0)
    assert parse_segment_text("40,14,40") == (40.0, 14.0, 40.0)


def test_engineer_action_separates_confirmation_correction_and_override() -> None:
    row = _row()
    assert classify_engineer_action(
        row,
        reason_code="CONFIRM_CORRECT",
        shape_code="F005",
        geometry_handles=["G1", "G2", "G3"],
        count=60,
        diameter_mm=8,
        production_length_cm=94,
        production_segments_cm=[40, 14, 40],
    ) == ACTION_CONFIRMATION
    assert classify_engineer_action(
        row,
        reason_code="WRONG_LENGTH",
        shape_code="F005",
        geometry_handles=["G1", "G2", "G3"],
        count=60,
        diameter_mm=8,
        production_length_cm=130,
        production_segments_cm=[40, 50, 40],
    ) == ACTION_CORRECTION
    assert classify_engineer_action(
        row,
        reason_code="WRONG_GEOMETRY",
        shape_code="F005",
        geometry_handles=["G9"],
        count=60,
        diameter_mm=8,
        production_length_cm=94,
        production_segments_cm=[40, 14, 40],
    ) == ACTION_OVERRIDE
    assert classify_engineer_action(
        row,
        reason_code="WRONG_OBJECT",
        shape_code="F005",
        geometry_handles=["G9"],
        count=60,
        diameter_mm=8,
        production_length_cm=94,
        production_segments_cm=[40, 14, 40],
    ) == ACTION_OVERRIDE


def test_diagnostic_reason_is_carried_into_engineer_resolution() -> None:
    assert engineer_reason_from_problem("WRONG_MASTER") == "WRONG_SHAPE"
    assert engineer_reason_from_problem("WRONG_OBJECT") == "WRONG_OBJECT"
    assert engineer_reason_from_problem("NO_GEOMETRY") == "NO_GEOMETRY"
    assert engineer_reason_from_problem("legacy-unknown") == "OTHER"
    assert engineer_reason_from_problem(None) is None


def test_save_engineer_decision_without_geometry_creates_complete_user_link(tmp_path: Path) -> None:
    payload = {
        "schema": "lion-rebar-engineer-decision/v1",
        "action_class": "OVERRIDE",
        "reason_code": "NO_GEOMETRY",
        "reason": "Липсва отделна геометрия",
        "note": "Производствената позиция е потвърдена от надписа и детайла.",
        "annotation": {
            "handle": "A1",
            "text": "60N8/s=20",
            "resolved_text": "60N8x94",
        },
        "original": {
            "count": 60,
            "diameter_mm": 8,
            "declared_length_cm": None,
            "shape_code": None,
            "geometry_handles": [],
        },
        "resolved": {
            "count": 60,
            "diameter_mm": 8,
            "production_length_cm": 94,
            "segment_lengths_cm": [40, 14, 40],
            "turn_angles_deg": [-90, -90],
            "shape_code": "F005",
            "shape_name": "ФИБА",
            "shape_revision": 3,
        },
        "geometry": {"handles": [], "layer": "", "basis": "OWNER_ENGINEER_DECISION"},
    }

    path = save_engineer_decision(tmp_path, payload)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["links"]) == 1
    link = data["links"][0]
    assert link["annotation_text"] == "60N8x94"
    assert link["annotation_count"] == 60
    assert link["diameter_mm"] == 8
    assert link["production_length_cm"] == 94
    assert link["production_segment_lengths_cm"] == [40.0, 14.0, 40.0]
    assert link["master_shape_code"] == "F005"
    assert link["geometry_handles"] == []
    assert link["geometry_optional_by_engineer"] is True
    assert link["approved_by"] == "OWNER_ENGINEER_DECISION"
    assert link["engineer_decision"]["action_class"] == "OVERRIDE"


def test_save_engineer_decision_rejects_duplicate_geometry_owner(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir(parents=True)
    (working / "approved_geometry_links.json").write_text(
        json.dumps(
            {
                "links": [
                    {
                        "annotation_handle": "OTHER",
                        "geometry_handles": ["G1"],
                    }
                ],
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "action_class": "OVERRIDE",
        "reason_code": "WRONG_GEOMETRY",
        "note": "Избирам правилната геометрия.",
        "annotation": {"handle": "A1", "text": "2N10x100"},
        "original": {},
        "resolved": {
            "count": 2,
            "diameter_mm": 10,
            "production_length_cm": 100,
            "segment_lengths_cm": [100],
            "shape_code": "F001",
            "shape_name": "ПРАВ ПРЪТ",
        },
        "geometry": {"handles": ["G1"], "layer": "ARM"},
    }
    with pytest.raises(ValueError, match="вече е одобрена"):
        save_engineer_decision(tmp_path, payload)


def test_engineer_action_treats_new_bend_dimensions_as_correction() -> None:
    row = _row()
    row["canonical_segment_lengths_cm"] = []
    assert classify_engineer_action(
        row,
        reason_code="CONFIRM_CORRECT",
        shape_code="F005",
        geometry_handles=["G1", "G2", "G3"],
        count=60,
        diameter_mm=8,
        production_length_cm=94,
        production_segments_cm=[40, 14, 40],
    ) == ACTION_CORRECTION


def test_engineer_numeric_validation_rejects_non_finite_values() -> None:
    from lion_rebar.engineer_decision import positive_float

    assert positive_float("nan") is None
    assert positive_float("inf") is None
    with pytest.raises(ValueError, match="крайно"):
        positive_float("nan", field="Обща дължина")
