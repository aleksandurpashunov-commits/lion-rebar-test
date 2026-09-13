"""A three-part declaration is not automatically a straight bar (rc44).

Owner-reported class, 2026-09-13: "без тихи и скрити грешки".

``_derive_declared_shape_evidence`` used to turn ANY zero-arc three-value
declaration into F002 "ПРАВ ПРЪТ С РЕДУКЦИЯ". The zero-arc part is sound --
these bars really carry no ARC entity -- but it is not proof of straightness:
this dialect draws its bends as sharp polyline corners, and the only ARCs in
the proxy are a decorative end-circle that ``_count_structural_bend_arcs``
correctly cancels out.

Measured on the real drawings, the annotation's OWN exploded geometry settles
it, and the two drawings disagree:

    koloni 22N25x481  [150, 21, 310]   legs at 90.0 / 75.96 / 90.0 degrees
    shaibi 34N12x415  [30, 355, 30]    legs at 90.0 /  0.00 / 90.0 degrees

Against the engine's own locked definitions:

    F002  "два хоризонтални прави участъка и наклонена редукция между тях"
          bend_count 2, segment_count 3
    F003  "U-форма, при която и двете външни рамена са по-къси от средния
          сегмент", bend_count 2, segment_count 3, bends of 90 degrees

So the koloni bars are genuinely F002 -- two parallel legs with an inclined
splice -- while the twenty shaibi bars are F003 U-bars that were being
exported as straight spliced bars. The shape now comes from the measured
geometry and the lengths from the declaration, each checked against the
other.
"""
from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.proxy_adapter import _read_declared_three_leg_shape


def _legs(*specs: tuple[tuple[float, float], tuple[float, float]]):
    return list(specs)


def _vertical(x: float, y0: float, y1: float):
    return ((x, y0), (x, y1))


def _horizontal(y: float, x0: float, x1: float):
    return ((x0, y), (x1, y))


class TestRealKoloniReduction:
    """22N25x481: declared [150, 21, 310]; drawn 150 / 20.62 / 310 with the
    middle leg inclined 14 degrees off the two parallel outer legs."""

    LEGS = _legs(
        _vertical(0.0, 0.0, 150.0),
        ((0.0, 150.0), (5.0, 170.0)),  # 20.62 long, 75.96 degrees
        _vertical(5.0, 170.0, 480.0),  # 310 long
    )
    SEGMENTS = (150.0, 21.0, 310.0)

    def test_an_inclined_middle_between_parallel_legs_is_f002(self) -> None:
        read = _read_declared_three_leg_shape(self.LEGS, self.SEGMENTS, 481.0)

        code, segments, reduction_angle = read

        assert (code, segments) == ("F002", (150.0, 21.0, 310.0))
        # The angle comes from the DECLARED reduction length and the measured
        # offset, so the designer's round 5 cm offset survives verbatim --
        # see proxy_adapter.reduction_offset_angle_deg.
        assert 21.0 * math.sin(math.radians(reduction_angle)) == pytest.approx(
            5.0, abs=1e-9
        )


class TestRealShaibiUBar:
    """34N12x415: declared [30, 355, 30]; drawn at twice scale (60 / 710 / 60)
    with the middle leg perpendicular to both outer legs."""

    LEGS = _legs(
        _vertical(0.0, 0.0, 60.0),
        _horizontal(60.0, 0.0, 710.0),
        _vertical(710.0, 60.0, 0.0),
    )
    SEGMENTS = (30.0, 355.0, 30.0)

    def test_perpendicular_legs_shorter_than_the_middle_are_f003(self) -> None:
        read = _read_declared_three_leg_shape(self.LEGS, self.SEGMENTS, 415.0)

        assert read == ("F003", (30.0, 355.0, 30.0), None)

    def test_the_drawing_scale_is_derived_not_assumed(self) -> None:
        """The koloni sheet draws 1 unit per cm and the shaibi sheet 2, so the
        check has to be proportional rather than absolute."""

        read = _read_declared_three_leg_shape(
            _legs(
                _vertical(0.0, 0.0, 150.0),
                _horizontal(150.0, 0.0, 1775.0),
                _vertical(1775.0, 150.0, 0.0),
            ),
            self.SEGMENTS,
            415.0,
        )

        assert read == ("F003", (30.0, 355.0, 30.0), None)

    def test_a_leg_at_almost_180_degrees_counts_as_parallel(self) -> None:
        """One real shaibi bar draws its middle leg right-to-left, so the raw
        angles come out 90 / 179.99 / 90. Wrapping at 180 is required or the
        two outer legs look 180 degrees apart instead of parallel."""

        read = _read_declared_three_leg_shape(
            _legs(
                _vertical(0.0, 40.0, 0.0),
                ((260.0, 40.0), (0.0, 40.0)),
                _vertical(260.0, 0.0, 40.0),
            ),
            (20.0, 130.0, 20.0),
            170.0,
        )

        assert read == ("F003", (20.0, 130.0, 20.0), None)


class TestFailClosed:
    def test_declared_lengths_that_do_not_match_the_drawing_are_refused(self) -> None:
        """The declaration and the geometry must describe the same bar. Here
        the drawn middle leg is far too long for the declared 21 cm."""

        legs = _legs(
            _vertical(0.0, 0.0, 150.0),
            ((0.0, 150.0), (60.0, 390.0)),
            _vertical(60.0, 390.0, 700.0),
        )

        assert _read_declared_three_leg_shape(legs, (150.0, 21.0, 310.0), 481.0) is None

    def test_a_fully_collinear_three_part_bar_is_refused(self) -> None:
        """No engine shape describes this: F002 requires an INCLINED middle.
        Rather than pick the nearest code, the operator decides."""

        legs = _legs(
            _vertical(0.0, 0.0, 150.0),
            _vertical(0.0, 150.0, 171.0),
            _vertical(0.0, 171.0, 481.0),
        )

        assert _read_declared_three_leg_shape(legs, (150.0, 21.0, 310.0), 481.0) is None

    def test_non_parallel_outer_legs_are_refused(self) -> None:
        legs = _legs(
            _vertical(0.0, 0.0, 60.0),
            _horizontal(60.0, 0.0, 710.0),
            ((710.0, 60.0), (740.0, 8.0)),
        )

        assert _read_declared_three_leg_shape(legs, (30.0, 355.0, 30.0), 415.0) is None

    def test_a_u_bar_whose_arm_is_longer_than_the_middle_is_refused(self) -> None:
        """The engine reserves that proportion for F005 and explicitly
        declares F003 as "both outer arms shorter than the middle", so this
        one is not ours to name."""

        legs = _legs(
            _vertical(0.0, 0.0, 400.0),
            _horizontal(400.0, 0.0, 100.0),
            _vertical(100.0, 400.0, 0.0),
        )

        assert _read_declared_three_leg_shape(legs, (200.0, 50.0, 200.0), 450.0) is None

    def test_the_wrong_number_of_legs_is_refused(self) -> None:
        assert (
            _read_declared_three_leg_shape(
                _legs(_vertical(0.0, 0.0, 150.0)), (150.0, 21.0, 310.0), 481.0
            )
            is None
        )

    def test_a_declaration_that_is_not_three_values_is_refused(self) -> None:
        legs = TestRealShaibiUBar.LEGS

        assert _read_declared_three_leg_shape(legs, (415.0,), 415.0) is None

    def test_segments_that_do_not_close_the_declared_total_are_refused(self) -> None:
        assert (
            _read_declared_three_leg_shape(
                TestRealShaibiUBar.LEGS, (30.0, 355.0, 30.0), 420.0
            )
            is None
        )

    def test_degenerate_geometry_is_refused(self) -> None:
        legs = _legs(
            _vertical(0.0, 0.0, 0.0),
            _horizontal(0.0, 0.0, 710.0),
            _vertical(710.0, 60.0, 0.0),
        )

        assert _read_declared_three_leg_shape(legs, (30.0, 355.0, 30.0), 415.0) is None


class TestF002IsASpliceNotAStairArm:
    """Owner, 2026-09-13: "да имаме такава мастър форма в стълбищата мисля",
    then, naming it the same day: "Като тази за стълбищен манш."

    Real shaibi position "4N16x323" in wall Ш 3-25/600 is two parallel 60 cm
    legs joined by a 203 cm diagonal at 42 degrees. The first version of this
    reader had no upper bound on the crank, so it named that F002 and let it
    through unreviewed. The engine already describes the family it really
    belongs to -- F019 "СТЪЛБИЩНО РАМО 5", whose own note records a real
    position with three segments and two shallow ~29 degree bends.

    The discriminator is not the angle but the proportion. F002 is
    "РЕДУКЦИЯ": a SHORT inclined splice between two long straight runs, as in
    the koloni bars [150, 21, 310] where the middle is the shortest segment.
    When the middle is the longest, the bar is a U (90 degrees) or -- shallow
    -- a stair arm, which the owner has now named: F019.

    The angle deliberately stays out of it. F019 carries
    ``angle_mode='AUTO'`` and no ``min/max_angle_deg`` in the engine, because
    stairs are designed at whatever pitch the building needs: the engine's own
    reference position 8N10x406 sits at about 29 degrees, this one at 42.
    """

    def test_a_short_inclined_splice_between_long_runs_is_f002(self) -> None:
        read = _read_declared_three_leg_shape(
            TestRealKoloniReduction.LEGS, TestRealKoloniReduction.SEGMENTS, 481.0
        )

        assert read is not None and read[0] == "F002"

    def test_a_long_diagonal_between_short_legs_is_a_stair_arm(self) -> None:
        """4N16x323, verbatim: legs 60 / 202.62 / 60 at 180 / 137.74 / 0.

        Until the owner named it, this returned None and the position reached
        Final Control with no shape at all, carrying a wrong F003 [90, 143,
        90] suggestion derived from one unrelated nearby dimension.
        """

        legs = _legs(
            ((60.0, 0.0), (0.0, 0.0)),
            ((0.0, 0.0), (-150.0, 136.3)),
            ((-150.0, 136.3), (-90.0, 136.3)),
        )

        read = _read_declared_three_leg_shape(legs, (60.0, 203.0, 60.0), 323.0)

        assert read is not None
        assert read[0] == "F019"
        assert read[1] == (60.0, 203.0, 60.0)
