from __future__ import annotations

from copy import deepcopy

from lion_rebar.dxf.batch_geometry_report import (
    _apply_dimension_derived_production_sizes,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.auto_gate import (
    auto_classification_gate_passes,
    is_deterministic_production_source,
    validate_production_segment_evidence,
)


def _annotation(handle: str, length: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "BLOCK_ATTR_TEXT",
        "ARM_TXT",
        {"insert": [0.0, 0.0, 0.0]},
        f"1N10x{length:g}",
    )


def _text(handle: str, value: float, x: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "TEXT",
        "koti",
        {"insert": [x, 5.0, 0.0]},
        f"{value:g}",
    )


def _dimension(
    handle: str,
    value: float,
    first: tuple[float, float],
    second: tuple[float, float],
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "DIMENSION",
        "koti",
        {
            "measurement": value,
            "text_midpoint": [
                (first[0] + second[0]) / 2.0,
                (first[1] + second[1]) / 2.0,
                0.0,
            ],
            "defpoint2": [first[0], first[1], 0.0],
            "defpoint3": [second[0], second[1], 0.0],
        },
        None,
    )


def _matcher_f004_row() -> dict:
    return {
        "annotation_handle": "ANN::BJ-FI-L",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 335.0,
        "master_shape_code": "F004",
        "candidate_handles": ["BAR-A", "BAR-B"],
        "canonical_segment_lengths_cm": [285.0, 50.0],
        "canonical_turn_angles_deg": [90.0],
        "dimension_evidence_applied": True,
        "dimension_text_handles": ["OWN-LONG", "OWN-HOOK"],
        "dimension_resolution_strategy": "A_E_DIRECT_SEGMENTS",
        "dimension_total_difference_cm": 0.0,
        "candidate_notes": [],
        "review_reason_codes": [],
        "global_conflict": False,
        "ownership_conflict": False,
        "master_ambiguous_shape_codes": [],
    }


def test_matcher_owned_canonical_beats_foreign_radius_dimensions() -> None:
    row = _matcher_f004_row()
    entities = [
        _annotation("ANN::BJ-FI-L", 335.0),
        _text("OWN-LONG", 285.0, 120.0),
        _text("OWN-HOOK", 50.0, 280.0),
        # Close enough for the legacy radius fallback, but not matcher-owned.
        _dimension("FOREIGN-25", 25.0, (0.0, 0.0), (25.0, 0.0)),
        _dimension("FOREIGN-310", 310.0, (0.0, 0.0), (310.0, 0.0)),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 1
    assert row["production_segments_cm"] == [285.0, 50.0]
    assert row["production_segment_evidence"]["source"] == (
        "MATCHER_LOCAL_A_E_CANONICAL"
    )
    assert "dimension_derived_evidence" not in row
    assert validate_production_segment_evidence(row)
    assert is_deterministic_production_source(row)


def test_matcher_dimension_handle_uses_dimension_measurement_when_text_is_empty() -> None:
    row = _matcher_f004_row()
    entities = [
        _annotation("ANN::BJ-FI-L", 335.0),
        _dimension("OWN-LONG", 284.99, (0.0, 0.0), (285.0, 0.0)),
        _dimension("OWN-HOOK", 50.01, (285.0, 0.0), (285.0, 50.0)),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 1
    assert row["production_segments_cm"] == [285.0, 50.0]
    assert validate_production_segment_evidence(row)


def test_conflicting_authoritative_vectors_fail_closed_without_radius_repair() -> None:
    row = _matcher_f004_row()
    row["production_segments_cm"] = [300.0, 35.0]
    row["dimension_derived_evidence"] = {"confidence": "HIGH"}
    entities = [
        _annotation("ANN::BJ-FI-L", 335.0),
        _text("OWN-LONG", 285.0, 120.0),
        _text("OWN-HOOK", 50.0, 280.0),
        _dimension("FOREIGN-35", 35.0, (0.0, 0.0), (35.0, 0.0)),
        _dimension("FOREIGN-300", 300.0, (0.0, 0.0), (300.0, 0.0)),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 0
    assert row["production_export_blocked"] is True
    assert row["production_export_block_reason"] == (
        "CONFLICTING_PRODUCTION_SEGMENT_EVIDENCE"
    )
    assert "production_segments_cm" not in row
    assert "production_segment_evidence" not in row


def test_owned_f006_empty_text_dimensions_produce_valid_contract() -> None:
    row = {
        "annotation_handle": "ST::BJ-FI-L",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 130.0,
        "master_shape_code": "F006",
        "candidate_handles": ["CAGE"],
        "candidate_notes": [],
        "review_reason_codes": [],
    }
    entities = [
        _annotation("ST::BJ-FI-L", 130.0),
        DxfEntityRecord(
            "CAGE",
            "LWPOLYLINE",
            "ARM_Bars",
            {
                "points": [
                    [0.0, 0.0, 0.0],
                    [35.0, 0.0, 0.0],
                    [35.0, 20.0, 0.0],
                    [0.0, 20.0, 0.0],
                ]
            },
        ),
        _dimension("D-W", 34.9897, (0.0, 0.0), (35.0, 0.0)),
        _dimension("D-H", 20.01, (35.0, 0.0), (35.0, 20.0)),
        _dimension("D-K", 9.999, (0.0, 0.0), (0.0, 0.0)),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 1
    assert row["production_segments_cm"] == [10.0, 20.0, 35.0, 20.0, 35.0, 10.0]
    assert row["production_segment_evidence"]["source"] == (
        "F006_OWNED_CONTOUR_DIMENSIONS"
    )
    assert validate_production_segment_evidence(row)
    assert is_deterministic_production_source(row)


def test_rounded_f006_canonical_beats_foreign_nearby_dimensions() -> None:
    canonical = [10.0, 34.0, 30.0, 34.0, 30.0, 10.0]
    row = {
        "annotation_handle": "ROUND::BJ-FI-L",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 150.0,
        "master_shape_code": "F006",
        "candidate_handles": ["TOP", "RIGHT", "BOTTOM", "LEFT", "K1", "K2"],
        "candidate_basis": "СЪСТАВНО ЗАКРЪГЛЕНО СТРЕМЕ",
        "canonical_segment_lengths_cm": canonical,
        "canonical_turn_angles_deg": [90.0, 90.0, 90.0, 90.0],
        "f006_production_status": "F006_PRODUCTION_EXACT",
        "f006_printed_dimensions_resolved": True,
        "local_f006_printed_dimension_handles": [
            "OWN-W",
            "OWN-H",
            "OWN-K",
        ],
        "local_detail_exclusive_dimension_handles": [
            "OWN-W",
            "OWN-H",
            "OWN-K",
            "FOREIGN",
        ],
        "candidate_notes": [],
        "review_reason_codes": [],
    }
    entities = [
        _annotation("ROUND::BJ-FI-L", 150.0),
        _text("OWN-W", 34.0, 5.0),
        _text("OWN-H", 30.0, 10.0),
        _dimension("OWN-K", 9.999, (0.0, 0.0), (10.0, 0.0)),
        _dimension("FOREIGN", 25.0, (0.0, 0.0), (25.0, 0.0)),
    ]

    assert _apply_dimension_derived_production_sizes([row], entities) == 1
    assert row["production_segments_cm"] == canonical
    assert row["production_segment_evidence"]["source"] == (
        "F006_ROUNDED_LOCAL_PRINTED"
    )
    assert "FOREIGN" not in row["production_segment_evidence"]["dimension_handles"]
    assert validate_production_segment_evidence(row)

    forged_owner = deepcopy(row)
    forged_owner["local_f006_printed_dimension_handles"] = [
        "OWN-W",
        "OWN-H",
        "FOREIGN",
    ]
    assert not validate_production_segment_evidence(forged_owner)

    malformed = deepcopy(row)
    malformed["production_segments_cm"] = canonical[:-1]
    malformed["production_segment_evidence"]["segments"] = canonical[:-1]
    malformed["canonical_segment_lengths_cm"] = canonical[:-1]
    assert not validate_production_segment_evidence(malformed)


def test_regular_f006_printed_exact_producer_builds_valid_contract() -> None:
    canonical = [10.0, 50.0, 20.0, 50.0, 20.0, 10.0]
    row = {
        "annotation_handle": "PRINTED::BJ-FI-L",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 160.0,
        "master_shape_code": "F006",
        "candidate_handles": ["R1", "R2", "R3", "R4", "K1", "K2"],
        "canonical_segment_lengths_cm": canonical,
        "canonical_turn_angles_deg": [90.0, 90.0, 90.0, 90.0],
        "f006_production_status": "F006_PRODUCTION_EXACT",
        "f006_printed_dimensions_resolved": True,
        "contextual_rule_code": "F006_PRINTED_MASTER_EQUATION",
        "candidate_notes": [],
        "review_reason_codes": [],
    }

    assert _apply_dimension_derived_production_sizes(
        [row], [_annotation("PRINTED::BJ-FI-L", 160.0)]
    ) == 1
    assert row["production_segments_cm"] == canonical
    assert row["production_segment_evidence"]["source"] == (
        "F006_PRINTED_MASTER_EXACT"
    )
    assert validate_production_segment_evidence(row)
    assert is_deterministic_production_source(row)


def test_truthy_legacy_mapping_and_status_cannot_open_auto_gate() -> None:
    row = {
        "annotation_handle": "FORGED::BJ-FI-L",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "matcher_disposition": "MANUAL",
        "master_shape_code": "F006",
        "declared_length_cm": 130.0,
        "f006_production_status": "F006_PRODUCTION_DERIVED",
        "production_segments_cm": [10.0, 20.0, 35.0, 20.0, 35.0, 10.0],
        "dimension_derived_evidence": {"confidence": "HIGH"},
        "production_segment_evidence": {"source": "TRUTHY"},
    }

    assert not validate_production_segment_evidence(row)
    assert not is_deterministic_production_source(row)
    assert not auto_classification_gate_passes(row)
