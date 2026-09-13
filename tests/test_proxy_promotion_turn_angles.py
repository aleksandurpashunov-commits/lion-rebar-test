"""Promoted turn angles must describe the bar that was actually drawn (rc44).

Owner-reported 2026-09-13, exporting a real koloni project:

    Excel + DXF export не завърши.
    F002 PXANN_96CE28 reduction turns must have opposite signs: 90° / 90°.

``_apply_proxy_declared_shape_auto_promotion`` wrote a blanket
``[90.0] * (len(segments) - 1)``. For a closed stirrup and for a U-bar that is
correct -- every corner turns the same way by a right angle. For F002 it is
wrong twice over:

* a reduction turns ONTO the incline and then back OFF it, so the two turns
  are opposite in sign (``excel_export._validate_f002_turn_consistency``
  enforces exactly that, with equal magnitudes within 0.2°);
* the angle is the crank angle, not 90° -- measured 14.04° on the real
  koloni bars, whose legs run at 90.0 / 75.96 / 90.0 degrees.

The magnitude now comes from the measured leg directions that
``proxy_adapter`` already records, so the exported schedule describes the bar
in the drawing rather than a right-angled invention.
"""
from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.batch_geometry_report import _proxy_turn_angles_deg
from lion_rebar.dxf.proxy_adapter import reduction_offset_angle_deg


class TestF002Reduction:
    """Real koloni bar "22N25x481": declared [150, 21, 310], drawn offset
    exactly 5.000 cm between the two parallel legs."""

    SEGMENTS = [150.0, 21.0, 310.0]
    ANGLE = reduction_offset_angle_deg(5.0, 21.0)

    def test_the_two_turns_are_opposite_in_sign(self) -> None:
        turns = _proxy_turn_angles_deg("F002", self.SEGMENTS, self.ANGLE)

        assert len(turns) == 2
        assert turns[0] * turns[1] < 0
        assert abs(abs(turns[0]) - abs(turns[1])) <= 0.2

    def test_the_angle_reproduces_the_designers_own_offset_exactly(self) -> None:
        """Owner, 2026-09-13: "отместването е точно 5, а дължината на
        редукцията е 21 точно". master_engine_v031 computes
        ``DY2 = reduction * sin(angle)``, so the angle has to put DY2 back on
        the round number the designer wrote."""

        turns = _proxy_turn_angles_deg("F002", self.SEGMENTS, self.ANGLE)

        assert 21.0 * math.sin(math.radians(abs(turns[0]))) == pytest.approx(
            5.0, abs=1e-9
        )
        assert 21.0 * math.cos(math.radians(abs(turns[0]))) == pytest.approx(
            math.sqrt(21.0**2 - 5.0**2), abs=1e-9
        )

    def test_the_drawn_direction_alone_would_miss_the_offset(self) -> None:
        """The schematic draws that leg 20.616 cm long, so its own direction
        gives 14.04° -- and 21*sin(14.04°) is 5.095, not 5. This is the
        regression the owner caught."""

        drawn_angle = math.degrees(math.atan2(5.0, 20.0))

        assert 21.0 * math.sin(math.radians(drawn_angle)) == pytest.approx(
            5.095, abs=0.005
        )
        assert abs(drawn_angle - self.ANGLE) > 0.25

    def test_without_a_proven_angle_nothing_is_invented(self) -> None:
        """Fail-closed: a blanket 90/90 is what broke the export."""

        assert _proxy_turn_angles_deg("F002", self.SEGMENTS, None) is None
        assert _proxy_turn_angles_deg("F002", self.SEGMENTS, 0.0) is None
        assert _proxy_turn_angles_deg("F002", self.SEGMENTS, 90.0) is None


class TestReductionAngleFromDeclaredNumbers:
    def test_the_real_koloni_numbers(self) -> None:
        assert reduction_offset_angle_deg(5.0, 21.0) == pytest.approx(13.7741, abs=1e-4)

    def test_the_real_shaibi_diagonal(self) -> None:
        """4N16x323, declared middle 203, measured offset 136.268."""

        assert reduction_offset_angle_deg(136.268, 203.0) == pytest.approx(
            42.1654, abs=1e-3
        )

    def test_an_offset_that_cannot_belong_to_the_declaration_is_refused(self) -> None:
        assert reduction_offset_angle_deg(25.0, 21.0) is None
        assert reduction_offset_angle_deg(21.0, 21.0) is None
        assert reduction_offset_angle_deg(0.0, 21.0) is None
        assert reduction_offset_angle_deg(5.0, 0.0) is None


class TestShapesThatDoTurnByRightAngles:
    def test_a_u_bar_turns_the_same_way_twice(self) -> None:
        turns = _proxy_turn_angles_deg("F003", [30.0, 355.0, 30.0], None)

        assert turns == (90.0, 90.0)

    def test_a_closed_stirrup_turns_five_times(self) -> None:
        turns = _proxy_turn_angles_deg("F006", [8.0, 46.0, 21.0, 46.0, 21.0, 8.0], None)

        assert turns == (90.0, 90.0, 90.0, 90.0, 90.0)

    def test_a_straight_bar_has_no_turns(self) -> None:
        assert _proxy_turn_angles_deg("F001", [415.0], None) == ()


def test_the_exported_angles_satisfy_the_excel_validator() -> None:
    """The contract this fix exists to meet, asserted against the real
    validator rather than restated."""

    from lion_rebar.export.excel_export import (
        ExportPosition,
        _validate_f002_turn_consistency,
    )

    turns = _proxy_turn_angles_deg(
        "F002", [150.0, 21.0, 310.0], reduction_offset_angle_deg(5.0, 21.0)
    )
    position = ExportPosition(
        annotation_handle="PXANN_96CE28",
        annotation_text="22N25x481",
        count=22,
        diameter_mm=25.0,
        production_length_cm=481.0,
        shape_code="F002",
        shape_name="ПРАВ ПРЪТ С РЕДУКЦИЯ",
        shape_revision=1,
        geometry_handles=(),
        frame_index=1,
        segment_lengths_cm=(150.0, 21.0, 310.0),
        turn_angles_deg=tuple(turns),
    )

    _validate_f002_turn_consistency(position)
    assert math.isclose(sum(position.segment_lengths_cm), 481.0)
