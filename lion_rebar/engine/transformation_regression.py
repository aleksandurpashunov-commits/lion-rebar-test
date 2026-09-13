from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import (
    ShapeReferenceProfile,
    match_master_shapes,
    points_from_reference_profile,
    reference_profiles_for_shape,
)
from lion_rebar.engine.models import EnginePackage, ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex

Point2D = tuple[float, float]

TRANSFORMATION_REGRESSION_SCHEMA = "lion-rebar/engine-transform-regression@1"
TRANSFORMATION_TEST_TYPE = "MATCHER_TRANSFORMATION_REGRESSION"
REPRESENTATION_LWPOLYLINE = "LWPOLYLINE"
REPRESENTATION_LINE_CHAIN = "LINE_CHAIN"
TOLERANCE_NONE = "NONE"
TOLERANCE_SAFE_COORDINATE_NOISE = "SAFE_COORDINATE_NOISE"
LENGTH_REFERENCE = "REFERENCE"
LENGTH_PARAMETRIC_A = "PARAMETRIC_A"
LENGTH_PARAMETRIC_B = "PARAMETRIC_B"


@dataclass(frozen=True)
class TransformationRegressionConfig:
    """Deterministic coverage policy for generated MASTER recognition tests.

    ``PAIRWISE`` covers every pair of values across the transformation axes.
    It is deliberately the default: the matrix stays practical when the
    library grows to hundreds of forms.  ``EXHAUSTIVE`` remains available for
    a focused release-candidate audit.
    """

    rotations_deg: tuple[float, ...] = (0.0, 37.0, 90.0, 173.0)
    scales: tuple[float, ...] = (1.0, 0.67, 1.85)
    representations: tuple[str, ...] = (
        REPRESENTATION_LWPOLYLINE,
        REPRESENTATION_LINE_CHAIN,
    )
    include_reversed_direction: bool = True
    include_allowed_mirror: bool = True
    include_review_only_mirror: bool = True
    include_parameter_variants: bool = True
    include_safe_tolerance: bool = True
    safe_coordinate_noise_ratio: float = 0.0005
    safe_coordinate_noise_cap_raw: float = 0.02
    coverage: str = "PAIRWISE"

    def normalized(self) -> "TransformationRegressionConfig":
        rotations = _unique_finite(self.rotations_deg, fallback=(0.0,))
        scales = tuple(
            value
            for value in _unique_finite(self.scales, fallback=(1.0,))
            if value > 0.0
        )
        if not scales:
            scales = (1.0,)
        representations = tuple(
            value
            for value in _unique_strings(self.representations)
            if value in {REPRESENTATION_LWPOLYLINE, REPRESENTATION_LINE_CHAIN}
        )
        if not representations:
            representations = (REPRESENTATION_LWPOLYLINE,)
        coverage = str(self.coverage or "PAIRWISE").strip().upper()
        if coverage not in {"PAIRWISE", "EXHAUSTIVE"}:
            coverage = "PAIRWISE"
        return TransformationRegressionConfig(
            rotations_deg=rotations,
            scales=scales,
            representations=representations,
            include_reversed_direction=bool(self.include_reversed_direction),
            include_allowed_mirror=bool(self.include_allowed_mirror),
            include_review_only_mirror=bool(self.include_review_only_mirror),
            include_parameter_variants=bool(self.include_parameter_variants),
            include_safe_tolerance=bool(self.include_safe_tolerance),
            safe_coordinate_noise_ratio=max(
                0.0,
                float(self.safe_coordinate_noise_ratio),
            ),
            safe_coordinate_noise_cap_raw=max(
                0.0,
                float(self.safe_coordinate_noise_cap_raw),
            ),
            coverage=coverage,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())


@dataclass(frozen=True)
class TransformationRegressionCase:
    test_id: str
    shape_code: str
    shape_revision: int
    profile_id: str
    representation: str
    geometry: dict[str, Any]
    rotation_deg: float
    scale: float
    mirrored: bool
    reversed_direction: bool
    length_variant: str
    segment_lengths_cm: tuple[float, ...]
    coordinate_tolerance_raw: float
    expected_shape_code: str
    expected_closed: bool
    allow_expected_in_ambiguity: bool = True
    mirror_requires_review_expected: bool = False
    test_type: str = TRANSFORMATION_TEST_TYPE

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TransformationRegressionCase":
        geometry = data.get("geometry")
        if not isinstance(geometry, Mapping):
            raise ValueError("Regression case geometry трябва да бъде обект.")
        return cls(
            test_id=str(data.get("test_id", "")).strip(),
            shape_code=str(data.get("shape_code", "")).strip().upper(),
            shape_revision=int(data.get("shape_revision", 1)),
            profile_id=str(data.get("profile_id", "")).strip(),
            representation=str(data.get("representation", "")).strip().upper(),
            geometry=dict(geometry),
            rotation_deg=_finite_float(data.get("rotation_deg"), "rotation_deg"),
            scale=_positive_float(data.get("scale"), "scale"),
            mirrored=bool(data.get("mirrored", False)),
            reversed_direction=bool(data.get("reversed_direction", False)),
            length_variant=str(data.get("length_variant", LENGTH_REFERENCE)).strip().upper(),
            segment_lengths_cm=tuple(
                _positive_float(value, "segment_lengths_cm")
                for value in data.get("segment_lengths_cm", ())
            ),
            coordinate_tolerance_raw=max(
                0.0,
                _finite_float(
                    data.get("coordinate_tolerance_raw", 0.0),
                    "coordinate_tolerance_raw",
                ),
            ),
            expected_shape_code=str(
                data.get("expected_shape_code") or data.get("shape_code") or ""
            )
            .strip()
            .upper(),
            expected_closed=bool(data.get("expected_closed", False)),
            allow_expected_in_ambiguity=bool(
                data.get("allow_expected_in_ambiguity", True)
            ),
            mirror_requires_review_expected=bool(
                data.get("mirror_requires_review_expected", False)
            ),
            test_type=str(
                data.get("test_type", TRANSFORMATION_TEST_TYPE)
            ).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["segment_lengths_cm"] = list(self.segment_lengths_cm)
        return payload


@dataclass(frozen=True)
class TransformationRegressionOmission:
    shape_code: str
    reason_code: str
    message: str

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "TransformationRegressionOmission":
        return cls(
            shape_code=str(data.get("shape_code", "")).strip().upper(),
            reason_code=str(data.get("reason_code", "")).strip().upper(),
            message=str(data.get("message", "")).strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TransformationRegressionFixture:
    engine_id: str
    engine_version: str
    engine_content_sha256: str
    config: dict[str, Any]
    cases: tuple[TransformationRegressionCase, ...]
    omissions: tuple[TransformationRegressionOmission, ...] = ()
    schema: str = TRANSFORMATION_REGRESSION_SCHEMA

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "TransformationRegressionFixture":
        schema = str(data.get("schema", ""))
        if schema != TRANSFORMATION_REGRESSION_SCHEMA:
            raise ValueError(f"Неподдържана regression схема: {schema or '—'}.")
        raw_cases = data.get("cases", ())
        raw_omissions = data.get("omissions", ())
        if not isinstance(raw_cases, (list, tuple)):
            raise ValueError("Regression fixture cases трябва да бъде списък.")
        if not isinstance(raw_omissions, (list, tuple)):
            raise ValueError("Regression fixture omissions трябва да бъде списък.")
        raw_config = data.get("config", {})
        if not isinstance(raw_config, Mapping):
            raise ValueError("Regression fixture config трябва да бъде обект.")
        fixture = cls(
            engine_id=str(data.get("engine_id", "")).strip(),
            engine_version=str(data.get("engine_version", "")).strip(),
            engine_content_sha256=str(
                data.get("engine_content_sha256", "")
            ).strip(),
            config=dict(raw_config),
            cases=tuple(
                TransformationRegressionCase.from_dict(item)
                for item in raw_cases
                if isinstance(item, Mapping)
            ),
            omissions=tuple(
                TransformationRegressionOmission.from_dict(item)
                for item in raw_omissions
                if isinstance(item, Mapping)
            ),
            schema=schema,
        )
        _validate_unique_case_ids(fixture.cases)
        return fixture

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "engine_content_sha256": self.engine_content_sha256,
            "config": self.config,
            "case_count": len(self.cases),
            "omission_count": len(self.omissions),
            "cases": [case.to_dict() for case in self.cases],
            "omissions": [item.to_dict() for item in self.omissions],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
        )


@dataclass(frozen=True)
class TransformationRegressionResult:
    test_id: str
    shape_code: str
    passed: bool
    matched_shape_code: str | None
    ambiguous_shape_codes: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransformationRegressionReport:
    case_count: int
    passed_count: int
    ambiguous_valid_count: int
    results: tuple[TransformationRegressionResult, ...]
    omissions: tuple[TransformationRegressionOmission, ...]

    @property
    def failures(self) -> tuple[TransformationRegressionResult, ...]:
        return tuple(result for result in self.results if not result.passed)

    @property
    def passed(self) -> bool:
        return not self.failures and not self.omissions

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "ambiguous_valid_count": self.ambiguous_valid_count,
            "failure_count": len(self.failures),
            "omission_count": len(self.omissions),
            "results": [result.to_dict() for result in self.results],
            "omissions": [item.to_dict() for item in self.omissions],
        }


def generate_transformation_regression_fixture(
    engine: EnginePackage,
    *,
    config: TransformationRegressionConfig | None = None,
) -> TransformationRegressionFixture:
    """Generate a stable geometry fixture for every active approved form.

    The result contains no time stamp and no random values.  The same Engine
    content and configuration always produce byte-for-byte identical JSON.
    """

    normalized = (config or TransformationRegressionConfig()).normalized()
    control_tests = _all_control_tests(engine)
    cases: list[TransformationRegressionCase] = []
    omissions: list[TransformationRegressionOmission] = []
    approved_shapes = sorted(
        (
            shape
            for shape in engine.shapes
            if shape.active
            and str(shape.state).strip().upper() in {"APPROVED", "LOCKED"}
        ),
        key=lambda item: (item.code.strip().upper(), item.revision),
    )
    policy = RecognitionPolicy.from_settings(engine.settings)

    for shape in approved_shapes:
        profiles = sorted(
            reference_profiles_for_shape(shape, control_tests),
            key=lambda item: (
                item.profile_id,
                tuple(round(value, 9) for value in item.segment_lengths_cm),
            ),
        )
        if not profiles:
            omissions.append(
                TransformationRegressionOmission(
                    shape_code=shape.code.strip().upper(),
                    reason_code="MISSING_REFERENCE_PROFILE",
                    message="Липсва валиден контролен профил с дължини на сегментите.",
                )
            )
            continue

        for profile_index, profile in enumerate(profiles, start=1):
            length_variants = _length_variants(
                shape,
                profile,
                include_parameter_variants=normalized.include_parameter_variants,
                control_tests=control_tests,
                policy=policy,
            )
            axes = _coverage_axes(shape, length_variants, normalized)
            rows = (
                tuple(itertools.product(*(values for _name, values in axes)))
                if normalized.coverage == "EXHAUSTIVE"
                else _pairwise_covering_rows(tuple(values for _name, values in axes))
            )
            for case_index, row in enumerate(rows, start=1):
                values = {
                    name: row[position]
                    for position, (name, _axis_values) in enumerate(axes)
                }
                variant_name = str(values["length_variant"])
                segment_lengths = length_variants[variant_name]
                synthetic_profile = ShapeReferenceProfile(
                    profile_id=f"{profile.profile_id}:{variant_name}",
                    segment_lengths_cm=segment_lengths,
                    source=profile.source,
                )
                source_points = points_from_reference_profile(
                    shape,
                    synthetic_profile,
                )
                if not _has_expected_segment_count(
                    source_points,
                    expected_count=len(shape.segments),
                    closed=bool(shape.closed),
                ):
                    if variant_name != LENGTH_REFERENCE:
                        # Some closed shapes have dimensional constraints that
                        # are not yet explicit in the Engine.  An arbitrary
                        # independent arm change would open the contour and is
                        # therefore not a safe generated test.
                        continue
                    omissions.append(
                        TransformationRegressionOmission(
                            shape_code=shape.code.strip().upper(),
                            reason_code="INVALID_REFERENCE_TOPOLOGY",
                            message=(
                                f"Профил {profile.profile_id} не създава "
                                f"{len(shape.segments)}-сегментна геометрия."
                            ),
                        )
                    )
                    continue
                transformed = _transform_points(
                    source_points,
                    rotation_deg=float(values["rotation_deg"]),
                    scale=float(values["scale"]),
                    mirrored=bool(values["mirrored"]),
                    reversed_direction=bool(values["reversed_direction"]),
                )
                tolerance_raw = 0.0
                if values["tolerance_mode"] == TOLERANCE_SAFE_COORDINATE_NOISE:
                    transformed, tolerance_raw = _apply_safe_coordinate_noise(
                        transformed,
                        ratio=normalized.safe_coordinate_noise_ratio,
                        cap_raw=normalized.safe_coordinate_noise_cap_raw,
                        closed=bool(shape.closed),
                    )
                representation = str(values["representation"])
                source_segment_types = tuple(
                    str(segment.segment_type or "LINE").strip().upper()
                    for segment in sorted(
                        shape.segments,
                        key=lambda item: item.index,
                    )
                )
                if bool(values["reversed_direction"]):
                    source_segment_types = tuple(reversed(source_segment_types))
                geometry = _geometry_payload(
                    transformed,
                    representation=representation,
                    closed=bool(shape.closed),
                    source_segment_types=source_segment_types,
                )
                code = shape.code.strip().upper()
                cases.append(
                    TransformationRegressionCase(
                        test_id=(
                            f"TR-{code}-P{profile_index:02d}-"
                            f"C{case_index:03d}"
                        ),
                        shape_code=code,
                        shape_revision=shape.revision,
                        profile_id=profile.profile_id,
                        representation=representation,
                        geometry=geometry,
                        rotation_deg=float(values["rotation_deg"]),
                        scale=float(values["scale"]),
                        mirrored=bool(values["mirrored"]),
                        reversed_direction=bool(values["reversed_direction"]),
                        length_variant=variant_name,
                        segment_lengths_cm=segment_lengths,
                        coordinate_tolerance_raw=tolerance_raw,
                        expected_shape_code=code,
                        expected_closed=bool(shape.closed),
                        allow_expected_in_ambiguity=True,
                        mirror_requires_review_expected=bool(
                            values["mirrored"]
                            and policy.mirror_requires_shape_permission
                            and not (shape.mirror_allowed or shape.symmetric)
                        ),
                    )
                )

    _validate_unique_case_ids(cases)
    engine_payload = engine.to_dict(include_checksum=False)
    content_sha = hashlib.sha256(
        json.dumps(
            engine_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return TransformationRegressionFixture(
        engine_id=engine.engine_id,
        engine_version=engine.version,
        engine_content_sha256=content_sha,
        config=normalized.to_dict(),
        cases=tuple(cases),
        omissions=tuple(_deduplicate_omissions(omissions)),
    )


def run_transformation_regression(
    engine: EnginePackage,
    fixture: TransformationRegressionFixture | Mapping[str, Any] | None = None,
    *,
    config: TransformationRegressionConfig | None = None,
) -> TransformationRegressionReport:
    """Execute generated/serialized cases through the production matcher."""

    resolved = (
        generate_transformation_regression_fixture(engine, config=config)
        if fixture is None
        else (
            fixture
            if isinstance(fixture, TransformationRegressionFixture)
            else TransformationRegressionFixture.from_dict(fixture)
        )
    )
    if resolved.engine_id and resolved.engine_id != engine.engine_id:
        raise ValueError(
            "Regression fixture е за друг Engine: "
            f"{resolved.engine_id}, текущ {engine.engine_id}."
        )
    policy = RecognitionPolicy.from_settings(engine.settings)
    tests = _all_control_tests(engine)
    shapes = tuple(
        shape
        for shape in engine.shapes
        if shape.active
        and str(shape.state).strip().upper() in {"APPROVED", "LOCKED"}
    )
    shape_index = EngineShapeIndex.from_shapes(shapes)
    results: list[TransformationRegressionResult] = []
    ambiguous_valid_count = 0

    for case in resolved.cases:
        try:
            points = points_from_regression_geometry(
                case.geometry,
                representation=case.representation,
                expected_closed=case.expected_closed,
            )
        except ValueError as exc:
            results.append(
                TransformationRegressionResult(
                    test_id=case.test_id,
                    shape_code=case.shape_code,
                    passed=False,
                    matched_shape_code=None,
                    ambiguous_shape_codes=(),
                    message=f"Невалидна fixture геометрия: {exc}",
                )
            )
            continue
        match = match_master_shapes(
            points,
            shapes,
            policy=policy,
            control_tests=tests,
            shape_index=shape_index,
        )
        if match is None:
            results.append(
                TransformationRegressionResult(
                    test_id=case.test_id,
                    shape_code=case.shape_code,
                    passed=False,
                    matched_shape_code=None,
                    ambiguous_shape_codes=(),
                    message="Matcher не върна MASTER форма.",
                )
            )
            continue
        ambiguous = tuple(match.ambiguous_shape_codes)
        exact = match.shape_code.strip().upper() == case.expected_shape_code
        expected_in_ambiguity = (
            case.allow_expected_in_ambiguity
            and case.expected_shape_code in {value.strip().upper() for value in ambiguous}
        )
        mirror_review_ok = (
            bool(match.mirror_requires_review)
            == bool(case.mirror_requires_review_expected)
        )
        passed = (exact or expected_in_ambiguity) and mirror_review_ok
        if passed and not exact:
            ambiguous_valid_count += 1
        if not (exact or expected_in_ambiguity):
            message = (
                f"Очаквана {case.expected_shape_code}, "
                f"matcher върна {match.shape_code}."
            )
        elif not mirror_review_ok:
            message = (
                "Разрешеното огледало е изпратено за ръчен контрол."
                if match.mirror_requires_review
                else "Очакваният контрол за огледало липсва."
            )
        elif not exact:
            message = (
                f"Очакваната {case.expected_shape_code} е запазена в "
                f"обясним конфликт: {', '.join(ambiguous)}."
            )
        else:
            message = "Успешна трансформационна регресия."
        results.append(
            TransformationRegressionResult(
                test_id=case.test_id,
                shape_code=case.shape_code,
                passed=passed,
                matched_shape_code=match.shape_code,
                ambiguous_shape_codes=ambiguous,
                message=message,
            )
        )

    passed_count = sum(result.passed for result in results)
    return TransformationRegressionReport(
        case_count=len(results),
        passed_count=passed_count,
        ambiguous_valid_count=ambiguous_valid_count,
        results=tuple(results),
        omissions=resolved.omissions,
    )


def points_from_regression_geometry(
    geometry: Mapping[str, Any],
    *,
    representation: str | None = None,
    expected_closed: bool | None = None,
) -> tuple[Point2D, ...]:
    """Materialize canonical points from LWPOLYLINE or ordered LINE metadata."""

    resolved_representation = str(
        representation or geometry.get("representation") or ""
    ).strip().upper()
    closed = bool(
        geometry.get("closed", False)
        if expected_closed is None
        else expected_closed
    )
    if resolved_representation == REPRESENTATION_LWPOLYLINE:
        raw_vertices = geometry.get("vertices")
        if not isinstance(raw_vertices, (list, tuple)):
            raise ValueError("LWPOLYLINE няма vertices.")
        points = tuple(_point(value) for value in raw_vertices)
        if closed and points and not _points_close(points[0], points[-1]):
            points += (points[0],)
    elif resolved_representation == REPRESENTATION_LINE_CHAIN:
        raw_entities = geometry.get("entities")
        if not isinstance(raw_entities, (list, tuple)) or not raw_entities:
            raise ValueError("LINE_CHAIN няма entities.")
        points_list: list[Point2D] = []
        for index, raw in enumerate(raw_entities):
            if not isinstance(raw, Mapping):
                raise ValueError("LINE_CHAIN entity не е обект.")
            start = _point(raw.get("start"))
            end = _point(raw.get("end"))
            if index == 0:
                points_list.extend((start, end))
                continue
            if not _points_close(points_list[-1], start):
                raise ValueError(
                    f"LINE_CHAIN прекъсва преди сегмент {index + 1}."
                )
            points_list.append(end)
        points = tuple(points_list)
        if closed and points and not _points_close(points[0], points[-1]):
            raise ValueError("LINE_CHAIN е маркирана затворена, но краищата не съвпадат.")
    else:
        raise ValueError(
            f"Неподдържано DXF представяне: {resolved_representation or '—'}."
        )
    if len(points) < 2:
        raise ValueError("Геометрията има по-малко от две точки.")
    return points


def _all_control_tests(engine: EnginePackage) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = [
        test for test in engine.tests if isinstance(test, Mapping)
    ]
    for shape in engine.shapes:
        rows.extend(test for test in shape.tests if isinstance(test, Mapping))
    return tuple(rows)


def _coverage_axes(
    shape: ShapeDefinition,
    length_variants: Mapping[str, tuple[float, ...]],
    config: TransformationRegressionConfig,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    mirrors = (False,)
    mirror_is_permitted = bool(shape.mirror_allowed) or bool(shape.symmetric)
    if (
        (config.include_allowed_mirror and mirror_is_permitted)
        or (config.include_review_only_mirror and not mirror_is_permitted)
    ):
        mirrors = (False, True)
    return (
        ("rotation_deg", tuple(config.rotations_deg)),
        ("scale", tuple(config.scales)),
        (
            "reversed_direction",
            (False, True) if config.include_reversed_direction else (False,),
        ),
        ("mirrored", mirrors),
        ("representation", tuple(config.representations)),
        ("length_variant", tuple(length_variants)),
        (
            "tolerance_mode",
            (TOLERANCE_NONE, TOLERANCE_SAFE_COORDINATE_NOISE)
            if config.include_safe_tolerance
            else (TOLERANCE_NONE,),
        ),
    )


@lru_cache(maxsize=64)
def _pairwise_covering_rows(
    axes: tuple[tuple[Any, ...], ...],
) -> tuple[tuple[Any, ...], ...]:
    """Greedy deterministic pairwise covering array for small categorical axes."""

    candidates = tuple(itertools.product(*axes))
    if not candidates:
        return ()
    if len(axes) == 1:
        return candidates
    uncovered: set[tuple[int, int, Any, Any]] = set()
    for left_index in range(len(axes)):
        for right_index in range(left_index + 1, len(axes)):
            for left in axes[left_index]:
                for right in axes[right_index]:
                    uncovered.add((left_index, right_index, left, right))

    selected: list[tuple[Any, ...]] = []
    baseline = candidates[0]
    selected.append(baseline)
    uncovered.difference_update(_covered_pairs(baseline))

    while uncovered:
        best: tuple[Any, ...] | None = None
        best_score = -1
        for candidate in candidates:
            if candidate in selected:
                continue
            score = len(_covered_pairs(candidate) & uncovered)
            if score > best_score:
                best = candidate
                best_score = score
        if best is None or best_score <= 0:
            break
        selected.append(best)
        uncovered.difference_update(_covered_pairs(best))
    return tuple(selected)


def _covered_pairs(row: tuple[Any, ...]) -> set[tuple[int, int, Any, Any]]:
    return {
        (left, right, row[left], row[right])
        for left in range(len(row))
        for right in range(left + 1, len(row))
    }


def _length_variants(
    shape: ShapeDefinition,
    profile: ShapeReferenceProfile,
    *,
    include_parameter_variants: bool,
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
) -> dict[str, tuple[float, ...]]:
    reference = tuple(float(value) for value in profile.segment_lengths_cm)
    variants: dict[str, tuple[float, ...]] = {LENGTH_REFERENCE: reference}
    if not include_parameter_variants:
        return variants

    segments = sorted(shape.segments, key=lambda item: item.index)
    if len(segments) != len(reference):
        return variants
    explicit_codes = _explicit_parametric_codes(shape)
    has_explicit_policy = explicit_codes is not None or any(
        isinstance(segment.raw, Mapping)
        and segment.raw.get("parametric") is True
        for segment in segments
    )
    groups: list[str | None] = []
    unique_groups: list[str] = []
    for position, segment in enumerate(segments):
        raw = segment.raw if isinstance(segment.raw, Mapping) else {}
        explicitly_fixed = raw.get("parametric") is False
        code = str(segment.length_code or f"SEGMENT_{position + 1}").strip().upper()
        is_parametric = (
            segment.fixed_length_cm is None
            and not explicitly_fixed
            and (explicit_codes is None or code in explicit_codes)
        )
        group = code if is_parametric else None
        groups.append(group)
        if group is not None and group not in unique_groups:
            unique_groups.append(group)
    if not unique_groups:
        return variants

    factor_sets = (
        (LENGTH_PARAMETRIC_A, (0.78, 1.17, 1.36, 0.91, 1.24, 0.84)),
        (LENGTH_PARAMETRIC_B, (1.31, 0.83, 1.12, 1.42, 0.74, 1.19)),
    )
    for variant_name, factors in factor_sets:
        by_group = {
            group: factors[index % len(factors)]
            for index, group in enumerate(unique_groups)
        }
        lengths = tuple(
            value * by_group[group] if group is not None else value
            for value, group in zip(reference, groups)
        )
        synthetic = ShapeReferenceProfile(
            profile_id=f"{profile.profile_id}:{variant_name}",
            segment_lengths_cm=lengths,
            source=profile.source,
        )
        points = points_from_reference_profile(shape, synthetic)
        if not _has_expected_segment_count(
            points,
            expected_count=len(shape.segments),
            closed=bool(shape.closed),
        ):
            continue
        if any(
            not math.isfinite(value) or value <= 1e-6
            for value in lengths
        ):
            continue
        if all(abs(left - right) <= 1e-9 for left, right in zip(lengths, reference)):
            continue
        if not has_explicit_policy:
            match = match_master_shapes(
                points,
                (shape,),
                policy=policy,
                control_tests=control_tests,
            )
            if match is None or match.shape_code.strip().upper() != shape.code.strip().upper():
                # Legacy Engine records do not yet state which arms are
                # parametric.  Vary only dimensions that the current generic
                # shape contract demonstrably accepts.  Once
                # parametric_length_codes is explicit, a matcher failure is
                # intentionally kept as a blocking regression.
                continue
        variants[variant_name] = lengths
    return variants


def _explicit_parametric_codes(shape: ShapeDefinition) -> set[str] | None:
    recognition = shape.recognition if isinstance(shape.recognition, Mapping) else {}
    raw = recognition.get("parametric_length_codes")
    if not isinstance(raw, (list, tuple, set)):
        return None
    return {
        str(value).strip().upper()
        for value in raw
        if str(value).strip()
    }


def _transform_points(
    points: Sequence[Point2D],
    *,
    rotation_deg: float,
    scale: float,
    mirrored: bool,
    reversed_direction: bool,
) -> tuple[Point2D, ...]:
    angle = math.radians(float(rotation_deg))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    transformed: tuple[Point2D, ...] = tuple(
        (
            round(((-x if mirrored else x) * cosine - y * sine) * scale + 123.4, 9),
            round(((-x if mirrored else x) * sine + y * cosine) * scale - 55.6, 9),
        )
        for x, y in points
    )
    if reversed_direction:
        transformed = tuple(reversed(transformed))
    return transformed


def _apply_safe_coordinate_noise(
    points: Sequence[Point2D],
    *,
    ratio: float,
    cap_raw: float,
    closed: bool,
) -> tuple[tuple[Point2D, ...], float]:
    lengths = [
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
        if math.hypot(right[0] - left[0], right[1] - left[1]) > 1e-9
    ]
    if not lengths or ratio <= 0.0 or cap_raw <= 0.0:
        return tuple(points), 0.0
    epsilon = min(min(lengths) * ratio, cap_raw)
    if epsilon <= 0.0:
        return tuple(points), 0.0
    x_pattern = (0.0, 0.5, -0.75, 1.0, -0.25)
    y_pattern = (0.0, -1.0, 0.25, 0.75, -0.5)
    # Equal source vertices must receive the same perturbation.  This matters
    # for closed hooks/overlaps such as F006: independently moving two copies
    # of the same vertex would invent a tiny extra segment instead of testing
    # coordinate tolerance.
    groups: list[Point2D] = []
    group_indexes: list[int] = []
    for point in points:
        group_index = next(
            (
                index
                for index, existing in enumerate(groups)
                if _points_close(point, existing)
            ),
            None,
        )
        if group_index is None:
            groups.append(point)
            group_index = len(groups) - 1
        group_indexes.append(group_index)
    noisy = tuple(
        (
            round(
                point[0]
                + epsilon * x_pattern[group_index % len(x_pattern)],
                9,
            ),
            round(
                point[1]
                + epsilon * y_pattern[group_index % len(y_pattern)],
                9,
            ),
        )
        for point, group_index in zip(points, group_indexes)
    )
    if closed and noisy:
        noisy = noisy[:-1] + (noisy[0],)
    return noisy, epsilon


def _geometry_payload(
    points: Sequence[Point2D],
    *,
    representation: str,
    closed: bool,
    source_segment_types: Sequence[str],
) -> dict[str, Any]:
    segment_types = [
        str(value or "LINE").strip().upper()
        for value in source_segment_types
    ]
    curve_indexes = [
        index
        for index, value in enumerate(segment_types, start=1)
        if value != "LINE"
    ]
    curve_metadata = {
        "source_segment_types": segment_types,
        "curve_model": (
            "CHORD_PROXY_FOR_TOPOLOGY_REGRESSION"
            if curve_indexes
            else "EXACT_LINE_SEGMENTS"
        ),
        "approximated_curve_segments": curve_indexes,
    }
    if representation == REPRESENTATION_LWPOLYLINE:
        vertices = list(points)
        if closed and len(vertices) > 1 and _points_close(vertices[0], vertices[-1]):
            vertices.pop()
        return {
            "representation": REPRESENTATION_LWPOLYLINE,
            "entity_type": "LWPOLYLINE",
            "closed": closed,
            "vertices": [[x, y] for x, y in vertices],
            **curve_metadata,
        }
    if representation == REPRESENTATION_LINE_CHAIN:
        return {
            "representation": REPRESENTATION_LINE_CHAIN,
            "entity_type": "LINE",
            "closed": closed,
            "entities": [
                {
                    "entity_type": "LINE",
                    "sequence": index,
                    "start": [left[0], left[1]],
                    "end": [right[0], right[1]],
                    "source_segment_type": (
                        segment_types[index - 1]
                        if index - 1 < len(segment_types)
                        else "LINE"
                    ),
                }
                for index, (left, right) in enumerate(
                    zip(points, points[1:]),
                    start=1,
                )
            ],
            **curve_metadata,
        }
    raise ValueError(f"Неподдържано представяне: {representation}.")


def _has_expected_segment_count(
    points: Sequence[Point2D],
    *,
    expected_count: int,
    closed: bool,
) -> bool:
    if len(points) < 2:
        return False
    if closed and not _points_close(points[0], points[-1]):
        return False
    return len(points) - 1 == expected_count


def _point(value: Any) -> Point2D:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError("Точка трябва да съдържа X и Y.")
    return (
        _finite_float(value[0], "X"),
        _finite_float(value[1], "Y"),
    )


def _points_close(left: Point2D, right: Point2D) -> bool:
    return math.hypot(left[0] - right[0], left[1] - right[1]) <= 1e-7


def _finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} не е число.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} трябва да бъде крайно число.")
    return result


def _positive_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} трябва да бъде > 0.")
    return result


def _unique_finite(
    values: Iterable[Any],
    *,
    fallback: tuple[float, ...],
) -> tuple[float, ...]:
    rows: list[float] = []
    for raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value in rows:
            continue
        rows.append(value)
    return tuple(rows) or fallback


def _unique_strings(values: Iterable[Any]) -> tuple[str, ...]:
    rows: list[str] = []
    for raw in values:
        value = str(raw or "").strip().upper()
        if value and value not in rows:
            rows.append(value)
    return tuple(rows)


def _validate_unique_case_ids(
    cases: Iterable[TransformationRegressionCase],
) -> None:
    seen: set[str] = set()
    for case in cases:
        if not case.test_id:
            raise ValueError("Regression case няма test_id.")
        if case.test_id in seen:
            raise ValueError(f"Дублиран regression test_id: {case.test_id}.")
        seen.add(case.test_id)


def _deduplicate_omissions(
    values: Iterable[TransformationRegressionOmission],
) -> tuple[TransformationRegressionOmission, ...]:
    rows: list[TransformationRegressionOmission] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.shape_code, value.reason_code, value.message)
        if key in seen:
            continue
        seen.add(key)
        rows.append(value)
    return tuple(rows)
