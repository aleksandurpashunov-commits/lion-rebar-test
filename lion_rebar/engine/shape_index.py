"""Canonical, data-driven index of approved ENGINE shapes.

The index deliberately knows nothing about individual ``F`` codes.  It derives
topology and orientation-independent signatures only from
:class:`~lion_rebar.engine.models.ShapeDefinition` data.

It is designed as the first (cheap) stage of recognition:

1. filter by segment/turn/closed/arc/radius topology;
2. compare canonical turn signatures;
3. let the matcher perform its more expensive profile and evidence scoring on
   the small returned set.

Lengths are intentionally absent from the topology signature.  A segment is
parametric unless the ENGINE explicitly records a fixed length; fixed lengths
and repeated parameter relations remain available as audit metadata on each
entry, but do not make a shape disappear during prefiltering.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from lion_rebar.engine.models import (
    EnginePackage,
    SegmentDefinition,
    ShapeDefinition,
)

Point2D = tuple[float, float]

APPROVED_SHAPE_STATES = frozenset({"APPROVED", "LOCKED"})
_LEFT_DIRECTIONS = frozenset({"LEFT", "НАЛЯВО"})


@dataclass(frozen=True, order=True)
class ShapeTopology:
    """Cheap structural features used before any detailed comparison.

    ``arc_count`` and ``radius_count`` may be ``None`` on an observed DXF
    geometry when its primitive type/radius information is unavailable.  In
    that case those two features act as unknown values, not as zero.
    """

    segment_count: int
    turn_count: int
    closed: bool
    arc_count: int | None = None
    radius_count: int | None = None

    @property
    def core_key(self) -> tuple[int, int, bool]:
        return self.segment_count, self.turn_count, self.closed


@dataclass(frozen=True)
class CanonicalShapeSignature:
    """Canonical descriptor independent of translation and rotation.

    Reverse traversal is always normalized.  Mirroring is normalized only when
    ``mirror_invariant`` is true, which is derived from the ENGINE shape.
    """

    value: str
    turns_deg: tuple[float, ...]
    segment_families: tuple[str, ...]
    radius_pattern: tuple[bool, ...]
    mirror_invariant: bool


@dataclass(frozen=True)
class TurnConstraint:
    """One ENGINE turn rule aligned with the shape's sorted segments.

    A fixed rule compares directly with ``reference_angle_deg``.  A parametric
    rule belongs to a shared ``parameter_code`` and uses ``coefficient`` to
    preserve relations such as ``(+theta, -theta)`` while allowing theta itself
    to change.  This is how a legacy ``ПРОЕКЦИИ_XY`` segment stops being
    accidentally frozen to its 45-degree drawing direction.
    """

    mode: str
    reference_angle_deg: float
    parameter_code: str = ""
    coefficient: float = 0.0
    min_angle_deg: float | None = None
    max_angle_deg: float | None = None

    @property
    def parametric(self) -> bool:
        return self.mode == "PARAMETRIC"


@dataclass(frozen=True)
class CanonicalSemanticSignature:
    """Canonical engineering identity, not merely visual topology."""

    value: str
    topology_value: str
    parameter_pattern: tuple[str, ...]
    turn_pattern: tuple[str, ...]
    relation_pattern: tuple[str, ...]
    mirror_invariant: bool


@dataclass(frozen=True)
class IndexedShape:
    """One immutable search record generated from an approved shape."""

    shape: ShapeDefinition
    topology: ShapeTopology
    canonical_signature: CanonicalShapeSignature
    discovery_signature: CanonicalShapeSignature
    traversal_signature: CanonicalShapeSignature
    semantic_signature: CanonicalSemanticSignature
    parameter_pattern: tuple[str, ...]
    fixed_lengths_cm: tuple[float | None, ...]
    turn_constraints: tuple[TurnConstraint, ...]
    canonical_code: str
    identity_key: str

    @property
    def code(self) -> str:
        return self.shape.code

    @property
    def mirror_allowed(self) -> bool:
        return bool(self.shape.mirror_allowed or self.shape.symmetric)

    @property
    def is_alias(self) -> bool:
        return self.code.strip().upper() != self.canonical_code


@dataclass(frozen=True)
class ShapeIndexMatch:
    """A topology-compatible ENGINE entry and its angular disagreement."""

    entry: IndexedShape
    max_angle_error_deg: float
    exact_signature: bool


@dataclass(frozen=True)
class ShapeIndexQuery:
    """Auditable result of one point-chain lookup."""

    topology: ShapeTopology
    prefiltered: tuple[IndexedShape, ...]
    matches: tuple[ShapeIndexMatch, ...]

    @property
    def exact(self) -> tuple[IndexedShape, ...]:
        return tuple(item.entry for item in self.matches if item.exact_signature)

    @property
    def shape_definitions(self) -> tuple[ShapeDefinition, ...]:
        return tuple(item.entry.shape for item in self.matches)

    @property
    def identity_groups(self) -> tuple[tuple[IndexedShape, ...], ...]:
        groups: dict[str, list[IndexedShape]] = defaultdict(list)
        for match in self.matches:
            groups[match.entry.identity_key].append(match.entry)
        return tuple(
            tuple(sorted(items, key=lambda entry: entry.code.strip().upper()))
            for _key, items in sorted(groups.items())
        )


@dataclass(frozen=True)
class _Descriptor:
    turns_deg: tuple[float, ...]
    segment_families: tuple[str, ...]
    radius_pattern: tuple[bool, ...]
    parameter_pattern: tuple[str, ...]
    fixed_lengths_cm: tuple[float | None, ...]
    turn_constraints: tuple[TurnConstraint, ...]
    ratio_relations: tuple[tuple[int, int, float], ...]


class EngineShapeIndex:
    """Fast canonical index built solely from approved ENGINE definitions.

    Rebuild this object whenever an Engine package is published or activated.
    The class itself is read-only after construction and can be safely reused
    for every position in one batch analysis.
    """

    def __init__(
        self,
        entries: Iterable[IndexedShape],
        *,
        angle_precision_deg: float = 0.1,
    ):
        precision = float(angle_precision_deg)
        if not math.isfinite(precision) or precision <= 0:
            raise ValueError("angle_precision_deg must be a positive finite number")
        self.angle_precision_deg = precision
        ordered = sorted(
            entries,
            key=lambda item: (
                item.shape.code.strip().upper(),
                int(item.shape.revision),
            ),
        )
        self._entries = tuple(ordered)
        buckets: dict[tuple[int, int, bool], list[IndexedShape]] = defaultdict(list)
        for entry in ordered:
            buckets[entry.topology.core_key].append(entry)
        self._by_core = {
            key: tuple(value)
            for key, value in buckets.items()
        }

    @classmethod
    def from_engine(
        cls,
        engine: EnginePackage,
        *,
        angle_precision_deg: float = 0.1,
    ) -> "EngineShapeIndex":
        return cls.from_shapes(
            engine.shapes,
            angle_precision_deg=angle_precision_deg,
        )

    @classmethod
    def from_shapes(
        cls,
        shapes: Iterable[ShapeDefinition],
        *,
        angle_precision_deg: float = 0.1,
    ) -> "EngineShapeIndex":
        precision = float(angle_precision_deg)
        source_shapes = tuple(shapes)
        shape_by_code = {
            shape.code.strip().upper(): shape
            for shape in source_shapes
            if shape.code.strip()
        }
        entries: list[IndexedShape] = []
        for shape in source_shapes:
            if not _is_indexable_shape(shape):
                continue
            code = shape.code.strip().upper()
            canonical_code = _shape_alias_root(code, shape_by_code) or code
            canonical_shape = shape_by_code.get(canonical_code, shape)
            family_id = (
                shape.family_id.strip().upper()
                or canonical_shape.family_id.strip().upper()
            )
            identity_key = (
                f"FAMILY:{family_id}"
                if family_id
                else f"SHAPE:{canonical_code}"
            )
            entry = _entry_from_shape(
                shape,
                precision,
                canonical_code=canonical_code,
                identity_key=identity_key,
            )
            if entry is not None:
                entries.append(entry)
        return cls(entries, angle_precision_deg=precision)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[IndexedShape, ...]:
        return self._entries

    def find(self, shape_code: str) -> IndexedShape | None:
        normalized = str(shape_code).strip().upper()
        return next(
            (
                entry
                for entry in self._entries
                if entry.shape.code.strip().upper() == normalized
            ),
            None,
        )

    def canonical_code(self, shape_code: str) -> str | None:
        entry = self.find(shape_code)
        return entry.canonical_code if entry is not None else None

    def identity_group(
        self,
        shape_code: str,
    ) -> tuple[IndexedShape, ...]:
        entry = self.find(shape_code)
        if entry is None:
            return ()
        return tuple(
            item
            for item in self._entries
            if item.identity_key == entry.identity_key
        )

    def prefilter(self, topology: ShapeTopology) -> tuple[IndexedShape, ...]:
        """Return only structurally compatible approved shapes.

        Unknown candidate arc/radius counts (``None``) deliberately do not
        reject a shape.  Known counts are exact safety filters.
        """

        bucket = self._by_core.get(topology.core_key, ())
        return tuple(
            entry
            for entry in bucket
            if (
                topology.arc_count is None
                or entry.topology.arc_count == topology.arc_count
            )
            and (
                topology.radius_count is None
                or entry.topology.radius_count == topology.radius_count
            )
        )

    def prefilter_points(
        self,
        points: Sequence[Point2D],
        *,
        closed: bool | None = None,
        segment_families: Sequence[str] | None = None,
        radius_pattern: Sequence[bool] | None = None,
        endpoint_tolerance: float = 1e-6,
        collinear_tolerance_deg: float = 0.0,
    ) -> tuple[ShapeDefinition, ...]:
        """Convenience API for the matcher hot path."""

        observed = _descriptor_from_points(
            points,
            closed=closed,
            segment_families=segment_families,
            radius_pattern=radius_pattern,
            endpoint_tolerance=endpoint_tolerance,
            collinear_tolerance_deg=collinear_tolerance_deg,
        )
        if observed is None:
            return ()
        descriptor, topology = observed
        del descriptor
        return tuple(entry.shape for entry in self.prefilter(topology))

    def query_points(
        self,
        points: Sequence[Point2D],
        *,
        closed: bool | None = None,
        segment_families: Sequence[str] | None = None,
        radius_pattern: Sequence[bool] | None = None,
        endpoint_tolerance: float = 1e-6,
        collinear_tolerance_deg: float = 0.0,
        angle_tolerance_deg: float = 0.0,
        allow_unpermitted_mirror: bool = False,
    ) -> ShapeIndexQuery | None:
        """Return topology and canonical-angle matches for an observed chain.

        The result never declares an engineering approval.  It is a candidate
        shortlist for the profile/evidence matcher.  Setting
        ``allow_unpermitted_mirror`` keeps a mirrored candidate discoverable
        even when the shape forbids automatic mirroring; the detailed matcher
        must then mark it for review rather than silently approve it.
        """

        observed = _descriptor_from_points(
            points,
            closed=closed,
            segment_families=segment_families,
            radius_pattern=radius_pattern,
            endpoint_tolerance=endpoint_tolerance,
            collinear_tolerance_deg=collinear_tolerance_deg,
        )
        if observed is None:
            return None
        descriptor, topology = observed
        prefiltered = self.prefilter(topology)
        tolerance = max(float(angle_tolerance_deg), 0.0)
        candidate_signatures = {
            False: _canonical_signature(
                descriptor,
                closed=topology.closed,
                allow_mirror=False,
                angle_precision_deg=self.angle_precision_deg,
            ),
            True: _canonical_signature(
                descriptor,
                closed=topology.closed,
                allow_mirror=True,
                angle_precision_deg=self.angle_precision_deg,
            ),
        }
        matches: list[ShapeIndexMatch] = []
        for entry in prefiltered:
            mirror_invariant = bool(
                entry.mirror_allowed or allow_unpermitted_mirror
            )
            candidate_signature = candidate_signatures[mirror_invariant]
            expected_signature = (
                entry.discovery_signature
                if mirror_invariant
                else entry.canonical_signature
            )
            signature_exact = (
                candidate_signature.value == expected_signature.value
            )
            angle_error = _best_angle_error(
                descriptor,
                _descriptor_from_entry(entry),
                closed=topology.closed,
                allow_mirror=mirror_invariant,
                compare_segment_families=segment_families is not None,
                compare_radius_pattern=radius_pattern is not None,
            )
            semantic_angle_exact = (
                math.isfinite(angle_error)
                and angle_error <= 1e-9
                and _descriptor_has_parametric_turns(
                    _descriptor_from_entry(entry)
                )
            )
            exact = signature_exact or semantic_angle_exact
            if exact or angle_error <= tolerance + 1e-9:
                matches.append(
                    ShapeIndexMatch(
                        entry=entry,
                        max_angle_error_deg=angle_error,
                        exact_signature=exact,
                    )
                )
        matches.sort(
            key=lambda item: (
                item.max_angle_error_deg,
                item.entry.shape.code.strip().upper(),
            )
        )
        return ShapeIndexQuery(
            topology=topology,
            prefiltered=prefiltered,
            matches=tuple(matches),
        )


def canonical_signature_from_shape(
    shape: ShapeDefinition,
    *,
    angle_precision_deg: float = 0.1,
) -> CanonicalShapeSignature | None:
    """Public helper used by Engine validation, reports and regression tests."""

    entry = _entry_from_shape(shape, float(angle_precision_deg))
    return entry.canonical_signature if entry is not None else None


def canonical_semantic_signature_from_shape(
    shape: ShapeDefinition,
    *,
    angle_precision_deg: float = 0.1,
) -> CanonicalSemanticSignature | None:
    """Return the collision-safe ENGINE identity for one shape.

    Unlike :func:`canonical_signature_from_shape`, this includes repeated
    parameter codes, explicit equal/ratio relations, symmetry constraints and
    parametric angle relations.  It remains invariant under the traversal and
    mirror operations permitted by the shape.
    """

    entry = _entry_from_shape(shape, float(angle_precision_deg))
    return entry.semantic_signature if entry is not None else None


def shape_semantic_domains_overlap(
    left: ShapeDefinition,
    right: ShapeDefinition,
    *,
    angle_precision_deg: float = 0.1,
) -> bool:
    """Return whether two shape definitions admit a common geometry.

    This is stricter than a topology collision and broader than canonical
    semantic equality.  It aligns every reverse/mirror/closed-start traversal
    with the same topology/angle signature, then solves the combined required
    equality, ratio and fixed-length-ratio constraints.  An unconstrained
    domain therefore overlaps a constrained subtype, while contradictory ratio
    systems do not.
    """

    if bool(left.closed) != bool(right.closed):
        return False
    left_descriptor = _descriptor_from_shape(left)
    right_descriptor = _descriptor_from_shape(right)
    if left_descriptor is None or right_descriptor is None:
        return False
    if len(left_descriptor.segment_families) != len(
        right_descriptor.segment_families
    ):
        return False

    left_variants = _descriptor_variants(
        left_descriptor,
        closed=bool(left.closed),
        allow_mirror=True,
    )
    right_variants = _descriptor_variants(
        right_descriptor,
        closed=bool(right.closed),
        allow_mirror=True,
    )
    right_by_topology: dict[str, list[_Descriptor]] = defaultdict(list)
    for descriptor in right_variants:
        right_by_topology[
            _signature_value(
                descriptor,
                bool(right.closed),
                float(angle_precision_deg),
            )
        ].append(descriptor)
    for left_variant in left_variants:
        topology = _signature_value(
            left_variant,
            bool(left.closed),
            float(angle_precision_deg),
        )
        for right_variant in right_by_topology.get(topology, ()):
            if _combined_length_relations_are_consistent(
                left_variant,
                right_variant,
            ):
                return True
    return False


def turn_constraints_from_shape(
    shape: ShapeDefinition,
) -> tuple[TurnConstraint, ...]:
    """Return fixed/parametric turn rules in the shape's native traversal."""

    descriptor = _descriptor_from_shape(shape)
    return descriptor.turn_constraints if descriptor is not None else ()


def transform_turn_constraints(
    constraints: Sequence[TurnConstraint],
    *,
    reversed_direction: bool = False,
    mirrored: bool = False,
    closed: bool = False,
) -> tuple[TurnConstraint, ...]:
    """Align ENGINE constraints with one matcher traversal variant."""

    result = tuple(constraints)
    if mirrored:
        result = tuple(_mirror_turn_constraint(item) for item in result)
    if reversed_direction:
        result = _reverse_turn_constraints(result, closed=closed)
    return result


def turn_sequence_error(
    observed_turns: Sequence[float],
    constraints: Sequence[TurnConstraint],
) -> float:
    """Public low-level comparator for an already aligned matcher variant."""

    return _turn_sequence_error(observed_turns, constraints)


def shape_turn_error(
    shape: ShapeDefinition,
    observed_turns: Sequence[float],
    *,
    allow_reverse: bool = True,
    allow_mirror: bool = True,
) -> float:
    """Compare an observed turn sequence with ENGINE angle semantics.

    ``ПРОЕКЦИИ_XY`` turns are solved as shared variables instead of being
    compared with their legacy compass-heading preview.  The returned value is
    the maximum residual in degrees; ``math.inf`` means incompatible topology
    or an out-of-range parametric angle.
    """

    expected = _descriptor_from_shape(shape)
    if expected is None or len(observed_turns) != len(expected.turns_deg):
        return float("inf")
    observed = _Descriptor(
        turns_deg=tuple(_normalize_angle(float(value)) for value in observed_turns),
        segment_families=expected.segment_families,
        radius_pattern=expected.radius_pattern,
        parameter_pattern=("",) * len(expected.segment_families),
        fixed_lengths_cm=(None,) * len(expected.segment_families),
        turn_constraints=tuple(
            _fixed_turn_constraint(float(value))
            for value in observed_turns
        ),
        ratio_relations=(),
    )
    observed_variants = _descriptor_variants(
        observed,
        closed=bool(shape.closed),
        allow_mirror=allow_mirror,
        allow_reverse=allow_reverse,
    )
    expected_variants = _descriptor_variants(
        expected,
        closed=bool(shape.closed),
        allow_mirror=False,
        allow_reverse=allow_reverse,
    )
    return min(
        (
            _turn_sequence_error(left.turns_deg, right.turn_constraints)
            for left in observed_variants
            for right in expected_variants
        ),
        default=float("inf"),
    )


def topology_from_shape(shape: ShapeDefinition) -> ShapeTopology | None:
    descriptor = _descriptor_from_shape(shape)
    if descriptor is None:
        return None
    return ShapeTopology(
        segment_count=len(descriptor.segment_families),
        turn_count=len(descriptor.turns_deg),
        closed=bool(shape.closed),
        arc_count=sum(value == "ARC" for value in descriptor.segment_families),
        radius_count=sum(descriptor.radius_pattern),
    )


def _entry_from_shape(
    shape: ShapeDefinition,
    angle_precision_deg: float,
    *,
    canonical_code: str | None = None,
    identity_key: str | None = None,
) -> IndexedShape | None:
    descriptor = _descriptor_from_shape(shape)
    if descriptor is None:
        return None
    topology = ShapeTopology(
        segment_count=len(descriptor.segment_families),
        turn_count=len(descriptor.turns_deg),
        closed=bool(shape.closed),
        arc_count=sum(value == "ARC" for value in descriptor.segment_families),
        radius_count=sum(descriptor.radius_pattern),
    )
    mirror_allowed = bool(shape.mirror_allowed or shape.symmetric)
    return IndexedShape(
        shape=shape,
        topology=topology,
        canonical_signature=_canonical_signature(
            descriptor,
            closed=bool(shape.closed),
            allow_mirror=mirror_allowed,
            angle_precision_deg=angle_precision_deg,
        ),
        discovery_signature=_canonical_signature(
            descriptor,
            closed=bool(shape.closed),
            allow_mirror=True,
            angle_precision_deg=angle_precision_deg,
        ),
        traversal_signature=_canonical_signature(
            descriptor,
            closed=bool(shape.closed),
            allow_mirror=False,
            angle_precision_deg=angle_precision_deg,
        ),
        semantic_signature=_canonical_semantic_signature(
            descriptor,
            closed=bool(shape.closed),
            allow_mirror=mirror_allowed,
            angle_precision_deg=angle_precision_deg,
        ),
        parameter_pattern=descriptor.parameter_pattern,
        fixed_lengths_cm=descriptor.fixed_lengths_cm,
        turn_constraints=descriptor.turn_constraints,
        canonical_code=(
            str(canonical_code).strip().upper()
            if canonical_code
            else shape.code.strip().upper()
        ),
        identity_key=(
            str(identity_key)
            if identity_key
            else (
                f"FAMILY:{shape.family_id.strip().upper()}"
                if shape.family_id.strip()
                else f"SHAPE:{shape.code.strip().upper()}"
            )
        ),
    )


def _is_indexable_shape(shape: ShapeDefinition) -> bool:
    return bool(
        shape.active
        and str(shape.state).strip().upper() in APPROVED_SHAPE_STATES
        and shape.code.strip()
        and shape.segments
    )


def _shape_alias_root(
    code: str,
    shape_by_code: dict[str, ShapeDefinition],
) -> str | None:
    current = str(code).strip().upper()
    visited: set[str] = set()
    while current:
        if current in visited:
            return None
        visited.add(current)
        shape = shape_by_code.get(current)
        if shape is None:
            return None
        target = str(shape.alias_of or "").strip().upper()
        if not target:
            return current
        current = target
    return None


def _descriptor_from_shape(shape: ShapeDefinition) -> _Descriptor | None:
    segments = sorted(shape.segments, key=lambda item: item.index)
    if not segments:
        return None

    headings: list[float] = []
    heading = _heading_for_direction(shape.initial_direction)
    if heading is None:
        heading = 0.0
    previous: SegmentDefinition | None = None
    for segment in segments:
        if previous is None or previous.bend_after_deg is None:
            absolute = _heading_for_direction(segment.drawing_direction)
            if absolute is not None:
                heading = absolute
        headings.append(_normalize_angle(heading))
        if segment.bend_after_deg is not None:
            sign = (
                1.0
                if str(segment.bend_direction or "RIGHT").strip().upper()
                in _LEFT_DIRECTIONS
                else -1.0
            )
            heading += sign * float(segment.bend_after_deg)
        previous = segment

    turns = tuple(
        _normalize_angle(right - left)
        for left, right in zip(headings, headings[1:])
    )
    if shape.closed and headings:
        turns += (_normalize_angle(headings[0] - headings[-1]),)

    families = tuple(_segment_family(segment.segment_type) for segment in segments)
    radius_pattern = tuple(
        bool(
            segment.radius_cm is not None
            and math.isfinite(float(segment.radius_cm))
            and float(segment.radius_cm) > 0
        )
        for segment in segments
    )
    parameter_pattern, ratio_relations = _parameter_semantics(shape, segments)
    fixed_lengths = tuple(
        (
            float(segment.fixed_length_cm)
            if segment.fixed_length_cm is not None
            and math.isfinite(float(segment.fixed_length_cm))
            else None
        )
        for segment in segments
    )
    turn_constraints = _turn_constraints_for_segments(
        segments,
        turns,
        closed=bool(shape.closed),
    )
    return _Descriptor(
        turns_deg=turns,
        segment_families=families,
        radius_pattern=radius_pattern,
        parameter_pattern=parameter_pattern,
        fixed_lengths_cm=fixed_lengths,
        turn_constraints=turn_constraints,
        ratio_relations=ratio_relations,
    )


def _descriptor_from_points(
    points: Sequence[Point2D],
    *,
    closed: bool | None,
    segment_families: Sequence[str] | None,
    radius_pattern: Sequence[bool] | None,
    endpoint_tolerance: float,
    collinear_tolerance_deg: float,
) -> tuple[_Descriptor, ShapeTopology] | None:
    clean = _clean_points(
        points,
        endpoint_tolerance=max(float(endpoint_tolerance), 0.0),
        collinear_tolerance_deg=max(float(collinear_tolerance_deg), 0.0),
    )
    if len(clean) < 2:
        return None
    inferred_closed = _points_close(
        clean[0],
        clean[-1],
        max(float(endpoint_tolerance), 1e-9),
    )
    resolved_closed = inferred_closed if closed is None else bool(closed)
    vertices = list(clean)
    if resolved_closed and _points_close(
        vertices[0],
        vertices[-1],
        max(float(endpoint_tolerance), 1e-9),
    ):
        vertices.pop()
    if len(vertices) < (3 if resolved_closed else 2):
        return None

    pairs = list(zip(vertices, vertices[1:]))
    if resolved_closed:
        pairs.append((vertices[-1], vertices[0]))
    vectors: list[Point2D] = []
    for left, right in pairs:
        vector = (right[0] - left[0], right[1] - left[1])
        if math.hypot(*vector) <= 1e-9:
            continue
        vectors.append(vector)
    if not vectors:
        return None

    turns = tuple(
        _vector_turn(left, right)
        for left, right in zip(vectors, vectors[1:])
    )
    if resolved_closed:
        turns += (_vector_turn(vectors[-1], vectors[0]),)
    segment_count = len(vectors)

    if segment_families is None:
        families = ("LINE",) * segment_count
        known_arc_count: int | None = None
    else:
        if len(segment_families) != segment_count:
            raise ValueError(
                "segment_families length must equal the observed segment count"
            )
        families = tuple(_segment_family(value) for value in segment_families)
        known_arc_count = sum(value == "ARC" for value in families)

    if radius_pattern is None:
        radii = (False,) * segment_count
        known_radius_count: int | None = None
    else:
        if len(radius_pattern) != segment_count:
            raise ValueError(
                "radius_pattern length must equal the observed segment count"
            )
        radii = tuple(bool(value) for value in radius_pattern)
        known_radius_count = sum(radii)

    descriptor = _Descriptor(
        turns_deg=turns,
        segment_families=families,
        radius_pattern=radii,
        parameter_pattern=("",) * segment_count,
        fixed_lengths_cm=(None,) * segment_count,
        turn_constraints=tuple(
            _fixed_turn_constraint(value)
            for value in turns
        ),
        ratio_relations=(),
    )
    topology = ShapeTopology(
        segment_count=segment_count,
        turn_count=len(turns),
        closed=resolved_closed,
        arc_count=known_arc_count,
        radius_count=known_radius_count,
    )
    return descriptor, topology


def _descriptor_from_entry(entry: IndexedShape) -> _Descriptor:
    source = _descriptor_from_shape(entry.shape)
    if source is None:  # Defensive: an index entry can only be built from one.
        raise ValueError(f"Invalid indexed shape: {entry.shape.code}")
    return source


def _canonical_signature(
    descriptor: _Descriptor,
    *,
    closed: bool,
    allow_mirror: bool,
    angle_precision_deg: float,
) -> CanonicalShapeSignature:
    variants = _descriptor_variants(
        descriptor,
        closed=closed,
        allow_mirror=allow_mirror,
    )
    canonical = min(
        variants,
        key=lambda item: _signature_value(item, closed, angle_precision_deg),
    )
    return CanonicalShapeSignature(
        value=_signature_value(canonical, closed, angle_precision_deg),
        turns_deg=tuple(
            _quantize_angle(value, angle_precision_deg)
            for value in canonical.turns_deg
        ),
        segment_families=canonical.segment_families,
        radius_pattern=canonical.radius_pattern,
        mirror_invariant=allow_mirror,
    )


def _canonical_semantic_signature(
    descriptor: _Descriptor,
    *,
    closed: bool,
    allow_mirror: bool,
    angle_precision_deg: float,
) -> CanonicalSemanticSignature:
    variants = _descriptor_variants(
        descriptor,
        closed=closed,
        allow_mirror=allow_mirror,
    )
    canonical = min(
        variants,
        key=lambda item: _semantic_signature_value(
            item,
            closed,
            angle_precision_deg,
        ),
    )
    topology_value = _signature_value(
        canonical,
        closed,
        angle_precision_deg,
    )
    parameter_pattern = _normalized_parameter_pattern(
        canonical.parameter_pattern
    )
    turn_pattern = _semantic_turn_pattern(
        canonical.turn_constraints,
        angle_precision_deg,
    )
    relation_pattern = _ratio_relation_pattern(canonical.ratio_relations)
    value = (
        f"{_semantic_topology_value(canonical, closed, angle_precision_deg)}"
        f"|p={','.join(parameter_pattern)}"
        f"|rel={','.join(relation_pattern)}"
    )
    return CanonicalSemanticSignature(
        value=value,
        topology_value=topology_value,
        parameter_pattern=parameter_pattern,
        turn_pattern=turn_pattern,
        relation_pattern=relation_pattern,
        mirror_invariant=allow_mirror,
    )


def _descriptor_variants(
    descriptor: _Descriptor,
    *,
    closed: bool,
    allow_mirror: bool,
    allow_reverse: bool = True,
) -> tuple[_Descriptor, ...]:
    bases = [descriptor]
    if allow_reverse:
        bases.append(_reverse_descriptor(descriptor, closed=closed))
    if allow_mirror:
        mirrored = _mirror_descriptor(descriptor)
        bases.append(mirrored)
        if allow_reverse:
            bases.append(_reverse_descriptor(mirrored, closed=closed))

    output: list[_Descriptor] = []
    seen: set[tuple] = set()
    for base in bases:
        shifts = range(len(base.segment_families)) if closed else range(1)
        for shift in shifts:
            variant = _rotate_descriptor(base, shift) if shift else base
            key = (
                variant.turns_deg,
                variant.segment_families,
                variant.radius_pattern,
                variant.parameter_pattern,
                variant.fixed_lengths_cm,
                variant.turn_constraints,
                variant.ratio_relations,
            )
            if key in seen:
                continue
            seen.add(key)
            output.append(variant)
    return tuple(output)


def _reverse_descriptor(descriptor: _Descriptor, *, closed: bool) -> _Descriptor:
    turns = tuple(-value for value in reversed(descriptor.turns_deg))
    if closed and turns:
        turns = _rotate(turns, 1)
    return _Descriptor(
        turns_deg=turns,
        segment_families=tuple(reversed(descriptor.segment_families)),
        radius_pattern=tuple(reversed(descriptor.radius_pattern)),
        parameter_pattern=tuple(reversed(descriptor.parameter_pattern)),
        fixed_lengths_cm=tuple(reversed(descriptor.fixed_lengths_cm)),
        turn_constraints=_reverse_turn_constraints(
            descriptor.turn_constraints,
            closed=closed,
        ),
        ratio_relations=_remap_ratio_relations(
            descriptor.ratio_relations,
            segment_count=len(descriptor.segment_families),
            mapping=lambda index: len(descriptor.segment_families) - 1 - index,
        ),
    )


def _mirror_descriptor(descriptor: _Descriptor) -> _Descriptor:
    return _Descriptor(
        turns_deg=tuple(-value for value in descriptor.turns_deg),
        segment_families=descriptor.segment_families,
        radius_pattern=descriptor.radius_pattern,
        parameter_pattern=descriptor.parameter_pattern,
        fixed_lengths_cm=descriptor.fixed_lengths_cm,
        turn_constraints=tuple(
            _mirror_turn_constraint(item)
            for item in descriptor.turn_constraints
        ),
        ratio_relations=descriptor.ratio_relations,
    )


def _rotate_descriptor(descriptor: _Descriptor, shift: int) -> _Descriptor:
    return _Descriptor(
        turns_deg=_rotate(descriptor.turns_deg, shift),
        segment_families=_rotate(descriptor.segment_families, shift),
        radius_pattern=_rotate(descriptor.radius_pattern, shift),
        parameter_pattern=_rotate(descriptor.parameter_pattern, shift),
        fixed_lengths_cm=_rotate(descriptor.fixed_lengths_cm, shift),
        turn_constraints=_rotate(descriptor.turn_constraints, shift),
        ratio_relations=_remap_ratio_relations(
            descriptor.ratio_relations,
            segment_count=len(descriptor.segment_families),
            mapping=lambda index: (
                index - shift
            ) % len(descriptor.segment_families),
        ),
    )


def _best_angle_error(
    observed: _Descriptor,
    expected: _Descriptor,
    *,
    closed: bool,
    allow_mirror: bool,
    compare_segment_families: bool,
    compare_radius_pattern: bool,
) -> float:
    observed_variants = _descriptor_variants(
        observed,
        closed=closed,
        allow_mirror=allow_mirror,
    )
    expected_variants = _descriptor_variants(
        expected,
        closed=closed,
        allow_mirror=False,
    )
    errors: list[float] = []
    for left in observed_variants:
        for right in expected_variants:
            if (
                compare_segment_families
                and left.segment_families != right.segment_families
            ):
                continue
            if (
                compare_radius_pattern
                and left.radius_pattern != right.radius_pattern
            ):
                continue
            if len(left.turns_deg) != len(right.turns_deg):
                continue
            errors.append(
                _turn_sequence_error(
                    left.turns_deg,
                    right.turn_constraints,
                )
            )
    return min(errors, default=float("inf"))


def _signature_value(
    descriptor: _Descriptor,
    closed: bool,
    angle_precision_deg: float,
) -> str:
    turns = ",".join(
        _format_angle(_quantize_angle(value, angle_precision_deg))
        for value in descriptor.turns_deg
    )
    segments = ",".join(
        f"{family}:{'R' if radius else '-'}"
        for family, radius in zip(
            descriptor.segment_families,
            descriptor.radius_pattern,
        )
    )
    return (
        f"{'C' if closed else 'O'}"
        f"|n={len(descriptor.segment_families)}"
        f"|s={segments}"
        f"|a={turns}"
    )


def _parameter_semantics(
    shape: ShapeDefinition,
    segments: Sequence[SegmentDefinition],
) -> tuple[tuple[str, ...], tuple[tuple[int, int, float], ...]]:
    """Compile legacy and v2 parameter rules into traversal-safe metadata."""

    count = len(segments)
    parents = list(range(count))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    by_length_code: dict[str, int] = {}
    for position, segment in enumerate(segments):
        code = str(segment.length_code or "").strip().upper()
        if not code:
            continue
        previous = by_length_code.get(code)
        if previous is None:
            by_length_code[code] = position
        else:
            union(previous, position)

    # Repeated legacy length codes are the more precise symmetry mapping.
    # Closed forms can be symmetric around an axis without the naive
    # first↔last traversal pairs being equal (for example
    # L1,L2,L3,L2,L3,L1).  Fall back to opposite traversal pairs only when the
    # ENGINE has not already expressed any repeated parameter relation.
    has_repeated_length_codes = len(by_length_code) < sum(
        1
        for segment in segments
        if str(segment.length_code or "").strip()
    )
    if shape.symmetric and not has_repeated_length_codes:
        for position in range(count // 2):
            union(position, count - 1 - position)

    position_by_index = {
        int(segment.index): position
        for position, segment in enumerate(segments)
    }
    ratio_relations: list[tuple[int, int, float]] = []
    for relation in shape.parameter_relations:
        if not relation.required:
            continue
        left = position_by_index.get(int(relation.left_segment))
        right = position_by_index.get(int(relation.right_segment))
        if left is None or right is None or left == right:
            continue
        kind = str(relation.kind or "EQUAL").strip().upper()
        ratio = float(relation.ratio)
        if kind in {"EQUAL", "EQ", "="} or (
            kind == "RATIO" and abs(ratio - 1.0) <= 1e-12
        ):
            union(left, right)
        elif kind == "RATIO" and math.isfinite(ratio) and ratio > 0:
            ratio_relations.append((left, right, ratio))

    fixed_by_root: dict[int, list[float]] = defaultdict(list)
    for position, segment in enumerate(segments):
        if segment.fixed_length_cm is not None:
            fixed_by_root[find(position)].append(float(segment.fixed_length_cm))

    aliases: dict[int, int] = {}
    output: list[str] = []
    for position, segment in enumerate(segments):
        root = find(position)
        fixed = fixed_by_root.get(root, [])
        if fixed:
            output.append(f"FIXED:{fixed[0]:.6g}")
            continue
        if root not in aliases:
            aliases[root] = len(aliases) + 1
        output.append(f"PARAM:{aliases[root]}")

    normalized_ratios = _remap_ratio_relations(
        tuple(ratio_relations),
        segment_count=count,
        mapping=lambda index: index,
    )
    return tuple(output), normalized_ratios


def _normalized_parameter_pattern(
    pattern: Sequence[str],
) -> tuple[str, ...]:
    aliases: dict[str, int] = {}
    output: list[str] = []
    for raw in pattern:
        token = str(raw)
        if token.startswith("PARAM:") and token != "PARAM:?":
            key = token.partition(":")[2]
            if key not in aliases:
                aliases[key] = len(aliases) + 1
            output.append(f"PARAM:{aliases[key]}")
        else:
            output.append(token)
    return tuple(output)


def _turn_constraints_for_segments(
    segments: Sequence[SegmentDefinition],
    turns: Sequence[float],
    *,
    closed: bool,
) -> tuple[TurnConstraint, ...]:
    if not turns:
        return ()

    parametric_segments = {
        position: _angle_parameter_for_segment(segment)
        for position, segment in enumerate(segments)
        if _segment_has_parametric_angle(segment)
    }
    raw_groups: list[tuple[str, float | None, float | None] | None] = []
    for turn_index in range(len(turns)):
        left = turn_index
        right = (turn_index + 1) % len(segments)
        adjacent = [
            value
            for position in (left, right)
            if (value := parametric_segments.get(position)) is not None
        ]
        if not adjacent:
            raw_groups.append(None)
            continue
        codes = sorted({item[0] for item in adjacent})
        minimums = [item[1] for item in adjacent if item[1] is not None]
        maximums = [item[2] for item in adjacent if item[2] is not None]
        raw_groups.append(
            (
                "&".join(codes),
                max(minimums) if minimums else None,
                min(maximums) if maximums else None,
            )
        )

    base_by_group: dict[str, float] = {}
    for turn, raw in zip(turns, raw_groups):
        if raw is None or abs(float(turn)) <= 1e-9:
            continue
        base_by_group.setdefault(raw[0], abs(float(turn)))

    output: list[TurnConstraint] = []
    for turn, raw in zip(turns, raw_groups):
        if raw is None:
            output.append(_fixed_turn_constraint(turn))
            continue
        group, minimum, maximum = raw
        base = base_by_group.get(group)
        if base is None or base <= 1e-9:
            output.append(_fixed_turn_constraint(turn))
            continue
        output.append(
            TurnConstraint(
                mode="PARAMETRIC",
                reference_angle_deg=_normalize_angle(float(turn)),
                parameter_code=group,
                coefficient=float(turn) / base,
                min_angle_deg=minimum,
                max_angle_deg=maximum,
            )
        )
    return tuple(output)


def _segment_has_parametric_angle(segment: SegmentDefinition) -> bool:
    mode = (
        str(segment.angle_mode or "AUTO")
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if mode in {"FIXED", "ФИКСИРАН", "ФИКСИРАН_ЪГЪЛ"}:
        return False
    if mode in {
        "PARAMETRIC",
        "PARAMETRIC_ANGLE",
        "PARAMETRIC_DIRECTION",
        "ПАРАМЕТРИЧЕН",
        "ПАРАМЕТРИЧЕН_ЪГЪЛ",
    }:
        return True
    method = str(segment.geometry_method or "").strip().upper()
    return bool(
        "ПРОЕКЦ" in method
        or "PROJECTION" in method
        or (
            str(segment.projection_x_code or "").strip()
            and str(segment.projection_y_code or "").strip()
        )
    )


def _angle_parameter_for_segment(
    segment: SegmentDefinition,
) -> tuple[str, float | None, float | None]:
    explicit = str(segment.angle_parameter_code or "").strip().upper()
    x_code = str(segment.projection_x_code or "").strip().upper()
    y_code = str(segment.projection_y_code or "").strip().upper()
    code = explicit or (
        f"ATAN2({y_code},{x_code})"
        if x_code and y_code
        else f"SEGMENT:{segment.index}"
    )
    return code, segment.min_angle_deg, segment.max_angle_deg


def _fixed_turn_constraint(value: float) -> TurnConstraint:
    return TurnConstraint(
        mode="FIXED",
        reference_angle_deg=_normalize_angle(float(value)),
    )


def _reverse_turn_constraints(
    constraints: Sequence[TurnConstraint],
    *,
    closed: bool,
) -> tuple[TurnConstraint, ...]:
    output = tuple(
        _negate_turn_constraint(item)
        for item in reversed(constraints)
    )
    if closed and output:
        output = _rotate(output, 1)
    return output


def _mirror_turn_constraint(constraint: TurnConstraint) -> TurnConstraint:
    return _negate_turn_constraint(constraint)


def _negate_turn_constraint(constraint: TurnConstraint) -> TurnConstraint:
    return TurnConstraint(
        mode=constraint.mode,
        reference_angle_deg=_normalize_angle(-constraint.reference_angle_deg),
        parameter_code=constraint.parameter_code,
        coefficient=-constraint.coefficient,
        min_angle_deg=constraint.min_angle_deg,
        max_angle_deg=constraint.max_angle_deg,
    )


def _turn_sequence_error(
    observed_turns: Sequence[float],
    expected: Sequence[TurnConstraint],
) -> float:
    if len(observed_turns) != len(expected):
        return float("inf")

    errors: list[float] = []
    grouped: dict[str, list[tuple[float, TurnConstraint]]] = defaultdict(list)
    for observed, constraint in zip(observed_turns, expected):
        if constraint.parametric:
            grouped[constraint.parameter_code].append((float(observed), constraint))
        else:
            errors.append(
                _angle_distance(observed, constraint.reference_angle_deg)
            )

    for rows in grouped.values():
        estimates = [
            observed / constraint.coefficient
            for observed, constraint in rows
            if abs(constraint.coefficient) > 1e-12
        ]
        if len(estimates) != len(rows) or not estimates:
            return float("inf")
        parameter = sum(estimates) / len(estimates)
        if not math.isfinite(parameter) or parameter <= 1e-9:
            return float("inf")
        minimum = max(
            (
                float(constraint.min_angle_deg)
                for _, constraint in rows
                if constraint.min_angle_deg is not None
            ),
            default=None,
        )
        maximum = min(
            (
                float(constraint.max_angle_deg)
                for _, constraint in rows
                if constraint.max_angle_deg is not None
            ),
            default=None,
        )
        if minimum is not None and parameter < minimum - 1e-9:
            return float("inf")
        if maximum is not None and parameter > maximum + 1e-9:
            return float("inf")
        errors.extend(
            _angle_distance(
                observed,
                constraint.coefficient * parameter,
            )
            for observed, constraint in rows
        )
    return max(errors, default=0.0)


def _descriptor_has_parametric_turns(descriptor: _Descriptor) -> bool:
    return any(item.parametric for item in descriptor.turn_constraints)


def _semantic_signature_value(
    descriptor: _Descriptor,
    closed: bool,
    angle_precision_deg: float,
) -> str:
    return (
        f"{_semantic_topology_value(descriptor, closed, angle_precision_deg)}"
        f"|p={','.join(_normalized_parameter_pattern(descriptor.parameter_pattern))}"
        f"|rel={','.join(_ratio_relation_pattern(descriptor.ratio_relations))}"
    )


def _semantic_topology_value(
    descriptor: _Descriptor,
    closed: bool,
    angle_precision_deg: float,
) -> str:
    segments = ",".join(
        f"{family}:{'R' if radius else '-'}"
        for family, radius in zip(
            descriptor.segment_families,
            descriptor.radius_pattern,
        )
    )
    turns = ",".join(
        _semantic_turn_pattern(
            descriptor.turn_constraints,
            angle_precision_deg,
        )
    )
    return (
        f"{'C' if closed else 'O'}"
        f"|n={len(descriptor.segment_families)}"
        f"|s={segments}"
        f"|a={turns}"
    )


def _semantic_turn_pattern(
    constraints: Sequence[TurnConstraint],
    angle_precision_deg: float,
) -> tuple[str, ...]:
    aliases: dict[str, int] = {}
    output: list[str] = []
    for constraint in constraints:
        if not constraint.parametric:
            value = _quantize_angle(
                constraint.reference_angle_deg,
                angle_precision_deg,
            )
            output.append(_format_angle(value))
            continue
        if constraint.parameter_code not in aliases:
            aliases[constraint.parameter_code] = len(aliases) + 1
        alias = aliases[constraint.parameter_code]
        coefficient = _format_angle(
            round(constraint.coefficient, 6)
        )
        minimum = (
            "*"
            if constraint.min_angle_deg is None
            else _format_angle(constraint.min_angle_deg)
        )
        maximum = (
            "*"
            if constraint.max_angle_deg is None
            else _format_angle(constraint.max_angle_deg)
        )
        output.append(f"@A{alias}*{coefficient}[{minimum}:{maximum}]")
    return tuple(output)


def _remap_ratio_relations(
    relations: Sequence[tuple[int, int, float]],
    *,
    segment_count: int,
    mapping,
) -> tuple[tuple[int, int, float], ...]:
    output: list[tuple[int, int, float]] = []
    for left, right, ratio in relations:
        mapped_left = int(mapping(int(left)))
        mapped_right = int(mapping(int(right)))
        if not (
            0 <= mapped_left < segment_count
            and 0 <= mapped_right < segment_count
            and mapped_left != mapped_right
            and math.isfinite(float(ratio))
            and float(ratio) > 0
        ):
            continue
        normalized_ratio = float(ratio)
        if mapped_left > mapped_right:
            mapped_left, mapped_right = mapped_right, mapped_left
            normalized_ratio = 1.0 / normalized_ratio
        output.append((mapped_left, mapped_right, normalized_ratio))
    return tuple(
        sorted(
            {
                (left, right, round(ratio, 9))
                for left, right, ratio in output
            }
        )
    )


def _ratio_relation_pattern(
    relations: Sequence[tuple[int, int, float]],
) -> tuple[str, ...]:
    return tuple(
        f"S{left + 1}={ratio:.9g}*S{right + 1}"
        for left, right, ratio in relations
    )


def _combined_length_relations_are_consistent(
    *descriptors: _Descriptor,
) -> bool:
    if not descriptors:
        return False
    count = len(descriptors[0].segment_families)
    if any(len(item.segment_families) != count for item in descriptors):
        return False

    parents = list(range(count))
    weights = [1.0] * count

    def find(index: int) -> tuple[int, float]:
        parent = parents[index]
        if parent == index:
            return index, 1.0
        root, parent_weight = find(parent)
        weights[index] *= parent_weight
        parents[index] = root
        return root, weights[index]

    def constrain(left: int, right: int, ratio: float) -> bool:
        if (
            not (0 <= left < count and 0 <= right < count)
            or left == right
            or not math.isfinite(float(ratio))
            or float(ratio) <= 0.0
        ):
            return False
        left_root, left_weight = find(left)
        right_root, right_weight = find(right)
        resolved_ratio = float(ratio)
        if left_root == right_root:
            return math.isclose(
                left_weight,
                resolved_ratio * right_weight,
                rel_tol=1e-6,
                abs_tol=1e-9,
            )
        parents[left_root] = right_root
        weights[left_root] = (
            resolved_ratio * right_weight / left_weight
        )
        return True

    for descriptor in descriptors:
        parameter_groups: dict[str, list[int]] = defaultdict(list)
        for index, token in enumerate(descriptor.parameter_pattern):
            normalized = str(token)
            if normalized.startswith("PARAM:") and normalized != "PARAM:?":
                parameter_groups[normalized].append(index)
        for members in parameter_groups.values():
            for right in members[1:]:
                if not constrain(members[0], right, 1.0):
                    return False

        fixed = [
            (index, float(value))
            for index, value in enumerate(descriptor.fixed_lengths_cm)
            if value is not None
            and math.isfinite(float(value))
            and float(value) > 0.0
        ]
        if fixed:
            anchor_index, anchor_value = fixed[0]
            for index, value in fixed[1:]:
                if not constrain(
                    index,
                    anchor_index,
                    value / anchor_value,
                ):
                    return False

        for left, right, ratio in descriptor.ratio_relations:
            if not constrain(left, right, ratio):
                return False
    return True


def _clean_points(
    points: Sequence[Point2D],
    *,
    endpoint_tolerance: float,
    collinear_tolerance_deg: float,
) -> tuple[Point2D, ...]:
    clean: list[Point2D] = []
    for raw in points:
        point = (float(raw[0]), float(raw[1]))
        if not all(math.isfinite(value) for value in point):
            continue
        if not clean or not _points_close(clean[-1], point, 1e-9):
            clean.append(point)
    if len(clean) < 3 or collinear_tolerance_deg <= 0:
        return tuple(clean)

    closed = _points_close(clean[0], clean[-1], max(endpoint_tolerance, 1e-9))
    work = clean[:-1] if closed else clean[:]
    changed = True
    while changed and len(work) >= (4 if closed else 3):
        changed = False
        result: list[Point2D] = []
        for index, point in enumerate(work):
            if not closed and index in {0, len(work) - 1}:
                result.append(point)
                continue
            previous = work[(index - 1) % len(work)]
            following = work[(index + 1) % len(work)]
            turn = abs(_signed_turn(previous, point, following))
            if (
                turn <= collinear_tolerance_deg
                or abs(turn - 180.0) <= collinear_tolerance_deg
            ):
                changed = True
                continue
            result.append(point)
        if len(result) < 2:
            break
        work = result
    if closed and work:
        work.append(work[0])
    return tuple(work)


def _heading_for_direction(value: str | None) -> float | None:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "RIGHT": 0.0,
        "НАДЯСНО": 0.0,
        "UP_RIGHT": 45.0,
        "НАГОРЕ_НАДЯСНО": 45.0,
        "UP": 90.0,
        "НАГОРЕ": 90.0,
        "UP_LEFT": 135.0,
        "НАГОРЕ_НАЛЯВО": 135.0,
        "LEFT": 180.0,
        "НАЛЯВО": 180.0,
        "DOWN_LEFT": -135.0,
        "НАДОЛУ_НАЛЯВО": -135.0,
        "DOWN": -90.0,
        "НАДОЛУ": -90.0,
        "DOWN_RIGHT": -45.0,
        "НАДОЛУ_НАДЯСНО": -45.0,
    }
    return mapping.get(normalized)


def _segment_family(value: str | None) -> str:
    normalized = str(value or "LINE").strip().upper()
    if "ARC" in normalized or normalized in {"ДЪГА", "RADIUS"}:
        return "ARC"
    return "LINE"


def _rotate(values: Sequence, shift: int) -> tuple:
    if not values:
        return ()
    shift %= len(values)
    return tuple(values[shift:]) + tuple(values[:shift])


def _quantize_angle(value: float, precision: float) -> float:
    quantized = round(_normalize_angle(value) / precision) * precision
    return _normalize_angle(quantized)


def _format_angle(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _normalize_angle(value: float) -> float:
    result = (float(value) + 180.0) % 360.0 - 180.0
    if abs(result + 180.0) <= 1e-9:
        return 180.0
    if abs(result) <= 1e-12:
        return 0.0
    return result


def _angle_distance(left: float, right: float) -> float:
    return abs(_normalize_angle(float(left) - float(right)))


def _vector_turn(left: Point2D, right: Point2D) -> float:
    cross = left[0] * right[1] - left[1] * right[0]
    dot = left[0] * right[0] + left[1] * right[1]
    return _normalize_angle(math.degrees(math.atan2(cross, dot)))


def _signed_turn(left: Point2D, middle: Point2D, right: Point2D) -> float:
    return _vector_turn(
        (middle[0] - left[0], middle[1] - left[1]),
        (right[0] - middle[0], right[1] - middle[1]),
    )


def _points_close(left: Point2D, right: Point2D, tolerance: float) -> bool:
    return math.hypot(left[0] - right[0], left[1] - right[1]) <= tolerance
