from __future__ import annotations

from lion_rebar.export.auto_gate import (
    auto_classification_gate_passes,
    auto_gate_eligibility_mode,
)


def test_canonical_strong_auto_row_passes() -> None:
    row = {
        "annotation_handle": "A1",
        "batch_status": "СИЛЕН",
        "matcher_disposition": "AUTO",
    }
    assert auto_classification_gate_passes(row)
    assert auto_gate_eligibility_mode(row) == "STATUS_AND_MATCHER"


def test_manual_block_row_with_unvalidated_f006_status_fails() -> None:
    row = {
        "annotation_handle": "A1::BJ-FI-L",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "matcher_disposition": "MANUAL",
        "f006_production_status": "F006_PRODUCTION_DERIVED",
        "dimension_derived_evidence": {"confidence": "HIGH"},
    }
    assert not auto_classification_gate_passes(row)
    assert auto_gate_eligibility_mode(row) == "INELIGIBLE"


def test_manual_row_without_deterministic_evidence_fails() -> None:
    row = {
        "annotation_handle": "A1::BJ-FI-L",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "matcher_disposition": "MANUAL",
    }
    assert not auto_classification_gate_passes(row)


def test_allowed_status_with_manual_matcher_fails() -> None:
    row = {
        "annotation_handle": "A1",
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "MANUAL",
    }
    assert not auto_classification_gate_passes(row)
