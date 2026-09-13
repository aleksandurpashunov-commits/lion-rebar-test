from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import (
    match_master_shapes,
    match_shape_length_relations,
)
from lion_rebar.engine.models import (
    EnginePackage,
    ParameterRelation,
    SegmentDefinition,
    ShapeDefinition,
)
from lion_rebar.engine.shape_index import shape_semantic_domains_overlap
from lion_rebar.engine.validation import validate_engine


def _segments(
    codes: tuple[str, ...],
    *,
    turns: tuple[float, ...],
) -> list[SegmentDefinition]:
    return [
        SegmentDefinition(
            index=index + 1,
            length_code=code,
            bend_after_deg=(turns[index] if index < len(turns) else None),
            bend_direction="LEFT",
        )
        for index, code in enumerate(codes)
    ]


def _shape(
    code: str,
    codes: tuple[str, ...],
    *,
    turns: tuple[float, ...],
    symmetric: bool = False,
    closed: bool = False,
    family_id: str = "",
    relations: list[ParameterRelation] | None = None,
) -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=code,
        state="APPROVED",
        mirror_allowed=True,
        symmetric=symmetric,
        closed=closed,
        family_id=family_id,
        parameter_relations=list(relations or ()),
        segments=_segments(codes, turns=turns),
    )


def _points(
    lengths: tuple[float, ...],
    turns: tuple[float, ...],
) -> tuple[tuple[float, float], ...]:
    result = [(0.0, 0.0)]
    heading = 0.0
    for index, length in enumerate(lengths):
        x, y = result[-1]
        angle = math.radians(heading)
        result.append(
            (
                x + length * math.cos(angle),
                y + length * math.sin(angle),
            )
        )
        if index < len(turns):
            heading += turns[index]
    return tuple(result)


def test_repeated_length_relation_is_scale_reverse_and_rotation_invariant() -> None:
    shape = _shape(
        "F005",
        ("SIDE", "MIDDLE", "SIDE", "TAIL"),
        turns=(90.0, 90.0, 90.0, 90.0),
        closed=True,
    )
    shifted = (300.0, 100.0, 40.0, 100.0)
    reversed_shifted = tuple(reversed(shifted))

    direct = match_shape_length_relations(shape, shifted)
    reverse = match_shape_length_relations(shape, reversed_shifted)
    wrong = match_shape_length_relations(
        shape,
        (300.0, 100.0, 40.0, 130.0),
    )

    assert direct.compatible
    assert direct.required_rule_count == 1
    assert direct.start_shift != 0
    assert reverse.compatible
    assert not wrong.compatible


def test_equal_u_keeps_real_f003_f005_semantic_overlap_visible() -> None:
    f003 = _shape(
        "F003",
        ("L1", "L2", "L3"),
        turns=(90.0, 90.0),
    )
    f005 = _shape(
        "F005",
        ("SIDE", "MIDDLE", "SIDE"),
        turns=(90.0, 90.0),
        symmetric=True,
    )
    lengths = (15.0, 205.0, 15.0)

    match = match_master_shapes(
        _points(lengths, (90.0, 90.0)),
        (f003, f005),
        policy=RecognitionPolicy(),
    )

    assert match is not None
    assert set(match.ambiguous_shape_codes) == {"F003", "F005"}
    assert match.score_gap == 0.0


def test_exact_owner_profile_wins_top1_but_keeps_the_overlap_conflict() -> None:
    """Recovery 2026-08: this test previously asserted the owner-confirmed
    silent-wrong defect (an exact F003 profile win on a palindromic instance
    reported with NO ambiguity).  A palindromic instance fully satisfies
    F005's required relation, so the specific code cannot be proven from
    geometry; the profile still decides top-1, but the F003/F005 conflict is
    mandatory (Закон 16, ZERO SILENT WRONG).  Only the documented printed
    A-E dimension resolution may clear it afterwards."""
    f003 = _shape(
        "F003",
        ("L1", "L2", "L3"),
        turns=(90.0, 90.0),
    )
    f005 = _shape(
        "F005",
        ("SIDE", "MIDDLE", "SIDE"),
        turns=(90.0, 90.0),
        symmetric=True,
    )
    lengths = (15.0, 205.0, 15.0)

    match = match_master_shapes(
        _points(lengths, (90.0, 90.0)),
        (f003, f005),
        policy=RecognitionPolicy(),
        control_tests=[
            {
                "shape_code": "F003",
                "test_id": "F003-EXACT-OWNER",
                "geometry": {"segment_lengths_cm": list(lengths)},
            }
        ],
    )

    assert match is not None
    assert match.shape_code == "F003"
    assert match.reference_profile_id == "F003-EXACT-OWNER"
    assert set(match.ambiguous_shape_codes) == {"F003", "F005"}


def test_asymmetric_u_rejects_f005_required_relation_and_keeps_f003() -> None:
    f003 = _shape(
        "F003",
        ("L1", "L2", "L3"),
        turns=(90.0, 90.0),
    )
    f005 = _shape(
        "F005",
        ("SIDE", "MIDDLE", "SIDE"),
        turns=(90.0, 90.0),
        symmetric=True,
    )

    match = match_master_shapes(
        _points((15.0, 205.0, 25.0), (90.0, 90.0)),
        (f003, f005),
        policy=RecognitionPolicy(),
    )

    assert match is not None
    assert match.shape_code == "F003"
    assert match.ambiguous_shape_codes == ()


def test_symmetric_f010_f011_overlap_is_visible_but_asymmetric_rejects_f011() -> None:
    turns = (90.0, 90.0, 90.0, 90.0)
    f010 = _shape(
        "F010",
        ("L1", "L2", "L3", "L4", "L5"),
        turns=turns,
    )
    f011 = _shape(
        "F011",
        ("A", "B", "C", "D", "E"),
        turns=turns,
        symmetric=True,
    )
    symmetric_lengths = (25.0, 47.0, 140.0, 47.0, 25.0)

    symmetric = match_master_shapes(
        _points(symmetric_lengths, turns),
        (f010, f011),
        policy=RecognitionPolicy(),
    )
    asymmetric = match_master_shapes(
        _points((200.0, 28.0, 19.0, 10.0, 113.0), turns),
        (f010, f011),
        policy=RecognitionPolicy(),
    )

    assert symmetric is not None
    assert set(symmetric.ambiguous_shape_codes) == {"F010", "F011"}
    assert asymmetric is not None
    assert asymmetric.shape_code == "F010"
    assert asymmetric.ambiguous_shape_codes == ()


def test_explicit_ratio_is_required_and_genuine_identity_ambiguity_remains() -> None:
    ratio_shape = _shape(
        "F100",
        ("A", "B", "C"),
        turns=(90.0, 90.0),
        relations=[
            ParameterRelation(
                kind="RATIO",
                left_segment=1,
                right_segment=3,
                ratio=2.0,
            )
        ],
    )
    valid = match_shape_length_relations(
        ratio_shape,
        (40.0, 100.0, 20.0),
    )
    invalid = match_shape_length_relations(
        ratio_shape,
        (40.0, 100.0, 30.0),
    )
    assert valid.compatible
    assert not invalid.compatible

    first = _shape(
        "F110",
        ("L1", "L2", "L3"),
        turns=(90.0, 90.0),
        family_id="IDENTITY-A",
    )
    second = _shape(
        "F111",
        ("X1", "X2", "X3"),
        turns=(90.0, 90.0),
        family_id="IDENTITY-B",
    )
    ambiguous = match_master_shapes(
        _points((40.0, 100.0, 20.0), (90.0, 90.0)),
        (first, second),
        policy=RecognitionPolicy(),
    )

    assert ambiguous is not None
    assert set(ambiguous.ambiguous_shape_codes) == {"F110", "F111"}


def test_publish_rejects_one_family_with_different_semantic_signatures() -> None:
    unconstrained = _shape(
        "F200",
        ("L1", "L2", "L3"),
        turns=(90.0, 90.0),
        family_id="U-FAMILY",
    )
    equal_ends = _shape(
        "F201",
        ("SIDE", "MIDDLE", "SIDE"),
        turns=(90.0, 90.0),
        family_id="U-FAMILY",
    )
    engine = EnginePackage(
        engine_id="test",
        title="test",
        version="1.0.0",
        state="DRAFT",
        shapes=[unconstrained, equal_ends],
    )

    draft = validate_engine(engine, for_publish=False)
    publish = validate_engine(engine, for_publish=True)

    assert any(
        issue.code == "FAMILY_SEMANTICS_MISMATCH"
        and issue.severity == "WARNING"
        for issue in draft
    )
    assert any(
        issue.code == "FAMILY_SEMANTICS_MISMATCH"
        and issue.severity == "ERROR"
        for issue in publish
    )


def test_publish_reports_one_clear_overlapping_parameter_domain_gate() -> None:
    unconstrained = _shape(
        "F300",
        ("L1", "L2", "L3"),
        turns=(90.0, 90.0),
    )
    equal_ends = _shape(
        "F301",
        ("SIDE", "MIDDLE", "SIDE"),
        turns=(90.0, 90.0),
    )
    incompatible_ratio = _shape(
        "F302",
        ("A", "B", "C"),
        turns=(90.0, 90.0),
        relations=[
            ParameterRelation(
                kind="RATIO",
                left_segment=1,
                right_segment=3,
                ratio=2.0,
            )
        ],
    )
    other_ratio = _shape(
        "F303",
        ("A", "B", "C"),
        turns=(90.0, 90.0),
        relations=[
            ParameterRelation(
                kind="RATIO",
                left_segment=1,
                right_segment=3,
                ratio=3.0,
            )
        ],
    )

    assert shape_semantic_domains_overlap(unconstrained, equal_ends)
    assert not shape_semantic_domains_overlap(
        incompatible_ratio,
        other_ratio,
    )

    engine = EnginePackage(
        engine_id="test-overlap",
        title="test",
        version="1.0.0",
        state="DRAFT",
        shapes=[unconstrained, equal_ends],
    )
    normal = validate_engine(engine, for_publish=False)
    publish = validate_engine(engine, for_publish=True)

    assert sum(
        issue.code == "SEMANTIC_DOMAIN_OVERLAP"
        and issue.severity == "WARNING"
        for issue in normal
    ) == 1
    assert sum(
        issue.code == "SEMANTIC_DOMAIN_OVERLAP"
        and issue.severity == "ERROR"
        for issue in publish
    ) == 1


def test_real_v034_publish_has_overlap_gates_not_audit_failure_cascade() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "TEST_HOLDOUT_01_ANALYSIS"
        / "diagnostics"
        / "master"
        / "engine_lion-rebar-master_v0.3.4.json"
    )
    if not path.exists():
        pytest.skip("Real v0.3.4 holdout ENGINE is not present.")
    engine = EnginePackage.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )

    issues = validate_engine(engine, for_publish=True)
    codes = [issue.code for issue in issues]

    assert "SEMANTIC_DOMAIN_OVERLAP" in codes
    assert "RECOGNITION_AUDIT_FAILED" not in codes
    assert "GENERATED_TRANSFORMATION_REGRESSION_FAILED" not in codes
