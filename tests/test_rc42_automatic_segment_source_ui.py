from __future__ import annotations

from types import SimpleNamespace

import pytest

from lion_rebar.ui.review_center import (
    ReviewCenterDialog,
    _production_segment_source_label,
)


class _TextSink:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, value: str) -> None:  # noqa: N802 - Qt-compatible stub
        self.value = value

    def setHtml(self, value: str) -> None:  # noqa: N802 - Qt-compatible stub
        self.value = value


def _row(source: str) -> dict:
    return {
        "annotation_handle": "H3::BJ-FI-L",
        "annotation_text": "36N8x245",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 36,
        "diameter_mm": 8,
        "declared_length_cm": 245,
        "candidate_handles": ["H3-CONTOUR"],
        "candidate_layer": "ARM-N10",
        "master_shape_code": "F006",
        "master_shape_name": "СТРЕМЕ",
        "production_segments_cm": [10, 20, 90, 20, 90, 10],
        "production_segment_evidence": {
            "source": source,
            "shape_code": "F006",
            "segments": [10, 20, 90, 20, 90, 10],
        },
        "review_reason_codes": ["F006_BORDERLINE_CURVE_OWNED_DIMENSIONS"],
    }


def test_borderline_f006_summary_names_automatic_owned_dimensions() -> None:
    row = _row("F006_BORDERLINE_CURVE_OWNED_DIMENSIONS")
    summary = _TextSink()
    dialog = SimpleNamespace(
        details=_TextSink(),
        review_reason_label=_TextSink(),
        position_summary_label=summary,
        _state={"decisions": {}},
        _handle=lambda selected: selected["annotation_handle"],
        geometry_sample_handles=[],
        f006_sample_handles=[],
    )

    ReviewCenterDialog._render_details(dialog, row)

    assert "АВТОМАТИЧНО ИЗВЕДЕНО ОТ СОБСТВЕНИ КОТИ" in summary.value
    assert "операторско/производствено решение" not in summary.value


@pytest.mark.parametrize(
    "source",
    (
        "F006_OWNED_CONTOUR_DIMENSIONS",
        "F006_ROUNDED_LOCAL_PRINTED",
        "F006_PRINTED_MASTER_EXACT",
        "STRICT_LOCAL_DIMENSIONS",
    ),
)
def test_known_local_dimension_sources_are_not_called_operator_decisions(
    source: str,
) -> None:
    assert _production_segment_source_label(_row(source)) == (
        "АВТОМАТИЧНО ИЗВЕДЕНО ОТ СОБСТВЕНИ КОТИ"
    )


def test_unknown_production_source_keeps_conservative_legacy_label() -> None:
    assert _production_segment_source_label(_row("UNRECOGNISED_SOURCE")) == (
        "операторско/производствено решение"
    )
