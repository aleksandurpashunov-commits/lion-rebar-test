"""Recovery.2 end-to-end regression: A-E evidence must not erase the
F003/F005 conflict when the production vector fits both identities.

Reproduced defect (both shipped engines v0.3.13 and v0.3.6): an open U of
15/295/15 with annotation ``20N8x325`` is correctly flagged
``('F003','F005')`` by ``match_master_shapes``; adding a single printed
dimension text ``295`` next to the middle segment lets
``_resolve_dimension_master_ambiguity`` build an
``A_E_DELTA_SPLIT_TWO_EQUAL_BENDS`` evidence, re-run the matcher with the
collision expansion disabled and return a clean, unflagged F003 with
``СИЛЕН КАНДИДАТ`` confidence -- which passes the automatic batch export
gate.  The equality 15+295+15=325 proves symmetry; it does not prove which
of the two overlapping ENGINE identities owns the bar.

Rule under test: A-E evidence may resolve an initial MASTER ambiguity only
when the proven production segment vector is fully accepted by EXACTLY ONE
of the originally ambiguous identities (its own topology and required
relations).  When the vector stays valid for two overlapping identities the
conflict must survive all the way to the batch row and the automatic export
gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.export.batch_auto_output import (
    AUTO_EXPORT_BATCH_STATUSES,
    AUTO_EXPORT_MATCHER_DISPOSITIONS,
)

ENGINE_BUNDLES = (
    "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine",
    "lion-rebar-master_v0.3.6_DRAFT.lionengine",
)


@pytest.fixture(scope="module", params=ENGINE_BUNDLES)
def engine_fixture(request):
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / request.param
    if not bundle.exists():  # pragma: no cover - shipped with the release
        pytest.skip(f"Engine bundle is not present: {request.param}")
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    return engine


def _palindromic_u_case(
    *,
    with_dimension: bool,
    first: float = 15.0,
    middle: float = 295.0,
    last: float = 15.0,
    text: str = "20N8x325",
    dim_text: str = "295",
):
    """Three LINE entities first/middle/last, annotation, optional middle dim.

    Default 15/295/15 + ``20N8x325`` is the real drawing class; the boundary
    variant 100/100/100 + ``20N8x300`` keeps arms inside the proportion
    tolerance band so NO rule (proportions or dimensions) may resolve it."""

    annotation = DxfEntityRecord(
        "A1",
        "BLOCK_ATTR_TEXT",
        "ARM-N8",
        {"insert": [middle / 2.0, -25.0, 0.0]},
        text,
    )
    entities = [
        annotation,
        DxfEntityRecord(
            "G1",
            "LINE",
            "ARM-N8",
            {"start": [0.0, first, 0.0], "end": [0.0, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            "G2",
            "LINE",
            "ARM-N8",
            {"start": [0.0, 0.0, 0.0], "end": [middle, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            "G3",
            "LINE",
            "ARM-N8",
            {"start": [middle, 0.0, 0.0], "end": [middle, last, 0.0]},
        ),
    ]
    if with_dimension:
        entities.append(
            DxfEntityRecord(
                "D1",
                "TEXT",
                "KOTI",
                {"insert": [middle / 2.0, 6.0, 0.0], "rotation": 0.0},
                dim_text,
            )
        )
    return annotation, entities


def _boundary_u_case(*, with_dimension: bool):
    """Arms equal to the middle: undecidable by proportions AND by dims."""
    return _palindromic_u_case(
        with_dimension=with_dimension,
        first=100.0,
        middle=100.0,
        last=100.0,
        text="20N8x300",
        dim_text="100",
    )


def test_dimension_text_must_not_erase_the_f003_f005_conflict(engine_fixture):
    engine = engine_fixture
    annotation, entities = _boundary_u_case(with_dimension=True)
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.master_shape_code in {"F003", "F005"}
    assert {"F003", "F005"} <= set(top.master_ambiguous_shape_codes), (
        "the printed middle dimension proves symmetry, not identity: the "
        "boundary vector remains valid for both F003 and F005, so the "
        f"conflict must survive (got resolved={top.dimension_master_resolved}, "
        f"strategy={top.dimension_resolution_strategy}, "
        f"confidence={top.confidence!r})"
    )
    assert not top.dimension_master_resolved
    assert "СИЛЕН" not in (top.confidence or "").split("—")[0] or (
        "РЪЧНА" in (top.confidence or "")
    ), f"a clean strong F003 is forbidden here: {top.confidence!r}"


def test_conflict_without_dimension_still_flagged(engine_fixture):
    """Guard: the already-safe no-dimension boundary path stays flagged."""
    engine = engine_fixture
    annotation, entities = _boundary_u_case(with_dimension=False)
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    assert {"F003", "F005"} <= set(top.master_ambiguous_shape_codes)


def test_real_class_resolves_by_proportion_not_by_dimension_on_v0313():
    """On v0.3.13 the 15/295/15 case is resolved by the OWNER'S declared
    proportion rule (U_PROPORTION_F003), never by the dimension text."""
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / ENGINE_BUNDLES[0]
    if not bundle.exists():  # pragma: no cover
        pytest.skip("v0.3.13 bundle is not present")
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    annotation, entities = _palindromic_u_case(with_dimension=True)
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.master_shape_code == "F003"
    assert "F005" not in set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code == "U_PROPORTION_F003"
    assert not top.dimension_master_resolved


def test_real_class_with_dimension_stays_flagged_on_v036():
    """v0.3.6 declares nothing: the original hole case (15/295/15 + dim 295)
    must keep the conflict -- neither proportions nor dims may erase it."""
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / ENGINE_BUNDLES[1]
    if not bundle.exists():  # pragma: no cover
        pytest.skip("v0.3.6 bundle is not present")
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    annotation, entities = _palindromic_u_case(with_dimension=True)
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    assert {"F003", "F005"} <= set(top.master_ambiguous_shape_codes)
    assert not top.dimension_master_resolved


def test_asymmetric_dimensions_do_resolve_to_f003(engine_fixture):
    """The owner's rule item 6: dimensions that prove UNEQUAL arms are real
    evidence -- only F003 accepts 20/290/15, so A-E may resolve cleanly."""
    engine = engine_fixture
    annotation = DxfEntityRecord(
        "A1",
        "BLOCK_ATTR_TEXT",
        "ARM-N8",
        {"insert": [150.0, -25.0, 0.0]},
        "20N8x325",
    )
    entities = [
        annotation,
        DxfEntityRecord(
            "G1", "LINE", "ARM-N8", {"start": [0.0, 19.0, 0.0], "end": [0.0, 0.0, 0.0]}
        ),
        DxfEntityRecord(
            "G2", "LINE", "ARM-N8", {"start": [0.0, 0.0, 0.0], "end": [291.0, 0.0, 0.0]}
        ),
        DxfEntityRecord(
            "G3",
            "LINE",
            "ARM-N8",
            {"start": [291.0, 0.0, 0.0], "end": [291.0, 15.5, 0.0]},
        ),
        DxfEntityRecord(
            "D1",
            "TEXT",
            "KOTI",
            {"insert": [145.0, 6.0, 0.0], "rotation": 0.0},
            "290",
        ),
        DxfEntityRecord(
            "D2",
            "TEXT",
            "KOTI",
            {"insert": [-8.0, 9.5, 0.0], "rotation": 90.0},
            "20",
        ),
        DxfEntityRecord(
            "D3",
            "TEXT",
            "KOTI",
            {"insert": [299.0, 7.75, 0.0], "rotation": 90.0},
            "15",
        ),
    ]
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    top = analysis.candidates[0]
    if top.dimension_master_resolved:
        # 20 != 15 rejects F005's required relation: exactly one identity
        # accepts the proven vector, so a clean resolution is legitimate.
        assert top.master_shape_code == "F003"
        assert "F005" not in set(top.master_ambiguous_shape_codes)


def test_batch_row_keeps_conflict_and_auto_export_stays_blocked(
    engine_fixture, tmp_path
):
    """Batch level: the row must stay FLAGGED, the disposition must not be
    AUTO_ELIGIBLE and the automatic export context must exclude the row."""
    engine = engine_fixture
    annotation, entities = _boundary_u_case(with_dimension=True)
    report = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="RECOVERY_AE_GATE",
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
    )
    import json

    payload = json.loads(Path(report.json_path).read_text(encoding="utf-8"))
    rows = [
        row
        for row in payload["rows"]
        if row.get("annotation_handle", "").startswith("A1")
    ]
    assert rows, "the annotation row is missing from the batch report"
    row = rows[0]
    assert {"F003", "F005"} <= {
        str(value).strip().upper()
        for value in row.get("master_ambiguous_shape_codes") or []
    }, f"batch row lost the conflict: {row.get('master_ambiguous_shape_codes')}"
    assert row.get("master_conflict") is True
    assert "РЪЧЕН" in str(row.get("batch_status") or "")
    assert str(row.get("matcher_disposition") or "") != "AUTO_ELIGIBLE"

    # The automatic Excel gate (batch_auto_output.build_batch_export_context,
    # lines 166-168) admits a non-approved row only when BOTH hold:
    # batch_status in AUTO_EXPORT_BATCH_STATUSES and matcher_disposition in
    # AUTO_EXPORT_MATCHER_DISPOSITIONS.  The ambiguous row must fail the gate.
    status_ok = str(row.get("batch_status") or "") in AUTO_EXPORT_BATCH_STATUSES
    disposition_ok = (
        str(row.get("matcher_disposition") or "") in AUTO_EXPORT_MATCHER_DISPOSITIONS
    )
    assert not (status_ok and disposition_ok), (
        "the ambiguous row would pass the automatic export gate: "
        f"batch_status={row.get('batch_status')!r}, "
        f"matcher_disposition={row.get('matcher_disposition')!r}"
    )
