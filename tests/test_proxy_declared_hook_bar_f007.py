"""A leg with two equal hooks is F007, not a U-bar (rc44).

Owner-reviewed project ``s-7`` (shaibi), 2026-09-13. Eighteen annotations
share one signature: three declared values whose last two are equal, one real
bend ARC, and a total that satisfies the engine's own F007 control equation.

    [31, 8, 8]  L=47   2*8 + 31 = 47     nine positions
    [21, 8, 8]  L=37   2*8 + 21 = 37     eight positions
    [26, 8, 8]  L=42   2*8 + 26 = 42     one position

F007 "S / C-ОБРАЗНА ФОРМА" states exactly that: "Контролната дължина е
2×L1 + L2; кривината е схематична."

``derive_shape`` typed all eighteen as F003 -- the U-form the engine defines
as 90 degree bends with both arms shorter than the middle. The bars are not
that: their own geometry carries a genuine ARC of radius 4.8 sweeping 135
degrees, which is a hook.

The consequence was not academic. The owner corrected eight of them by hand
to F007; the other TEN were auto-approved as F003 and shipped in the
specification -- roughly 1218 bars whose weight is right and whose bending
schedule is wrong.

Turn angles stay empty, matching both the engine note and every one of the
owner's eight decisions.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.batch_geometry_report import _proxy_turn_angles_deg
from lion_rebar.dxf.proxy_adapter import _read_declared_hook_bar


class TestRealShaibiHookBars:
    @pytest.mark.parametrize(
        "declared, length, expected",
        [
            ((21.0, 8.0, 8.0), 37.0, (8.0, 21.0, 8.0)),
            ((31.0, 8.0, 8.0), 47.0, (8.0, 31.0, 8.0)),
            ((26.0, 8.0, 8.0), 42.0, (8.0, 26.0, 8.0)),
        ],
    )
    def test_the_declaration_is_read_as_hook_leg_hook(
        self, declared: tuple[float, ...], length: float, expected: tuple[float, ...]
    ) -> None:
        assert _read_declared_hook_bar(declared, length) == expected

    def test_the_result_matches_the_engines_control_equation(self) -> None:
        hook, leg, hook_again = _read_declared_hook_bar((21.0, 8.0, 8.0), 37.0)

        assert hook == hook_again
        assert 2 * hook + leg == 37.0

    def test_f007_carries_no_turn_angles(self) -> None:
        """"кривината е схематична" -- and all eight owner decisions recorded
        an empty turn-angle list."""

        assert _proxy_turn_angles_deg("F007", [8.0, 21.0, 8.0], None) == ()


class TestFailClosed:
    def test_unequal_hooks_are_refused(self) -> None:
        assert _read_declared_hook_bar((21.0, 8.0, 9.0), 38.0) is None

    def test_a_total_that_does_not_satisfy_the_equation_is_refused(self) -> None:
        assert _read_declared_hook_bar((21.0, 8.0, 8.0), 40.0) is None

    def test_a_hook_longer_than_the_leg_is_refused(self) -> None:
        """Then it is not a hook, and the engine reserves that proportion for
        another shape."""

        assert _read_declared_hook_bar((8.0, 21.0, 21.0), 50.0) is None

    def test_the_u_bar_grammar_is_not_claimed(self) -> None:
        """``[30, 355, 30]`` is the shaibi U-bar: outer values equal, but they
        are the FIRST and LAST, and 2*355 + 30 is not 415."""

        assert _read_declared_hook_bar((30.0, 355.0, 30.0), 415.0) is None

    def test_a_stirrup_declaration_is_not_claimed(self) -> None:
        assert _read_declared_hook_bar((21.0, 18.0, 21.0, 18.0, 8.0), 94.0) is None

    def test_non_positive_values_are_refused(self) -> None:
        assert _read_declared_hook_bar((21.0, 0.0, 0.0), 21.0) is None
