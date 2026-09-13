"""The proxy dialect declares a closed stirrup EXACTLY -- read it, don't guess.

Owner-reported class, 2026-09-12: eleven koloni positions carried production
sizes that silently contradicted their own declared length (e.g. "100N8x94"
exported as 18x18 hook 8 -> unfolds to 88 cm, not 94). The numbers were not
missing from the drawing: the designer had written ``21, 18, 21, 18, 8``.

Root cause (measured, not assumed). ``_derive_declared_shape_evidence`` fed
these declared numbers to ``vertical_shape_resolver.derive_shape``, which is
built for a DIFFERENT dialect -- one where each size is a ``DIMENSION``
placed somewhere on the sheet, and the right sizes are picked by being
NEAREST the bar. The proxy dialect has no such geometry, so the adapter gave
every number the same anchor point. With all distances equal, the resolver's
``(gap_rank, hook, distance_sum)`` score ties, and ``_read_stirrup`` keeps
whichever combination it happened to reach first. ``collect_dimensions``
sorts ties by ascending value, so the SMALLER side is always reached first:
``(18, 18)`` -- which the resolver accepts because its bend-allowance window
(``0 <= gap <= 8``) was designed to absorb a genuine ~5 cm allowance in that
other dialect -- beats the exactly-closing ``(21, 18)``.

The fix is not to re-tune that shared score: in the DIMENSION dialect a ~5 cm
gap is correct and expected, so "smallest gap wins" would be wrong there.
The fix is that this dialect never needed a spatial resolver at all. It
states the closed stirrup outright as ``[a, b, a, b, hook(, hook)]`` and
``2*(a + b + hook)`` equals the declared length to the centimetre -- verified
on 56 of 56 bent koloni annotations in the real drawing, no exceptions.

Fail-closed is part of the fix: a declaration SHAPED like a closed stirrup
whose arithmetic does not close is returned as unknown, so the position
reaches the operator, instead of being handed to the resolver to guess at.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.proxy_adapter import _read_declared_closed_stirrup


class TestRealDrawingDeclarations:
    """Every case below is a verbatim declaration from
    ``23_83_koloni_ot +18.18 do +21.48.dxf``."""

    @pytest.mark.parametrize(
        "declared, length, expected",
        [
            # The eleven that were silently wrong before this fix.
            ((21.0, 18.0, 21.0, 18.0, 8.0), 94.0, (21.0, 18.0, 8.0)),
            ((21.0, 17.0, 21.0, 17.0, 8.0, 8.0), 92.0, (21.0, 17.0, 8.0)),
            ((31.0, 32.0, 31.0, 32.0, 8.0), 142.0, (31.0, 32.0, 8.0)),
            # Genuinely square -- correct before, but only MEDIUM because the
            # resolver counted DISTINCT numbers and a square stirrup has two.
            ((31.0, 31.0, 31.0, 31.0, 8.0, 8.0), 140.0, (31.0, 31.0, 8.0)),
            # Cases the resolver already got right, which must stay right.
            ((51.0, 26.0, 51.0, 26.0, 8.0, 8.0), 170.0, (51.0, 26.0, 8.0)),
            ((61.0, 31.0, 61.0, 31.0, 8.0), 200.0, (61.0, 31.0, 8.0)),
            ((156.0, 31.0, 156.0, 31.0, 8.0), 390.0, (156.0, 31.0, 8.0)),
            ((18.0, 31.0, 18.0, 31.0, 8.0, 8.0), 114.0, (18.0, 31.0, 8.0)),
        ],
    )
    def test_declared_sizes_are_read_verbatim(
        self, declared: tuple[float, ...], length: float, expected: tuple[float, ...]
    ) -> None:
        assert _read_declared_closed_stirrup(declared, length) == expected

    def test_the_read_always_unfolds_back_to_the_declared_length(self) -> None:
        """The whole point: what we export must reproduce the length the
        designer wrote. This is the assertion the old path failed."""

        declared, length = (21.0, 18.0, 21.0, 18.0, 8.0), 94.0

        width, height, hook = _read_declared_closed_stirrup(declared, length)

        assert hook + width + height + width + height + hook == length


class TestFailClosed:
    def test_arithmetic_that_does_not_close_is_refused(self) -> None:
        """Shaped like a stirrup, but 2*(20+18+8) = 92, not 94. Nothing here
        is trustworthy, so the operator decides -- we do not fall back to
        guessing a combination that happens to fit."""

        assert _read_declared_closed_stirrup((20.0, 18.0, 20.0, 18.0, 8.0), 94.0) is None

    def test_a_non_repeating_prefix_is_not_a_closed_stirrup(self) -> None:
        assert _read_declared_closed_stirrup((21.0, 18.0, 19.0, 18.0, 8.0), 94.0) is None

    def test_the_shaibi_open_shape_is_left_to_the_existing_path(self) -> None:
        """``[leg, hook, hook]`` summing to the length is the F003 grammar in
        the shaibi drawing (18 of 18 annotations). It is not a closed
        stirrup and must not be claimed by this reader."""

        assert _read_declared_closed_stirrup((21.0, 8.0, 8.0), 37.0) is None
        assert _read_declared_closed_stirrup((31.0, 8.0, 8.0), 47.0) is None

    def test_mismatched_hooks_are_refused(self) -> None:
        assert _read_declared_closed_stirrup((21.0, 18.0, 21.0, 18.0, 8.0, 9.0), 94.0) is None

    def test_a_hook_larger_than_a_side_is_refused(self) -> None:
        """The hook is the short return leg by definition. Anything else
        means the numbers are not what we think they are."""

        assert _read_declared_closed_stirrup((8.0, 18.0, 8.0, 18.0, 9.0), 70.0) is None

    def test_too_few_or_too_many_trailing_hooks_are_refused(self) -> None:
        assert _read_declared_closed_stirrup((21.0, 18.0, 21.0, 18.0), 78.0) is None
        assert (
            _read_declared_closed_stirrup((21.0, 18.0, 21.0, 18.0, 8.0, 8.0, 8.0), 94.0)
            is None
        )

    def test_non_positive_values_are_refused(self) -> None:
        assert _read_declared_closed_stirrup((21.0, 0.0, 21.0, 0.0, 8.0), 58.0) is None
