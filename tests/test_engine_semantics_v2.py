from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from lion_rebar.engine.models import (
    EnginePackage,
    ParameterRelation,
    SegmentDefinition,
    ShapeDefinition,
)
from lion_rebar.engine.shape_index import (
    EngineShapeIndex,
    canonical_semantic_signature_from_shape,
    shape_turn_error,
    turn_constraints_from_shape,
)
from lion_rebar.engine.validation import (
    detect_shape_semantic_collisions,
    has_errors,
    validate_engine,
)


def _segment(
    index: int,
    length_code: str,
    *,
    bend: float | None = None,
    direction: str = "LEFT",
    drawing_direction: str | None = None,
    geometry_method: str = "DIRECT_LENGTH",
    projection_x_code: str | None = None,
    projection_y_code: str | None = None,
    fixed: float | None = None,
    angle_mode: str = "AUTO",
    min_angle: float | None = None,
    max_angle: float | None = None,
) -> SegmentDefinition:
    return SegmentDefinition(
        index=index,
        length_code=length_code,
        bend_after_deg=bend,
        bend_direction=direction,
        drawing_direction=drawing_direction,
        geometry_method=geometry_method,
        projection_x_code=projection_x_code,
        projection_y_code=projection_y_code,
        fixed_length_cm=fixed,
        angle_mode=angle_mode,
        min_angle_deg=min_angle,
        max_angle_deg=max_angle,
    )


def _u_shape(
    code: str,
    *,
    length_codes: tuple[str, str, str] = ("L1", "L2", "L3"),
    symmetric: bool = False,
    family_id: str = "",
    alias_of: str | None = None,
    relations: list[ParameterRelation] | None = None,
) -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=code,
        state="APPROVED",
        mirror_allowed=True,
        symmetric=symmetric,
        family_id=family_id,
        alias_of=alias_of,
        parameter_relations=list(relations or ()),
        segments=[
            _segment(1, length_codes[0], bend=90.0),
            _segment(2, length_codes[1], bend=90.0),
            _segment(3, length_codes[2]),
        ],
    )


def _engine(*shapes: ShapeDefinition) -> EnginePackage:
    return EnginePackage(
        engine_id="test-engine",
        title="Test",
        version="1.0.0",
        state="DRAFT",
        shapes=list(shapes),
    )


def test_legacy_json_loads_with_v2_defaults_and_roundtrips() -> None:
    engine = EnginePackage.from_dict(
        {
            "engine_id": "legacy",
            "title": "Legacy",
            "version": "0.3.4",
            "shapes": [
                {
                    "code": "F100",
                    "name": "Legacy shape",
                    "state": "APPROVED",
                    "segments": [
                        {
                            "index": 1,
                            "length_code": "L1",
                            "geometry_method": "DIRECT_LENGTH",
                        }
                    ],
                }
            ],
        }
    )

    shape = engine.shapes[0]
    assert shape.family_id == ""
    assert shape.alias_of is None
    assert shape.parameter_relations == []
    assert shape.segments[0].angle_mode == "AUTO"

    loaded_again = EnginePackage.from_dict(engine.to_dict())
    assert loaded_again.shapes[0].code == "F100"
    assert loaded_again.shapes[0].segments[0].angle_mode == "AUTO"


def test_real_v034_remains_readable_and_only_dangerous_publish_pairs_block() -> None:
    """Compatibility check against the untouched holdout MASTER artifact."""

    path = (
        Path(__file__).resolve().parents[3]
        / "TEST_HOLDOUT_01_ANALYSIS"
        / "diagnostics"
        / "master"
        / "engine_lion-rebar-master_v0.3.4.json"
    )
    if not path.exists():
        pytest.skip("Real v0.3.4 holdout ENGINE is not present in this checkout.")

    engine = EnginePackage.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )
    normal_issues = validate_engine(engine, for_publish=False)

    assert engine.version == "0.3.4"
    assert engine.find_shape("F002") is not None
    assert not has_errors(normal_issues)
    assert {
        item.shape_codes
        for item in detect_shape_semantic_collisions(engine)
    } == {
        ("F009", "F010"),
        ("F013", "F018"),
        ("F014", "F015"),
        ("F016", "F017"),
    }
    assert all(
        item.severity == "WARNING"
        for item in normal_issues
        if item.code == "CANONICAL_COLLISION_DANGEROUS"
    )

    publish_candidate = EnginePackage.from_dict(engine.to_dict())
    publish_candidate.production_ready = False
    publish_issues = validate_engine(
        publish_candidate,
        for_publish=True,
    )
    publish_errors = [
        item
        for item in publish_issues
        if item.severity == "ERROR"
    ]
    assert sum(
        item.code == "CANONICAL_COLLISION_DANGEROUS"
        for item in publish_errors
    ) == 4
    assert sum(
        item.code == "SEMANTIC_DOMAIN_OVERLAP"
        for item in publish_errors
    ) == 3
    assert {
        item.code for item in publish_errors
    } == {
        "CANONICAL_COLLISION_DANGEROUS",
        "SEMANTIC_DOMAIN_OVERLAP",
    }


def test_repeated_codes_explicit_equal_and_symmetric_compile_to_same_relation() -> None:
    repeated = _u_shape(
        "F100",
        length_codes=("SIDE", "MIDDLE", "SIDE"),
    )
    explicit = _u_shape(
        "F101",
        relations=[
            ParameterRelation(
                kind="EQUAL",
                left_segment=1,
                right_segment=3,
            )
        ],
    )
    symmetric = _u_shape("F102", symmetric=True)
    independent = _u_shape("F103")

    repeated_signature = canonical_semantic_signature_from_shape(repeated)
    explicit_signature = canonical_semantic_signature_from_shape(explicit)
    symmetric_signature = canonical_semantic_signature_from_shape(symmetric)
    independent_signature = canonical_semantic_signature_from_shape(independent)

    assert repeated_signature is not None
    assert explicit_signature is not None
    assert symmetric_signature is not None
    assert independent_signature is not None
    assert repeated_signature.value == explicit_signature.value
    assert repeated_signature.value == symmetric_signature.value
    assert repeated_signature.parameter_pattern == (
        "PARAM:1",
        "PARAM:2",
        "PARAM:1",
    )
    assert independent_signature.value != repeated_signature.value


def test_ratio_relation_participates_in_canonical_identity() -> None:
    independent = _u_shape("F100")
    ratio = _u_shape(
        "F101",
        relations=[
            ParameterRelation(
                kind="RATIO",
                left_segment=1,
                right_segment=3,
                ratio=2.0,
            )
        ],
    )

    independent_signature = canonical_semantic_signature_from_shape(independent)
    ratio_signature = canonical_semantic_signature_from_shape(ratio)

    assert independent_signature is not None
    assert ratio_signature is not None
    assert independent_signature.value != ratio_signature.value
    assert ratio_signature.relation_pattern == ("S1=0.5*S3",) or (
        ratio_signature.relation_pattern == ("S1=2*S3",)
    )


def test_projection_xy_turns_are_parametric_and_keep_plus_minus_relation() -> None:
    shape = ShapeDefinition(
        code="F200",
        name="Projection reduction",
        state="APPROVED",
        mirror_allowed=True,
        segments=[
            _segment(1, "L1", drawing_direction="RIGHT"),
            _segment(
                2,
                "L2",
                drawing_direction="UP_RIGHT",
                geometry_method="ПРОЕКЦИИ_XY",
                projection_x_code="DX2",
                projection_y_code="DY2",
            ),
            _segment(3, "L3", drawing_direction="RIGHT"),
        ],
    )
    constraints = turn_constraints_from_shape(shape)

    assert tuple(item.mode for item in constraints) == (
        "PARAMETRIC",
        "PARAMETRIC",
    )
    assert constraints[0].parameter_code == constraints[1].parameter_code
    assert (constraints[0].coefficient, constraints[1].coefficient) == (
        1.0,
        -1.0,
    )
    assert shape_turn_error(
        shape,
        (30.0, -30.0),
        allow_reverse=False,
        allow_mirror=False,
    ) == 0.0

    angle = math.radians(30.0)
    points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0 + 80.0 * math.cos(angle), 80.0 * math.sin(angle)),
        (300.0, 80.0 * math.sin(angle)),
    )
    query = EngineShapeIndex.from_shapes([shape]).query_points(points)
    assert query is not None
    assert tuple(item.code for item in query.exact) == ("F200",)


def test_projection_angle_range_rejects_out_of_range_candidate() -> None:
    shape = ShapeDefinition(
        code="F200",
        name="Bound projection",
        state="APPROVED",
        segments=[
            _segment(1, "L1", drawing_direction="RIGHT"),
            _segment(
                2,
                "L2",
                drawing_direction="UP_RIGHT",
                geometry_method="PROJECTIONS_XY",
                projection_x_code="DX",
                projection_y_code="DY",
                min_angle=35.0,
                max_angle=60.0,
            ),
            _segment(3, "L3", drawing_direction="RIGHT"),
        ],
    )

    assert math.isinf(
        shape_turn_error(
            shape,
            (30.0, -30.0),
            allow_reverse=False,
            allow_mirror=False,
        )
    )
    assert shape_turn_error(
        shape,
        (40.0, -40.0),
        allow_reverse=False,
        allow_mirror=False,
    ) == 0.0


def test_publish_blocks_unclassified_canonical_duplicate() -> None:
    engine = _engine(_u_shape("F100"), _u_shape("F101"))

    collisions = detect_shape_semantic_collisions(engine)
    issues = validate_engine(engine, for_publish=True)

    assert len(collisions) == 1
    assert collisions[0].kind == "DANGEROUS_DUPLICATE"
    assert any(
        item.code == "CANONICAL_COLLISION_DANGEROUS"
        and item.severity == "ERROR"
        for item in issues
    )
    assert has_errors(issues)


def test_reviewed_collision_keeps_distinct_codes_and_is_publish_safe() -> None:
    engine = _engine(_u_shape("F100"), _u_shape("F101"))
    engine.extensions["semantic_collision_resolutions"] = [
        {
            "shape_codes": ["F100", "F101"],
            "mode": "PROFILE_OR_MANUAL_REVIEW",
            "manual_review_on_ambiguity": True,
            "authorized_by": "Александър Пашунов",
            "reason": "Различен конструктивен контекст при еднаква геометрия.",
        }
    ]

    issues = validate_engine(engine, for_publish=True)

    assert any(
        item.code == "CANONICAL_COLLISION_REVIEWED"
        and item.severity == "WARNING"
        for item in issues
    )
    assert not any(
        item.code == "CANONICAL_COLLISION_DANGEROUS"
        for item in issues
    )
    assert not has_errors(issues)
    assert [shape.code for shape in engine.shapes] == ["F100", "F101"]
    assert all(shape.alias_of is None for shape in engine.shapes)
    assert all(shape.family_id == "" for shape in engine.shapes)


def test_reviewed_overlap_is_pair_specific_and_requires_owner_audit_fields() -> None:
    general = _u_shape("F100")
    constrained = _u_shape(
        "F101",
        relations=[
            ParameterRelation(
                kind="EQUAL",
                left_segment=1,
                right_segment=3,
            )
        ],
    )
    engine = _engine(general, constrained)
    engine.extensions["semantic_collision_resolutions"] = [
        {
            "shape_codes": ["F100", "F101"],
            "mode": "PROFILE_OR_MANUAL_REVIEW",
            "manual_review_on_ambiguity": True,
            "authorized_by": "Александър Пашунов",
            "reason": "По-специфичната форма остава отделен MASTER код.",
        }
    ]

    issues = validate_engine(engine, for_publish=True)

    assert any(
        item.code == "SEMANTIC_DOMAIN_OVERLAP_REVIEWED"
        and item.severity == "WARNING"
        for item in issues
    )
    assert not has_errors(issues)

    engine.extensions["semantic_collision_resolutions"][0]["authorized_by"] = ""
    invalid = validate_engine(engine, for_publish=True)
    assert any(
        item.code == "COLLISION_RESOLUTION_AUTH"
        and item.severity == "ERROR"
        for item in invalid
    )
    assert has_errors(invalid)


def test_declared_family_collision_is_audited_but_publish_safe() -> None:
    engine = _engine(
        _u_shape("F100", family_id="STAIR.LANDING"),
        _u_shape("F101", family_id="STAIR.LANDING"),
    )

    collisions = detect_shape_semantic_collisions(engine)
    issues = validate_engine(engine, for_publish=True)

    assert collisions[0].kind == "FAMILY"
    assert any(item.code == "CANONICAL_COLLISION_FAMILY" for item in issues)
    assert not has_errors(issues)


def test_alias_collision_resolves_to_canonical_code() -> None:
    canonical = _u_shape("F100", family_id="U")
    alias = _u_shape(
        "F101",
        family_id="U",
        alias_of="f100",
    )
    engine = _engine(canonical, alias)

    collisions = detect_shape_semantic_collisions(engine)
    issues = validate_engine(engine, for_publish=True)

    assert collisions[0].kind == "ALIAS"
    assert collisions[0].canonical_code == "F100"
    assert any(item.code == "CANONICAL_COLLISION_ALIAS" for item in issues)
    assert not has_errors(issues)

    index = EngineShapeIndex.from_shapes(engine.shapes)
    assert index.canonical_code("F101") == "F100"
    assert tuple(
        item.code for item in index.identity_group("F101")
    ) == ("F100", "F101")


def test_alias_must_have_same_semantics_and_alias_graph_cannot_cycle() -> None:
    canonical = _u_shape("F100")
    wrong = ShapeDefinition(
        code="F101",
        name="Wrong alias",
        state="APPROVED",
        alias_of="F100",
        segments=[_segment(1, "L1")],
    )
    mismatch_issues = validate_engine(
        _engine(canonical, wrong),
        for_publish=True,
    )
    assert any(
        item.code == "ALIAS_SEMANTICS_MISMATCH"
        for item in mismatch_issues
    )

    left = _u_shape("F102", alias_of="F103")
    right = _u_shape("F103", alias_of="F102")
    cycle_issues = validate_engine(
        _engine(left, right),
        for_publish=True,
    )
    assert any(item.code == "ALIAS_CYCLE" for item in cycle_issues)


def test_invalid_parameter_and_angle_constraints_block_publish() -> None:
    shape = _u_shape(
        "F100",
        relations=[
            ParameterRelation(
                kind="RATIO",
                left_segment=1,
                right_segment=99,
                ratio=-2.0,
            )
        ],
    )
    shape.segments[1].angle_mode = "PARAMETRIC"
    shape.segments[1].min_angle_deg = 80.0
    shape.segments[1].max_angle_deg = 20.0

    issues = validate_engine(_engine(shape), for_publish=True)
    codes = {item.code for item in issues}

    assert "PARAMETER_RELATION_SEGMENTS" in codes
    assert "PARAMETER_RELATION_RATIO" not in codes  # invalid target is primary
    assert "ANGLE_RANGE_ORDER" in codes
    assert has_errors(issues)
