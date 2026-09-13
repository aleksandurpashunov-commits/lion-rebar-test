"""AUTO approval for arc-verified declared shapes (rc44).

Owner-requested 2026-09-12: "целим програмата ни да е професионална с
минимално инженерно вмешателство". These positions carry no geometric
candidate, so the old gate held every one of them for a manual review that
demonstrably added no safety -- a full real review pass confirmed eleven
arc-verified F006 stirrups as straight bars instead.

What replaces it is deliberately narrow and re-proven twice: the writer
(``_apply_proxy_declared_shape_auto_promotion``) and the gate
(``auto_gate.validate_production_segment_evidence``) each enforce the same
conditions independently, so widening one alone cannot widen AUTO.
"""
from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import (
    _apply_proxy_declared_shape_auto_promotion,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.proxy_adapter import PROXY_DECLARED_SHAPE_CODE
from lion_rebar.export.auto_gate import (
    auto_classification_gate_passes,
    validate_production_segment_evidence,
)


_SHAPES = (
    SimpleNamespace(code="F001", name="ПРАВ ПРЪТ"),
    SimpleNamespace(code="F003", name="ПРЪТ С ДВА ВИНКЕЛА"),
    SimpleNamespace(code="F006", name="СТРЕМЕ (БИГЛА)"),
)


def _annotation(
    handle: str,
    *,
    shape_code: str,
    segments: list[float],
    arcs: int,
    confidence: str = "HIGH",
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="BLOCK_ATTR_TEXT",
        layer="ARM_KOLONI",
        text="x",
        geometry={"insert": [0.0, 0.0, 0.0]},
        raw={
            "declared_shape_source": PROXY_DECLARED_SHAPE_CODE,
            "declared_shape_code": shape_code,
            "declared_shape_segments_cm": segments,
            "declared_shape_confidence": confidence,
            "declared_shape_bend_arc_count": arcs,
        },
    )


def _row(handle: str, *, length: float, **extra) -> dict:
    row = {
        "annotation_handle": handle,
        "declared_length_cm": length,
        "review_reason_codes": [],
        "matcher_blocker_codes": [],
    }
    row.update(extra)
    return row


def _promote(rows, annotations):
    return _apply_proxy_declared_shape_auto_promotion(
        rows,
        annotations_by_handle=annotations,
        shape_definitions=_SHAPES,
    )


def test_arc_verified_f006_is_promoted_and_clears_the_auto_gate() -> None:
    """The dominant real case: 5 structural bend arcs prove the family, the
    designer's own 51/26/8 breakdown closes 170 cm exactly once unfolded
    into the locked MASTER traversal (hook, A, B, A, B, hook)."""

    row = _row("PXANN_A", length=170.0)
    annotations = {
        "PXANN_A": _annotation(
            "PXANN_A", shape_code="F006", segments=[51.0, 26.0, 8.0], arcs=5
        )
    }

    assert _promote([row], annotations) == 1
    assert row["master_shape_code"] == "F006"
    assert row["master_shape_name"] == "СТРЕМЕ (БИГЛА)"
    assert row["production_segments_cm"] == [8.0, 51.0, 26.0, 51.0, 26.0, 8.0]
    assert sum(row["production_segments_cm"]) == 170.0
    # The gate re-proves everything from the persisted row alone.
    assert validate_production_segment_evidence(row)
    assert auto_classification_gate_passes(row)


def test_straight_f001_with_zero_arcs_is_promoted() -> None:
    row = _row("PXANN_B", length=415.0)
    annotations = {
        "PXANN_B": _annotation(
            "PXANN_B", shape_code="F001", segments=[415.0], arcs=0
        )
    }

    assert _promote([row], annotations) == 1
    assert row["master_shape_code"] == "F001"
    assert row["production_segments_cm"] == [415.0]
    assert auto_classification_gate_passes(row)


def test_f002_reduction_splice_is_never_auto_approved() -> None:
    """F002 shares "zero bend arcs" with F001 but carries a three-part
    breakdown whose middle value is a reduction, not a length -- exactly the
    distinction that already produced one real misclassification
    (owner-caught, 2026-09-11). It stays a manual decision."""

    row = _row("PXANN_C", length=481.0)
    annotations = {
        "PXANN_C": _annotation(
            "PXANN_C", shape_code="F002", segments=[150.0, 21.0, 310.0], arcs=0
        )
    }

    assert _promote([row], annotations) == 0
    assert "master_shape_code" not in row
    assert not auto_classification_gate_passes(row)


def test_medium_resolver_confidence_stays_manual() -> None:
    row = _row("PXANN_D", length=170.0)
    annotations = {
        "PXANN_D": _annotation(
            "PXANN_D",
            shape_code="F006",
            segments=[51.0, 26.0, 8.0],
            arcs=5,
            confidence="MEDIUM",
        )
    }

    assert _promote([row], annotations) == 0
    assert not auto_classification_gate_passes(row)


def test_far_association_or_ownership_conflict_stays_manual() -> None:
    """A competing geometric claim means the generic matcher disagrees about
    which geometry this text owns; a human decides, never AUTO."""

    far = _row("PXANN_E", length=170.0, review_reason_codes=["FAR_ASSOCIATION"])
    conflicted = _row(
        "PXANN_F",
        length=170.0,
        matcher_blocker_codes=["GEOMETRY_OWNERSHIP_CONFLICT"],
    )
    annotations = {
        handle: _annotation(
            handle, shape_code="F006", segments=[51.0, 26.0, 8.0], arcs=5
        )
        for handle in ("PXANN_E", "PXANN_F")
    }

    assert _promote([far, conflicted], annotations) == 0
    assert not auto_classification_gate_passes(far)
    assert not auto_classification_gate_passes(conflicted)


def test_weak_matcher_shape_loses_to_the_arc_proof() -> None:
    """The real failure this fixes: a coincidental length hit on a generic
    layer (which carries no diameter for geometry_candidates.py to reject
    against) labelled a stirrup "F001 straight bar". Five real structural
    bend arcs say otherwise, and the matcher's own confidence says manual
    review -- so the arc proof wins and the row is corrected, not queued."""

    row = _row(
        "PXANN_G",
        length=170.0,
        master_shape_code="F001",
        batch_status="ЗА РЪЧЕН ПРЕГЛЕД",
    )
    annotations = {
        "PXANN_G": _annotation(
            "PXANN_G", shape_code="F006", segments=[51.0, 26.0, 8.0], arcs=5
        )
    }

    assert _promote([row], annotations) == 1
    assert row["master_shape_code"] == "F006"
    assert auto_classification_gate_passes(row)


def test_strong_matcher_shape_is_never_overruled_silently() -> None:
    """When BOTH sources are strong and disagree, neither wins: the row is
    flagged, pulled back to manual review, and can no longer export through
    AUTO carrying a contradicted shape.

    Narrowed 2026-09-13 by the owner's own review of project ``s-7``. This
    test was first written for eight real "43N8x37"-family rows where the
    matcher claimed F001 and the declaration said F003, and holding them was
    right while the true answer was unknown. The owner then resolved all eight
    as F007, and their geometry carries a real 135-degree arc -- so that
    particular disagreement is settled by physics rather than by scores, and
    now promotes (see tests/test_arc_proof_beats_a_straight_claim.py).

    The guard itself stands for every disagreement physics cannot settle:
    two BENT families claiming the same bar.
    """

    row = _row(
        "PXANN_H",
        length=37.0,
        master_shape_code="F006",
        batch_status="СИЛЕН ПО ГЕОМЕТРИЯ",
        matcher_disposition="CONTROL",
    )
    annotations = {
        "PXANN_H": _annotation(
            "PXANN_H", shape_code="F003", segments=[8.0, 21.0, 8.0], arcs=1
        )
    }

    assert _promote([row], annotations) == 0
    assert "DECLARED_SHAPE_CONTRADICTS_GEOMETRY" in row["review_reason_codes"]
    assert row["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert row["master_shape_code"] == "F006"  # not silently rewritten
    assert not auto_classification_gate_passes(row)


def test_segments_that_do_not_close_the_declared_total_are_rejected() -> None:
    """Fail-closed: the declared breakdown must account for the declared
    length, or the position is not proven at all."""

    row = _row("PXANN_I", length=999.0)
    annotations = {
        "PXANN_I": _annotation(
            "PXANN_I", shape_code="F006", segments=[51.0, 26.0, 8.0], arcs=5
        )
    }

    assert _promote([row], annotations) == 0
    assert not auto_classification_gate_passes(row)


def test_non_proxy_row_can_never_use_this_evidence_source() -> None:
    """The gate accepts this evidence only for this dialect's own
    annotations, even if a row somehow carried the evidence mapping."""

    row = {
        "annotation_handle": "PLAIN_TEXT_HANDLE",
        "declared_length_cm": 170.0,
        "master_shape_code": "F006",
        "production_segments_cm": [8.0, 51.0, 26.0, 51.0, 26.0, 8.0],
        "production_segment_evidence": {
            "source": PROXY_DECLARED_SHAPE_CODE,
            "shape_code": "F006",
            "segments": [8.0, 51.0, 26.0, 51.0, 26.0, 8.0],
            "confidence": "HIGH",
            "bend_arc_count": 5,
        },
    }

    assert not validate_production_segment_evidence(row)
    assert not auto_classification_gate_passes(row)
