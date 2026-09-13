from __future__ import annotations

from lion_rebar.review_center_model import (
    ACTIONABLE_CLASS,
    F006_STRONG_STATUS,
    GEOMETRY_STRONG_STATUS,
    build_control_sample,
    control_sample_progress,
    f006_bulk_eligible,
    geometry_bulk_eligible,
    review_queue_rows,
)


def _geometry_row(
    handle: str,
    *,
    shape: str,
    segments: int,
) -> dict:
    return {
        "annotation_class": ACTIONABLE_CLASS,
        "annotation_handle": handle,
        "annotation_text": f"8N10x{100 + int(handle[1:])}",
        "annotation_count": 8,
        "batch_status": GEOMETRY_STRONG_STATUS,
        "matcher_disposition": "CONTROL",
        "candidate_handles": [f"C-{handle}"],
        "master_shape_code": shape,
        "segment_count": segments,
        "diameter_layer_match": False,
        "diameter_mm": 10,
        "declared_length_cm": 100,
        "numeric_difference": 0,
        "data_quality_flags": [],
        "global_conflict": False,
        "master_conflict": False,
        "master_ambiguous_shape_codes": [],
    }


def _f006_row(handle: str) -> dict:
    return {
        "annotation_class": ACTIONABLE_CLASS,
        "annotation_handle": handle,
        "annotation_text": f"4N8x{handle}",
        "annotation_count": 4,
        "batch_status": F006_STRONG_STATUS,
        "matcher_disposition": "CONTROL",
        "candidate_handles": [f"{handle}-{index}" for index in range(6)],
        "master_shape_code": "F006",
        "segment_count": 6,
        "contextual_rule_code": "F006_COMPOSITE_STIRRUP",
        "diameter_mm": 8,
        "effective_geometry_length_cm": 160,
        "data_quality_flags": [],
        "global_conflict": False,
        "master_conflict": False,
        "master_ambiguous_shape_codes": [],
    }


def test_geometry_control_sample_is_exact_representative_and_preserves_progress() -> None:
    rows = [
        _geometry_row(
            f"G{index}",
            shape=("F010", "F011", "F013", "F015")[index % 4],
            segments=(5, 5, 2, 3)[index % 4],
        )
        for index in range(14)
    ]

    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
        preferred_handles=("G7",),
    )

    assert len(sample) == 10
    assert len(set(sample)) == 10
    assert sample[0] == "G7"
    represented = {
        (row["master_shape_code"], row["segment_count"])
        for row in rows
        if row["annotation_handle"] in sample
    }
    assert represented == {
        ("F010", 5),
        ("F011", 5),
        ("F013", 2),
        ("F015", 3),
    }


def test_geometry_progress_separates_sample_from_bulk_candidates() -> None:
    rows = [
        _geometry_row(f"G{index}", shape="F013", segments=2)
        for index in range(14)
    ]
    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
        preferred_handles=("G0",),
    )

    progress = control_sample_progress(
        rows,
        sample_handles=sample,
        eligibility=geometry_bulk_eligible,
        approved_handles={"G0"},
        decisions={"G0": {"decision": "APPROVED"}},
    )

    assert progress.total == 10
    assert progress.approved == 1
    assert progress.pending == 9
    assert progress.problem_handles == ()
    assert progress.bulk_remaining == 4
    assert progress.passed is False


def test_auto_approved_handle_without_explicit_human_approval_does_not_unlock_sample() -> None:
    rows = [
        _geometry_row(f"G{index}", shape="F013", segments=2)
        for index in range(12)
    ]
    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
    )
    auto_approved_handle = sample[-1]

    progress = control_sample_progress(
        rows,
        sample_handles=sample,
        eligibility=geometry_bulk_eligible,
        approved_handles=set(sample),
        decisions={
            handle: {"decision": "APPROVED"}
            for handle in sample
            if handle != auto_approved_handle
        },
    )

    assert progress.approved == 9
    assert progress.pending == 1
    assert progress.problem_handles == ()
    assert progress.bulk_remaining == 2
    assert progress.passed is False


def test_control_sample_unlocks_only_after_every_selected_row_is_approved() -> None:
    rows = [
        _geometry_row(f"G{index}", shape="F013", segments=2)
        for index in range(12)
    ]
    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
    )

    progress = control_sample_progress(
        rows,
        sample_handles=sample,
        eligibility=geometry_bulk_eligible,
        approved_handles=set(sample),
        decisions={
            handle: {"decision": "APPROVED"}
            for handle in sample
        },
    )

    assert progress.approved == 10
    assert progress.pending == 0
    assert progress.bulk_remaining == 2
    assert progress.passed is True


def test_problem_in_selected_control_row_blocks_bulk_with_exact_handle() -> None:
    rows = [
        _geometry_row(f"G{index}", shape="F013", segments=2)
        for index in range(12)
    ]
    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
    )
    problem_handle = sample[3]

    progress = control_sample_progress(
        rows,
        sample_handles=sample,
        eligibility=geometry_bulk_eligible,
        approved_handles=set(sample) - {problem_handle},
        decisions={
            **{
                handle: {"decision": "APPROVED"}
                for handle in sample
                if handle != problem_handle
            },
            problem_handle: {"decision": "FLAGGED"},
        },
    )

    assert progress.problem_handles == (problem_handle,)
    assert progress.pending == 0
    assert progress.passed is False


def test_manual_assignment_does_not_count_as_control_approval() -> None:
    rows = [
        _geometry_row(f"G{index}", shape="F013", segments=2)
        for index in range(12)
    ]
    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
        preferred_handles=("G0",),
    )

    progress = control_sample_progress(
        rows,
        sample_handles=sample,
        eligibility=geometry_bulk_eligible,
        approved_handles={"G0"},
        decisions={"G0": {"decision": "MANUAL_ASSIGNED"}},
    )

    assert progress.approved == 0
    assert progress.pending == 10
    assert progress.passed is False


def test_f006_control_sample_is_limited_to_five() -> None:
    rows = [_f006_row(f"S{index}") for index in range(8)]

    sample = build_control_sample(
        rows,
        eligibility=f006_bulk_eligible,
        size=5,
    )
    progress = control_sample_progress(
        rows,
        sample_handles=sample,
        eligibility=f006_bulk_eligible,
    )

    assert len(sample) == 5
    assert progress.total == 5
    assert progress.pending == 5
    assert progress.bulk_remaining == 3
    assert progress.passed is False


def test_sample_queue_shows_exact_sample_and_moves_completed_row_to_bottom() -> None:
    rows = [
        _geometry_row(f"G{index}", shape="F013", segments=2)
        for index in range(12)
    ]
    sample = build_control_sample(
        rows,
        eligibility=geometry_bulk_eligible,
        size=10,
        preferred_handles=("G0",),
    )

    queued = review_queue_rows(
        rows,
        queue_code="GEOMETRY_SAMPLE",
        geometry_sample_handles=sample,
        approved_handles={"G0"},
        decisions={"G0": {"decision": "APPROVED"}},
    )

    assert len(queued) == 10
    assert {row["annotation_handle"] for row in queued} == set(sample)
    assert queued[-1]["annotation_handle"] == "G0"
