from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import (
    _apply_unanimous_family_consensus,
    _apply_matcher_decisions,
    _apply_global_geometry_conflicts,
    _data_quality_flags,
    _data_quality_notes,
    _quantity_review_required,
    _resolve_global_geometry_ownership,
    _status_counts,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.text_parser import parse_rebar_text
from lion_rebar.engine.models import ShapeDefinition


def test_large_count_is_advisory_not_parser_error():
    parsed = parse_rebar_text("4362N10x97")

    assert _data_quality_flags(parsed) == []
    assert _quantity_review_required(parsed) is True
    notes = _data_quality_notes(parsed)
    assert len(notes) == 1
    assert "столчета" in notes[0]
    assert "фиби" in notes[0]
    assert "C-ове" in notes[0]


def test_normal_count_needs_no_quantity_review():
    parsed = parse_rebar_text("32N10x350")

    assert _data_quality_flags(parsed) == []
    assert _data_quality_notes(parsed) == []
    assert _quantity_review_required(parsed) is False


def test_no_candidate_inherits_only_unanimous_family_not_geometry() -> None:
    peers = [
        {
            "annotation_text": "25N8x135",
            "batch_status": "СИЛНО СТРЕМЕ F006",
            "candidate_handles": [f"G{index}"],
            "master_shape_code": "F006",
            "master_conflict": False,
            "master_ambiguous_shape_codes": [],
        }
        for index in range(3)
    ]
    missing = {
        "annotation_text": "25N8x135",
        "batch_status": "НЯМА КАНДИДАТ",
        "candidate_handles": [],
        "candidate_notes": [],
        "analysis_warnings": [],
    }

    _apply_unanimous_family_consensus([*peers, missing])

    assert missing["inferred_family_code"] == "F006"
    assert missing["inferred_family_evidence_count"] == 3
    assert missing["candidate_handles"] == []
    assert missing["batch_status"] == "НЯМА КАНДИДАТ"


def test_family_consensus_rejects_split_master_codes() -> None:
    rows = [
        {
            "annotation_text": "8N10x200",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "candidate_handles": [f"G{index}"],
            "master_shape_code": code,
            "master_conflict": False,
            "master_ambiguous_shape_codes": [],
        }
        for index, code in enumerate(("F003", "F003", "F005"), start=1)
    ]
    missing = {
        "annotation_text": "8N10x200",
        "batch_status": "НЯМА КАНДИДАТ",
        "candidate_handles": [],
    }

    _apply_unanimous_family_consensus([*rows, missing])

    assert "inferred_family_code" not in missing


def test_partially_shared_geometry_is_a_global_conflict():
    rows = [
        {
            "annotation_class": "ACTIONABLE",
            "annotation_handle": "A",
            "annotation_text": "8N10x225",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "candidate_handles": ["L1", "L2", "L3"],
            "candidate_notes": [],
            "analysis_warnings": [],
        },
        {
            "annotation_class": "ACTIONABLE",
            "annotation_handle": "B",
            "annotation_text": "8N12x483",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "candidate_handles": ["L1", "L3"],
            "candidate_notes": [],
            "analysis_warnings": [],
        },
    ]

    _apply_global_geometry_conflicts(rows)

    assert all(row["global_conflict"] for row in rows)
    assert all(
        "припокриваща се DXF геометрия" in row["candidate_notes"][0]
        for row in rows
    )


def _ownership_row(
    handle: str,
    text: str,
    candidate_handles: list[str],
    *,
    declared: float,
    geometry: float,
    raw_difference: float,
    matcher_difference: float | None = None,
) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": text,
        "declared_length_cm": declared,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": candidate_handles,
        "candidate_layer": "ARM-GORNA",
        "segment_count": len(candidate_handles),
        "geometry_length_raw": geometry,
        "effective_geometry_length_cm": declared,
        "raw_numeric_difference": raw_difference,
        "numeric_difference": (
            raw_difference if matcher_difference is None else matcher_difference
        ),
        "global_conflict": False,
        "distance_raw": 20.0,
        "candidate_notes": [],
        "analysis_warnings": [],
        "master_shape_code": "F016",
        "master_ambiguous_shape_codes": [],
        "review_reason_codes": [],
        "alternative_candidates": [],
    }


def test_exact_full_chain_owns_geometry_over_partial_nearby_label():
    rows = [
        _ownership_row(
            "REAL",
            "8N10x190",
            ["L1", "L2", "L3"],
            declared=190,
            geometry=190.78,
            raw_difference=0.78,
        ),
        _ownership_row(
            "FOREIGN",
            "8N8x150",
            ["L1", "L2"],
            declared=150,
            geometry=140.78,
            raw_difference=9.22,
        ),
    ]

    _resolve_global_geometry_ownership(rows, RecognitionPolicy())
    _apply_global_geometry_conflicts(rows)

    assert rows[0]["candidate_handles"] == ["L1", "L2", "L3"]
    assert rows[0]["global_conflict"] is False
    assert rows[1]["batch_status"] == "НЯМА КАНДИДАТ"
    assert rows[1]["candidate_handles"] == []
    assert rows[1]["ownership_rejected"] is True
    assert rows[1]["ownership_winner_annotations"] == ["8N10x190"]


def test_raw_length_exposes_foreign_schematic_candidate_even_when_matcher_delta_is_zero():
    rows = [
        _ownership_row(
            "REAL",
            "8N10x225",
            ["A", "B", "C"],
            declared=225,
            geometry=222.92,
            raw_difference=2.08,
        ),
        _ownership_row(
            "FOREIGN",
            "8N12x483",
            ["A", "C"],
            declared=483,
            geometry=210.92,
            raw_difference=272.08,
            matcher_difference=0.0,
        ),
    ]

    _resolve_global_geometry_ownership(rows, RecognitionPolicy())

    assert rows[0]["candidate_handles"] == ["A", "B", "C"]
    assert rows[1]["batch_status"] == "НЯМА КАНДИДАТ"
    rejected = rows[1]["ownership_rejected_candidate"]
    assert rejected["raw_numeric_difference"] == 272.08
    assert rejected["handles"] == ["A", "C"]


def _alternative_candidate(
    handles: list[str],
    *,
    rank: int = 2,
    confidence: str = "СИЛЕН КАНДИДАТ — НЕОДОБРЕН",
) -> dict:
    return {
        "rank": rank,
        "handles": handles,
        "layer": "ARM-GORNA-N8",
        "segment_count": len(handles),
        "geometry_length_raw": 150.0,
        "effective_geometry_length_cm": 150.0,
        "raw_numeric_difference": 0.0,
        "numeric_difference": 0.0,
        "distance_raw": 24.0,
        "centroid_distance_raw": 30.0,
        "layer_diameter_mm": 8.0,
        "diameter_layer_match": True,
        "score": 0.1,
        "score_gap": 0.2,
        "distance_gap_raw": 20.0,
        "confidence": confidence,
        "basis": "СВЪРЗАНИ СЕГМЕНТИ",
        "notes": ["Валидиран резервен кандидат"],
        "canonical_signature": "O|n=2",
        "orientation_invariant": True,
        "master_shape_code": "F004",
        "master_shape_name": "Г-образна",
        "master_ambiguous_shape_codes": [],
        "review_reason_codes": [],
    }


def test_foreign_top_candidate_recovers_free_alternative_as_manual_only():
    owner = _ownership_row(
        "OWNER",
        "8N10x190",
        ["L1", "L2", "L3"],
        declared=190,
        geometry=190.5,
        raw_difference=0.5,
    )
    foreign = _ownership_row(
        "FOREIGN",
        "8N8x150",
        ["L1", "L2"],
        declared=150,
        geometry=140,
        raw_difference=10,
    )
    foreign["alternative_candidates"] = [_alternative_candidate(["A1", "A2"])]

    rows = [owner, foreign]
    _resolve_global_geometry_ownership(rows, RecognitionPolicy())
    _apply_global_geometry_conflicts(rows)

    assert owner["candidate_handles"] == ["L1", "L2", "L3"]
    assert foreign["candidate_handles"] == ["A1", "A2"]
    assert foreign["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert foreign["ownership_reassigned"] is True
    assert foreign["ownership_reassignment_rank"] == 2
    assert "GLOBAL_ALTERNATIVE_RECOVERY" in foreign["review_reason_codes"]
    assert foreign["global_conflict"] is False
    assert foreign["ownership_rejected_candidate"]["handles"] == ["L1", "L2"]
    assert _status_counts(rows)["ownership_reassigned"] == 1


def test_occupied_alternative_is_not_recovered():
    owner = _ownership_row(
        "OWNER",
        "8N10x190",
        ["L1", "L2", "L3"],
        declared=190,
        geometry=190.5,
        raw_difference=0.5,
    )
    second_owner = _ownership_row(
        "SECOND",
        "8N8x150",
        ["A1", "A2"],
        declared=150,
        geometry=150,
        raw_difference=0,
    )
    foreign = _ownership_row(
        "FOREIGN",
        "8N8x150",
        ["L1", "L2"],
        declared=150,
        geometry=140,
        raw_difference=10,
    )
    foreign["alternative_candidates"] = [_alternative_candidate(["A1", "A2"])]

    rows = [owner, second_owner, foreign]
    _resolve_global_geometry_ownership(rows, RecognitionPolicy())

    assert foreign["batch_status"] == "НЯМА КАНДИДАТ"
    assert foreign["candidate_handles"] == []
    assert foreign["ownership_reassigned"] is False


def test_recovered_risky_alternative_never_becomes_manual_or_strong():
    owner = _ownership_row(
        "OWNER",
        "8N10x190",
        ["L1", "L2", "L3"],
        declared=190,
        geometry=190.5,
        raw_difference=0.5,
    )
    foreign = _ownership_row(
        "FOREIGN",
        "8N8x150",
        ["L1", "L2"],
        declared=150,
        geometry=140,
        raw_difference=10,
    )
    foreign["alternative_candidates"] = [
        _alternative_candidate(
            ["R1", "R2"],
            confidence="СЛАБ / РИСКОВ КАНДИДАТ",
        )
    ]

    _resolve_global_geometry_ownership([owner, foreign], RecognitionPolicy())

    assert foreign["ownership_reassigned"] is True
    assert foreign["batch_status"] == "РИСК"
    assert foreign["candidate_confidence"] == "СЛАБ / РИСКОВ КАНДИДАТ"


def test_equal_raw_length_bands_remain_a_visible_conflict():
    rows = [
        _ownership_row(
            "A1",
            "8N10x198",
            ["L1", "L2", "L3"],
            declared=198,
            geometry=195,
            raw_difference=3,
        ),
        _ownership_row(
            "A2",
            "8N10x198",
            ["L1", "L2", "L3"],
            declared=198,
            geometry=195,
            raw_difference=3,
        ),
    ]

    _resolve_global_geometry_ownership(rows, RecognitionPolicy())
    _apply_global_geometry_conflicts(rows)

    assert all(row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД" for row in rows)
    assert all(row["global_conflict"] for row in rows)


def test_exact_band_conflict_is_resolved_only_when_three_margins_agree():
    owner = _ownership_row(
        "OWNER",
        "8N10x1000",
        ["L1", "L2", "L3"],
        declared=1000,
        geometry=1000,
        raw_difference=0,
    )
    owner.update({"distance_raw": 10.0, "candidate_score": 0.10})
    foreign = _ownership_row(
        "FOREIGN",
        "8N10x1004",
        ["L1", "L2", "L3"],
        declared=1004,
        geometry=1000,
        raw_difference=4,
    )
    foreign.update({"distance_raw": 30.0, "candidate_score": 0.20})

    rows = [owner, foreign]
    _resolve_global_geometry_ownership(rows, RecognitionPolicy())
    _apply_global_geometry_conflicts(rows)

    assert owner["candidate_handles"] == ["L1", "L2", "L3"]
    assert owner["global_conflict"] is False
    assert foreign["candidate_handles"] == []
    assert foreign["ownership_tiebreak_resolved"] is True
    assert _status_counts(rows)["ownership_tiebreak_resolved"] == 1


def test_exact_band_length_advantage_alone_does_not_break_conflict():
    owner = _ownership_row(
        "OWNER",
        "8N10x1000",
        ["L1", "L2", "L3"],
        declared=1000,
        geometry=1000,
        raw_difference=0,
    )
    owner.update({"distance_raw": 24.0, "candidate_score": 0.10})
    foreign = _ownership_row(
        "FOREIGN",
        "8N10x1004",
        ["L1", "L2", "L3"],
        declared=1004,
        geometry=1000,
        raw_difference=4,
    )
    foreign.update({"distance_raw": 28.0, "candidate_score": 0.20})

    rows = [owner, foreign]
    _resolve_global_geometry_ownership(rows, RecognitionPolicy())
    _apply_global_geometry_conflicts(rows)

    assert all(row["candidate_handles"] == ["L1", "L2", "L3"] for row in rows)
    assert all(row["global_conflict"] for row in rows)
    assert _status_counts(rows)["ownership_tiebreak_resolved"] == 0


def test_master_ambiguity_blocks_exact_band_ownership_tiebreak():
    owner = _ownership_row(
        "OWNER",
        "8N10x1000",
        ["L1", "L2", "L3"],
        declared=1000,
        geometry=1000,
        raw_difference=0,
    )
    owner.update({"distance_raw": 10.0, "candidate_score": 0.10})
    foreign = _ownership_row(
        "FOREIGN",
        "8N10x1004",
        ["L1", "L2", "L3"],
        declared=1004,
        geometry=1000,
        raw_difference=4,
    )
    foreign.update(
        {
            "distance_raw": 30.0,
            "candidate_score": 0.20,
            "master_conflict": True,
            "master_ambiguous_shape_codes": ["F009", "F010"],
        }
    )

    rows = [owner, foreign]
    _resolve_global_geometry_ownership(rows, RecognitionPolicy())
    _apply_global_geometry_conflicts(rows)

    assert all(row["global_conflict"] for row in rows)
    assert _status_counts(rows)["ownership_tiebreak_resolved"] == 0


def test_explainable_decision_separates_clean_control_from_real_ambiguity():
    clean = _ownership_row(
        "CLEAN",
        "8N10x190",
        ["L1", "L2", "L3"],
        declared=190,
        geometry=190,
        raw_difference=0,
    )
    clean.update(
        {
            "batch_status": "СИЛЕН",
            "diameter_mm": 10,
            "layer_diameter_mm": 10,
            "diameter_layer_match": True,
            "master_reference_profile_id": "T-F016-001",
            "candidate_score_gap": 0.25,
            "canonical_signature": "O|n=3",
            "data_quality_flags": [],
        }
    )
    ambiguous = dict(clean)
    ambiguous.update(
        {
            "annotation_handle": "AMB",
            "master_shape_code": "F014",
            "master_ambiguous_shape_codes": ["F014", "F015"],
            "master_conflict": True,
            "review_reason_codes": ["AMBIGUOUS_MASTER"],
        }
    )
    shapes = [
        ShapeDefinition(code="F016", name="F016", state="LOCKED", active=True),
        ShapeDefinition(code="F014", name="F014", state="LOCKED", active=True),
        ShapeDefinition(code="F015", name="F015", state="LOCKED", active=True),
    ]

    _apply_matcher_decisions(
        [clean, ambiguous],
        shape_definitions=shapes,
        policy=RecognitionPolicy(),
    )

    assert clean["matcher_disposition"] == "CONTROL"
    assert clean["matcher_blocker_codes"] == []
    assert ambiguous["matcher_disposition"] == "MANUAL"
    assert "AMBIGUOUS_MASTER" in ambiguous["matcher_blocker_codes"]
