"""rc36 regressions for the explicit ``ЕСОВЕ`` / F007 detail context.

The drawing may contain a thick closed outline which is only a visual
reference.  Its perimeter is not a production centreline and the nearby
dimensions are not sufficient to invent F007 segment lengths.  The explicit
``СХЕМА ЕСОВЕ`` label may therefore identify only the MASTER family; the
operator still confirms the exact production segments.
"""

from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import (
    _apply_dimension_derived_production_sizes,
)
from lion_rebar.dxf.local_detail_resolver import local_detail_shape_code
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.ui.review_center import _dimension_evidence_html


def _dimension(handle: str, measurement: float, x: float, y: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "DIMENSION",
        "koti",
        {
            "measurement": measurement,
            "text_midpoint": [x, y, 0.0],
            "defpoint": [x, y, 0.0],
        },
        text=f"{measurement:g}",
    )


def test_explicit_esove_heading_is_f007_local_context() -> None:
    assert local_detail_shape_code("%%UСХЕМА ЕСОВЕ ПРИ КОЛОНИ") == "F007"
    assert local_detail_shape_code("Схема есове при шайби") == "F007"
    assert (
        local_detail_shape_code("S за продължаване /на шайби и колони/")
        == "F007"
    )


def test_spacing_or_arbitrary_latin_s_is_not_f007_context() -> None:
    assert local_detail_shape_code("s=20") is None
    assert local_detail_shape_code("S") is None
    assert local_detail_shape_code("SOME RANDOM DETAIL") is None


def test_esove_context_suggests_only_f007_family_without_geometry_or_segments() -> None:
    row = {
        "annotation_handle": "DB88",
        "annotation_text": "2170N10x47",
        "declared_length_cm": 47.0,
        "master_shape_code": None,
        "candidate_handles": [],
        "candidate_notes": [],
        "local_detail_shape_code": "F007",
        "local_detail_label_handle": "DB32",
        "local_detail_label_text": "%%UСХЕМА ЕСОВЕ ПРИ КОЛОНИ",
        "local_detail_rule_code": "LOCAL_DETAIL_LABEL_F007",
    }
    entities = [
        DxfEntityRecord(
            "DB88",
            "BLOCK_ATTR_TEXT",
            "ARM_TXT",
            {"insert": [0.0, 0.0, 0.0]},
            text="2170N10x47",
        ),
        # These reproduce the kind of nearby overall dimensions seen in R-3.
        # They must remain visible evidence, but must not turn into an F004 or
        # an invented F007 production vector.
        _dimension("DB81", 50.0, 5.0, 2.0),
        _dimension("DB84", 36.5, 8.0, 2.0),
        _dimension("DB87", 25.0, 5.0, 5.0),
    ]

    resolved = _apply_dimension_derived_production_sizes([row], entities)

    assert resolved == 0
    assert row["suggested_shape_code"] == "F007"
    assert row.get("suggested_segments_cm") == []
    assert row.get("production_segments_cm") in (None, [])
    assert row.get("master_shape_code") in (None, "")
    assert row.get("candidate_handles") == []
    assert row.get("candidate_geometry_handle") in (None, "")
    assert row["suggested_shape_code"] != "F004"


def test_review_html_explains_family_only_f007_without_guessing_segments() -> None:
    html = _dimension_evidence_html(
        {
            "suggested_shape_code": "F007",
            "suggested_segments_cm": [],
            "suggested_evidence": {
                "family_only": True,
                "source": "ИЗРИЧЕН ЛОКАЛЕН НАДПИС ЕСОВЕ",
            },
        }
    )

    assert "F007" in html
    assert "РАЗПОЗНАТ" in html.upper()
    assert "сегмент" in html.lower()
    assert "11.5" not in html
    assert "13.5" not in html
    assert "24" not in html
