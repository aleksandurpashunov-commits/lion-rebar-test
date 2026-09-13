from __future__ import annotations

import math

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import match_master_shapes
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.engine.shape_index import (
    EngineShapeIndex,
    ShapeTopology,
    canonical_signature_from_shape,
)


def _segment(
    index: int,
    code: str,
    *,
    bend: float | None = None,
    direction: str = "LEFT",
    segment_type: str = "LINE",
    radius: float | None = None,
    fixed: float | None = None,
) -> SegmentDefinition:
    return SegmentDefinition(
        index=index,
        length_code=code,
        bend_after_deg=bend,
        bend_direction=direction,
        segment_type=segment_type,
        radius_cm=radius,
        fixed_length_cm=fixed,
    )


def _open_shape(
    code: str,
    *,
    mirror_allowed: bool,
    state: str = "APPROVED",
    active: bool = True,
) -> ShapeDefinition:
    # Deliberately chiral turn sequence.  Its mirror is not equivalent merely
    # by traversing the chain from the opposite end.
    return ShapeDefinition(
        code=code,
        name=code,
        state=state,
        active=active,
        mirror_allowed=mirror_allowed,
        segments=[
            _segment(1, "L1", bend=90.0, direction="LEFT"),
            _segment(2, "L2", bend=45.0, direction="LEFT"),
            _segment(3, "L3", bend=30.0, direction="RIGHT"),
            _segment(4, "L4"),
        ],
    )


def _points_from_lengths(
    lengths: tuple[float, ...],
    turns: tuple[float, ...],
) -> tuple[tuple[float, float], ...]:
    points = [(0.0, 0.0)]
    heading = 0.0
    for index, length in enumerate(lengths):
        left = points[-1]
        radians = math.radians(heading)
        points.append(
            (
                left[0] + length * math.cos(radians),
                left[1] + length * math.sin(radians),
            )
        )
        if index < len(turns):
            heading += turns[index]
    return tuple(points)


def _transform(
    points: tuple[tuple[float, float], ...],
    *,
    rotation: float = 0.0,
    mirror: bool = False,
    reverse: bool = False,
) -> tuple[tuple[float, float], ...]:
    angle = math.radians(rotation)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    output = []
    for x, y in points:
        if mirror:
            y = -y
        output.append(
            (
                x * cosine - y * sine + 1234.5,
                x * sine + y * cosine - 987.6,
            )
        )
    if reverse:
        output.reverse()
    return tuple(output)


def test_index_contains_only_active_approved_or_locked_shapes() -> None:
    approved = _open_shape("F100", mirror_allowed=True)
    locked = _open_shape("F101", mirror_allowed=True, state="LOCKED")
    draft = _open_shape("F102", mirror_allowed=True, state="DRAFT")
    retired = _open_shape("F103", mirror_allowed=True, state="RETIRED")
    inactive = _open_shape("F104", mirror_allowed=True, active=False)

    index = EngineShapeIndex.from_engine(
        EnginePackage(
            engine_id="test",
            title="test",
            version="1",
            shapes=[approved, locked, draft, retired, inactive],
        )
    )

    assert len(index) == 2
    assert tuple(entry.code for entry in index.entries) == ("F100", "F101")


def test_allowed_mirror_rotation_and_reverse_have_one_signature() -> None:
    shape = _open_shape("F100", mirror_allowed=True)
    index = EngineShapeIndex.from_shapes([shape])
    base = _points_from_lengths(
        (20.0, 70.0, 35.0, 12.0),
        (90.0, 45.0, -30.0),
    )
    expected = index.find("F100")
    assert expected is not None

    for rotation in (0.0, 17.0, 90.0, 223.0):
        for mirror in (False, True):
            for reverse in (False, True):
                query = index.query_points(
                    _transform(
                        base,
                        rotation=rotation,
                        mirror=mirror,
                        reverse=reverse,
                    )
                )
                assert query is not None
                assert tuple(entry.code for entry in query.exact) == ("F100",)
                assert query.matches[0].max_angle_error_deg < 1e-8


def test_forbidden_mirror_is_not_silently_normalized() -> None:
    shape = _open_shape("F100", mirror_allowed=False)
    index = EngineShapeIndex.from_shapes([shape])
    base = _points_from_lengths(
        (20.0, 70.0, 35.0, 12.0),
        (90.0, 45.0, -30.0),
    )

    direct = index.query_points(_transform(base, rotation=61.0, reverse=True))
    mirrored = index.query_points(_transform(base, rotation=61.0, mirror=True))

    assert direct is not None
    assert tuple(entry.code for entry in direct.exact) == ("F100",)
    assert mirrored is not None
    assert mirrored.prefiltered[0].code == "F100"
    assert not mirrored.matches

    discoverable_for_review = index.query_points(
        _transform(base, rotation=61.0, mirror=True),
        allow_unpermitted_mirror=True,
    )
    assert discoverable_for_review is not None
    assert tuple(entry.code for entry in discoverable_for_review.exact) == ("F100",)


def test_closed_shape_start_vertex_rotation_and_reverse_are_invariant() -> None:
    square = ShapeDefinition(
        code="F200",
        name="Closed square",
        state="LOCKED",
        closed=True,
        symmetric=True,
        segments=[
            _segment(1, "L1", bend=90.0, direction="LEFT"),
            _segment(2, "L2", bend=90.0, direction="LEFT"),
            _segment(3, "L1", bend=90.0, direction="LEFT"),
            _segment(4, "L2"),
        ],
    )
    index = EngineShapeIndex.from_shapes([square])
    points = (
        (0.0, 0.0),
        (40.0, 0.0),
        (40.0, 25.0),
        (0.0, 25.0),
        (0.0, 0.0),
    )
    shifted = points[2:-1] + points[:3]
    reversed_shifted = tuple(reversed(shifted))

    for value in (points, shifted, reversed_shifted):
        query = index.query_points(value, closed=True)
        assert query is not None
        assert tuple(entry.code for entry in query.exact) == ("F200",)


def test_closed_arc_and_radius_pattern_stays_aligned_when_reversed() -> None:
    shape = ShapeDefinition(
        code="F201",
        name="Closed with one curved segment",
        state="APPROVED",
        closed=True,
        mirror_allowed=True,
        segments=[
            _segment(
                1,
                "L1",
                bend=90.0,
                direction="LEFT",
                segment_type="ARC",
                radius=6.0,
            ),
            _segment(2, "L2", bend=90.0, direction="LEFT"),
            _segment(3, "L3", bend=90.0, direction="LEFT"),
            _segment(4, "L4"),
        ],
    )
    index = EngineShapeIndex.from_shapes([shape])
    points = (
        (0.0, 0.0),
        (40.0, 0.0),
        (40.0, 25.0),
        (0.0, 25.0),
        (0.0, 0.0),
    )
    direct_families = ("ARC", "LINE", "LINE", "LINE")
    direct_radii = (True, False, False, False)
    reversed_points = tuple(reversed(points))
    reversed_families = tuple(reversed(direct_families))
    reversed_radii = tuple(reversed(direct_radii))

    direct = index.query_points(
        points,
        closed=True,
        segment_families=direct_families,
        radius_pattern=direct_radii,
    )
    reversed_query = index.query_points(
        reversed_points,
        closed=True,
        segment_families=reversed_families,
        radius_pattern=reversed_radii,
    )

    assert direct is not None
    assert reversed_query is not None
    assert direct.exact[0].code == "F201"
    assert reversed_query.exact[0].code == "F201"


def test_prefilter_uses_segment_turn_closed_arc_and_radius_features() -> None:
    open_line = ShapeDefinition(
        code="F300",
        name="Open line",
        state="APPROVED",
        segments=[
            _segment(1, "L1", bend=90.0),
            _segment(2, "L2"),
        ],
    )
    open_arc = ShapeDefinition(
        code="F301",
        name="Open arc",
        state="APPROVED",
        segments=[
            _segment(1, "L1", bend=90.0, segment_type="ARC", radius=8.0),
            _segment(2, "L2"),
        ],
    )
    closed_line = ShapeDefinition(
        code="F302",
        name="Closed",
        state="APPROVED",
        closed=True,
        segments=[
            _segment(1, "L1", bend=90.0),
            _segment(2, "L2", bend=90.0),
            _segment(3, "L3"),
        ],
    )
    index = EngineShapeIndex.from_shapes([open_line, open_arc, closed_line])

    unknown_primitive_type = index.prefilter(
        ShapeTopology(
            segment_count=2,
            turn_count=1,
            closed=False,
        )
    )
    exact_line = index.prefilter(
        ShapeTopology(
            segment_count=2,
            turn_count=1,
            closed=False,
            arc_count=0,
            radius_count=0,
        )
    )
    exact_arc = index.prefilter(
        ShapeTopology(
            segment_count=2,
            turn_count=1,
            closed=False,
            arc_count=1,
            radius_count=1,
        )
    )

    assert tuple(entry.code for entry in unknown_primitive_type) == ("F300", "F301")
    assert tuple(entry.code for entry in exact_line) == ("F300",)
    assert tuple(entry.code for entry in exact_arc) == ("F301",)

    # A point-only chain does not prove whether its curved-looking primitive
    # was stored as LINE or ARC.  The index keeps both in the shortlist and
    # reports the angular match as non-exact evidence.
    point_only = index.query_points(((0.0, 0.0), (20.0, 0.0), (20.0, -30.0)))
    assert point_only is not None
    assert tuple(entry.code for entry in point_only.prefiltered) == ("F300", "F301")
    assert tuple(item.entry.code for item in point_only.matches) == ("F300", "F301")


def test_parametric_lengths_do_not_change_type_signature() -> None:
    shape = ShapeDefinition(
        code="F400",
        name="Parametric U",
        state="APPROVED",
        mirror_allowed=True,
        segments=[
            _segment(1, "SIDE", bend=90.0),
            _segment(2, "MIDDLE", bend=90.0),
            _segment(3, "SIDE"),
        ],
    )
    index = EngineShapeIndex.from_shapes([shape])

    short = _points_from_lengths((150.0, 300.0, 150.0), (-90.0, -90.0))
    long = _points_from_lengths((220.0, 480.0, 220.0), (-90.0, -90.0))

    assert index.query_points(short).exact[0].code == "F400"  # type: ignore[union-attr]
    assert index.query_points(long).exact[0].code == "F400"  # type: ignore[union-attr]
    entry = index.find("F400")
    assert entry is not None
    assert entry.parameter_pattern == ("PARAM:1", "PARAM:2", "PARAM:1")


def test_fixed_lengths_are_audited_but_not_used_as_prefilter() -> None:
    shape = ShapeDefinition(
        code="F500",
        name="Fixed audit",
        state="APPROVED",
        segments=[
            _segment(1, "L1", bend=90.0, fixed=25.0),
            _segment(2, "L2", fixed=40.0),
        ],
    )
    index = EngineShapeIndex.from_shapes([shape])
    entry = index.find("F500")

    assert entry is not None
    assert entry.fixed_lengths_cm == (25.0, 40.0)
    assert entry.parameter_pattern == ("FIXED:25", "FIXED:40")
    query = index.query_points(((0.0, 0.0), (100.0, 0.0), (100.0, -300.0)))
    assert query is not None
    assert query.exact[0].code == "F500"


def test_public_signature_helper_obeys_shape_mirror_policy() -> None:
    allowed = _open_shape("F600", mirror_allowed=True)
    forbidden = _open_shape("F601", mirror_allowed=False)

    allowed_signature = canonical_signature_from_shape(allowed)
    forbidden_signature = canonical_signature_from_shape(forbidden)

    assert allowed_signature is not None
    assert forbidden_signature is not None
    assert allowed_signature.mirror_invariant is True
    assert forbidden_signature.mirror_invariant is False


def test_match_master_shapes_accepts_a_compile_once_index() -> None:
    target = _open_shape("F700", mirror_allowed=True)
    wrong_topology = ShapeDefinition(
        code="F701",
        name="Two segments",
        state="APPROVED",
        segments=[
            _segment(1, "L1", bend=90.0),
            _segment(2, "L2"),
        ],
    )
    shapes = (wrong_topology, target)
    index = EngineShapeIndex.from_shapes(shapes)
    points = _transform(
        _points_from_lengths(
            (20.0, 70.0, 35.0, 12.0),
            (90.0, 45.0, -30.0),
        ),
        rotation=131.0,
        mirror=True,
        reverse=True,
    )

    match = match_master_shapes(
        points,
        shapes,
        policy=RecognitionPolicy(),
        shape_index=index,
    )

    assert match is not None
    assert match.shape_code == "F700"


def test_matcher_collapses_alias_to_canonical_engine_identity() -> None:
    canonical = _open_shape("F710", mirror_allowed=True)
    canonical.family_id = "OPEN-CHIRAL"
    alias = _open_shape("F711", mirror_allowed=True)
    alias.family_id = "OPEN-CHIRAL"
    alias.alias_of = "F710"
    shapes = (canonical, alias)
    index = EngineShapeIndex.from_shapes(shapes)
    points = _points_from_lengths(
        (20.0, 70.0, 35.0, 12.0),
        (90.0, 45.0, -30.0),
    )

    match = match_master_shapes(
        points,
        shapes,
        policy=RecognitionPolicy(),
        shape_index=index,
    )

    assert match is not None
    assert match.shape_code == "F710"
    assert match.canonical_shape_code == "F710"
    assert match.identity_key == "FAMILY:OPEN-CHIRAL"
    assert match.equivalent_shape_codes == ("F710", "F711")
    assert match.ambiguous_shape_codes == ()
    assert match.score_gap is None


def test_matcher_collapses_declared_family_without_false_ambiguity() -> None:
    first = _open_shape("F720", mirror_allowed=True)
    first.family_id = "DECLARED-FAMILY"
    second = _open_shape("F721", mirror_allowed=True)
    second.family_id = "DECLARED-FAMILY"
    shapes = (first, second)
    points = _points_from_lengths(
        (20.0, 70.0, 35.0, 12.0),
        (90.0, 45.0, -30.0),
    )

    match = match_master_shapes(
        points,
        shapes,
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes(shapes),
        control_tests=[
            {
                "shape_code": "F721",
                "test_id": "F721-PREFERRED-PROFILE",
                "geometry": {
                    "segment_lengths_cm": [20.0, 70.0, 35.0, 12.0],
                },
            }
        ],
    )

    assert match is not None
    # The best concrete profile belongs to F721, but a declared family has one
    # stable representative instead of changing code from project to project.
    assert match.shape_code == "F720"
    assert match.reference_profile_id == "F721-PREFERRED-PROFILE"
    assert match.equivalent_shape_codes == ("F720", "F721")
    assert match.ambiguous_shape_codes == ()


def test_matcher_keeps_distinct_engine_identities_ambiguous() -> None:
    first = _open_shape("F730", mirror_allowed=True)
    first.family_id = "FAMILY-A"
    second = _open_shape("F731", mirror_allowed=True)
    second.family_id = "FAMILY-B"
    shapes = (first, second)
    points = _points_from_lengths(
        (20.0, 70.0, 35.0, 12.0),
        (90.0, 45.0, -30.0),
    )

    match = match_master_shapes(
        points,
        shapes,
        policy=RecognitionPolicy(),
        shape_index=EngineShapeIndex.from_shapes(shapes),
    )

    assert match is not None
    assert set(match.ambiguous_shape_codes) == {"F730", "F731"}
    assert match.equivalent_shape_codes == ("F730",)


def test_index_shortlists_one_topology_from_a_500_shape_library() -> None:
    """Library growth must not turn every position into a 500-form comparison."""

    nuisance: list[ShapeDefinition] = []
    for number in range(499):
        segment_count = 1 + (number % 6)
        nuisance.append(
            ShapeDefinition(
                code=f"F{1000 + number}",
                name=f"Nuisance {number}",
                state="APPROVED",
                segments=[
                    _segment(
                        index,
                        f"L{index}",
                        bend=90.0 if index < segment_count else None,
                    )
                    for index in range(1, segment_count + 1)
                ],
            )
        )

    target = ShapeDefinition(
        code="F9999",
        name="Seven segment target",
        state="LOCKED",
        mirror_allowed=True,
        segments=[
            _segment(
                index,
                f"L{index}",
                bend=90.0 if index < 7 else None,
            )
            for index in range(1, 8)
        ],
    )
    index = EngineShapeIndex.from_shapes([*nuisance, target])
    points = _points_from_lengths(
        (20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0),
        (90.0, 90.0, 90.0, 90.0, 90.0, 90.0),
    )

    query = index.query_points(points)

    assert len(index) == 500
    assert query is not None
    assert tuple(entry.code for entry in query.prefiltered) == ("F9999",)
    assert tuple(entry.code for entry in query.exact) == ("F9999",)
