from __future__ import annotations

import json

from lion_rebar.dxf.batch_geometry_report import (
    _apply_review_groups,
    _review_group_counts,
    _review_group_metadata,
    _status_counts,
)
from lion_rebar.dxf.matcher_evidence import (
    EvidenceBand,
    MatcherDecisionContext,
    build_review_group_key,
    evaluate_matcher_candidate,
)
from lion_rebar.review_center_model import (
    load_review_report,
    review_group_representatives,
    review_queue_rows,
)


def _candidate(**overrides):
    row = {
        "annotation_handle": "TXT-A",
        "annotation_text": "12N14x284",
        "annotation_class": "ACTIONABLE",
        "candidate_handles": ["D81", "D82", "D83", "D87", "D88"],
        "candidate_layer": "Arm - N14",
        "segment_count": 5,
        "geometry_length_raw": 284.0,
        "effective_geometry_length_cm": 284.0,
        "declared_length_cm": 284.0,
        "numeric_difference": 0.0,
        "raw_numeric_difference": 0.0,
        "distance_raw": 18.425,
        "diameter_mm": 14.0,
        "layer_diameter_mm": 14.0,
        "diameter_layer_match": True,
        "candidate_score_gap": 0.25,
        "canonical_signature": "O|n=5|r=0.1,0.2,0.4,0.2,0.1|a=-90,-90,-90,-90",
        "master_shape_code": "F011",
        "master_shape_name": "КАПИТЕЛИ",
        "master_reference_profile_id": "T-F011-001",
        "master_ambiguous_shape_codes": [],
        "review_reason_codes": [],
        "near_gap_healed": False,
        "alternative_candidates": [{"master_shape_code": "F010"}],
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


def test_form_ambiguity_does_not_erase_clear_geometry_ownership() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(
            master_ambiguous_shape_codes=["F010", "F011"],
            review_reason_codes=["AMBIGUOUS_MASTER"],
        ),
        context=_context(),
    )

    assert decision.form_classification_confidence is EvidenceBand.REVIEW
    assert decision.geometry_ownership_confidence is EvidenceBand.CLEAR
    assert {
        item.code for item in decision.form_classification_evidence
    } >= {"TOPOLOGY_MATCH", "GEOMETRY_GAP"}
    assert {
        item.code for item in decision.geometry_ownership_evidence
    } >= {"TEXT_DISTANCE", "CANDIDATE_SEPARATION", "GEOMETRY_OWNERSHIP"}
    payload = decision.to_dict()
    assert payload["form_classification_confidence"] == "REVIEW"
    assert payload["geometry_ownership_confidence"] == "CLEAR"
    assert "%" not in json.dumps(payload, ensure_ascii=False)


def test_ownership_conflict_does_not_erase_clear_form_classification() -> None:
    decision = evaluate_matcher_candidate(
        _candidate(),
        context=_context(global_geometry_conflict=True),
    )

    assert decision.form_classification_confidence is EvidenceBand.EXACT
    assert decision.geometry_ownership_confidence is EvidenceBand.REVIEW
    assert "GEOMETRY_OWNERSHIP_CONFLICT" in {
        gate.code for gate in decision.safety_gates
    }


def test_review_group_key_uses_exact_conservative_evidence_not_handles() -> None:
    first = _candidate(
        matcher_disposition="CONTROL",
        candidate_handles=["A", "B", "C"],
    )
    repeated = _candidate(
        annotation_handle="TXT-B",
        matcher_disposition="CONTROL",
        candidate_handles=["X", "Y", "Z"],
    )

    assert build_review_group_key(first) == build_review_group_key(repeated)
    assert build_review_group_key(first).startswith("RG2-")
    assert build_review_group_key(first) != build_review_group_key(
        {**repeated, "annotation_text": "12n14x284"}
    )
    assert build_review_group_key(first) != build_review_group_key(
        {**repeated, "candidate_layer": "ARM-GORNA"}
    )
    assert build_review_group_key(first) != build_review_group_key(
        {**repeated, "master_conflict": True}
    )


def test_batch_group_metadata_has_one_stable_representative() -> None:
    rows = [
        _candidate(
            annotation_handle="B",
            batch_status="СИЛЕН",
            matcher_disposition="CONTROL",
        ),
        _candidate(
            annotation_handle="A",
            batch_status="СИЛЕН",
            matcher_disposition="CONTROL",
            candidate_handles=["OTHER-1"],
        ),
        _candidate(
            annotation_handle="C",
            annotation_text="8N10x190",
            batch_status="ЗА РЪЧЕН ПРЕГЛЕД",
            matcher_disposition="MANUAL",
        ),
    ]

    _apply_review_groups(rows)

    assert rows[0]["review_group_key"] == rows[1]["review_group_key"]
    assert rows[0]["review_group_size"] == rows[1]["review_group_size"] == 2
    assert rows[1]["review_group_representative"] is True
    assert rows[0]["review_group_representative_handle"] == "A"
    counts = _review_group_counts(rows)
    assert counts == {
        "actionable_groups": 2,
        "multi_position_groups": 1,
        "positions_saved": 1,
        "largest_group": 2,
    }
    metadata = _review_group_metadata(rows)
    assert len(metadata) == 2
    repeated = next(item for item in metadata if item["size"] == 2)
    assert repeated["representative_handle"] == "A"
    assert repeated["member_handles"] == ["A", "B"]


def test_conflict_summary_separates_master_from_ownership_without_double_count() -> None:
    rows = [
        {
            "annotation_class": "ACTIONABLE",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "master_conflict": True,
            "global_conflict": False,
        },
        {
            "annotation_class": "ACTIONABLE",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "master_conflict": False,
            "global_conflict": True,
        },
        {
            "annotation_class": "ACTIONABLE",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "master_conflict": True,
            "global_conflict": True,
        },
    ]

    counts = _status_counts(rows)

    assert counts["master_ambiguity"] == 2
    assert counts["ownership_conflict"] == 2
    assert counts["conflict"] == 3


def test_legacy_v3_report_gets_new_fields_and_groups_on_load(tmp_path) -> None:
    rows = [
        {
            **_candidate(
                annotation_handle="B",
                batch_status="СИЛЕН",
                matcher_disposition="CONTROL",
            ),
            "global_conflict": False,
            "master_conflict": False,
            "matcher_evidence_band": "CLEAR",
        },
        {
            **_candidate(
                annotation_handle="A",
                batch_status="СИЛЕН",
                matcher_disposition="CONTROL",
                candidate_handles=["OTHER"],
            ),
            "global_conflict": False,
            "master_conflict": False,
            "matcher_evidence_band": "CLEAR",
        },
    ]
    path = tmp_path / "legacy_report.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-geometry-batch-report/v3",
                "project_name": "LEGACY",
                "generated_at": "2026-07-28T10:00:00+03:00",
                "summary": {"global_conflicts": 0},
                "rows": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = load_review_report(path)

    assert report.summary["master_ambiguities"] == 0
    assert report.summary["ownership_conflicts"] == 0
    assert report.summary["total_conflicts"] == 0
    assert report.summary["review_groups"] == 1
    assert report.summary["grouped_positions_saved"] == 1
    assert len(report.review_groups) == 1
    assert report.review_groups[0]["representative_handle"] == "A"
    assert all(
        row["form_classification_confidence"] == "CLEAR"
        and row["geometry_ownership_confidence"] == "CLEAR"
        for row in report.rows
    )
    representatives = review_group_representatives(report.rows)
    assert [row["annotation_handle"] for row in representatives] == ["A"]


def test_groups_queue_shows_one_unresolved_member_per_group_without_propagation() -> None:
    rows = [
        _candidate(
            annotation_handle="B",
            batch_status="СИЛЕН",
            matcher_disposition="CONTROL",
        ),
        _candidate(
            annotation_handle="A",
            batch_status="СИЛЕН",
            matcher_disposition="CONTROL",
            candidate_handles=["OTHER"],
        ),
        _candidate(
            annotation_handle="C",
            annotation_text="8N10x190",
            batch_status="ЗА РЪЧЕН ПРЕГЛЕД",
            matcher_disposition="MANUAL",
        ),
    ]
    _apply_review_groups(rows)

    initial = review_queue_rows(rows, queue_code="GROUPS")
    assert len(initial) == 2
    repeated = next(row for row in initial if row["review_group_size"] == 2)
    assert repeated["annotation_handle"] == "A"
    assert repeated["review_group_unresolved_count"] == 2

    after_one_decision = review_queue_rows(
        rows,
        queue_code="GROUPS",
        decisions={"A": {"decision": "APPROVED"}},
    )
    assert len(after_one_decision) == 2
    repeated = next(
        row for row in after_one_decision if row["review_group_size"] == 2
    )
    assert repeated["annotation_handle"] == "B"
    assert repeated["review_group_unresolved_count"] == 1

    after_both_decisions = review_queue_rows(
        rows,
        queue_code="GROUPS",
        decisions={
            "A": {"decision": "APPROVED"},
            "B": {"decision": "APPROVED"},
        },
    )
    assert [row["annotation_handle"] for row in after_both_decisions] == ["C"]
