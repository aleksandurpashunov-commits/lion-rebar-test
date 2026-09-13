from __future__ import annotations

import math

from lion_rebar.dxf.dimension_resolver import dimension_evidence_for_shape
from lion_rebar.dxf.geometry_candidates import (
    _resolve_unique_master_dimensions,
    find_geometry_candidates,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import match_master_shapes
from lion_rebar.engine.models import (
    ParameterRelation,
    SegmentDefinition,
    ShapeDefinition,
)


def _shape(
    code: str,
    profile: tuple[float, float],
    *,
    relations: list[ParameterRelation] | None = None,
) -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=code,
        state="APPROVED",
        active=True,
        mirror_allowed=True,
        segments=[
            SegmentDefinition(
                index=1,
                length_code="L1",
                bend_after_deg=29.0,
                bend_direction="RIGHT",
            ),
            SegmentDefinition(index=2, length_code="L2"),
        ],
        parameter_relations=list(relations or ()),
        recognition={"reference_segment_lengths_cm": list(profile)},
    )


# Recovery.2 (правило 7): A-E доказателството разрешава двусмислие само когато
# производственият вектор се приема от ТОЧНО ЕДНА от двусмислените
# идентичности.  Двойка, различима единствено по контролен профил, не е
# доказуема от коти, които пасват и на двете.  Затова синтетичният F018 тук
# носи изрично задължително отношение L1 = 4 x L2: начертаната геометрия
# (268/67 = 4:1) го удовлетворява, така че първоначалното F013/F018
# двусмислие остава, а печатните коти 285/50 (285 != 4 x 50) отхвърлят F018
# — двойката е реално различима и котите я решават законно към F013.
def _f018_relations() -> list[ParameterRelation]:
    return [
        ParameterRelation(
            kind="RATIO",
            left_segment=1,
            right_segment=2,
            ratio=4.0,
        )
    ]


def _case(*, conflicting_dimensions: bool = False):
    angle = math.radians(-29.0)
    corner = (268.0, 0.0)
    end = (corner[0] + 67.0 * math.cos(angle), corner[1] + 67.0 * math.sin(angle))
    annotation = DxfEntityRecord(
        "A1",
        "BLOCK_ATTR_TEXT",
        "ARM-N10",
        {"insert": [130.0, -20.0, 0.0]},
        "8N10x335",
    )
    entities = [
        annotation,
        DxfEntityRecord(
            "G1",
            "LINE",
            "ARM-N10",
            {"start": [0.0, 0.0, 0.0], "end": [*corner, 0.0]},
        ),
        DxfEntityRecord(
            "G2",
            "LINE",
            "ARM-N10",
            {"start": [*corner, 0.0], "end": [*end, 0.0]},
        ),
        DxfEntityRecord(
            "D1",
            "TEXT",
            "KOTI",
            {"insert": [134.0, 8.0, 0.0], "rotation": 0.0},
            "285",
        ),
        DxfEntityRecord(
            "D2",
            "TEXT",
            "KOTI",
            {
                "insert": [(corner[0] + end[0]) / 2.0, (corner[1] + end[1]) / 2.0 - 6.0, 0.0],
                "rotation": 151.0,
            },
            "50",
        ),
    ]
    if conflicting_dimensions:
        entities.extend(
            [
                DxfEntityRecord(
                    "D3",
                    "TEXT",
                    "KOTI",
                    {"insert": [134.0, 8.0, 0.0], "rotation": 0.0},
                    "250",
                ),
                DxfEntityRecord(
                    "D4",
                    "TEXT",
                    "KOTI",
                    {
                        "insert": [
                            (corner[0] + end[0]) / 2.0,
                            (corner[1] + end[1]) / 2.0 - 6.0,
                            0.0,
                        ],
                        "rotation": 151.0,
                    },
                    "85",
                ),
            ]
        )
    return annotation, entities, ((0.0, 0.0), corner, end)


def test_a_e_dimensions_resolve_profile_ambiguity_without_using_drawn_ratio() -> None:
    f013 = _shape("F013", (285.0, 50.0))
    f018 = _shape("F018", (150.0, 50.0), relations=_f018_relations())
    annotation, entities, _points = _case()

    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=(f013, f018),
    )

    candidate = analysis.candidates[0]
    assert candidate.master_shape_code == "F013"
    assert candidate.master_ambiguous_shape_codes == ()
    assert candidate.dimension_master_resolved is True
    assert candidate.dimension_original_ambiguous_shape_codes == ("F013", "F018")
    assert candidate.canonical_segment_lengths_cm == (285.0, 50.0)
    assert "D2" in candidate.dimension_text_handles


def test_a_e_resolver_exposes_multiple_exact_interpretations_instead_of_picking_one() -> None:
    f013 = _shape("F013", (285.0, 50.0))
    _annotation, entities, points = _case(conflicting_dimensions=True)

    evidence = dimension_evidence_for_shape(
        points,
        f013,
        entities,
        declared_length_cm=335.0,
    )

    lengths = {
        min(
            tuple(round(value, 3) for value in item.segment_lengths_cm),
            tuple(reversed(tuple(round(value, 3) for value in item.segment_lengths_cm))),
        )
        for item in evidence
    }
    assert (50.0, 285.0) in lengths
    assert (85.0, 250.0) in lengths


def test_conflicting_a_e_interpretations_keep_master_ambiguity() -> None:
    f013 = _shape("F013", (285.0, 50.0))
    f018 = _shape("F018", (250.0, 85.0))
    annotation, entities, _points = _case(conflicting_dimensions=True)

    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=(f013, f018),
    )

    candidate = analysis.candidates[0]
    assert candidate.dimension_master_resolved is False
    assert candidate.master_ambiguous_shape_codes == ("F013", "F018")


def test_total_minus_main_dimension_infers_one_25_cm_bend() -> None:
    shape = ShapeDefinition(
        code="F004",
        name="ЕДИН ВИНКЕЛ",
        state="LOCKED",
        segments=[
            SegmentDefinition(
                index=1,
                length_code="L1",
                bend_after_deg=90.0,
                bend_direction="LEFT",
            ),
            SegmentDefinition(index=2, length_code="L2"),
        ],
    )
    points = ((0.0, 0.0), (525.0, 0.0), (525.0, 25.0))
    entities = [
        DxfEntityRecord(
            "MAIN",
            "TEXT",
            "KOTI",
            {"insert": [260.0, -8.0, 0.0], "rotation": 0.0},
            "525",
        )
    ]

    evidence = dimension_evidence_for_shape(
        points,
        shape,
        entities,
        declared_length_cm=550.0,
    )

    assert evidence[0].segment_lengths_cm == (525.0, 25.0)
    assert evidence[0].strategy == "A_E_TOTAL_MINUS_PRINTED"


def test_two_equal_bends_split_delta_only_when_drawn_end_arms_agree() -> None:
    shape = ShapeDefinition(
        code="F003",
        name="ДВА ВИНКЕЛА",
        state="LOCKED",
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90.0),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90.0),
            SegmentDefinition(index=3, length_code="L3"),
        ],
    )
    entities = [
        DxfEntityRecord(
            "MAIN",
            "TEXT",
            "KOTI",
            {"insert": [260.0, -8.0, 0.0], "rotation": 0.0},
            "525",
        )
    ]

    symmetric = dimension_evidence_for_shape(
        ((0.0, 15.0), (0.0, 0.0), (525.0, 0.0), (525.0, 15.0)),
        shape,
        entities,
        declared_length_cm=555.0,
    )
    asymmetric = dimension_evidence_for_shape(
        ((0.0, 10.0), (0.0, 0.0), (525.0, 0.0), (525.0, 35.0)),
        shape,
        entities,
        declared_length_cm=555.0,
    )

    assert symmetric[0].segment_lengths_cm == (15.0, 525.0, 15.0)
    assert symmetric[0].strategy == "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS"
    assert asymmetric == ()


def _independent_f005() -> ShapeDefinition:
    return ShapeDefinition(
        code="F005",
        name="ФИБА",
        state="LOCKED",
        active=True,
        mirror_allowed=True,
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90.0),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90.0),
            SegmentDefinition(index=3, length_code="L3"),
        ],
        recognition={"reference_segment_lengths_cm": [66.0, 18.0, 66.0]},
    )


def _perpendicular_bridge_case(*, competing: bool = False):
    points = ((66.0, 18.0), (0.0, 18.0), (0.0, 0.0), (66.0, 0.0))
    entities = [
        DxfEntityRecord(
            "A1",
            "BLOCK_ATTR_TEXT",
            "ARM-N10",
            {"insert": [30.0, -20.0, 0.0]},
            "1N10x140",
        ),
        DxfEntityRecord(
            "D61",
            "TEXT",
            "KOTI",
            {"insert": [33.0, 24.0, 0.0], "rotation": 0.0},
            "61",
        ),
        # The designer's value for the vertical bridge is horizontal text.
        # It is valid perpendicular evidence and is spatially on the bridge.
        DxfEntityRecord(
            "D18",
            "TEXT",
            "KOTI",
            {"insert": [-4.0, 9.0, 0.0], "rotation": 0.0},
            "18",
        ),
        DxfEntityRecord(
            "FAR",
            "TEXT",
            "KOTI",
            {"insert": [500.0, 500.0, 0.0], "rotation": 0.0},
            "79",
        ),
    ]
    if competing:
        entities.extend(
            [
                DxfEntityRecord(
                    "FOREIGN-60",
                    "TEXT",
                    "KOTI",
                    {"insert": [33.0, 23.0, 0.0], "rotation": 0.0},
                    "60",
                ),
                DxfEntityRecord(
                    "FOREIGN-20",
                    "TEXT",
                    "KOTI",
                    {"insert": [-3.0, 9.0, 0.0], "rotation": 0.0},
                    "20",
                ),
            ]
        )
    return points, entities


def test_perpendicular_bridge_text_maps_61_18_to_canonical_61_18_61() -> None:
    shape = _independent_f005()
    points, entities = _perpendicular_bridge_case()
    policy = RecognitionPolicy()
    initial = match_master_shapes(points, (shape,), policy=policy)
    assert initial is not None

    resolved = _resolve_unique_master_dimensions(
        points,
        declared_length_cm=140.0,
        annotation_handle="A1",
        entities=entities,
        master_match=initial,
        shape_definitions=(shape,),
        control_tests=(),
        policy=policy,
        shape_index=None,
    )

    assert resolved is not None
    _match, evidence = resolved
    canonical = min(
        evidence.segment_lengths_cm,
        tuple(reversed(evidence.segment_lengths_cm)),
    )
    assert canonical == (61.0, 18.0, 61.0)
    assert canonical != (18.0, 61.0, 61.0)
    assert set(evidence.text_handles) == {"D18", "D61"}
    assert "FAR" not in evidence.text_handles


def test_close_foreign_dimension_pair_keeps_resolution_fail_closed() -> None:
    shape = _independent_f005()
    points, entities = _perpendicular_bridge_case(competing=True)
    policy = RecognitionPolicy()
    initial = match_master_shapes(points, (shape,), policy=policy)
    assert initial is not None

    resolved = _resolve_unique_master_dimensions(
        points,
        declared_length_cm=140.0,
        annotation_handle="A1",
        entities=entities,
        master_match=initial,
        shape_definitions=(shape,),
        control_tests=(),
        policy=policy,
        shape_index=None,
    )

    assert resolved is None


def test_f005_total_minus_bridge_prefers_two_exact_printed_arms() -> None:
    shape = _independent_f005()
    points = (
        (0.0, 18.0),
        (90.0, 18.0),
        (90.0, 0.0),
        (40.0, 0.0),
    )
    entities = [
        DxfEntityRecord(
            "D90",
            "TEXT",
            "KOTI",
            {"insert": [63.0, 32.0, 0.0], "rotation": 0.0},
            "90",
        ),
        DxfEntityRecord(
            "D19",
            "TEXT",
            "KOTI",
            {"insert": [105.0, 9.0, 0.0], "rotation": 0.0},
            "19",
        ),
        DxfEntityRecord(
            "D50",
            "TEXT",
            "KOTI",
            {"insert": [63.0, -9.0, 0.0], "rotation": 0.0},
            "50",
        ),
    ]
    policy = RecognitionPolicy()
    initial = match_master_shapes(points, (shape,), policy=policy)
    assert initial is not None

    resolved = _resolve_unique_master_dimensions(
        points,
        declared_length_cm=160.0,
        annotation_handle="A1",
        entities=entities,
        master_match=initial,
        shape_definitions=(shape,),
        control_tests=(),
        policy=policy,
        shape_index=None,
    )

    assert resolved is not None
    _match, evidence = resolved
    assert evidence.segment_lengths_cm == (90.0, 20.0, 50.0)
    assert evidence.text_handles == ("D90", "D50")
    assert evidence.strategy == "A_E_TOTAL_MINUS_PRINTED"


def test_dimension_measurement_without_text_is_valid_numeric_evidence() -> None:
    shape = ShapeDefinition(
        code="F004",
        name="ЕДИН ВИНКЕЛ",
        state="LOCKED",
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90.0),
            SegmentDefinition(index=2, length_code="L2"),
        ],
    )
    points = ((0.0, 0.0), (100.0, 0.0), (100.0, 20.0))
    entities = [
        DxfEntityRecord(
            "DIM100",
            "DIMENSION",
            "KOTI",
            {
                "measurement": 99.999,
                "text_midpoint": [50.0, -8.0, 0.0],
                "defpoint2": [0.0, 0.0, 0.0],
                "defpoint3": [100.0, 0.0, 0.0],
            },
            None,
        )
    ]

    evidence = dimension_evidence_for_shape(
        points,
        shape,
        entities,
        declared_length_cm=120.0,
    )

    assert evidence
    assert evidence[0].segment_lengths_cm == (100.0, 20.0)
    assert evidence[0].text_handles == ("DIM100",)


def test_blank_dimension_measurement_requires_both_endpoints_on_candidate() -> None:
    shape = ShapeDefinition(
        code="F004",
        name="ЕДИН ВИНКЕЛ",
        state="LOCKED",
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90.0),
            SegmentDefinition(index=2, length_code="L2"),
        ],
    )
    points = ((0.0, 0.0), (1035.0, 0.0), (1035.0, 35.0))
    entities = [
        DxfEntityRecord(
            "LOCAL1035",
            "TEXT",
            "ARMTXT",
            {"insert": [500.0, -8.0, 0.0], "rotation": 0.0},
            "1035",
        ),
        DxfEntityRecord(
            "FOREIGN32",
            "DIMENSION",
            "KOTI",
            {
                "measurement": 32.0,
                "text_midpoint": [1035.0, 10.0, 0.0],
                "defpoint2": [400.0, 300.0, 0.0],
                "defpoint3": [700.0, 300.0, 0.0],
            },
            None,
        ),
    ]

    evidence = dimension_evidence_for_shape(
        points,
        shape,
        entities,
        declared_length_cm=1070.0,
    )

    assert evidence
    assert evidence[0].segment_lengths_cm == (1035.0, 35.0)
    assert evidence[0].text_handles == ("LOCAL1035",)
    assert all("FOREIGN32" not in row.text_handles for row in evidence)


def test_f003_three_adjacent_rows_keep_middle_15_dimensions_on_middle_row() -> None:
    shape = ShapeDefinition(
        code="F003",
        name="ДВА ВИНКЕЛА",
        state="LOCKED",
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90.0),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90.0),
            SegmentDefinition(index=3, length_code="L3"),
        ],
    )
    # Normalized coordinates from the three adjacent R-4 beam rows
    # 4N18x700 / 3N10x690 / 2N12x700.  All nine numeric texts are deliberately
    # supplied to every resolution call: the middle row's 15 cm texts are
    # within the generic 60-unit search radius of both neighboring contours.
    entities = [
        DxfEntityRecord(
            "TOP-LEFT-20",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [-22.511, 12.099, 0.0], "rotation": 90.0},
            "20",
        ),
        DxfEntityRecord(
            "TOP-MAIN-660",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [322.559, 13.343, 0.0], "rotation": 0.0},
            "660",
        ),
        DxfEntityRecord(
            "TOP-RIGHT-20",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [677.190, 7.965, 0.0], "rotation": 90.0},
            "20",
        ),
        DxfEntityRecord(
            "MID-LEFT-15",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [-22.511, 47.762, 0.0], "rotation": 90.0},
            "15",
        ),
        DxfEntityRecord(
            "MID-MAIN-660",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [327.551, 74.006, 0.0], "rotation": 0.0},
            "660",
        ),
        DxfEntityRecord(
            "MID-RIGHT-15",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [677.191, 43.821, 0.0], "rotation": 90.0},
            "15",
        ),
        DxfEntityRecord(
            "BOTTOM-LEFT-20",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [-22.511, 88.009, 0.0], "rotation": 90.0},
            "20",
        ),
        DxfEntityRecord(
            "BOTTOM-MAIN-660",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [327.551, 114.254, 0.0], "rotation": 0.0},
            "660",
        ),
        DxfEntityRecord(
            "BOTTOM-RIGHT-20",
            "TEXT",
            "ARMTXT1",
            {"visual_anchor": [677.191, 84.068, 0.0], "rotation": 90.0},
            "20",
        ),
    ]
    rows = (
        (
            ((0.0, 25.0), (0.0, 0.0), (660.102, 0.0), (660.102, 25.0)),
            700.0,
            (20.0, 660.0, 20.0),
            "TOP-MAIN-660",
        ),
        (
            (
                (0.0, 45.663),
                (0.0, 60.663),
                (660.102, 60.663),
                (660.102, 45.663),
            ),
            690.0,
            (15.0, 660.0, 15.0),
            "MID-MAIN-660",
        ),
        (
            (
                (0.0, 75.911),
                (0.0, 100.911),
                (660.102, 100.911),
                (660.102, 75.911),
            ),
            700.0,
            (20.0, 660.0, 20.0),
            "BOTTOM-MAIN-660",
        ),
    )

    resolved = []
    for points, declared, expected, main_handle in rows:
        evidence = dimension_evidence_for_shape(
            points,
            shape,
            entities,
            declared_length_cm=declared,
        )

        assert evidence
        assert evidence[0].segment_lengths_cm == expected
        assert evidence[0].text_handles == (main_handle,)
        assert evidence[0].strategy == "A_E_DELTA_SPLIT_TWO_EQUAL_BENDS"
        resolved.append(evidence[0])

    for evidence in (resolved[0], resolved[2]):
        assert {"MID-LEFT-15", "MID-RIGHT-15"}.isdisjoint(
            evidence.text_handles
        )
