from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "verify_rc35_r008_full_floor.py"
)
SPEC = importlib.util.spec_from_file_location(
    "verify_rc35_r008_full_floor",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_exact_coverage_rejects_duplicates_and_non_production() -> None:
    MODULE._assert_exact_handle_coverage(
        ["A", "B"],
        {"A", "B"},
        excluded={"X"},
        label="synthetic",
    )
    with pytest.raises(RuntimeError, match="coverage mismatch"):
        MODULE._assert_exact_handle_coverage(
            ["A", "A", "B"],
            {"A", "B"},
            label="synthetic",
        )
    with pytest.raises(RuntimeError, match="NON_PRODUCTION"):
        MODULE._assert_exact_handle_coverage(
            ["A", "X"],
            {"A", "X"},
            excluded={"X"},
            label="synthetic",
        )


def test_frame_expectations_are_derived_and_fail_closed() -> None:
    rows = {
        "A": {"frame_index": 1, "frame_category_code": "BOTTOM"},
        "B": {"frame_index": 1, "frame_category_code": "BOTTOM"},
        "C": {"frame_index": 2, "frame_category_code": "BEAMS"},
    }
    counts, categories, category_counts = (
        MODULE._derive_frame_expectations(rows, {"A", "B", "C"})
    )
    assert counts == {1: 2, 2: 1}
    assert categories == {1: "BOTTOM", 2: "BEAMS"}
    assert category_counts == {"BOTTOM": 2, "BEAMS": 1}

    inconsistent = dict(rows)
    inconsistent["D"] = {
        "frame_index": 1,
        "frame_category_code": "TOP",
    }
    with pytest.raises(RuntimeError, match="disagree"):
        MODULE._derive_frame_expectations(inconsistent, inconsistent)


def test_frame_expectations_reject_missing_production_handle() -> None:
    with pytest.raises(RuntimeError, match="absent from batch"):
        MODULE._derive_frame_expectations({}, {"MISSING"})
