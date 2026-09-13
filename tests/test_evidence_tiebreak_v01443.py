from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import _promote_ownership_alternative
from lion_rebar.dxf.geometry_candidates import (
    _dimension_evidence_candidate_advantage,
    _strong_distance_limit_for_row,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy


def _match(code: str = "F004") -> SimpleNamespace:
    return SimpleNamespace(shape_code=code, ambiguous_shape_codes=())


def test_f007_exact_rule_has_narrow_row_specific_distance() -> None:
    policy = RecognitionPolicy(c_shape_curve_strong_distance_raw=60.0)
    generic = {"declared_length_cm": 40.0, "contextual_rule_code": None}
    f007 = {
        "declared_length_cm": 40.0,
        "contextual_rule_code": "F007_ESOVE_CURVED_CENTERLINE",
    }

    assert _strong_distance_limit_for_row(generic, policy) == 45.0
    # The generic strong limit stays 45.  Only the fully proved F007
    # contextual row may use the separately configured 60-unit gate.
    assert _strong_distance_limit_for_row(f007, policy) == 60.0


def test_complete_ae_evidence_resolves_only_same_master_local_tie() -> None:
    policy = RecognitionPolicy(exact_length_abs=5.0)
    first = {
        "declared_length_cm": 165.0,
        "numeric_difference": 0.0,
        "distance_raw": 4.95,
        "score": -0.05,
        "segment_count": 2,
        "dimension_evidence_applied": True,
        "dimension_total_difference_cm": 0.0,
        "master_match": _match("F004"),
        "review_reason_codes": (),
    }
    second = {
        "distance_raw": 9.31,
        "score": -0.02,
        "segment_count": 2,
        "dimension_evidence_applied": False,
        "master_match": _match("F004"),
    }

    assert _dimension_evidence_candidate_advantage(first, second, policy)
    second["dimension_evidence_applied"] = True
    assert not _dimension_evidence_candidate_advantage(first, second, policy)
    second["dimension_evidence_applied"] = False
    second["master_match"] = _match("F003")
    assert not _dimension_evidence_candidate_advantage(first, second, policy)


def _verified_alternative() -> dict:
    return {
        "rank": 2,
        "handles": ["A", "B", "C", "D", "E"],
        "layer": "Arm - N10",
        "segment_count": 5,
        "geometry_length_raw": 350.0,
        "effective_geometry_length_cm": 350.0,
        "raw_numeric_difference": 0.0,
        "numeric_difference": 0.0,
        "distance_raw": 7.9,
        "centroid_distance_raw": 20.0,
        "layer_diameter_mm": 10.0,
        "diameter_layer_match": True,
        "confidence": "СИЛЕН КАНДИДАТ — НЕОДОБРЕН",
        "basis": "СВЪРЗАНИ СЕГМЕНТИ",
        "score": -0.07,
        "score_gap": 0.39,
        "distance_gap_raw": 4.0,
        "notes": [],
        "canonical_signature": "O|n=5",
        "master_shape_code": "F010",
        "master_shape_name": "F010",
        "master_ambiguous_shape_codes": [],
        "dimension_evidence_applied": True,
        "dimension_total_difference_cm": 0.0,
        "dimension_resolution_strategy": "A_E_TOTAL_MINUS_PRINTED",
        "canonical_segment_lengths_cm": [180.0, 28.0, 19.0, 10.0, 113.0],
        "review_reason_codes": [],
    }


def test_verified_free_alternative_becomes_strong_but_not_approved() -> None:
    row = {"declared_length_cm": 350.0, "alternative_candidates": []}
    _promote_ownership_alternative(
        row,
        _verified_alternative(),
        policy=RecognitionPolicy(exact_length_abs=5.0),
        rejected_candidate={"handles": ["FOREIGN"]},
        overlap=["FOREIGN"],
        winner_labels=["8N10x832"],
    )

    assert row["batch_status"] == "СИЛЕН"
    assert row["ownership_reassigned"] is True
    assert row["ownership_recovery_verified"] is True
    assert row["ownership_rejected"] is False
    assert "GLOBAL_ALTERNATIVE_RECOVERY_VERIFIED" in row["review_reason_codes"]
    assert row.get("approved_in_project") is not True


def test_incomplete_free_alternative_stays_manual() -> None:
    alternative = _verified_alternative()
    alternative["dimension_evidence_applied"] = False
    row = {"declared_length_cm": 350.0, "alternative_candidates": []}
    _promote_ownership_alternative(
        row,
        alternative,
        policy=RecognitionPolicy(exact_length_abs=5.0),
        rejected_candidate={"handles": ["FOREIGN"]},
        overlap=["FOREIGN"],
        winner_labels=["8N10x832"],
    )

    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert row["ownership_recovery_verified"] is False
    assert row["ownership_rejected"] is True
