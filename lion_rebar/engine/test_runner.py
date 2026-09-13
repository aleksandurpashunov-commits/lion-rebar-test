from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from lion_rebar.engine.models import EnginePackage, ShapeDefinition


@dataclass(frozen=True)
class EngineTestResult:
    test_id: str
    shape_code: str
    passed: bool
    expected_cm: float | None
    calculated_cm: float | None
    message: str


def parse_dimensions(value: str | dict[str, Any] | None) -> dict[str, float]:
    if isinstance(value, dict):
        parsed: dict[str, float] = {}
        for key, raw in value.items():
            try:
                parsed[str(key).upper()] = float(raw)
            except (TypeError, ValueError):
                continue
        return parsed
    result: dict[str, float] = {}
    for part in str(value or "").split(";"):
        match = re.match(r"^\s*([A-ZА-Я]+\d*)\s*=\s*(-?\d+(?:[.,]\d+)?)\s*$", part, re.IGNORECASE)
        if match:
            result[match.group(1).upper()] = float(match.group(2).replace(",", "."))
    return result


def calculate_shape_segment_lengths(
    shape: ShapeDefinition,
    dimensions: dict[str, float],
) -> tuple[float, ...]:
    """Resolve one concrete length for every MASTER segment.

    The same resolver is used by the engine control tests and by the geometric
    matcher.  This makes a successful control test an automatic recognition
    profile, so adding a new approved form no longer requires matcher code.
    """

    lengths: list[float] = []
    for segment in sorted(shape.segments, key=lambda item: item.index):
        if segment.fixed_length_cm is not None:
            lengths.append(float(segment.fixed_length_cm))
            continue

        method = segment.geometry_method.upper()
        if (
            "ПИТАГОР" in method
            or "PYTHAG" in method
            or (segment.projection_x_code and segment.projection_y_code)
        ):
            x_code = (segment.projection_x_code or "").upper()
            y_code = (segment.projection_y_code or "").upper()
            if x_code not in dimensions or y_code not in dimensions:
                raise ValueError(
                    f"Липсват проекции {x_code}/{y_code} за {shape.code} сегмент {segment.index}."
                )
            lengths.append(math.hypot(dimensions[x_code], dimensions[y_code]))
            continue

        code = segment.length_code.upper()
        if code not in dimensions:
            if segment.required:
                raise ValueError(f"Липсва размер {code} за {shape.code} сегмент {segment.index}.")
            continue
        lengths.append(float(dimensions[code]))
    return tuple(lengths)


def calculate_shape_total(shape: ShapeDefinition, dimensions: dict[str, float]) -> float:
    return sum(calculate_shape_segment_lengths(shape, dimensions))


def required_dimension_codes(shape: ShapeDefinition) -> tuple[str, ...]:
    """Return the user-entered dimension codes needed by one shape."""

    codes: list[str] = []
    for segment in sorted(shape.segments, key=lambda item: item.index):
        if segment.fixed_length_cm is not None:
            continue
        if segment.projection_x_code and segment.projection_y_code:
            candidates = (segment.projection_x_code, segment.projection_y_code)
        else:
            candidates = (segment.length_code,)
        for raw in candidates:
            code = str(raw or "").strip().upper()
            if code and code not in codes:
                codes.append(code)
    return tuple(codes)


def build_control_test(
    shape: ShapeDefinition,
    dimensions_value: str | dict[str, Any] | None,
    *,
    test_id: str,
) -> dict[str, Any]:
    """Create a canonical engine test that doubles as a matcher profile."""

    dimensions = parse_dimensions(dimensions_value)
    lengths = calculate_shape_segment_lengths(shape, dimensions)
    if not lengths:
        raise ValueError(f"Форма {shape.code} няма измерими сегменти.")
    if any(value <= 0 for value in lengths):
        raise ValueError("Всички размери трябва да са положителни.")
    canonical_dimensions = "; ".join(
        f"{code}={dimensions[code]:g}"
        for code in required_dimension_codes(shape)
        if code in dimensions
    )
    return {
        "test_id": str(test_id).strip(),
        "shape_code": shape.code.upper(),
        "expected_total_cm": sum(lengths),
        "dimensions": canonical_dimensions,
        "tolerance_cm": 0.11,
        "purpose": "ENGINE_TEST_AND_MATCHER_PROFILE",
    }


def run_engine_tests(engine: EnginePackage) -> list[EngineTestResult]:
    results: list[EngineTestResult] = []
    tests = list(engine.tests)
    for shape in engine.shapes:
        tests.extend(shape.tests)

    seen_ids: set[str] = set()
    for index, test in enumerate(tests, start=1):
        test_id = str(
            test.get("test_id")
            or test.get("ID_ТЕСТ")
            or f"TEST-{index:03d}"
        )
        if test_id in seen_ids:
            test_id = f"{test_id}#{index}"
        seen_ids.add(test_id)
        shape_code = str(test.get("shape_code") or test.get("КОД_ФОРМА") or "").upper()
        shape = engine.find_shape(shape_code)
        expected_raw = test.get("expected_total_cm", test.get("ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ"))
        expected = _to_float(expected_raw)
        dimensions = parse_dimensions(
            test.get("dimensions", test.get("ОЧАКВАНИ_РАЗМЕРИ_СМ"))
        )
        tolerance = _to_float(test.get("tolerance_cm")) or 0.11

        if shape is None:
            results.append(
                EngineTestResult(
                    test_id=test_id,
                    shape_code=shape_code,
                    passed=False,
                    expected_cm=expected,
                    calculated_cm=None,
                    message=f"Форма {shape_code} не е намерена.",
                )
            )
            continue
        if expected is None:
            results.append(
                EngineTestResult(
                    test_id=test_id,
                    shape_code=shape_code,
                    passed=False,
                    expected_cm=None,
                    calculated_cm=None,
                    message="Липсва очаквана обща дължина.",
                )
            )
            continue
        try:
            calculated = calculate_shape_total(shape, dimensions)
        except ValueError as exc:
            results.append(
                EngineTestResult(
                    test_id=test_id,
                    shape_code=shape_code,
                    passed=False,
                    expected_cm=expected,
                    calculated_cm=None,
                    message=str(exc),
                )
            )
            continue
        passed = abs(calculated - expected) <= tolerance
        results.append(
            EngineTestResult(
                test_id=test_id,
                shape_code=shape_code,
                passed=passed,
                expected_cm=expected,
                calculated_cm=calculated,
                message=(
                    "Успешен тест."
                    if passed
                    else f"Очаквани {expected:.3f} cm, изчислени {calculated:.3f} cm."
                ),
            )
        )
    return results


def generate_engine_transformation_fixture(
    engine: EnginePackage,
    *,
    config: Any = None,
) -> Any:
    """Build the deterministic matcher matrix exposed by the Engine runner.

    The local import avoids a module cycle: the production matcher reuses the
    segment-length resolver from this module.
    """

    from lion_rebar.engine.transformation_regression import (
        generate_transformation_regression_fixture,
    )

    return generate_transformation_regression_fixture(engine, config=config)


def run_engine_transformation_tests(
    engine: EnginePackage,
    fixture: Any = None,
    *,
    config: Any = None,
) -> Any:
    """Run a generated or JSON-loaded matcher fixture through production code."""

    from lion_rebar.engine.transformation_regression import (
        run_transformation_regression,
    )

    return run_transformation_regression(
        engine,
        fixture,
        config=config,
    )


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
