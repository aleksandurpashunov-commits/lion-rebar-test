"""The ``PXANN_`` status bypass covers DECLARED evidence only (rc44).

Owner-reported class, 2026-09-12: "без тихи и скрити грешки".

``is_deterministic_production_source`` lets a row clear AUTO even while the
geometry matcher stays MANUAL. That is sound for ``::BJ-FI-L`` and for the
``PXANN_`` proxy dialect for ONE reason: the numbers come from the designer's
own structured declaration rather than from guessing at geometry.

The first version of the ``PXANN_`` arm keyed on the handle prefix alone, so
the bypass also covered rows whose production segments came from the GEOMETRY
MATCHER -- the exact opposite of the justification. Real case in
``38_83_shaibi_+18.18 do +21.48.dxf``:

    12N12x240   declared F001, bend_arc_count 0  (proven straight)
                exported F003 [50, 140, 50]      (matcher, foreign candidate)
                batch_status "ЗА РЪЧЕН ПРЕГЛЕД"  -- bypassed anyway

A straight bar left for the operator was silently shipped as a bent one. The
bypass now requires the evidence to actually BE the declared proxy source;
matcher-derived evidence on a proxy row faces the ordinary status and
disposition gate like any other matcher result.
"""
from __future__ import annotations

from lion_rebar.dxf.proxy_adapter import PROXY_DECLARED_SHAPE_CODE
from lion_rebar.export.auto_gate import (
    auto_classification_gate_passes,
    is_deterministic_production_source,
)


def _declared_evidence() -> dict:
    return {
        "source": PROXY_DECLARED_SHAPE_CODE,
        "shape_code": "F001",
        "segments": [390.0],
        "confidence": "HIGH",
        "bend_arc_count": 0,
        "declared_segments_cm": [390.0],
    }


def _matcher_evidence() -> dict:
    """Verbatim from the real "12N12x240" row."""

    return {
        "source": "MATCHER_LOCAL_A_E_CANONICAL",
        "shape_code": "F003",
        "segments": [50.0, 140.0, 50.0],
        "turn_angles_deg": [],
        "confidence": "HIGH",
        "candidate_handles": ["7A5339"],
        "dimension_handles": ["7A5332"],
        "dimension_measurements_cm": [{"handle": "7A5332", "measurement_cm": 140.0}],
        "dimension_resolution_strategy": "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS",
        "dimension_total_difference_cm": 0.0,
    }


def _row(*, evidence: dict, status: str, **extra) -> dict:
    """Field-for-field the real "12N12x240" row, so the assertions below
    exercise the same validation path production does. Verified against the
    live report: before the fix it reported
    ``det=True valid=True gate=True`` at status "ЗА РЪЧЕН ПРЕГЛЕД"."""

    row = {
        "annotation_handle": "PXANN_7A5319",
        "annotation_text": "12N12x240",
        "diameter_mm": 12.0,
        "declared_length_cm": 240.0,
        "production_segment_evidence": evidence,
        "production_segments_cm": list(evidence["segments"]),
        "canonical_segment_lengths_cm": list(evidence["segments"]),
        "master_shape_code": evidence["shape_code"],
        "batch_status": status,
        "matcher_disposition": "MANUAL",
        "candidate_handles": ["7A5339"],
        "candidate_confidence": "ДАЛЕЧНА ВРЪЗКА — РЪЧНА ПРОВЕРКА",
        "dimension_evidence_applied": True,
        "dimension_resolution_strategy": "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS",
        "dimension_text_handles": ["7A5332"],
        "dimension_total_difference_cm": 0.0,
        "review_reason_codes": ["FAR_ASSOCIATION"],
        "matcher_blocker_codes": [],
    }
    row.update(extra)
    return row


def test_matcher_evidence_on_a_proxy_row_does_not_bypass_the_status_gate() -> None:
    """The real silent-error case. The matcher's own verdict was MANUAL and
    the batch status said so; nothing about the proxy text makes that
    verdict deterministic."""

    row = _row(evidence=_matcher_evidence(), status="ЗА РЪЧЕН ПРЕГЛЕД")

    assert is_deterministic_production_source(row) is False
    assert auto_classification_gate_passes(row) is False


def test_declared_proxy_evidence_still_bypasses_the_status_gate() -> None:
    """This is the behaviour the bypass exists for and it must survive: the
    status reflects a foreign candidate the row does not rely on."""

    row = _row(
        evidence=_declared_evidence(),
        status="РИСК",
        master_shape_code="F001",
        production_segments_cm=[390.0],
        canonical_segment_lengths_cm=[390.0],
        declared_length_cm=390.0,
        annotation_text="20N10x390",
        dimension_evidence_applied=False,
        dimension_resolution_strategy="",
        dimension_text_handles=[],
        review_reason_codes=[],
    )

    assert is_deterministic_production_source(row) is True
    assert auto_classification_gate_passes(row) is True


def test_a_proxy_row_with_matcher_evidence_and_a_clean_status_still_passes() -> None:
    """Narrowing the bypass must not reject matcher results that were never
    relying on it -- they go through the ordinary gate."""

    row = _row(
        evidence=_matcher_evidence(),
        status="СИЛЕН",
        matcher_disposition="AUTO",
    )

    assert is_deterministic_production_source(row) is False
    assert auto_classification_gate_passes(row) is True


def test_the_block_attribute_dialect_is_untouched() -> None:
    row = _row(evidence=_matcher_evidence(), status="ЗА РЪЧЕН ПРЕГЛЕД")
    row["annotation_handle"] = "AB12::BJ-FI-L"

    assert is_deterministic_production_source(row) is True
