from __future__ import annotations

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import (
    match_master_shapes,
    points_from_reference_profile,
    reference_profiles_for_shape,
)
from lion_rebar.engine.models import (
    EnginePackage,
    SegmentDefinition,
    ShapeDefinition,
)
from lion_rebar.engine.recognition_audit import audit_engine_recognition

CONTROL_TESTS = [
    {
        "ID_ТЕСТ": "T-F001-001",
        "КОД_ФОРМА": "F001",
        "ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ": 200,
        "ОЧАКВАНИ_РАЗМЕРИ_СМ": "L1=200",
    },
    {
        "ID_ТЕСТ": "T-F010-001",
        "КОД_ФОРМА": "F010",
        "ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ": 370,
        "ОЧАКВАНИ_РАЗМЕРИ_СМ": "L1=200; L2=28; L3=19; L4=10; L5=113",
    },
    {
        "ID_ТЕСТ": "T-F011-001",
        "КОД_ФОРМА": "F011",
        "ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ": 284,
        "ОЧАКВАНИ_РАЗМЕРИ_СМ": "L1=25; L2=47; L3=140; L4=47; L5=25",
    },
]


def _segment(
    index: int,
    code: str,
    direction: str,
    bend: float | None = None,
    bend_direction: str = "LEFT",
) -> SegmentDefinition:
    return SegmentDefinition(
        index=index,
        length_code=code,
        drawing_direction=direction,
        bend_after_deg=bend,
        bend_direction=bend_direction,
    )


def _shapes() -> list[ShapeDefinition]:
    return [
        ShapeDefinition(
            code="F001",
            name="ПРАВ ПРЪТ",
            state="LOCKED",
            locked=True,
            symmetric=True,
            mirror_allowed=True,
            segments=[_segment(1, "L1", "НАДЯСНО")],
        ),
        ShapeDefinition(
            code="F010",
            name="НЕСТАНДАРТНА ФОРМА – ДОЛНА СКАРА",
            state="APPROVED",
            mirror_allowed=True,
            segments=[
                _segment(1, "L1", "НАДЯСНО", 90),
                _segment(2, "L2", "НАГОРЕ", 90),
                _segment(3, "L3", "НАЛЯВО", 90),
                _segment(4, "L4", "НАДОЛУ", 90),
                _segment(5, "L5", "НАДЯСНО"),
            ],
        ),
        ShapeDefinition(
            code="F011",
            name="НЕСТАНДАРТНА ФОРМА – КАПИТЕЛИ",
            state="APPROVED",
            symmetric=True,
            mirror_allowed=True,
            initial_direction="LEFT",
            segments=[
                _segment(1, "L1", "НАЛЯВО", 90),
                _segment(2, "L2", "НАДОЛУ", 90),
                _segment(3, "L3", "НАДЯСНО", 90),
                _segment(4, "L4", "НАГОРЕ", 90),
                _segment(5, "L5", "НАЛЯВО"),
            ],
        ),
    ]


def _line(
    handle: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    layer: str = "Arm - N14",
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer=layer,
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
    )


def _annotation(y: float = -60.0) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text="12N14x284",
        geometry={"insert": [45.0, y, 0.0], "height": 3.0},
    )


def _f011_lines(scale: float = 1.0, *, layer: str = "Arm - N14") -> list[DxfEntityRecord]:
    raw = [
        ((0.0, 0.0), (-25.0, 0.0)),
        ((-25.0, 0.0), (-25.0, -47.0)),
        ((-25.0, -47.0), (115.0, -47.0)),
        ((115.0, -47.0), (115.0, 0.0)),
        ((115.0, 0.0), (90.0, 0.0)),
    ]
    return [
        _line(
            f"D8{index}",
            (left[0] * scale, left[1] * scale),
            (right[0] * scale, right[1] * scale),
            layer=layer,
        )
        for index, (left, right) in enumerate(raw, start=1)
    ]


def test_f011_profile_beats_f010_topology_collision() -> None:
    shapes = _shapes()
    entities = [_annotation(), *_f011_lines()]

    analysis = find_geometry_candidates(
        entities[0],
        entities,
        shape_definitions=shapes,
        control_tests=CONTROL_TESTS,
    )

    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.master_shape_code == "F011"
    assert top.master_reference_profile_id == "T-F011-001"
    # Recovery 2026-08: this symmetric instance also fully satisfies the
    # unconstrained F010, so the instance-level F010/F011 conflict is now
    # mandatory; the profile still decides top-1, but never silently.
    assert set(top.master_ambiguous_shape_codes) == {"F010", "F011"}
    assert len(top.handles) == 5
    assert any(
        "ENGINE отношенията са изпълнени" in note
        for note in top.notes
    )
    assert any("ENGINE relation:" in note for note in top.notes)


def test_uniformly_scaled_f011_stays_f011_and_full_chain_wins() -> None:
    shapes = _shapes()
    entities = [_annotation(y=-120.0), *_f011_lines(scale=2.0)]

    analysis = find_geometry_candidates(
        entities[0],
        entities,
        shape_definitions=shapes,
        control_tests=CONTROL_TESTS,
    )

    top = analysis.candidates[0]
    assert top.master_shape_code == "F011"
    assert top.uniform_scale_factor == 2.0
    # Recovery 2026-08: the mandatory F010/F011 instance conflict now joins
    # the schematic-scale reason; the row still lands in manual review.
    assert top.review_reason_codes == ("AMBIGUOUS_MASTER", "SCHEMATIC_SCALE")
    assert top.confidence == "КОНФЛИКТ МЕЖДУ MASTER ФОРМИ — РЪЧНА ПРОВЕРКА"
    assert len(top.handles) == 5
    assert all(
        candidate.master_shape_code != "F001" or candidate.rank > top.rank
        for candidate in analysis.candidates
    )


def test_far_f011_is_found_but_never_strong() -> None:
    shapes = _shapes()
    entities = [_annotation(y=-180.0), *_f011_lines()]

    analysis = find_geometry_candidates(
        entities[0],
        entities,
        shape_definitions=shapes,
        control_tests=CONTROL_TESTS,
    )

    top = analysis.candidates[0]
    assert top.master_shape_code == "F011"
    assert "FAR_ASSOCIATION" in top.review_reason_codes
    # Recovery 2026-08: the instance conflict label takes precedence, but the
    # far association is still recorded and the row still cannot be strong.
    assert top.confidence == "КОНФЛИКТ МЕЖДУ MASTER ФОРМИ — РЪЧНА ПРОВЕРКА"
    assert top.confidence.endswith("РЪЧНА ПРОВЕРКА")


def test_explicit_wrong_layer_diameter_remains_a_hard_block() -> None:
    shapes = _shapes()
    entities = [_annotation(), *_f011_lines(layer="Arm - N12")]

    analysis = find_geometry_candidates(
        entities[0],
        entities,
        shape_definitions=shapes,
        control_tests=CONTROL_TESTS,
    )

    assert not analysis.candidates
    assert analysis.rejection_counts["ГРЕШЕН ДИАМЕТЪР В СЛОЯ"] > 0


def test_topology_only_collision_is_reported_not_hidden() -> None:
    shapes = _shapes()
    profile = reference_profiles_for_shape(shapes[2], CONTROL_TESTS)[0]
    points = points_from_reference_profile(shapes[2], profile)

    match = match_master_shapes(
        points,
        shapes,
        policy=RecognitionPolicy(),
        control_tests=(),
    )

    assert match is not None
    assert set(match.ambiguous_shape_codes) == {"F010", "F011"}


def test_engine_matcher_matrix_is_a_permanent_regression_gate() -> None:
    engine = EnginePackage(
        engine_id="lion-rebar-master",
        title="Matcher regression",
        version="0.3.4",
        state="DRAFT",
        production_ready=True,
        shapes=_shapes(),
        tests=list(CONTROL_TESTS),
    )

    audit = audit_engine_recognition(engine)

    assert audit.case_count == 144
    assert audit.passed_count == 144
    assert audit.passed
