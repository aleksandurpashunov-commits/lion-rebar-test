from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import (
    ShapeReferenceProfile,
    match_master_shapes,
    points_from_reference_profile,
    reference_profiles_for_shape,
)
from lion_rebar.engine.models import EnginePackage, ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex


@dataclass(frozen=True)
class RecognitionAuditFailure:
    shape_code: str
    profile_id: str
    rotation_deg: float
    mirrored: bool
    reversed_direction: bool
    scale: float
    matched_shape_code: str | None
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RecognitionAuditResult:
    shape_count: int
    profile_count: int
    case_count: int
    passed_count: int
    ambiguous_count: int
    failures: tuple[RecognitionAuditFailure, ...]
    ambiguous_pairs: tuple[tuple[str, ...], ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def audit_engine_recognition(
    engine: EnginePackage,
    *,
    rotations_deg: Iterable[float] = (0.0, 37.0, 90.0, 173.0),
    scales: Iterable[float] = (0.5, 1.0, 2.0),
) -> RecognitionAuditResult:
    """Prove recognition invariance for every active approved MASTER shape."""

    policy = RecognitionPolicy.from_settings(engine.settings)
    tests = tuple(engine.tests) + tuple(
        test
        for shape in engine.shapes
        for test in shape.tests
    )
    shapes = tuple(
        shape
        for shape in engine.shapes
        if shape.active and str(shape.state).upper() in {"APPROVED", "LOCKED"}
    )
    shape_index = EngineShapeIndex.from_shapes(shapes)
    failures: list[RecognitionAuditFailure] = []
    ambiguous_pairs: set[tuple[str, ...]] = set()
    profile_count = 0
    case_count = 0
    passed_count = 0
    ambiguous_count = 0

    for shape in shapes:
        profiles = reference_profiles_for_shape(shape, tests)
        if not profiles:
            failures.append(
                RecognitionAuditFailure(
                    shape_code=shape.code,
                    profile_id="—",
                    rotation_deg=0.0,
                    mirrored=False,
                    reversed_direction=False,
                    scale=1.0,
                    matched_shape_code=None,
                    reason="Липсва валиден контролен профил.",
                )
            )
            continue
        profile_count += len(profiles)
        for profile in profiles:
            points = points_from_reference_profile(shape, profile)
            for rotation in rotations_deg:
                rotated = _rotate_and_translate(points, float(rotation))
                for mirrored in (False, True):
                    reflected = (
                        tuple((point[0], -point[1]) for point in rotated)
                        if mirrored
                        else rotated
                    )
                    for reversed_direction in (False, True):
                        oriented = (
                            tuple(reversed(reflected))
                            if reversed_direction
                            else reflected
                        )
                        for scale in scales:
                            case_count += 1
                            transformed = _scale_about_first(oriented, float(scale))
                            match = match_master_shapes(
                                transformed,
                                shapes,
                                policy=policy,
                                control_tests=tests,
                                shape_index=shape_index,
                            )
                            if match is None or match.shape_code.upper() != shape.code.upper():
                                failures.append(
                                    _failure(
                                        shape,
                                        profile,
                                        rotation=float(rotation),
                                        mirrored=mirrored,
                                        reversed_direction=reversed_direction,
                                        scale=float(scale),
                                        matched=match.shape_code if match else None,
                                    )
                                )
                                continue
                            passed_count += 1
                            if match.ambiguous_shape_codes:
                                ambiguous_count += 1
                                ambiguous_pairs.add(
                                    tuple(sorted(match.ambiguous_shape_codes))
                                )

    return RecognitionAuditResult(
        shape_count=len(shapes),
        profile_count=profile_count,
        case_count=case_count,
        passed_count=passed_count,
        ambiguous_count=ambiguous_count,
        failures=tuple(failures),
        ambiguous_pairs=tuple(sorted(ambiguous_pairs)),
    )


def audit_engine_generated_regression(
    engine: EnginePackage,
    fixture: Any = None,
    *,
    config: Any = None,
) -> RecognitionAuditResult:
    """Compatibility adapter for the deterministic generated fixture.

    ``audit_engine_recognition`` above intentionally keeps its established
    exhaustive matrix and case counts.  New release gates may opt into the
    compact pairwise fixture through this adapter without changing existing
    callers or stored audit expectations.
    """

    from lion_rebar.engine.transformation_regression import (
        TransformationRegressionFixture,
        generate_transformation_regression_fixture,
        run_transformation_regression,
    )

    resolved = (
        generate_transformation_regression_fixture(engine, config=config)
        if fixture is None
        else (
            fixture
            if isinstance(fixture, TransformationRegressionFixture)
            else TransformationRegressionFixture.from_dict(fixture)
        )
    )
    report = run_transformation_regression(engine, resolved)
    cases_by_id = {case.test_id: case for case in resolved.cases}
    failures: list[RecognitionAuditFailure] = []
    ambiguous_pairs: set[tuple[str, ...]] = set()
    ambiguous_count = 0

    for result in report.results:
        if result.ambiguous_shape_codes:
            ambiguous_count += 1
            ambiguous_pairs.add(tuple(sorted(result.ambiguous_shape_codes)))
        if result.passed:
            continue
        case = cases_by_id.get(result.test_id)
        failures.append(
            RecognitionAuditFailure(
                shape_code=result.shape_code,
                profile_id=case.profile_id if case is not None else "—",
                rotation_deg=case.rotation_deg if case is not None else 0.0,
                mirrored=case.mirrored if case is not None else False,
                reversed_direction=(
                    case.reversed_direction if case is not None else False
                ),
                scale=case.scale if case is not None else 1.0,
                matched_shape_code=result.matched_shape_code,
                reason=(
                    result.message
                    if case is None
                    else (
                        f"{result.message} representation={case.representation}, "
                        f"length_variant={case.length_variant}, "
                        f"coordinate_tolerance={case.coordinate_tolerance_raw:g}."
                    )
                ),
            )
        )

    for omission in report.omissions:
        failures.append(
            RecognitionAuditFailure(
                shape_code=omission.shape_code,
                profile_id="—",
                rotation_deg=0.0,
                mirrored=False,
                reversed_direction=False,
                scale=1.0,
                matched_shape_code=None,
                reason=f"{omission.reason_code}: {omission.message}",
            )
        )

    active_shapes = {
        shape.code.strip().upper()
        for shape in engine.shapes
        if shape.active
        and str(shape.state).strip().upper() in {"APPROVED", "LOCKED"}
    }
    profiles = {
        (case.shape_code, case.profile_id)
        for case in resolved.cases
    }
    return RecognitionAuditResult(
        shape_count=len(active_shapes),
        profile_count=len(profiles),
        case_count=report.case_count,
        passed_count=report.passed_count,
        ambiguous_count=ambiguous_count,
        failures=tuple(failures),
        ambiguous_pairs=tuple(sorted(ambiguous_pairs)),
    )


def _failure(
    shape: ShapeDefinition,
    profile: ShapeReferenceProfile,
    *,
    rotation: float,
    mirrored: bool,
    reversed_direction: bool,
    scale: float,
    matched: str | None,
) -> RecognitionAuditFailure:
    return RecognitionAuditFailure(
        shape_code=shape.code,
        profile_id=profile.profile_id,
        rotation_deg=rotation,
        mirrored=mirrored,
        reversed_direction=reversed_direction,
        scale=scale,
        matched_shape_code=matched,
        reason=(
            "Не е намерена MASTER форма."
            if matched is None
            else f"Грешно разпозната като {matched}."
        ),
    )


def _rotate_and_translate(
    points: tuple[tuple[float, float], ...],
    angle_deg: float,
) -> tuple[tuple[float, float], ...]:
    angle = math.radians(angle_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return tuple(
        (
            x * cosine - y * sine + 123.4,
            x * sine + y * cosine - 55.6,
        )
        for x, y in points
    )


def _scale_about_first(
    points: tuple[tuple[float, float], ...],
    scale: float,
) -> tuple[tuple[float, float], ...]:
    if not points:
        return ()
    origin_x, origin_y = points[0]
    return tuple(
        (
            origin_x + (x - origin_x) * scale,
            origin_y + (y - origin_y) * scale,
        )
        for x, y in points
    )
