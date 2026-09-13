from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.engine.legacy_import import LegacyImportError, import_shape_draft_json
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engine.project_shape_candidate import (
    CANDIDATE_SCHEMA,
    build_project_shape_candidate,
    suggest_next_shape_code,
    write_project_shape_candidate,
)


def _payload(
    points: tuple[tuple[float, float], ...],
    *,
    annotation_handle: str = "A1C",
    physical_connected: bool = True,
    near_gap_healed: bool = False,
    training_eligible: bool = True,
) -> dict:
    return {
        "mode": "DRAFT",
        "shape_code": None,
        "shape_name": "СТЪЛБИЩНО РАМО — КАНДИДАТ",
        "reason": "Липсва MASTER форма",
        "note": "Проверена избрана геометрия.",
        "save_training_example": training_eligible,
        "geometry": {
            "handles": ["9C6", "9C7"],
            "entity_types": ["LINE"],
            "layers": ["ARM-GORNA"],
            "points": points,
            "closed": False,
            "connected": True,
            "physical_connected": physical_connected,
            "branched": False,
            "segment_count": 2,
            "segment_lengths_cm": [100.0, 50.0],
            "turn_angles_deg": [-90.0],
            "total_length_cm": 150.0,
            "canonical_signature": "O|n=2|r=0.33333,0.66667|a=-90.00",
            "component_count": 1 if physical_connected else 2,
            "inferred_composite": False,
            "near_gap_healed": near_gap_healed,
            "near_gap_bridge_gap_raw": 1.4 if near_gap_healed else None,
            "near_gap_bridge_handles": ["9C6", "9C7"] if near_gap_healed else [],
            "training_eligible": training_eligible,
            "warnings": ["Контролно предупреждение."],
        },
        "dimensions": {
            "source": "ANNOTATION",
            "production_length_cm": 150.0,
            "production_segment_lengths_cm": [],
            "measured_geometry_length_cm": 150.0,
        },
        "annotation": {
            "handle": annotation_handle,
            "text": "8N10x150",
            "count": 8,
            "diameter_mm": 10,
            "declared_length_cm": 150.0,
        },
    }


def test_next_code_accounts_for_engine_and_unimported_project_candidates():
    shapes = [
        ShapeDefinition(code="F001", name="ПРАВ"),
        ShapeDefinition(code="F018", name="ПОСЛЕДНА"),
    ]

    assert suggest_next_shape_code(shapes) == "F019"
    assert suggest_next_shape_code(shapes, reserved_codes=["F019", "F020"]) == "F021"


def test_safe_candidate_is_directly_importable_as_engine_studio_draft(tmp_path: Path):
    pack = build_project_shape_candidate(
        _payload(((10.0, 10.0), (110.0, 10.0), (110.0, -40.0))),
        existing_shapes=[ShapeDefinition(code="F018", name="EXISTING")],
        project_name="TEST_PROJECT",
        created_at="2026-07-27T12:00:00+03:00",
    )

    assert pack["schema"] == CANDIDATE_SCHEMA
    candidate = pack["candidate"]
    assert candidate["suggested_shape_code"] == "F019"
    assert candidate["never_publish_automatically"] is True
    assert candidate["engine_studio_import"]["ready"] is True
    assert candidate["exact_dxf_geometry"]["ordered_points"] == [
        [10.0, 10.0],
        [110.0, 10.0],
        [110.0, -40.0],
    ]
    assert candidate["production"]["length_cm"] == 150.0
    assert candidate["normalized_topology"]["segment_count"] == 2
    assert candidate["normalized_topology"]["bend_directions"] == ["RIGHT"]

    path = write_project_shape_candidate(tmp_path, pack)
    imported = import_shape_draft_json(path)
    assert len(imported) == 1
    assert imported[0].code == "F019"
    assert imported[0].state == "DRAFT"
    assert imported[0].mirror_allowed is False
    assert [segment.fixed_length_cm for segment in imported[0].segments] == [None, None]
    assert imported[0].segments[0].bend_after_deg == 90.0
    assert imported[0].segments[0].bend_direction == "RIGHT"
    assert imported[0].recognition["reference_segment_profiles"][0][
        "segment_lengths_cm"
    ] == [100.0, 50.0]
    assert json.loads(path.read_text(encoding="utf-8"))["candidate"][
        "candidate_id"
    ] == candidate["candidate_id"]


def test_gap_healed_candidate_is_saved_but_cannot_be_imported(tmp_path: Path):
    pack = build_project_shape_candidate(
        _payload(
            ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0)),
            physical_connected=False,
            near_gap_healed=True,
            training_eligible=False,
        ),
        existing_shapes=[ShapeDefinition(code="F018", name="EXISTING")],
        project_name="TEST_PROJECT",
    )

    decision = pack["candidate"]["engine_studio_import"]
    assert decision["ready"] is False
    assert "PHYSICALLY_DISCONNECTED_GEOMETRY" in decision["blocker_codes"]
    assert "HEALED_GAP_IS_NOT_TRAINING_TRUTH" in decision["blocker_codes"]
    assert pack["forms"] == []

    path = write_project_shape_candidate(tmp_path, pack)
    assert path.is_file()
    with pytest.raises(LegacyImportError, match="не съдържа разпознаваема форма"):
        import_shape_draft_json(path)


def test_curved_candidate_is_preserved_but_not_flattened_into_line_draft(
    tmp_path: Path,
):
    payload = _payload(((0.0, 0.0), (100.0, 0.0), (100.0, 50.0)))
    payload["geometry"]["contains_curves"] = True
    payload["geometry"]["curve_segment_count"] = 1

    pack = build_project_shape_candidate(
        payload,
        existing_shapes=[ShapeDefinition(code="F018", name="EXISTING")],
        project_name="TEST_PROJECT",
    )

    candidate = pack["candidate"]
    assert candidate["exact_dxf_geometry"]["contains_curves"] is True
    assert (
        "CURVED_GEOMETRY_REQUIRES_ENGINEERING"
        in candidate["engine_studio_import"]["blocker_codes"]
    )
    assert pack["forms"] == []
    path = write_project_shape_candidate(tmp_path, pack)
    with pytest.raises(LegacyImportError, match="не съдържа разпознаваема форма"):
        import_shape_draft_json(path)


def test_normalized_candidate_points_ignore_rotation_mirror_and_direction():
    base = _payload(((0.0, 0.0), (100.0, 0.0), (100.0, 50.0)))
    transformed = _payload(
        ((-50.0, 100.0), (0.0, 100.0), (0.0, 0.0)),
        annotation_handle="A2",
    )

    base_pack = build_project_shape_candidate(base, project_name="P")
    transformed_pack = build_project_shape_candidate(transformed, project_name="P")

    assert (
        base_pack["candidate"]["normalized_topology"]["canonical_points_unit_total"]
        == transformed_pack["candidate"]["normalized_topology"][
            "canonical_points_unit_total"
        ]
    )
    assert (
        base_pack["candidate"]["normalized_topology"][
            "parameter_invariant_topology_signature"
        ]
        == transformed_pack["candidate"]["normalized_topology"][
            "parameter_invariant_topology_signature"
        ]
    )

    different_lengths = _payload(
        ((0.0, 0.0), (220.0, 0.0), (220.0, 15.0)),
        annotation_handle="A3",
    )
    different_pack = build_project_shape_candidate(different_lengths, project_name="P")
    assert (
        base_pack["candidate"]["normalized_topology"][
            "parameter_invariant_topology_signature"
        ]
        == different_pack["candidate"]["normalized_topology"][
            "parameter_invariant_topology_signature"
        ]
    )


def test_physically_closed_candidate_keeps_closed_topology_on_import(tmp_path: Path):
    payload = _payload(
        (
            (0.0, 0.0),
            (40.0, 0.0),
            (40.0, 25.0),
            (0.0, 25.0),
            (0.0, 0.0),
        )
    )
    payload["geometry"].update(
        {
            "closed": True,
            "segment_count": 4,
            "segment_lengths_cm": [40.0, 25.0, 40.0, 25.0],
            "turn_angles_deg": [90.0, 90.0, 90.0, 90.0],
            "total_length_cm": 130.0,
        }
    )
    payload["dimensions"]["production_length_cm"] = 130.0

    pack = build_project_shape_candidate(payload, project_name="P")
    path = write_project_shape_candidate(tmp_path, pack)
    shape = import_shape_draft_json(path)[0]

    assert pack["candidate"]["engine_studio_import"]["ready"] is True
    assert shape.closed is True
    assert len(shape.segments) == 4
    assert all(segment.bend_after_deg == 90.0 for segment in shape.segments)
