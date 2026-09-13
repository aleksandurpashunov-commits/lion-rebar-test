from __future__ import annotations

import itertools
import json

from lion_rebar.engine.models import (
    EnginePackage,
    SegmentDefinition,
    ShapeDefinition,
)
from lion_rebar.engine.recognition_audit import (
    audit_engine_generated_regression,
    audit_engine_recognition,
)
from lion_rebar.engine.test_runner import (
    generate_engine_transformation_fixture,
    run_engine_transformation_tests,
)
from lion_rebar.engine.transformation_regression import (
    LENGTH_PARAMETRIC_A,
    LENGTH_PARAMETRIC_B,
    LENGTH_REFERENCE,
    REPRESENTATION_LINE_CHAIN,
    REPRESENTATION_LWPOLYLINE,
    TOLERANCE_NONE,
    TOLERANCE_SAFE_COORDINATE_NOISE,
    TransformationRegressionConfig,
    TransformationRegressionFixture,
    generate_transformation_regression_fixture,
    points_from_regression_geometry,
    run_transformation_regression,
)


def _segment(
    index: int,
    code: str,
    *,
    bend: float | None = None,
    bend_direction: str = "LEFT",
    fixed: float | None = None,
) -> SegmentDefinition:
    return SegmentDefinition(
        index=index,
        length_code=code,
        fixed_length_cm=fixed,
        bend_after_deg=bend,
        bend_direction=bend_direction,
    )


def _shape(*, mirror_allowed: bool = True) -> ShapeDefinition:
    return ShapeDefinition(
        code="F120",
        name="REGRESSION TEST SHAPE",
        state="APPROVED",
        mirror_allowed=mirror_allowed,
        segments=[
            _segment(1, "L1", bend=90, bend_direction="LEFT"),
            _segment(2, "L2", bend=45, bend_direction="RIGHT"),
            _segment(3, "L3", bend=90, bend_direction="RIGHT"),
            _segment(4, "L4"),
        ],
    )


def _engine(*, mirror_allowed: bool = True) -> EnginePackage:
    return EnginePackage(
        engine_id="regression-engine",
        title="Regression Engine",
        version="1.2.3",
        production_ready=True,
        shapes=[_shape(mirror_allowed=mirror_allowed)],
        tests=[
            {
                "test_id": "T-F120-001",
                "shape_code": "F120",
                "dimensions": "L1=60; L2=35; L3=90; L4=20",
                "expected_total_cm": 205,
            }
        ],
    )


def _case_axis_value(case, axis: str):
    if axis == "tolerance_mode":
        return (
            TOLERANCE_SAFE_COORDINATE_NOISE
            if case.coordinate_tolerance_raw > 0
            else TOLERANCE_NONE
        )
    return getattr(case, axis)


def test_generator_is_deterministic_and_pairwise_complete() -> None:
    engine = _engine()

    first = generate_transformation_regression_fixture(engine)
    second = generate_transformation_regression_fixture(engine)

    assert first.to_json() == second.to_json()
    assert len(first.cases) == len({case.test_id for case in first.cases})
    assert not first.omissions

    expected_axes = {
        "rotation_deg": {0.0, 37.0, 90.0, 173.0},
        "scale": {1.0, 0.67, 1.85},
        "reversed_direction": {False, True},
        "mirrored": {False, True},
        "representation": {
            REPRESENTATION_LWPOLYLINE,
            REPRESENTATION_LINE_CHAIN,
        },
        "length_variant": {
            LENGTH_REFERENCE,
            LENGTH_PARAMETRIC_A,
            LENGTH_PARAMETRIC_B,
        },
        "tolerance_mode": {
            TOLERANCE_NONE,
            TOLERANCE_SAFE_COORDINATE_NOISE,
        },
    }
    for axis, values in expected_axes.items():
        assert {_case_axis_value(case, axis) for case in first.cases} == values

    axis_names = tuple(expected_axes)
    for left_index, right_index in itertools.combinations(
        range(len(axis_names)),
        2,
    ):
        left_name = axis_names[left_index]
        right_name = axis_names[right_index]
        observed = {
            (
                _case_axis_value(case, left_name),
                _case_axis_value(case, right_name),
            )
            for case in first.cases
        }
        expected = set(
            itertools.product(
                expected_axes[left_name],
                expected_axes[right_name],
            )
        )
        assert observed == expected

    exhaustive_count = 4 * 3 * 2 * 2 * 2 * 3 * 2
    assert len(first.cases) < exhaustive_count / 10


def test_fixture_round_trip_and_both_dxf_representations() -> None:
    fixture = generate_transformation_regression_fixture(_engine())
    loaded = TransformationRegressionFixture.from_dict(
        json.loads(fixture.to_json())
    )

    assert loaded.to_json() == fixture.to_json()
    representations = {case.representation for case in loaded.cases}
    assert representations == {
        REPRESENTATION_LWPOLYLINE,
        REPRESENTATION_LINE_CHAIN,
    }
    for case in loaded.cases:
        points = points_from_regression_geometry(
            case.geometry,
            representation=case.representation,
            expected_closed=case.expected_closed,
        )
        assert len(points) == 5


def test_generated_matrix_passes_through_production_matcher() -> None:
    report = run_transformation_regression(_engine())

    assert report.case_count > 0
    assert report.passed_count == report.case_count
    assert report.passed
    assert not report.failures


def test_disallowed_mirror_is_discoverable_but_requires_review() -> None:
    engine = _engine(mirror_allowed=False)
    fixture = generate_transformation_regression_fixture(
        engine
    )
    mirrored = [case for case in fixture.cases if case.mirrored]
    report = run_transformation_regression(engine, fixture)

    assert fixture.cases
    assert mirrored
    assert all(case.mirror_requires_review_expected for case in mirrored)
    assert all(
        not case.mirror_requires_review_expected
        for case in fixture.cases
        if not case.mirrored
    )
    assert report.passed
    assert report.passed_count == report.case_count


def test_fixed_segment_does_not_change_in_parameter_variants() -> None:
    shape = ShapeDefinition(
        code="F121",
        name="FIXED AND PARAMETRIC",
        state="LOCKED",
        mirror_allowed=True,
        segments=[
            _segment(1, "FIXED", bend=90, fixed=10),
            _segment(2, "L1", bend=90),
            _segment(3, "L1"),
        ],
    )
    engine = EnginePackage(
        engine_id="fixed-engine",
        title="Fixed Engine",
        version="1.0.0",
        shapes=[shape],
        tests=[
            {
                "test_id": "T-F121-001",
                "shape_code": "F121",
                "dimensions": "L1=40",
                "expected_total_cm": 90,
            }
        ],
    )

    fixture = generate_transformation_regression_fixture(engine)
    by_variant = {}
    for case in fixture.cases:
        by_variant.setdefault(case.length_variant, case.segment_lengths_cm)

    assert set(by_variant) == {
        LENGTH_REFERENCE,
        LENGTH_PARAMETRIC_A,
        LENGTH_PARAMETRIC_B,
    }
    assert all(lengths[0] == 10 for lengths in by_variant.values())
    assert all(lengths[1] == lengths[2] for lengths in by_variant.values())
    assert by_variant[LENGTH_REFERENCE] != by_variant[LENGTH_PARAMETRIC_A]


def test_engine_test_runner_exposes_generator_and_runner() -> None:
    engine = _engine()
    config = TransformationRegressionConfig(
        rotations_deg=(0.0, 90.0),
        scales=(1.0,),
        include_parameter_variants=False,
        include_safe_tolerance=False,
    )

    fixture = generate_engine_transformation_fixture(engine, config=config)
    report = run_engine_transformation_tests(engine, fixture)

    assert isinstance(fixture, TransformationRegressionFixture)
    assert report.passed
    assert report.case_count == report.passed_count


def test_recognition_audit_compatibility_keeps_legacy_api_and_counts() -> None:
    engine = _engine()

    legacy = audit_engine_recognition(engine)
    generated = audit_engine_generated_regression(engine)

    assert legacy.case_count == 48
    assert legacy.passed
    assert generated.case_count > 0
    assert generated.passed
    assert generated.passed_count == generated.case_count


def test_curve_segments_are_explicit_chord_proxies_not_silent_omissions() -> None:
    shape = ShapeDefinition(
        code="F122",
        name="CURVE METADATA",
        state="APPROVED",
        mirror_allowed=True,
        segments=[
            _segment(1, "L1", bend=90),
            _segment(2, "L2", bend=135, bend_direction="RIGHT"),
            SegmentDefinition(
                index=3,
                length_code="L3",
                segment_type="ARC",
            ),
        ],
    )
    engine = EnginePackage(
        engine_id="curve-engine",
        title="Curve Engine",
        version="1.0.0",
        shapes=[shape],
        tests=[
            {
                "test_id": "T-F122-001",
                "shape_code": "F122",
                "dimensions": "L1=40; L2=75; L3=30",
                "expected_total_cm": 145,
            }
        ],
    )

    fixture = generate_transformation_regression_fixture(engine)

    assert fixture.cases
    assert not fixture.omissions
    assert {
        case.geometry["curve_model"] for case in fixture.cases
    } == {"CHORD_PROXY_FOR_TOPOLOGY_REGRESSION"}
    assert all(
        case.geometry["approximated_curve_segments"]
        == ([1] if case.reversed_direction else [3])
        for case in fixture.cases
    )


def test_safe_noise_preserves_repeated_hook_vertices() -> None:
    shape = ShapeDefinition(
        code="F123",
        name="CLOSED HOOK",
        state="APPROVED",
        closed=True,
        symmetric=True,
        mirror_allowed=True,
        segments=[
            _segment(1, "L1", bend=135),
            _segment(2, "L2", bend=90),
            _segment(3, "L3", bend=90),
            _segment(4, "L2", bend=90),
            _segment(5, "L3", bend=135),
            _segment(6, "L1"),
        ],
    )
    engine = EnginePackage(
        engine_id="hook-engine",
        title="Hook Engine",
        version="1.0.0",
        shapes=[shape],
        tests=[
            {
                "test_id": "T-F123-001",
                "shape_code": "F123",
                "dimensions": "L1=10; L2=45; L3=25",
                "expected_total_cm": 160,
            }
        ],
    )

    fixture = generate_transformation_regression_fixture(engine)
    noisy_cases = [
        case for case in fixture.cases if case.coordinate_tolerance_raw > 0
    ]
    report = run_transformation_regression(engine, fixture)

    assert noisy_cases
    assert report.passed
    assert report.passed_count == report.case_count
