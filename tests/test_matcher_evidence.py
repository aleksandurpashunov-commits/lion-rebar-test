from __future__ import annotations

import json

import pytest

from lion_rebar.dxf.matcher_evidence import (
    EvidenceBand,
    MatcherCalibration,
    MatcherDecisionContext,
    MatcherDisposition,
    evaluate_matcher_candidate,
    evaluate_report_row,
)


def _candidate(**overrides):
    row = {
        "annotation_handle": "TXT-1",
        "candidate_handles": ["G1", "G2", "G3"],
        "candidate_layer": "Arm - N14",
        "segment_count": 3,
        "geometry_length_raw": 284.0,
        "effective_geometry_length_cm": 284.0,
        "declared_length_cm": 284.0,
        "numeric_difference": 0.0,
        "raw_numeric_difference": 0.0,
        "distance_raw": 18.0,
        "diameter_mm": 14.0,
        "layer_diameter_mm": 14.0,
        "diameter_layer_match": True,
        "candidate_score": -0.18,
        "candidate_score_gap": 0.20,
        "canonical_signature": "O|n=3|r=0.1,0.8,0.1|a=-90,-90",
        "master_shape_code": "F100",
        "master_shape_name": "ТЕСТОВА ПАРАМЕТРИЧНА ФОРМА",
        "master_reference_profile_id": "F100-CONTROL-1",
        "master_shape_mirrored": False,
        "master_shape_reversed": False,
        "master_ambiguous_shape_codes": [],
        "mirror_requires_review": False,
        "review_reason_codes": [],
        "near_gap_healed": False,
        "canonical_segment_lengths_cm": [28.4, 227.2, 28.4],
        "canonical_turn_angles_deg": [-90.0, -90.0],
        "alternative_candidates": [{"master_shape_code": "F200"}],
    }
    row.update(overrides)
    return row


def _context(**overrides):
    values = {
        "annotation_complete": True,
        "master_approved": True,
        "alternative_count": 1,
    }
    values.update(overrides)
    return MatcherDecisionContext(**values)


def _calibration(**overrides):
    values = {
        "calibration_id": "CAL-ENGINE-36-A",
        "engine_fingerprint": "sha256:123456",
        "validated_shape_codes": ("F100",),
        "regression_case_count": 96,
        "holdout_case_count": 12,
        "false_accept_count": 0,
        "released_for_auto": True,
    }
    values.update(overrides)
    return MatcherCalibration(**values)


def _evidence(decision, code):
    return next(item for item in decision.evidence if item.code == code)


def test_clear_candidate_without_field_calibration_stays_in_control() -> None:
    decision = evaluate_matcher_candidate(_candidate(), context=_context())

    assert decision.disposition is MatcherDisposition.CONTROL
    assert decision.evidence_band is EvidenceBand.CLEAR
    assert not decision.is_auto_eligible
    assert any("калибрация" in reason.lower() for reason in decision.reasons)


def test_released_calibration_allows_only_gate_clean_candidate_to_auto() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.AUTO_ELIGIBLE
    assert decision.is_auto_eligible
    assert decision.master_shape_code == "F100"
    assert decision.calibration_id == "CAL-ENGINE-36-A"
    assert not decision.safety_gates


def test_master_ambiguity_is_manual_even_with_released_calibration() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            master_ambiguous_shape_codes=["F100", "F101"],
            review_reason_codes=["AMBIGUOUS_MASTER"],
        ),
        context=_context(),
        calibration=_calibration(validated_shape_codes=("*",)),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert {gate.code for gate in decision.safety_gates} >= {
        "AMBIGUOUS_MASTER"
    }
    assert _evidence(decision, "TOPOLOGY_MATCH").band is EvidenceBand.REVIEW


def test_controlled_gap_is_explained_and_forced_to_manual() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            near_gap_healed=True,
            near_gap_bridge_gap_raw=1.40588,
            near_gap_bridge_handles=["G2", "G3"],
            review_reason_codes=["NEAR_GAP_HEALED"],
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert "NEAR_GAP" in {gate.code for gate in decision.safety_gates}
    gap = _evidence(decision, "GEOMETRY_GAP")
    assert gap.band is EvidenceBand.REVIEW
    assert gap.value["gap_raw"] == 1.40588
    assert gap.value["bridge_handles"] == ["G2", "G3"]


def test_hooked_closure_is_always_manual_even_with_released_calibration() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(review_reason_codes=["HOOKED_CLOSURE"]),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert not decision.is_auto_eligible
    assert "HOOKED_CLOSURE" in {
        gate.code for gate in decision.safety_gates
    }


def test_unapproved_master_is_unknown_not_a_low_percentage_guess() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(),
        context=_context(master_approved=False),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.UNKNOWN
    assert "UNAPPROVED_MASTER" in {gate.code for gate in decision.safety_gates}
    assert "%" not in json.dumps(decision.to_dict(), ensure_ascii=False)


def test_mirrored_and_reversed_transforms_are_auditable() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            master_shape_mirrored=True,
            master_shape_reversed=True,
            uniform_scale_factor=1.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.AUTO_ELIGIBLE
    assert decision.transforms.rotation_normalized is True
    assert decision.transforms.rotation_deg is None
    assert decision.transforms.mirrored is True
    assert decision.transforms.reversed_direction is True
    assert "огледален" in decision.transforms.explanation.lower()


def test_mirror_without_engine_permission_is_manual() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            master_shape_mirrored=True,
            mirror_requires_review=True,
            review_reason_codes=["MIRROR_REVIEW"],
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert "MIRROR_NOT_PERMITTED" in {
        gate.code for gate in decision.safety_gates
    }


def test_report_adapter_exposes_topology_parameters_layer_distance_and_ambiguity() -> None:
    row = _candidate(
        annotation_class="ACTIONABLE",
        global_conflict=False,
        quantity_review_required=False,
        candidate_notes=["Ориентацията е нормализирана"],
        rejection_counts={"ГРЕШЕН ДИАМЕТЪР В СЛОЯ": 2},
    )
    decision = evaluate_report_row(
        row,
        approved_master_codes={"F100"},
        calibration=_calibration(),
    )

    categories = {item.category for item in decision.evidence}
    assert categories >= {
        "topology",
        "parameters",
        "association",
        "ambiguity",
        "layer",
    }
    assert _evidence(decision, "PARAMETER_LENGTH").value["difference_cm"] == 0.0
    assert _evidence(decision, "TEXT_DISTANCE").value["distance_raw"] == 18.0
    assert _evidence(decision, "LAYER_NOISE").value["rejection_counts"] == {
        "ГРЕШЕН ДИАМЕТЪР В СЛОЯ": 2
    }
    json.dumps(decision.to_dict(), ensure_ascii=False)


def test_small_ranking_separation_is_manual_not_auto() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(candidate_score_gap=0.01),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert "WEAK_CANDIDATE_SEPARATION" in {
        gate.code for gate in decision.safety_gates
    }
    assert (
        _evidence(decision, "CANDIDATE_SEPARATION").band
        is EvidenceBand.REVIEW
    )


def test_calibration_with_false_accept_never_enables_auto() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(),
        context=_context(),
        calibration=_calibration(false_accept_count=1),
    )

    assert decision.disposition is MatcherDisposition.CONTROL
    assert any("грешно автоматично" in reason.lower() for reason in decision.reasons)


def test_no_candidate_is_unknown_with_explicit_reason() -> None:
    decision = evaluate_matcher_candidate(
        None,
        context=MatcherDecisionContext(master_approved=False),
    )

    assert decision.disposition is MatcherDisposition.UNKNOWN
    assert "NO_CANDIDATE" in {gate.code for gate in decision.safety_gates}
    assert _evidence(decision, "NO_CANDIDATE").band is EvidenceBand.UNKNOWN


@pytest.mark.parametrize(
    ("candidate_overrides", "context_overrides", "expected_code", "expected_disposition"),
    [
        (
            {"distance_raw": 120.0, "review_reason_codes": ["FAR_ASSOCIATION"]},
            {},
            "FAR_ASSOCIATION",
            MatcherDisposition.MANUAL,
        ),
        (
            {"review_reason_codes": ["SCHEMATIC_SCALE"], "uniform_scale_factor": 0.5},
            {},
            "SCHEMATIC_SCALE",
            MatcherDisposition.MANUAL,
        ),
        (
            {"layer_diameter_mm": 12.0, "diameter_layer_match": False},
            {},
            "LAYER_MISMATCH",
            MatcherDisposition.UNKNOWN,
        ),
        (
            {
                "geometry_length_raw": 304.0,
                "effective_geometry_length_cm": 304.0,
                "numeric_difference": 20.0,
            },
            {},
            "LENGTH_MISMATCH",
            MatcherDisposition.MANUAL,
        ),
        (
            {},
            {"annotation_complete": False},
            "INCOMPLETE_TEXT",
            MatcherDisposition.UNKNOWN,
        ),
    ],
)
def test_named_safety_blockers_are_stable_for_ui_and_reports(
    candidate_overrides,
    context_overrides,
    expected_code,
    expected_disposition,
) -> None:
    decision = evaluate_matcher_candidate(
        _candidate(**candidate_overrides),
        context=_context(**context_overrides),
        calibration=_calibration(),
    )

    assert decision.disposition is expected_disposition
    assert expected_code in {gate.code for gate in decision.safety_gates}


def test_generic_layer_distance_advantage_resolves_small_score_gap() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=None,
            diameter_layer_match=False,
            candidate_score_gap=0.02,
            candidate_distance_gap_raw=27.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.CONTROL
    # The layer remains unconfirmed, but candidate separation itself is no longer
    # the blocker because the same distance gate already made the batch candidate strong.
    assert "WEAK_CANDIDATE_SEPARATION" not in {
        gate.code for gate in decision.safety_gates
    }
    separation = _evidence(decision, "CANDIDATE_SEPARATION")
    assert separation.band is EvidenceBand.CLEAR
    assert separation.value["resolution_basis"] == "DISTANCE_GAP"


def test_generic_layer_small_score_and_small_distance_gap_stays_manual() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=None,
            diameter_layer_match=False,
            candidate_score_gap=0.02,
            candidate_distance_gap_raw=10.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert "WEAK_CANDIDATE_SEPARATION" in {
        gate.code for gate in decision.safety_gates
    }
    separation = _evidence(decision, "CANDIDATE_SEPARATION")
    assert separation.band is EvidenceBand.REVIEW
    assert separation.value["resolution_basis"] == "INSUFFICIENT_EVIDENCE"


def test_dimension_tiebreak_resolves_small_score_and_distance_gap() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=None,
            diameter_layer_match=False,
            candidate_score_gap=0.02,
            candidate_distance_gap_raw=4.0,
            review_reason_codes=["DIMENSION_CANDIDATE_TIEBREAK"],
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert "WEAK_CANDIDATE_SEPARATION" not in {
        gate.code for gate in decision.safety_gates
    }
    separation = _evidence(decision, "CANDIDATE_SEPARATION")
    assert separation.band is EvidenceBand.CLEAR
    assert separation.value["resolution_basis"] == "DIMENSION_A_E"


def test_distance_separation_does_not_override_length_mismatch() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=None,
            diameter_layer_match=False,
            candidate_score_gap=0.02,
            candidate_distance_gap_raw=30.0,
            geometry_length_raw=260.0,
            effective_geometry_length_cm=260.0,
            numeric_difference=24.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    assert "LENGTH_MISMATCH" in {gate.code for gate in decision.safety_gates}
    assert "WEAK_CANDIDATE_SEPARATION" not in {
        gate.code for gate in decision.safety_gates
    }


def test_generic_layer_uses_stricter_score_gap_threshold() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=None,
            diameter_layer_match=False,
            candidate_score_gap=0.05,
            candidate_distance_gap_raw=10.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.MANUAL
    separation = _evidence(decision, "CANDIDATE_SEPARATION")
    assert separation.band is EvidenceBand.REVIEW
    assert separation.value["score_advantage_threshold"] == pytest.approx(0.10)
    assert separation.value["resolution_basis"] == "INSUFFICIENT_EVIDENCE"


def test_generic_layer_score_gap_at_generic_threshold_is_clear() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=None,
            diameter_layer_match=False,
            candidate_score_gap=0.11,
            candidate_distance_gap_raw=10.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.CONTROL
    separation = _evidence(decision, "CANDIDATE_SEPARATION")
    assert separation.band is EvidenceBand.CLEAR
    assert separation.value["score_advantage_threshold"] == pytest.approx(0.10)
    assert separation.value["resolution_basis"] == "SCORE_GAP"


def test_diameter_coded_layer_uses_standard_score_gap_threshold() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            layer_diameter_mm=14.0,
            diameter_layer_match=True,
            candidate_score_gap=0.05,
            candidate_distance_gap_raw=4.0,
        ),
        context=_context(),
        calibration=_calibration(),
    )

    assert decision.disposition is MatcherDisposition.AUTO_ELIGIBLE
    separation = _evidence(decision, "CANDIDATE_SEPARATION")
    assert separation.band is EvidenceBand.CLEAR
    assert separation.value["score_advantage_threshold"] == pytest.approx(0.04)
    assert separation.value["resolution_basis"] == "SCORE_GAP"
