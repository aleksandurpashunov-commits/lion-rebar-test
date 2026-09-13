"""Тестове за отчета след проект.

Централното твърдение: отчетът СЪБИРА доказателства и НЕ променя правила.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.review.learning_report import (
    build_learning_report,
    stable_conventions,
    write_learning_report,
)


def _stirrup(handle, width, height, hook, declared):
    return {
        "annotation_handle": f"{handle}::BJ-FI-L",
        "master_shape_code": "F006",
        "declared_length_cm": declared,
        "production_segments_cm": [width, height, hook],
        "batch_status": "СИЛНО СТРЕМЕ F006",
    }


def test_hook_convention_is_observed_from_the_drawing():
    rows = [_stirrup(f"A{i}", 35, 20, 10, 135) for i in range(6)]
    report = build_learning_report("Проект", rows)
    hook = next(c for c in report.conventions if "куката" in c.name)
    assert hook.value == 10.0
    assert hook.is_stable


def test_bend_allowance_is_measured_not_assumed():
    rows = [_stirrup(f"A{i}", 35, 20, 10, 135) for i in range(6)]
    report = build_learning_report("Проект", rows)
    allowance = next(c for c in report.conventions if "огъване" in c.name)
    assert allowance.value == 5.0
    assert allowance.occurrences == 6


def test_inconsistent_observation_is_not_stable():
    rows = ([_stirrup(f"A{i}", 35, 20, 10, 135) for i in range(3)]
            + [_stirrup(f"B{i}", 35, 20, 7, 135) for i in range(3)])
    report = build_learning_report("Проект", rows)
    hook = next(c for c in report.conventions if "куката" in c.name)
    assert not hook.is_stable


def test_annotation_source_is_detected():
    rows = [_stirrup(f"A{i}", 35, 20, 10, 135) for i in range(4)]
    report = build_learning_report("Проект", rows)
    source = next(c for c in report.conventions if "Източник" in c.name)
    assert source.value == "БЛОКОВЕ С АТРИБУТИ"


def test_refusals_are_counted_by_reason():
    report = build_learning_report(
        "Проект", [],
        blockers=[{"reason_codes": ["MATCHER_REVIEW", "BATCH_STATUS"]},
                  {"reason_codes": ["MATCHER_REVIEW"]}])
    assert report.refusals["MATCHER_REVIEW"] == 2
    assert report.refusals["BATCH_STATUS"] == 1


def test_operator_disagreements_are_recorded():
    """Къде операторът е избрал различно от предложеното."""

    report = build_learning_report(
        "Проект", [],
        decisions=[{"event": "ENGINEER_DECISION",
                    "payload": {"annotation_handle": "A1", "action_class": "CORRECTION",
                                "rejected_master_shape_code": "F006",
                                "shape_code": "F003", "note": "не е стреме"}}])
    assert report.disagreements[0]["proposed"] == "F006"
    assert report.disagreements[0]["chosen"] == "F003"


def test_agreement_is_not_recorded_as_disagreement():
    report = build_learning_report(
        "Проект", [],
        decisions=[{"payload": {"rejected_master_shape_code": "F006",
                                "shape_code": "F006"}}])
    assert report.disagreements == []


def test_report_states_that_nothing_is_applied_automatically():
    payload = build_learning_report("Проект", []).to_dict()
    assert "не се променя" in payload["note"]


def test_stable_conventions_are_only_proposals():
    rows = [_stirrup(f"A{i}", 35, 20, 10, 135) for i in range(6)]
    report = build_learning_report("Проект", rows)
    proposals = stable_conventions(report)
    assert proposals
    assert all(item.is_stable for item in proposals)


def test_report_is_written_atomically():
    with tempfile.TemporaryDirectory() as directory:
        rows = [_stirrup("A1", 35, 20, 10, 135)]
        path = write_learning_report(directory, build_learning_report("П", rows))
        assert path.name == "PROJECT_LEARNING_REPORT.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == "lion-rebar-learning-report/v1"
        assert not list(Path(directory, "working").glob("*.tmp"))


def test_empty_project_does_not_crash():
    report = build_learning_report("Празен", [])
    assert report.conventions == []
    assert report.totals["positions"] == 0
