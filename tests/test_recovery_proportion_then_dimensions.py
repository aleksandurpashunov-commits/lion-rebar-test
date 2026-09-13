"""Recovery.2 follow-up: proportion resolution must chain into A-E dims.

Real workflow (owner variant 1): the drawn bar is slightly off (25/660.1/25 =
710.1) while the designer's annotation says 700, and printed dimensions
25/650/25 carry the production values.  The proportion rule resolves the
F003/F005 pair to the unique identity; the printed A-E values must then align
the production length for that unique shape (the same
``_resolve_unique_master_dimensions`` path an initially-unambiguous match
gets), so the row can become exact instead of stalling in manual review with
the drawn 10.1 cm difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.package_io import inspect_engine_bundle

V0313 = "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"


@pytest.fixture(scope="module")
def engine():
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / V0313
    if not bundle.exists():  # pragma: no cover - shipped with the release
        pytest.skip(f"Engine bundle is not present: {V0313}")
    package, _manifest, _names = inspect_engine_bundle(bundle)
    return package


def _case_with_dims():
    """Drawn 25/660.1/25 (sum 710.1), annotation 3N20x700, dims 25/650/25."""
    annotation = DxfEntityRecord(
        "A1",
        "BLOCK_ATTR_TEXT",
        "ARM-N20",
        {"insert": [330.0, -30.0, 0.0]},
        "3N20x700",
    )
    entities = [
        annotation,
        DxfEntityRecord(
            "G1",
            "LINE",
            "ARM-N20",
            {"start": [0.0, 25.0, 0.0], "end": [0.0, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            "G2",
            "LINE",
            "ARM-N20",
            {"start": [0.0, 0.0, 0.0], "end": [660.1, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            "G3",
            "LINE",
            "ARM-N20",
            {"start": [660.1, 0.0, 0.0], "end": [660.1, 25.0, 0.0]},
        ),
        DxfEntityRecord(
            "D1",
            "TEXT",
            "ARM_TXT1",
            {"insert": [330.0, 8.0, 0.0], "rotation": 0.0, "height": 12.0},
            "650",
        ),
        DxfEntityRecord(
            "D2",
            "TEXT",
            "ARM_TXT1",
            {"insert": [-10.0, 12.5, 0.0], "rotation": 90.0, "height": 12.0},
            "25",
        ),
        DxfEntityRecord(
            "D3",
            "TEXT",
            "ARM_TXT1",
            {"insert": [670.0, 12.5, 0.0], "rotation": 90.0, "height": 12.0},
            "25",
        ),
    ]
    return annotation, entities


def test_proportion_resolution_then_printed_dims_align_the_length(engine):
    annotation, entities = _case_with_dims()
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    # Идентичността идва от пропорционалното правило на собственика...
    assert top.master_shape_code == "F003"
    assert top.contextual_rule_code == "U_PROPORTION_F003"
    assert "F005" not in set(top.master_ambiguous_shape_codes)
    # ...а печатните коти изравняват производствената дължина към L=700.
    assert top.dimension_evidence_applied, (
        "печатните 25/650/25 не са приложени след пропорционалното решение; "
        f"numeric_difference={top.numeric_difference}"
    )
    assert top.numeric_difference == pytest.approx(0.0, abs=1e-6)
    assert top.canonical_segment_lengths_cm == (25.0, 650.0, 25.0)


def test_without_dims_the_length_difference_still_asks_for_review(engine):
    """Guard: same bar without printed dims keeps the honest 10.1 cm
    difference and stays in manual review -- the drawing mismatch is real."""
    annotation, entities = _case_with_dims()
    entities = [e for e in entities if not e.handle.startswith("D")]
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.master_shape_code == "F003"
    assert top.numeric_difference == pytest.approx(10.1, abs=0.05)
    assert "РЪЧНА" in top.confidence
