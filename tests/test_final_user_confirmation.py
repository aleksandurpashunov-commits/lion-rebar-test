from __future__ import annotations

from lion_rebar.review_center_model import (
    final_confirmation_eligible,
    final_confirmation_progress,
    is_position_key_legend_reference,
)


def _safe(handle: str) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": "2N12x700",
        "annotation_count": 2,
        "diameter_mm": 12.0,
        "declared_length_cm": 700.0,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "CONTROL",
        "candidate_handles": [f"G-{handle}"],
        "master_shape_code": "F003",
        "master_shape_name": "ПРЪТ С ДВА ВИНКЕЛА",
        "canonical_segment_lengths_cm": [20, 660, 20],
        "numeric_difference": 0.0,
        "data_quality_flags": [],
        "master_ambiguous_shape_codes": [],
        "global_conflict": False,
        "geometry_owner_conflict": False,
        "master_conflict": False,
    }


def test_only_safe_control_rows_are_final_confirmation_eligible() -> None:
    safe = _safe("A1")
    assert final_confirmation_eligible(safe) is True

    conflict = _safe("A2")
    conflict["geometry_owner_conflict"] = True
    assert final_confirmation_eligible(conflict) is False

    manual = _safe("A3")
    manual["matcher_disposition"] = "MANUAL"
    assert final_confirmation_eligible(manual) is False

    no_geometry = _safe("A4")
    no_geometry["candidate_handles"] = []
    assert final_confirmation_eligible(no_geometry) is False


def test_final_confirmation_unlocks_only_after_engineering_cases_are_resolved() -> None:
    safe = _safe("A1")
    conflict = _safe("A2")
    conflict["geometry_owner_conflict"] = True

    progress = final_confirmation_progress([safe, conflict])
    assert progress.safe_remaining == 1
    assert progress.engineering_remaining == 1
    assert progress.ready_for_final_confirmation is False

    resolved = final_confirmation_progress(
        [safe, conflict],
        decisions={"A2": {"decision": "ENGINEER_RESOLVED"}},
        approved_handles={"A2"},
    )
    assert resolved.safe_remaining == 1
    assert resolved.engineering_remaining == 0
    assert resolved.ready_for_final_confirmation is True

    complete = final_confirmation_progress(
        [safe, conflict],
        decisions={
            "A1": {"decision": "APPROVED_BULK_FINAL_USER_CONFIRMATION"},
            "A2": {"decision": "ENGINEER_RESOLVED"},
        },
        approved_handles={"A1", "A2"},
    )
    assert complete.complete is True


def test_recorded_problem_is_never_counted_as_bulk_remaining() -> None:
    from lion_rebar.review_center_model import control_sample_progress, geometry_bulk_eligible

    sample = _safe("SAMPLE")
    problem = _safe("PROBLEM")
    progress = control_sample_progress(
        [sample, problem],
        sample_handles=["SAMPLE"],
        eligibility=geometry_bulk_eligible,
        approved_handles={"SAMPLE"},
        decisions={
            "SAMPLE": {"decision": "APPROVED"},
            "PROBLEM": {"decision": "PROBLEM_RECORDED"},
        },
    )
    assert progress.passed is True
    assert progress.bulk_remaining == 0


def test_position_key_legend_reference_marker_survives_report_and_link_formats() -> None:
    row = _safe("LEGEND")
    row["position_key_legend_reference"] = True
    assert is_position_key_legend_reference(row) is True
    assert is_position_key_legend_reference(
        {"candidate_basis": "СВЪРЗАНИ СЕГМЕНТИ; POSITION_KEY_LEGEND_REFERENCE"}
    ) is True
    assert is_position_key_legend_reference(
        {"candidate_notes": ["Геометрията е легендна референция, а не локална собственост."]}
    ) is True
    assert is_position_key_legend_reference({"candidate_basis": "LOCAL"}) is False
