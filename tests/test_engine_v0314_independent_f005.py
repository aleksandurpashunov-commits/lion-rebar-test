from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.export.master_engine_v031 import (
    parameters_from_segments,
    render_master_svg,
    total_for,
)


BUNDLE = (
    "lion-rebar-master_v0.3.14_"
    "F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
)


@pytest.fixture(scope="module")
def engine():
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / BUNDLE
    assert bundle.is_file(), f"Missing bundled Engine: {bundle}"
    package, _manifest, _names = inspect_engine_bundle(bundle)
    return package


def _u_case(
    first: float,
    middle: float,
    last: float,
    text: str,
    *,
    annotation_handle: str,
):
    diameter = text.upper().split("N", 1)[1].split("X", 1)[0].strip()
    layer = f"ARM-N{diameter}"
    annotation = DxfEntityRecord(
        annotation_handle,
        "BLOCK_ATTR_TEXT",
        layer,
        {"insert": [middle / 2.0, -25.0, 0.0]},
        text,
    )
    return annotation, [
        annotation,
        DxfEntityRecord(
            f"{annotation_handle}-G1",
            "LINE",
            layer,
            {"start": [0.0, first, 0.0], "end": [0.0, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            f"{annotation_handle}-G2",
            "LINE",
            layer,
            {"start": [0.0, 0.0, 0.0], "end": [middle, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            f"{annotation_handle}-G3",
            "LINE",
            layer,
            {"start": [middle, 0.0, 0.0], "end": [middle, last, 0.0]},
        ),
    ]


def _top(engine, case):
    annotation, entities = case
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    return analysis.candidates[0]


def test_v0314_engine_declares_and_tests_independent_f005_arms(engine) -> None:
    assert engine.version == "0.3.14"
    f003 = engine.find_shape("F003")
    f005 = engine.find_shape("F005")
    assert f003 is not None and f005 is not None
    assert [segment.length_code for segment in f005.segments] == [
        "L1",
        "L2",
        "L3",
    ]
    assert f005.symmetric is False
    assert "requires_equal_end_arms" not in f005.recognition
    assert (
        f003.recognition["u_outer_arms_relation"]
        == "BOTH_SHORTER_THAN_MIDDLE"
    )
    assert (
        f005.recognition["u_outer_arms_relation"]
        == "BOTH_LONGER_THAN_MIDDLE"
    )

    results = {result.test_id: result for result in run_engine_tests(engine)}
    assert results["T-F005-001"].passed
    assert results["T-F005-002"].passed
    assert results["T-F005-002"].calculated_cm == pytest.approx(160.0)


def test_legacy_f005_total_params_and_render_preserve_asymmetry() -> None:
    params = parameters_from_segments(
        "F005",
        (90.0, 20.0, 50.0),
        production_length_cm=160.0,
    )
    assert params.values == {"L1": 90.0, "L2": 20.0, "L3": 50.0}
    assert params.expanded_segments_cm == (90.0, 20.0, 50.0)
    assert params.total_length_cm == pytest.approx(160.0)
    assert total_for("F005", params.values) == pytest.approx(160.0)
    svg = render_master_svg("F005", params.values)
    assert ">90<" in svg
    assert ">20<" in svg
    assert ">50<" in svg

    legacy = parameters_from_segments(
        "F005",
        (61.0, 18.0),
        production_length_cm=140.0,
    )
    assert legacy.values == {"L1": 61.0, "L2": 18.0, "L3": 61.0}


@pytest.mark.parametrize(
    ("segments", "text", "handle"),
    [
        ((70.0, 10.0, 70.0), "10N10x150", "CD20::BJ-FI-L"),
        ((90.0, 18.0, 50.0), "17N8x158", "DB78::BJ-FI-L"),
    ],
)
def test_symmetric_and_asymmetric_hairpins_resolve_to_f005(
    engine,
    segments,
    text,
    handle,
) -> None:
    top = _top(
        engine,
        _u_case(*segments, text, annotation_handle=handle),
    )
    assert top.master_shape_code == "F005"
    assert "F003" not in set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code == "U_PROPORTION_F005"


def test_asymmetric_short_outer_arms_resolve_to_f003(engine) -> None:
    top = _top(
        engine,
        _u_case(
            15.0,
            300.0,
            25.0,
            "8N10x340",
            annotation_handle="SHORT-U",
        ),
    )
    assert top.master_shape_code == "F003"
    assert "F005" not in set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code == "U_PROPORTION_F003"


@pytest.mark.parametrize(
    "segments",
    [
        (90.0, 40.0, 20.0),
        (100.0, 100.0, 100.0),
    ],
)
def test_mixed_and_boundary_u_cases_remain_ambiguous(engine, segments) -> None:
    top = _top(
        engine,
        _u_case(
            *segments,
            f"5N10x{sum(segments):g}",
            annotation_handle="UNRESOLVED-U",
        ),
    )
    assert {"F003", "F005"} <= set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code is None
