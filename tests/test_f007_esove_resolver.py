from math import cos, radians, sin
from pathlib import Path

import pytest

from lion_rebar.dxf.geometry_candidates import _c_shape_curve_override
from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import match_master_shapes
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex


def _observed_points(*, turn_deg: float = 135.0, ratio: float = 2.0):
    long_length = 20.0
    short_length = long_length / ratio
    middle = (long_length, 0.0)
    end = (
        middle[0] + short_length * cos(radians(turn_deg)),
        middle[1] + short_length * sin(radians(turn_deg)),
    )
    return (0.0, 0.0), middle, end


def _resolve(**overrides):
    arguments = {
        "declared_length_cm": 40.0,
        "measured_geometry_length_cm": 41.44,
        "layer": "ESove",
        "ordered_points": _observed_points(),
        "source_curve_count": 1,
        "normalized_bend_count": 1,
        "unresolved_curve_count": 0,
        "policy": RecognitionPolicy(),
    }
    arguments.update(overrides)
    return _c_shape_curve_override(**arguments)


def _real_esove_curve() -> DxfEntityRecord:
    """Normalized copy of the real holdout curve behind 6N6x40."""

    return DxfEntityRecord(
        handle="ESOVE-CURVE",
        entity_type="LWPOLYLINE",
        layer="ESove",
        geometry={
            "points": [
                [0.0, 0.0, 0.0],
                [-24.2999999996, 0.0, 0.6681786379178724],
                [-25.3606601716, -2.5606601718, 0.0],
                [-18.3640141229, -9.59859036296, 0.0],
            ],
            "closed": False,
        },
    )


def _find_real_esove(*, distance_raw: float):
    root = Path(__file__).resolve().parents[1]
    bundle = (
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.11_ARC_LENGTH_STIRRUP_GROUP_PRODUCTION_DRAFT.lionengine"
    )
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    annotation = DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARMTXT",
        text="6N6x40",
        geometry={"insert": [0.0, distance_raw, 0.0], "height": 2.5},
    )
    curve = _real_esove_curve()
    settings = dict(engine.settings["geometry_recognition"])
    settings["use_visual_text_extent"] = False
    return find_geometry_candidates(
        annotation,
        [annotation, curve],
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=RecognitionPolicy.from_settings(settings),
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )


def test_esove_curve_restores_f007_production_parameters() -> None:
    override = _resolve()

    assert override is not None
    assert override.code == "F007_ESOVE_CURVED_CENTERLINE"
    assert override.effective_length_cm == pytest.approx(40.0)
    assert override.segment_lengths_cm == pytest.approx((10.0, 20.0, 10.0))
    assert override.turn_angles_deg == pytest.approx((90.0, 135.0))

    bundle = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.8_CANONICAL_FORMS_PRODUCTION_DRAFT.lionengine"
    )
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    match = match_master_shapes(
        override.points,
        engine.shapes,
        policy=RecognitionPolicy.from_settings(engine.settings),
        control_tests=engine.tests,
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )

    assert match is not None
    assert match.shape_code == "F007"
    assert not match.ambiguous_shape_codes


def test_complete_real_esove_evidence_is_strong_inside_narrow_60_unit_gate() -> None:
    analysis = _find_real_esove(distance_raw=57.0)

    assert analysis.candidates
    candidate = analysis.candidates[0]
    assert candidate.master_shape_code == "F007"
    assert candidate.geometry_length_raw == pytest.approx(37.7582787012733)
    assert candidate.effective_geometry_length_cm == pytest.approx(40.0)
    assert candidate.confidence == "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА"
    assert "F007_EXACT_EXTENDED_ASSOCIATION" in candidate.review_reason_codes
    assert "FAR_ASSOCIATION" not in candidate.review_reason_codes


def test_real_esove_outside_narrow_60_unit_gate_stays_manual() -> None:
    analysis = _find_real_esove(distance_raw=61.0)

    assert analysis.candidates
    candidate = analysis.candidates[0]
    assert candidate.master_shape_code == "F007"
    assert candidate.confidence == "ДАЛЕЧНА ВРЪЗКА — РЪЧНА ПРОВЕРКА"
    assert "FAR_ASSOCIATION" in candidate.review_reason_codes
    assert "F007_EXACT_EXTENDED_ASSOCIATION" not in candidate.review_reason_codes


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"layer": "ARM_Bars"},
        {"source_curve_count": 0},
        {"source_curve_count": 2},
        {"normalized_bend_count": 0},
        {"unresolved_curve_count": 1},
        {"measured_geometry_length_cm": 43.0},
        {"ordered_points": _observed_points(turn_deg=110.0)},
        {"ordered_points": _observed_points(ratio=3.0)},
    ],
)
def test_esove_curve_rejects_incomplete_evidence(overrides) -> None:
    assert _resolve(**overrides) is None
