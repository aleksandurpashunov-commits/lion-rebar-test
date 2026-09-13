"""A FOREIGN candidate cannot veto a proxy row's own declaration (rc44).

Owner's domain rule, stated 2026-09-11: "всеки един от надписите за опис
които ни трябват са доста близко до геометриите които отговарят за тях" --
a label always sits next to its own shape, so a DISTANT geometric match is
by definition not this bar's geometry.

``FAR_ASSOCIATION`` / ``GEOMETRY_OWNERSHIP_CONFLICT`` are statements about a
CANDIDATE. The declared evidence in this dialect does not rest on any
candidate: the bend-arc count is measured on the annotation's OWN exploded
proxy geometry (``PXG_<own parent>_*``), the segments come from its own text,
and the two are tied together by arithmetic that has to close exactly.
Blocking the declaration because some unrelated entity elsewhere on
``arm_koloni`` happened to match on length lets a stranger overrule the
designer.

Real koloni positions this held back, all declared F006 with five measured
bend arcs and an exactly-closing breakdown, all matched to a distant
straight bar:

    54N8x250, 27N8x260, 27N8x170, 50N8x210 (x2)   candidates 98E9DF, 9BD1FC
                                                  -- each shared by two
                                                  different positions

The distinction that keeps this safe: if the competing candidate IS the
annotation's own proxy geometry, the two proofs genuinely disagree about the
same object and the row still goes to a human.
"""
from __future__ import annotations

from lion_rebar.export.auto_gate import _proxy_arc_verified_evidence_is_owned


def _evidence(arcs: int = 5, confidence: str = "HIGH") -> dict:
    return {
        "source": "PROXY_ARC_VERIFIED_DECLARED_SEGMENTS",
        "shape_code": "F006",
        "segments": [8.0, 86.0, 31.0, 86.0, 31.0, 8.0],
        "confidence": confidence,
        "bend_arc_count": arcs,
    }


def _row(**extra) -> dict:
    """The real "54N8x250" row: own proxy 98E9E1, far candidate 98E9DF."""

    row = {
        "annotation_handle": "PXANN_98E9E1",
        "annotation_text": "54N8x250",
        "master_shape_code": "F006",
        "review_reason_codes": ["FAR_ASSOCIATION"],
        "matcher_blocker_codes": ["FAR_ASSOCIATION", "LENGTH_MISMATCH"],
        "candidate_handles": ["98E9DF"],
    }
    row.update(extra)
    return row


_SEGMENTS = (8.0, 86.0, 31.0, 86.0, 31.0, 8.0)
_TOTAL = 250.0


def test_a_far_candidate_that_is_not_our_geometry_does_not_block() -> None:
    assert (
        _proxy_arc_verified_evidence_is_owned(_row(), _evidence(), _SEGMENTS, _TOTAL)
        is True
    )


def test_an_ownership_conflict_over_foreign_geometry_does_not_block() -> None:
    """Two positions fighting over an entity that belongs to NEITHER of them
    says nothing about either one's own declaration."""

    row = _row(
        matcher_blocker_codes=["GEOMETRY_OWNERSHIP_CONFLICT", "FAR_ASSOCIATION"],
    )

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is True


def test_a_conflict_over_our_OWN_proxy_geometry_still_blocks() -> None:
    """Here the competing claim is about the very geometry the arc count was
    measured on, so the two proofs contradict each other on the same object.
    That is a real disagreement and a human decides it."""

    row = _row(candidate_handles=["PXG_98E9E1_3_0"])

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is False


def test_a_mixed_candidate_set_containing_our_own_geometry_still_blocks() -> None:
    row = _row(candidate_handles=["98E9DF", "PXG_98E9E1_3_0"])

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is False


def test_a_global_conflict_over_foreign_geometry_does_not_block() -> None:
    """``global_conflict`` reads like a whole-drawing verdict but is not one:
    the batch report raises it, together with ``geometry_owner_conflict``,
    when one DXF entity is the FIRST CANDIDATE of several annotations. It is
    therefore another statement about a candidate. Real case: 98E9DF is the
    first candidate of both "54N8x250" and "27N8x260" and belongs to
    neither.

    MASTER-shape ambiguity is a different signal (``master_conflict`` /
    ``AMBIGUOUS_MASTER``) and is asserted separately below.
    """

    row = _row(global_conflict=True, geometry_owner_conflict=True)

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is True


def test_a_global_conflict_over_our_own_geometry_still_blocks() -> None:
    row = _row(global_conflict=True, candidate_handles=["PXG_98E9E1_3_0"])

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is False


def test_master_shape_ambiguity_still_blocks_whoever_owns_the_geometry() -> None:
    """Genuine uncertainty about WHICH master shape applies is not a claim
    about a candidate, so candidate ownership cannot clear it."""

    assert (
        _proxy_arc_verified_evidence_is_owned(
            _row(master_conflict=True), _evidence(), _SEGMENTS, _TOTAL
        )
        is False
    )
    assert (
        _proxy_arc_verified_evidence_is_owned(
            _row(review_reason_codes=["FAR_ASSOCIATION", "AMBIGUOUS_MASTER"]),
            _evidence(),
            _SEGMENTS,
            _TOTAL,
        )
        is False
    )


def test_an_owner_conflict_flag_over_foreign_geometry_does_not_block() -> None:
    """``geometry_owner_conflict`` is the row-level form of the same
    candidate claim: real koloni positions "54N8x250" and "27N8x260" both
    matched 98E9DF, an entity neither of them owns. Two strangers arguing
    over a third party's geometry proves nothing about either declaration."""

    row = _row(
        geometry_owner_conflict=True,
        matcher_blocker_codes=["GEOMETRY_OWNERSHIP_CONFLICT", "FAR_ASSOCIATION"],
    )

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is True


def test_an_owner_conflict_flag_over_our_own_geometry_still_blocks() -> None:
    row = _row(geometry_owner_conflict=True, candidate_handles=["PXG_98E9E1_3_0"])

    assert _proxy_arc_verified_evidence_is_owned(row, _evidence(), _SEGMENTS, _TOTAL) is False


def test_every_other_condition_is_still_enforced() -> None:
    """Relaxing the candidate veto must not relax anything else."""

    # MEDIUM confidence stays manual.
    assert (
        _proxy_arc_verified_evidence_is_owned(
            _row(), _evidence(confidence="MEDIUM"), _SEGMENTS, _TOTAL
        )
        is False
    )
    # Zero arcs cannot prove a bent family.
    assert (
        _proxy_arc_verified_evidence_is_owned(
            _row(), _evidence(arcs=0), _SEGMENTS, _TOTAL
        )
        is False
    )
    # The breakdown must still close the declared total.
    assert (
        _proxy_arc_verified_evidence_is_owned(_row(), _evidence(), _SEGMENTS, 260.0)
        is False
    )
    # Still has to be one of this dialect's annotations.
    assert (
        _proxy_arc_verified_evidence_is_owned(
            _row(annotation_handle="9BD1FC"), _evidence(), _SEGMENTS, _TOTAL
        )
        is False
    )
