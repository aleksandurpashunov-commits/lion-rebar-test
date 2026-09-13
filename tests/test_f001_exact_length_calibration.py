from __future__ import annotations

import pytest

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


def _policy() -> RecognitionPolicy:
    return RecognitionPolicy(
        exact_length_abs=5.0,
        exact_length_ratio=0.005,
        straight_bar_exact_length_rule_enabled=True,
        straight_bar_exact_length_master_codes=("F001",),
        straight_bar_exact_length_ratio=0.007,
        straight_bar_exact_length_max_abs_cm=7.0,
    )


def _f001() -> ShapeDefinition:
    return ShapeDefinition(
        code="F001",
        name="ПРАВ ПРЪТ",
        state="APPROVED",
        mirror_allowed=True,
        segments=[SegmentDefinition(index=1, length_code="L1")],
    )


def test_f001_tolerance_promotes_only_the_calibrated_750_boundary() -> None:
    policy = _policy()

    assert policy.exact_length_tolerance_for_master(750.0, "F001") == pytest.approx(5.25)
    assert policy.exact_length_tolerance_for_master(900.0, "F001") == pytest.approx(6.30)
    assert policy.exact_length_tolerance_for_master(270.0, "F001") == pytest.approx(5.00)
    assert policy.exact_length_tolerance_for_master(750.0, "F003") == pytest.approx(5.00)
    assert 5.048 < policy.exact_length_tolerance_for_master(750.0, "F001")
    assert 7.500 > policy.exact_length_tolerance_for_master(900.0, "F001")
    assert 5.359 > policy.exact_length_tolerance_for_master(270.0, "F001")


def test_unambiguous_f001_uses_extended_gate_but_900_stays_manual() -> None:
    policy = _policy()
    shape = _f001()

    def analyze(declared: float, drawn: float):
        annotation = DxfEntityRecord(
            handle=f"A-{declared:g}",
            entity_type="TEXT",
            layer="ARM_TXT",
            text=f"2N8x{declared:g}",
            geometry={"insert": [0.0, 5.0, 0.0], "height": 3.0},
        )
        line = DxfEntityRecord(
            handle=f"G-{declared:g}",
            entity_type="LINE",
            layer="ARM_Bars",
            geometry={"start": [0.0, 0.0, 0.0], "end": [drawn, 0.0, 0.0]},
        )
        return find_geometry_candidates(
            annotation,
            [annotation, line],
            shape_definitions=[shape],
            policy=policy,
        ).candidates[0]

    boundary = analyze(750.0, 744.9519)
    non_boundary = analyze(900.0, 892.5)

    assert boundary.master_shape_code == "F001"
    assert boundary.confidence == "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА"
    assert "F001_EXTENDED_EXACT_LENGTH" in boundary.review_reason_codes
    assert non_boundary.master_shape_code == "F001"
    assert non_boundary.confidence == "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"
    assert "F001_EXTENDED_EXACT_LENGTH" not in non_boundary.review_reason_codes


def test_disabled_f001_rule_keeps_750_for_manual_review() -> None:
    policy = RecognitionPolicy(
        exact_length_abs=5.0,
        exact_length_ratio=0.005,
        straight_bar_exact_length_rule_enabled=False,
    )
    assert policy.exact_length_tolerance_for_master(750.0, "F001") == pytest.approx(5.0)
