from __future__ import annotations

import pytest

from lion_rebar.dxf.batch_geometry_report import (
    _apply_dimension_derived_production_sizes,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.auto_gate import (
    is_deterministic_production_source,
    validate_production_segment_evidence,
)


def _annotation(handle: str, diameter_mm: int, length_cm: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "BLOCK_ATTR_TEXT",
        "ARM_TXT",
        {"insert": [0.0, 0.0, 0.0]},
        f"4N{diameter_mm}x{length_cm:g}",
    )


def _printed_dimension(
    handle: str,
    value_cm: float,
    *,
    x: float,
    rotation: float = 0.0,
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "TEXT",
        "ARM_TXT1",
        {"insert": [x, 5.0, 0.0], "rotation": rotation},
        f"{value_cm:g}",
    )


def _foreign_dimension(handle: str, value_cm: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "DIMENSION",
        "koti",
        {
            "measurement": value_cm,
            "text_midpoint": [0.0, 0.0, 0.0],
            "defpoint2": [0.0, 0.0, 0.0],
            "defpoint3": [value_cm, 0.0, 0.0],
        },
        None,
    )


@pytest.mark.parametrize(
    (
        "annotation_handle",
        "diameter_mm",
        "declared_length_cm",
        "candidate_handles",
        "main_dimension_handle",
        "main_length_cm",
    ),
    [
        (
            "11F21::BJ-FI-L",
            18,
            700.0,
            ("11F10", "11F4A", "11F53"),
            "11F51",
            660.0,
        ),
        (
            "11FD8::BJ-FI-L",
            18,
            700.0,
            ("11FC7", "12001", "1200A"),
            "12008",
            660.0,
        ),
        (
            "1208F::BJ-FI-L",
            18,
            700.0,
            ("1207E", "120B8", "120C1"),
            "120BF",
            660.0,
        ),
        (
            "12146::BJ-FI-L",
            18,
            700.0,
            ("12135", "1216F", "12178"),
            "12176",
            660.0,
        ),
        (
            "1225D::BJ-FI-L",
            16,
            640.0,
            ("1224C", "12283", "1228C"),
            "1228A",
            600.0,
        ),
        (
            "12311::BJ-FI-L",
            16,
            640.0,
            ("12300", "12337", "12340"),
            "1233E",
            600.0,
        ),
        (
            "123C4::BJ-FI-L",
            16,
            640.0,
            ("123B3", "123EA", "123F3"),
            "123F1",
            600.0,
        ),
    ],
    ids=["11F21", "11FD8", "1208F", "12146", "1225D", "12311", "123C4"],
)
def test_r4_beam_f003_uses_audited_equal_arm_dimensions_before_radius_fallback(
    annotation_handle: str,
    diameter_mm: int,
    declared_length_cm: float,
    candidate_handles: tuple[str, str, str],
    main_dimension_handle: str,
    main_length_cm: float,
) -> None:
    expected = [20.0, main_length_cm, 20.0]
    row = {
        "annotation_handle": annotation_handle,
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": declared_length_cm,
        "master_shape_code": "F003",
        "candidate_handles": list(candidate_handles),
        "canonical_segment_lengths_cm": expected,
        "canonical_turn_angles_deg": [90.0, 90.0],
        "dimension_evidence_applied": True,
        "dimension_text_handles": [main_dimension_handle],
        "dimension_resolution_strategy": "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS",
        "dimension_total_difference_cm": 0.0,
        "candidate_notes": [],
        "review_reason_codes": [],
        "global_conflict": False,
        "ownership_conflict": False,
        "master_ambiguous_shape_codes": [],
    }
    entities = [
        _annotation(annotation_handle, diameter_mm, declared_length_cm),
        _printed_dimension(main_dimension_handle, main_length_cm, x=100.0),
        # These values are deliberately close to the annotation and close a
        # different F003 equation. They model the old proximity-only result
        # (40 + middle + 40) and must not overwrite matcher-owned A-E evidence.
        _foreign_dimension(f"FOREIGN-END-{annotation_handle}", 40.0),
        _foreign_dimension(
            f"FOREIGN-MAIN-{annotation_handle}", declared_length_cm - 80.0
        ),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 1
    assert row["production_segments_cm"] == expected
    assert "dimension_derived_evidence" not in row

    evidence = row["production_segment_evidence"]
    assert evidence["source"] == "MATCHER_LOCAL_A_E_CANONICAL"
    assert evidence["segments"] == expected
    assert evidence["candidate_handles"] == list(candidate_handles)
    assert evidence["dimension_handles"] == [main_dimension_handle]
    assert evidence["dimension_measurements_cm"] == [
        {"handle": main_dimension_handle, "measurement_cm": main_length_cm},
    ]
    assert evidence["dimension_resolution_strategy"] == (
        "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS"
    )
    assert validate_production_segment_evidence(row)
    assert is_deterministic_production_source(row)
