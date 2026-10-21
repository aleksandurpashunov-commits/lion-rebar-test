from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engine.shape_index import (
    EngineShapeIndex,
    canonical_semantic_signature_from_shape,
    shape_semantic_domains_overlap,
    transform_turn_constraints,
    turn_constraints_from_shape,
    turn_sequence_error,
)
from lion_rebar.engine.test_runner import (
    calculate_shape_segment_lengths,
    parse_dimensions,
)

Point2D = tuple[float, float]

# Locked MASTER profiles that have an approved absolute control geometry.
# F009 is not a generic five-segment topology: its approved control is
# 8 + 79 + 15 + 79 + 8 = 189 cm. Without this profile any open chain with
# five right-angle segments can be falsely labelled F009.
_FIXED_MASTER_SEGMENT_PROFILES: dict[str, tuple[float, ...]] = {
    "F009": (8.0, 79.0, 15.0, 79.0, 8.0),
}


@dataclass(frozen=True)
class ShapeFingerprint:
    segment_count: int
    closed: bool
    length_ratios: tuple[float, ...]
    turn_angles_deg: tuple[float, ...]
    canonical_signature: str
    rotation_invariant: bool = True
    reversal_invariant: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ShapeMatch:
    shape_code: str
    shape_name: str
    shape_revision: int
    mirrored: bool
    reversed_direction: bool
    max_angle_error_deg: float
    max_length_ratio_error: float | None
    score: float
    confidence: str
    reference_profile_id: str | None = None
    uniform_scale_factor: float | None = None
    mirror_requires_review: bool = False
    ambiguous_shape_codes: tuple[str, ...] = ()
    score_gap: float | None = None
    canonical_shape_code: str | None = None
    equivalent_shape_codes: tuple[str, ...] = ()
    identity_key: str | None = None
    required_relation_count: int = 0
    max_relation_error: float | None = None
    relation_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Descriptor:
    ratios: tuple[float, ...]
    turns: tuple[float, ...]
    mirrored: bool
    reversed_direction: bool


@dataclass(frozen=True)
class ShapeReferenceProfile:
    """Concrete segment proportions learned from an engine test or review."""

    profile_id: str
    segment_lengths_cm: tuple[float, ...]
    source: str

    @property
    def total_cm(self) -> float:
        return sum(self.segment_lengths_cm)


@dataclass(frozen=True)
class ShapeLengthRelationMatch:
    """Auditable result of required ENGINE length-relation matching.

    The observed values may be raw lengths or normalized ratios.  All checks
    are relative, so uniform scaling has no influence.  Reverse traversal is
    always considered; closed shapes additionally try every possible start
    segment.  A shape without required relations is compatible but contributes
    no specificity evidence.
    """

    compatible: bool
    required_rule_count: int
    max_relative_error: float | None
    max_normalized_error: float | None
    reversed_direction: bool = False
    start_shift: int = 0
    evidence: tuple[str, ...] = ()


def fingerprint_from_points(
    points: Sequence[Point2D],
    *,
    closed: bool | None = None,
    implicit_closure: bool = False,
    policy: RecognitionPolicy,
) -> ShapeFingerprint | None:
    clean = normalize_drawn_polyline(points, policy.collinear_merge_tolerance_deg)
    if len(clean) < 2:
        return None
    inferred_closed = _inferred_closed(clean, policy.endpoint_tolerance_raw)
    closed = inferred_closed if closed is None else bool(closed)
    if closed and not inferred_closed and not implicit_closure:
        clean = tuple(clean) + (clean[0],)

    descriptors = _descriptors(
        clean,
        closed=closed,
        allow_reverse=policy.allow_reversed_direction,
        allow_mirror=False,
        implicit_closure=implicit_closure,
    )
    if not descriptors:
        return None
    canonical = min(descriptors, key=_descriptor_key)
    signature = _signature(canonical, closed)
    return ShapeFingerprint(
        segment_count=len(canonical.ratios),
        closed=closed,
        length_ratios=canonical.ratios,
        turn_angles_deg=canonical.turns,
        canonical_signature=signature,
        rotation_invariant=policy.rotation_invariant,
        reversal_invariant=policy.allow_reversed_direction,
    )


def match_shape_length_relations(
    shape: ShapeDefinition,
    observed_segment_lengths: Sequence[float],
    *,
    default_relative_tolerance: float = 0.05,
    allow_reverse: bool = True,
) -> ShapeLengthRelationMatch:
    """Validate required ENGINE segment relations against observed geometry.

    Required relations come from three data-driven sources:

    * repeated non-empty ``length_code`` values;
    * opposite segment pairs on a ``symmetric`` shape;
    * required explicit ``EQUAL`` / ``RATIO`` parameter relations.

    ``RATIO`` follows the ENGINE convention ``left == ratio * right``.
    ``ParameterRelation.tolerance`` is interpreted as a relative matcher
    tolerance; absent values use ``default_relative_tolerance``.  Invalid
    required relation metadata rejects the shape defensively (publication
    validation reports the authoring error separately).
    """

    segments = sorted(shape.segments, key=lambda item: item.index)
    count = len(segments)
    try:
        observed = tuple(float(value) for value in observed_segment_lengths)
    except (TypeError, ValueError):
        observed = ()
    if (
        count == 0
        or len(observed) != count
        or any(not math.isfinite(value) or value <= 0.0 for value in observed)
    ):
        return ShapeLengthRelationMatch(
            compatible=False,
            required_rule_count=0,
            max_relative_error=None,
            max_normalized_error=None,
            evidence=("Несъвместим брой или невалидни дължини на сегментите.",),
        )

    default_tolerance = max(
        min(float(default_relative_tolerance), 1.0),
        1e-6,
    )
    rules: dict[tuple[int, int, float], tuple[float, str]] = {}
    invalid_required: list[str] = []

    def add_rule(
        left: int,
        right: int,
        ratio: float,
        *,
        tolerance: float | None,
        source: str,
    ) -> None:
        if (
            left == right
            or not (0 <= left < count and 0 <= right < count)
            or not math.isfinite(float(ratio))
            or float(ratio) <= 0.0
        ):
            invalid_required.append(source)
            return
        resolved_ratio = float(ratio)
        if left > right:
            left, right = right, left
            resolved_ratio = 1.0 / resolved_ratio
        resolved_tolerance = (
            default_tolerance
            if tolerance is None
            else max(min(float(tolerance), 1.0), 1e-6)
        )
        key = (left, right, round(resolved_ratio, 9))
        previous = rules.get(key)
        if previous is None:
            rules[key] = (resolved_tolerance, source)
        else:
            # Duplicate declarations strengthen, never relax, the same rule.
            rules[key] = (
                min(previous[0], resolved_tolerance),
                f"{previous[1]}; {source}",
            )

    by_length_code: dict[str, list[int]] = {}
    for position, segment in enumerate(segments):
        code = str(segment.length_code or "").strip().upper()
        if code:
            by_length_code.setdefault(code, []).append(position)
    for code, positions in by_length_code.items():
        for right in positions[1:]:
            add_rule(
                positions[0],
                right,
                1.0,
                tolerance=None,
                source=f"повторен length_code {code}",
            )

    # Legacy closed shapes such as F006 already encode their actual symmetry
    # mapping with repeated length codes (L1,L2,L3,L2,L3,L1).  Applying an
    # additional naive first↔last blanket would incorrectly merge L2 and L3.
    # When no repeated code exists (notably F011), ``symmetric`` supplies the
    # missing opposite-pair relations.
    has_repeated_length_codes = any(
        len(positions) > 1
        for positions in by_length_code.values()
    )
    if shape.symmetric and not has_repeated_length_codes:
        for left in range(count // 2):
            add_rule(
                left,
                count - 1 - left,
                1.0,
                tolerance=None,
                source="symmetric",
            )

    position_by_index = {
        int(segment.index): position
        for position, segment in enumerate(segments)
    }
    for relation in shape.parameter_relations:
        if not relation.required:
            continue
        left = position_by_index.get(int(relation.left_segment))
        right = position_by_index.get(int(relation.right_segment))
        kind = str(relation.kind or "EQUAL").strip().upper()
        if left is None or right is None or kind not in {"EQUAL", "EQ", "=", "RATIO"}:
            invalid_required.append(
                f"невалидна required relation {relation.left_segment}/{relation.right_segment}"
            )
            continue
        ratio = (
            1.0
            if kind in {"EQUAL", "EQ", "="}
            else float(relation.ratio)
        )
        add_rule(
            left,
            right,
            ratio,
            tolerance=relation.tolerance,
            source=(
                f"{kind} S{relation.left_segment}/S{relation.right_segment}"
            ),
        )

    if invalid_required:
        return ShapeLengthRelationMatch(
            compatible=False,
            required_rule_count=len(rules) + len(invalid_required),
            max_relative_error=None,
            max_normalized_error=None,
            evidence=tuple(invalid_required),
        )
    if not rules:
        return ShapeLengthRelationMatch(
            compatible=True,
            required_rule_count=0,
            max_relative_error=None,
            max_normalized_error=None,
        )

    variants: list[tuple[tuple[float, ...], bool, int]] = []
    bases = [(observed, False)]
    if allow_reverse:
        bases.append((tuple(reversed(observed)), True))
    seen: set[tuple[float, ...]] = set()
    for base, reversed_direction in bases:
        shifts = range(count) if shape.closed else range(1)
        for shift in shifts:
            values = _rotate(base, shift) if shift else base
            key = tuple(round(value, 12) for value in values)
            if key in seen:
                continue
            seen.add(key)
            variants.append((values, reversed_direction, shift))

    best: tuple[
        float,
        float,
        bool,
        int,
        tuple[str, ...],
        bool,
    ] | None = None
    for values, reversed_direction, shift in variants:
        relative_errors: list[float] = []
        normalized_errors: list[float] = []
        evidence: list[str] = []
        compatible = True
        for (left, right, ratio), (tolerance, source) in rules.items():
            expected_left = ratio * values[right]
            denominator = max(abs(values[left]), abs(expected_left), 1e-12)
            error = abs(values[left] - expected_left) / denominator
            normalized_error = error / tolerance
            relative_errors.append(error)
            normalized_errors.append(normalized_error)
            evidence.append(
                (
                    f"S{left + 1}={ratio:g}×S{right + 1}: "
                    f"грешка {error:.4f}, допуск {tolerance:.4f} ({source})"
                )
            )
            if normalized_error > 1.0 + 1e-9:
                compatible = False
        candidate = (
            max(normalized_errors, default=0.0),
            max(relative_errors, default=0.0),
            reversed_direction,
            shift,
            tuple(evidence),
            compatible,
        )
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    if best is None:
        return ShapeLengthRelationMatch(
            compatible=False,
            required_rule_count=len(rules),
            max_relative_error=None,
            max_normalized_error=None,
        )
    normalized_error, relative_error, reversed_direction, shift, evidence, compatible = best
    return ShapeLengthRelationMatch(
        compatible=compatible,
        required_rule_count=len(rules),
        max_relative_error=relative_error,
        max_normalized_error=normalized_error,
        reversed_direction=reversed_direction,
        start_shift=shift,
        evidence=evidence,
    )


def match_master_shapes(
    points: Sequence[Point2D],
    shapes: Iterable[ShapeDefinition] | None,
    *,
    policy: RecognitionPolicy,
    control_tests: Iterable[Mapping[str, Any]] | None = None,
    shape_index: EngineShapeIndex | None = None,
    candidate_closed_override: bool | None = None,
    implicit_closure: bool = False,
    enforce_instance_collisions: bool = True,
) -> ShapeMatch | None:
    clean = normalize_drawn_polyline(points, policy.collinear_merge_tolerance_deg)
    if len(clean) < 2:
        return None
    inferred_candidate_closed = _inferred_closed(
        clean,
        policy.endpoint_tolerance_raw,
    )
    candidate_closed = (
        inferred_candidate_closed
        if candidate_closed_override is None
        else bool(candidate_closed_override)
    )
    if implicit_closure and not candidate_closed:
        return None
    candidate_total = _polyline_length(clean)
    observed_ratios, _observed_turns = _ratios_and_turns(
        clean,
        candidate_closed,
        implicit_closure=implicit_closure,
    )
    tests = tuple(control_tests or ())
    shape_pool = tuple(shapes or ())
    if shape_index is not None and implicit_closure:
        expected_segment_count = max(0, len(clean) - 1)
        indexed_shortlist = tuple(
            entry.shape
            for entry in shape_index.entries
            if entry.topology.closed
            and entry.topology.segment_count == expected_segment_count
        )
        if shape_pool:
            allowed = {
                (shape.code.strip().upper(), int(shape.revision))
                for shape in shape_pool
            }
            shape_pool = tuple(
                shape
                for shape in indexed_shortlist
                if (shape.code.strip().upper(), int(shape.revision)) in allowed
            )
        else:
            shape_pool = indexed_shortlist
    elif shape_index is not None:
        index_query = shape_index.query_points(
            clean,
            closed=candidate_closed,
            endpoint_tolerance=policy.endpoint_tolerance_raw,
            # ``clean`` has already passed through the shared simplifier.
            collinear_tolerance_deg=0.0,
            angle_tolerance_deg=policy.angle_match_tolerance_deg,
            # Existing recognition law keeps a forbidden mirror discoverable
            # but forces it to review via ``mirror_requires_review`` below.
            allow_unpermitted_mirror=True,
        )
        indexed_shortlist = (
            index_query.shape_definitions
            if index_query is not None
            else ()
        )
        if not indexed_shortlist:
            # A few closed/schematic definitions intentionally contain
            # overlapping hook segments which the geometric simplifier merges.
            # The index is an acceleration layer, never a new hard rejection
            # law: preserve the detailed matcher's established behaviour by
            # falling back only to approved entries with equal closed/open
            # topology.
            indexed_shortlist = tuple(
                entry.shape
                for entry in shape_index.entries
                if entry.topology.closed == candidate_closed
            )
        if shape_pool:
            allowed = {
                (shape.code.strip().upper(), int(shape.revision))
                for shape in shape_pool
            }
            shape_pool = tuple(
                shape
                for shape in indexed_shortlist
                if (shape.code.strip().upper(), int(shape.revision)) in allowed
            )
        else:
            shape_pool = indexed_shortlist

    best_by_shape: dict[str, ShapeMatch] = {}
    ratio_gate_blocked: dict[str, ShapeDefinition] = {}
    for shape in shape_pool:
        if not shape.active or shape.state == "RETIRED" or not shape.segments:
            continue
        if bool(shape.closed) != candidate_closed:
            continue

        profiles = reference_profiles_for_shape(shape, tests)
        # Keep a topology-only fallback even when tested proportions exist.
        # MASTER dimensions are parametric in real projects; a different set
        # of L1/L2/... values must produce an ambiguity for review, never make
        # the approved form disappear.
        profile_options: tuple[ShapeReferenceProfile | None, ...] = profiles + (None,)
        # An approved MASTER shape must still be discoverable when mirrored.
        # If its own policy forbids mirroring, the match is retained but marked
        # review-only instead of disappearing.
        approved_shape = str(shape.state).upper() in {"APPROVED", "LOCKED"}
        allow_mirror = bool(shape.mirror_allowed or shape.symmetric or approved_shape)
        candidate_variants = _descriptors(
            clean,
            closed=candidate_closed,
            allow_reverse=policy.allow_reversed_direction,
            allow_mirror=allow_mirror,
            implicit_closure=implicit_closure,
        )
        relation_match = match_shape_length_relations(
            shape,
            observed_ratios,
            default_relative_tolerance=(
                # Hooked closure is an explicitly review-only recovery.  Its
                # terminal tangent intersections are extrapolated through
                # bend radii, so repeated sides can differ noticeably in the
                # semantic straight-line approximation.  This wider bounded
                # gate only keeps the closed MASTER discoverable; the
                # HOOKED_CLOSURE reason still forbids automatic approval.
                min(
                    max(float(policy.length_ratio_tolerance), 0.20),
                    0.25,
                )
                if implicit_closure
                else min(
                    float(policy.length_ratio_tolerance),
                    0.05,
                )
            ),
            allow_reverse=policy.allow_reversed_direction,
        )
        if (
            relation_match.required_rule_count > 0
            and not relation_match.compatible
        ):
            # Required ENGINE semantics are a hard gate.  A topology/profile
            # resemblance may not override a failed equality or ratio rule.
            continue
        base_turn_constraints = turn_constraints_from_shape(shape)

        for profile in profile_options:
            expected_points, expected_has_fixed_ratios, expected_total_cm = _points_from_shape(
                shape,
                segment_lengths_cm=(
                    profile.segment_lengths_cm if profile is not None else None
                ),
            )
            expected_points = simplify_polyline(
                expected_points,
                policy.collinear_merge_tolerance_deg,
            )
            if len(expected_points) < 2:
                continue
            expected_variants = _descriptors(
                expected_points,
                closed=bool(shape.closed),
                allow_reverse=True,
                allow_mirror=False,
                implicit_closure=False,
            )
            for candidate in candidate_variants:
                for expected in expected_variants:
                    if len(candidate.ratios) != len(expected.ratios):
                        continue
                    if len(candidate.turns) != len(expected.turns):
                        continue
                    if any(
                        constraint.parametric
                        for constraint in base_turn_constraints
                    ):
                        aligned_constraints = transform_turn_constraints(
                            base_turn_constraints,
                            reversed_direction=expected.reversed_direction,
                            mirrored=expected.mirrored,
                            closed=bool(shape.closed),
                        )
                        angle_error = turn_sequence_error(
                            candidate.turns,
                            aligned_constraints,
                        )
                        if not math.isfinite(angle_error):
                            continue
                    else:
                        # Preserve the proven closed-loop rotation alignment
                        # for ordinary fixed-angle shapes.  Parametric
                        # constraints need the solver above; fixed turns are
                        # already represented exactly by this expected variant.
                        angle_error = max(
                            (
                                _angle_distance(left, right)
                                for left, right in zip(
                                    candidate.turns,
                                    expected.turns,
                                )
                            ),
                            default=0.0,
                        )
                    if angle_error > policy.angle_match_tolerance_deg:
                        continue
                    ratio_error: float | None = None
                    ratio_tolerance = float(policy.length_ratio_tolerance)
                    if expected_total_cm is not None:
                        ratio_tolerance = min(ratio_tolerance, 0.03)
                    if expected_has_fixed_ratios:
                        ratio_error = max(
                            (
                                abs(left - right)
                                for left, right in zip(candidate.ratios, expected.ratios)
                            ),
                            default=0.0,
                        )
                        if ratio_error > ratio_tolerance:
                            # The shape already passed topology, angles and its
                            # own required relations; only the concrete profile
                            # proportions rejected it.  Remember it: such a
                            # shape is still applicable to this instance for
                            # the mandatory-conflict check below.
                            ratio_gate_blocked.setdefault(
                                shape.code.strip().upper(), shape
                            )
                            continue

                    # Tested proportions are stronger evidence than topology
                    # alone.  This removes deterministic "first code wins"
                    # errors such as F011 being reported as F010.
                    score = angle_error / max(policy.angle_match_tolerance_deg, 1e-6)
                    if ratio_error is not None:
                        score += ratio_error / max(ratio_tolerance, 1e-6)
                        score -= 0.20
                    else:
                        score += 0.25
                    # Required relations are exclusion rules, not implicit
                    # precedence.  An unconstrained form may legitimately
                    # have equal arms, so two compatible ENGINE identities
                    # keep equal rank unless a concrete owner profile (or an
                    # explicit future precedence rule) distinguishes them.
                    mirror_requires_review = bool(
                        candidate.mirrored
                        and policy.mirror_requires_shape_permission
                        and not (shape.mirror_allowed or shape.symmetric)
                    )
                    if mirror_requires_review:
                        score += 0.05
                    match = ShapeMatch(
                        shape_code=shape.code,
                        shape_name=shape.name,
                        shape_revision=shape.revision,
                        mirrored=candidate.mirrored,
                        reversed_direction=candidate.reversed_direction,
                        max_angle_error_deg=angle_error,
                        max_length_ratio_error=ratio_error,
                        score=score,
                        confidence=(
                            (
                                "СЪВПАДЕНИЕ С MASTER ПРОФИЛ И "
                                "ЗАДЪЛЖИТЕЛНИ ОТНОШЕНИЯ"
                            )
                            if (
                                profile is not None
                                and relation_match.required_rule_count > 0
                            )
                            else (
                                "СЪВПАДЕНИЕ С КОНТРОЛЕН MASTER ПРОФИЛ"
                                if profile is not None
                                else (
                                    "СЪВПАДЕНИЕ СЪС ЗАДЪЛЖИТЕЛНИ "
                                    "MASTER ОТНОШЕНИЯ"
                                    if relation_match.required_rule_count > 0
                                    else "СЪВПАДЕНИЕ С MASTER ТОПОЛОГИЯ"
                                )
                            )
                        ),
                        reference_profile_id=profile.profile_id if profile is not None else None,
                        uniform_scale_factor=(
                            candidate_total / expected_total_cm
                            if expected_total_cm is not None and expected_total_cm > 1e-9
                            else None
                        ),
                        mirror_requires_review=mirror_requires_review,
                        required_relation_count=(
                            relation_match.required_rule_count
                        ),
                        max_relation_error=(
                            relation_match.max_relative_error
                        ),
                        relation_evidence=relation_match.evidence,
                    )
                    code = shape.code.strip().upper()
                    previous = best_by_shape.get(code)
                    if previous is None or match.score < previous.score:
                        best_by_shape[code] = match

    ranked = sorted(
        best_by_shape.values(),
        key=lambda item: (item.score, item.shape_code.upper()),
    )
    if not ranked:
        return None

    # ENGINE aliases and explicitly declared families are one engineering
    # identity.  They remain individually indexed for audit and migration, but
    # must not manufacture a matcher ambiguity merely because two equivalent
    # codes have the same score.
    identity_index = shape_index or EngineShapeIndex.from_shapes(shape_pool)
    grouped: dict[str, list[ShapeMatch]] = {}
    for item in ranked:
        entry = identity_index.find(item.shape_code)
        identity_key = (
            entry.identity_key
            if entry is not None
            else f"SHAPE:{item.shape_code.strip().upper()}"
        )
        grouped.setdefault(identity_key, []).append(item)

    identity_matches: list[ShapeMatch] = []
    for identity_key, members in grouped.items():
        ordered_members = sorted(
            members,
            key=lambda item: (item.score, item.shape_code.upper()),
        )
        evidence_match = ordered_members[0]
        member_entries = tuple(
            entry
            for item in ordered_members
            if (entry := identity_index.find(item.shape_code)) is not None
        )
        canonical_code = min(
            (
                entry.canonical_code
                for entry in member_entries
                if entry.canonical_code
            ),
            default=evidence_match.shape_code.strip().upper(),
        )
        canonical_entry = identity_index.find(canonical_code)
        equivalent_codes = tuple(
            sorted(
                {
                    item.shape_code.strip().upper()
                    for item in ordered_members
                    if item.shape_code.strip()
                }
            )
        )
        identity_matches.append(
            replace(
                evidence_match,
                shape_code=canonical_code,
                shape_name=(
                    canonical_entry.shape.name
                    if canonical_entry is not None
                    else evidence_match.shape_name
                ),
                shape_revision=(
                    canonical_entry.shape.revision
                    if canonical_entry is not None
                    else evidence_match.shape_revision
                ),
                canonical_shape_code=canonical_code,
                equivalent_shape_codes=equivalent_codes,
                identity_key=identity_key,
            )
        )

    identity_matches.sort(
        key=lambda item: (item.score, item.shape_code.upper()),
    )
    best = identity_matches[0]
    gap = (
        None
        if len(identity_matches) < 2
        else identity_matches[1].score - best.score
    )
    ambiguous = tuple(
        item.shape_code
        for item in identity_matches
        if item.score - best.score <= float(policy.master_ambiguity_score_gap)
    )
    if enforce_instance_collisions:
        ambiguous = _with_instance_identity_collisions(
            ambiguous,
            winner_code=best.shape_code,
            shape_pool=shape_pool,
            applicable_codes=set(best_by_shape) | set(ratio_gate_blocked),
            identity_index=identity_index,
        )
    return replace(
        best,
        ambiguous_shape_codes=ambiguous if len(ambiguous) > 1 else (),
        score_gap=gap,
    )


def _with_instance_identity_collisions(
    ambiguous: tuple[str, ...],
    *,
    winner_code: str,
    shape_pool: Sequence[ShapeDefinition],
    applicable_codes: set[str],
    identity_index: EngineShapeIndex,
) -> tuple[str, ...]:
    """Force a conflict when two ENGINE identities accept the same instance.

    A score gap produced by one concrete control profile per shape is not
    geometric evidence.  When the winner and another *applicable* shape share
    the ENGINE's canonical semantic signature, or their semantic domains
    overlap per the ENGINE's own solver (``shape_semantic_domains_overlap`` --
    e.g. an unconstrained form and its constrained subtype, F003 over F005),
    a concrete geometry that both forms fully accept cannot prove either code
    and the conflict is mandatory no matter how large that gap is.

    Three conditions keep this from manufacturing false conflicts:

    * the other shape must share the winner's canonical semantic signature
      (the ENGINE's own equality test), or the two semantic domains must
      overlap per the ENGINE's own solver -- not a heuristic;
    * it must NOT already be the same engineering identity.  An alias and a
      declared ``family_id`` group are one form under two codes; those
      collapse to one answer and must stay unambiguous.
      ``EngineShapeIndex.identity_key`` already encodes exactly that collapse;
    * it must be *applicable to this concrete geometry*: either it produced a
      match, or it reached the proportional ratio gate, which means it had
      already passed the closed flag, the segment and turn counts, the angle
      tolerance and its own required length relations.  That last condition is
      what keeps a declared-symmetric form out of the conflict when the
      candidate is not symmetric at all.
    """

    if not applicable_codes:
        return ambiguous
    normalized_winner = winner_code.strip().upper()
    by_code = {shape.code.strip().upper(): shape for shape in shape_pool}
    winner = by_code.get(normalized_winner)
    if winner is None:
        return ambiguous
    winner_signature = canonical_semantic_signature_from_shape(winner)

    winner_entry = identity_index.find(normalized_winner)
    winner_identity = (
        winner_entry.identity_key if winner_entry is not None else None
    )

    already = {item.strip().upper() for item in ambiguous}
    extra: list[str] = []
    for code in applicable_codes:
        if code == normalized_winner or code in already:
            continue
        shape = by_code.get(code)
        if shape is None:
            continue
        entry = identity_index.find(code)
        if (
            winner_identity is not None
            and entry is not None
            and entry.identity_key == winner_identity
        ):
            # Alias or declared family: one engineering identity, not a clash.
            continue
        signature = canonical_semantic_signature_from_shape(shape)
        if (
            winner_signature is not None
            and signature is not None
            and signature.value == winner_signature.value
        ):
            extra.append(shape.code)
            continue
        if shape_semantic_domains_overlap(winner, shape):
            extra.append(shape.code)
    if not extra:
        return ambiguous
    return tuple(sorted({*ambiguous, *extra, winner_code.strip().upper()}))


def reference_profiles_for_shape(
    shape: ShapeDefinition,
    control_tests: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[ShapeReferenceProfile, ...]:
    """Return validated concrete segment profiles for one MASTER shape.

    Profiles may be explicitly stored in ``shape.recognition``, supplied by
    successful engine control tests, or recorded by the owner's manual review.
    Invalid/incomplete records are ignored; topology-only matching remains as a
    safe fallback.
    """

    rows: list[ShapeReferenceProfile] = []
    recognition = shape.recognition if isinstance(shape.recognition, Mapping) else {}

    explicit = recognition.get("reference_segment_profiles")
    if isinstance(explicit, (list, tuple)):
        for index, value in enumerate(explicit, start=1):
            raw_lengths = (
                value.get("segment_lengths_cm")
                if isinstance(value, Mapping)
                else value
            )
            profile = _profile_from_lengths(
                f"{shape.code}-EXPLICIT-{index}",
                raw_lengths,
                source="MASTER_RECOGNITION",
                expected_count=len(shape.segments),
            )
            if profile is not None:
                rows.append(profile)
    for key in ("reference_segment_lengths_cm", "segment_lengths_cm"):
        profile = _profile_from_lengths(
            f"{shape.code}-{key.upper()}",
            recognition.get(key),
            source="MASTER_RECOGNITION",
            expected_count=len(shape.segments),
        )
        if profile is not None:
            rows.append(profile)

    for index, test in enumerate(control_tests or (), start=1):
        if not isinstance(test, Mapping):
            continue
        shape_code = str(
            test.get("shape_code")
            or test.get("КОД_ФОРМА")
            or test.get("shape")
            or ""
        ).strip().upper()
        if shape_code != shape.code.strip().upper():
            continue
        profile_id = str(
            test.get("test_id")
            or test.get("ID_ТЕСТ")
            or test.get("profile_id")
            or f"{shape.code}-PROFILE-{index}"
        )

        geometry = test.get("geometry")
        raw_lengths = None
        if isinstance(geometry, Mapping):
            raw_lengths = geometry.get("segment_lengths_cm")
        raw_lengths = (
            raw_lengths
            or test.get("segment_lengths_cm")
            or test.get("production_segment_lengths_cm")
        )
        profile = _profile_from_lengths(
            profile_id,
            raw_lengths,
            source="OWNER_REVIEW",
            expected_count=len(shape.segments),
        )
        if profile is not None:
            rows.append(profile)
            continue

        dimensions = parse_dimensions(
            test.get("dimensions", test.get("ОЧАКВАНИ_РАЗМЕРИ_СМ"))
        )
        if not dimensions:
            continue
        try:
            lengths = calculate_shape_segment_lengths(shape, dimensions)
        except ValueError:
            continue
        expected_total = _optional_float(
            test.get("expected_total_cm", test.get("ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ"))
        )
        if expected_total is not None and abs(sum(lengths) - expected_total) > max(
            0.20,
            expected_total * 0.002,
        ):
            continue
        profile = _profile_from_lengths(
            profile_id,
            lengths,
            source="ENGINE_CONTROL_TEST",
            expected_count=len(shape.segments),
        )
        if profile is not None:
            rows.append(profile)

    fixed_profile = _FIXED_MASTER_SEGMENT_PROFILES.get(shape.code.strip().upper())
    if fixed_profile is not None:
        profile = _profile_from_lengths(
            f"{shape.code}-LOCKED-FALLBACK",
            fixed_profile,
            source="LOCKED_FALLBACK",
            expected_count=len(shape.segments),
        )
        if profile is not None:
            rows.append(profile)

    unique: list[ShapeReferenceProfile] = []
    seen: set[tuple[float, ...]] = set()
    for profile in rows:
        total = max(profile.total_cm, 1e-9)
        key = tuple(round(value / total, 6) for value in profile.segment_lengths_cm)
        if key in seen:
            continue
        seen.add(key)
        unique.append(profile)
    return tuple(unique)


def points_from_reference_profile(
    shape: ShapeDefinition,
    profile: ShapeReferenceProfile,
) -> tuple[Point2D, ...]:
    points, _fixed, _total = _points_from_shape(
        shape,
        segment_lengths_cm=profile.segment_lengths_cm,
    )
    return points


# --------------------------------------------------------------------------- #
# Заоблени огъвки, начертани като многоъгълник
#
# ЗАЩО. Измерено на 06.10.2026 върху ``S-_H01-1001_Bottom_Reinforcement`` —
# чертеж от бюро, с което не е работено. Там всяка огъвка е НАЧЕРТАНА като
# заобление от осем къси отсечки. Прът „8N10x124/20“, който е обикновено „С“ от
# три участъка, стига до разпознаването с ДЕВЕТНАЙСЕТ:
#
#   46,609 · 0,447 · 0,479 · 0,480 · 0,446 · 0,446 · 0,479 · 0,480 · 0,447 ·
#   18,218 · 0,446 · 0,480 · 0,480 · 0,446 · 0,446 · 0,480 · 0,479 · 0,447 ·
#   46,609
#
# Завоите в двете поредици сумират точно −90,01° и −90,01°. Нито една MASTER
# форма няма 19 участъка, затова топологията не съвпадаше с нищо и 67 позиции
# оставаха без форма — макар че в чертежа няма НИТО ЕДНА непозната форма.
#
# ``simplify_polyline`` не ги слива и е права да не ги слива: тя маха само
# КОЛИНЕАРНИ продължения, а всяка отсечка от заоблението завива с ~11°.
#
# Правилото тук е различно и тясно: поредица от КЪСИ отсечки с ЕДНОПОСОЧНИ
# МАЛКИ завои, оградена от дълги отсечки, е ЕДНА огъвка. Върховете ѝ се
# заменят с пресечната точка на двете прави около нея — геометрично точната
# точка, която заоблението закръглява.
#
# ИЗМЕРЕНО ПРЕДИ ВКЛЮЧВАНЕ: върху 6703 полилинии в 13-те познати чертежа
# правилото намира **нула** заобления, тоест не може да промени познат резултат.
# В новия чертеж намира 108 заобления в 74 полилинии и сваля върховете от
# 1372 на 472.

#: Най-големият единичен завой ВЪТРЕ в заобление. Измерено: стъпките са
#: 7,89–11,04°. Истинската огъвка е ЕДНА стъпка от 45° нагоре.
FILLET_MAX_STEP_DEG = 20.0

#: Отсечките на ЕДНО заобление са почти еднакви помежду си. Измерено:
#: 0,446–0,480, тоест разсейване 1,08. Този признак, а не самата дължина,
#: отделя заоблението от късо РАМО: при „2*5N12x150“ рамото е 15,93 при
#: най-дълъг участък 125,93 — късо спрямо него, но 35 пъти по-дълго от
#: отсечките на заоблението до него.
#:
#: ИЗМЕРЕНО и НЕ прието (06.10.2026): сравнение с текущата медиана и разсейване
#: 3,0 слива и куките на стремената — „33N8x228“ става 64 -> 6 участъка, тоест
#: F006. Но дължината до пресечните точки НАДВИШАВА надписаната (234,5 при 228;
#: „36N6x20“ дава 26,7 при 20), а надвишаването е отказ при всяка форма. Там
#: въпросът вече не е топология, а конвенция външни срещу осови размери — и тя
#: по изрично правило се СВЕРЯВА, не се допуска.
FILLET_LENGTH_SPREAD = 2.0

#: Колко по-дълъг трябва да е съседът, който огражда заоблението. Измерено:
#: 18,218 / 0,480 = 38, а най-тясното в корпуса е доста над 3.
FILLET_NEIGHBOUR_MIN_RATIO = 3.0

#: Най-малко КЪСИ отсечки в поредицата. Две не са заобление, а форма.
FILLET_MIN_RUN_SEGMENTS = 3

#: Поредицата трябва да завърта наистина, иначе сливането нищо не значи.
FILLET_MIN_TOTAL_TURN_DEG = 30.0

#: Пресечната точка не бива да е по-далече от толкова пъти по-късия съсед.
#: Пази от почти успоредни прави, при които пресечната точка отлита.
FILLET_MAX_CORNER_REACH = 3.0


def _line_intersection(
    a1: Point2D, a2: Point2D, b1: Point2D, b2: Point2D
) -> Point2D | None:
    """Пресечната точка на правите ``a1a2`` и ``b1b2``; ``None`` при успоредни."""

    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    знаменател = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(знаменател) < 1e-9:
        return None
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    return (
        (a * (x3 - x4) - (x1 - x2) * b) / знаменател,
        (a * (y3 - y4) - (y1 - y2) * b) / знаменател,
    )


def fillet_runs(
    points: Sequence[Point2D],
) -> tuple[tuple[int, int, Point2D], ...]:
    """Заобленията в полилинията: (първа отсечка, последна отсечка, пресечна точка).

    Единственото място, където се решава кое е заобление. ``collapse_fillets``
    и модулът за отпечатаните коти по рамената (``chain_printed_dimensions``)
    четат оттук, за да виждат една и съща огъвка — иначе едното свива хордите,
    а другото ги брои за рамена. Условията са описани над константите.
    """

    P = [(float(p[0]), float(p[1])) for p in points]
    if len(P) < 5:
        return ()
    дължини = [math.dist(P[i], P[i + 1]) for i in range(len(P) - 1)]
    завои: list[float | None] = [None] * len(P)
    for i in range(1, len(P) - 1):
        завои[i] = _signed_turn(P[i - 1], P[i], P[i + 1])

    def заобление_от(начало_отсечка: int):
        """(край, пресечна точка) ако от тази отсечка започва заобление.

        Поредицата се разтяга, докато отсечките остават ПОДОБНИ по дължина и
        завоите между тях са малки и еднопосочни. Подобието е признакът, който
        отделя заоблението от късо рамо.
        """

        if начало_отсечка < 1 or начало_отсечка > len(дължини) - 2:
            return None
        първа = дължини[начало_отсечка]
        if първа <= 0:
            return None
        завой = завои[начало_отсечка]
        if завой is None or abs(завой) > FILLET_MAX_STEP_DEG:
            return None
        знак = 1 if завой > 0 else -1
        край_отсечка = начало_отсечка
        while край_отсечка + 1 <= len(дължини) - 2:
            следваща = дължини[край_отсечка + 1]
            завой_напред = завои[край_отсечка + 1]
            if завой_напред is None or abs(завой_напред) > FILLET_MAX_STEP_DEG:
                break
            if (1 if завой_напред > 0 else -1) != знак:
                break
            if следваща <= 0:
                break
            отношение = max(следваща, първа) / min(следваща, първа)
            if отношение > FILLET_LENGTH_SPREAD:
                break
            край_отсечка += 1
        if (край_отсечка - начало_отсечка + 1) < FILLET_MIN_RUN_SEGMENTS:
            return None
        вътре = дължини[начало_отсечка : край_отсечка + 1]
        преди, след = дължини[начало_отсечка - 1], дължини[край_отсечка + 1]
        най_дълга_вътре = max(вътре)
        if най_дълга_вътре <= 0:
            return None
        if (
            преди < FILLET_NEIGHBOUR_MIN_RATIO * най_дълга_вътре
            or след < FILLET_NEIGHBOUR_MIN_RATIO * най_дълга_вътре
        ):
            return None
        вътрешни_завои = [
            завои[k]
            for k in range(начало_отсечка, край_отсечка + 2)
            if завои[k] is not None
        ]
        if not вътрешни_завои:
            return None
        if not (
            all(з > 0 for з in вътрешни_завои) or all(з < 0 for з in вътрешни_завои)
        ):
            return None
        if any(abs(з) > FILLET_MAX_STEP_DEG for з in вътрешни_завои):
            return None
        if abs(sum(вътрешни_завои)) < FILLET_MIN_TOTAL_TURN_DEG:
            return None
        пресечна = _line_intersection(
            P[начало_отсечка - 1],
            P[начало_отсечка],
            P[край_отсечка + 1],
            P[край_отсечка + 2],
        )
        if пресечна is None:
            return None
        обхват = FILLET_MAX_CORNER_REACH * min(преди, след)
        if (
            math.dist(пресечна, P[начало_отсечка]) > обхват
            or math.dist(пресечна, P[край_отсечка + 1]) > обхват
        ):
            return None
        return край_отсечка, пресечна

    поредици: list[tuple[int, int, Point2D]] = []
    връх = 0
    while връх < len(P):
        намерено = заобление_от(връх) if връх < len(дължини) else None
        if намерено is not None:
            край_отсечка, пресечна = намерено
            поредици.append((връх, край_отсечка, пресечна))
            връх = край_отсечка + 2
            continue
        връх += 1
    return tuple(поредици)


def collapse_fillets(points: Sequence[Point2D]) -> tuple[Point2D, ...]:
    """Заоблената огъвка става един връх. Виж обяснението над константите.

    Правилото е върху ОТСЕЧКИТЕ, не върху върховете: поредица от КЪСИ отсечки,
    оградена от двете страни с дълга, е едно заобление. Така дългият среден
    участък между две огъвки ги разделя, вместо да ги слее в една.

    Нищо не се гади: поредица, която не отговаря на ВСИЧКИ условия, остава
    непокътната, а когато двете прави са успоредни или пресечната им точка
    отлита, поредицата също остава — отказът е по-добър от догадката.
    """

    P = [(float(p[0]), float(p[1])) for p in points]
    if len(P) < 5:
        # Както досега: под пет точки — непокътнато, без чистене на повторения.
        return tuple(P)
    резултат: list[Point2D] = []
    следващ = 0
    for начало_отсечка, край_отсечка, пресечна in fillet_runs(P):
        резултат.extend(P[следващ:начало_отсечка])
        резултат.append(пресечна)
        следващ = край_отсечка + 2
    резултат.extend(P[следващ:])

    изчистен: list[Point2D] = []
    for точка in резултат:
        if not изчистен or not _points_close(изчистен[-1], точка, 1e-8):
            изчистен.append(точка)
    return tuple(изчистен)


def normalize_drawn_polyline(
    points: Sequence[Point2D],
    collinear_tolerance_deg: float,
) -> tuple[Point2D, ...]:
    """Начертаната полилиния, подготвена за разпознаване на форма.

    Два слоя, в този ред: колинеарните продължения отпадат
    (``simplify_polyline``), после заоблената огъвка става един връх
    (``collapse_fillets``). Прилага се САМО върху начертаното — очакваната
    форма от дефиницията на Engine-а минава непокътната.
    """

    return collapse_fillets(simplify_polyline(points, collinear_tolerance_deg))


def simplify_polyline(
    points: Sequence[Point2D],
    collinear_tolerance_deg: float,
) -> tuple[Point2D, ...]:
    clean: list[Point2D] = []
    for point in points:
        point = (float(point[0]), float(point[1]))
        if not clean or not _points_close(clean[-1], point, 1e-8):
            clean.append(point)
    if len(clean) < 3:
        return tuple(clean)

    closed = _points_close(clean[0], clean[-1], 1e-8)
    work = clean[:-1] if closed else clean[:]
    changed = True
    while changed and len(work) >= (3 if not closed else 4):
        changed = False
        result: list[Point2D] = []
        count = len(work)
        for index, point in enumerate(work):
            if not closed and index in {0, count - 1}:
                result.append(point)
                continue
            previous = work[(index - 1) % count]
            following = work[(index + 1) % count]
            turn = abs(_signed_turn(previous, point, following))
            # Only a same-direction 0-degree continuation is redundant.  A
            # 180-degree reversal is an engineering hook/return segment and
            # must remain part of the topology.
            if turn <= collinear_tolerance_deg:
                changed = True
                continue
            result.append(point)
        if len(result) < 2:
            break
        if closed and len(result) > 1 and _points_close(result[0], result[-1], 1e-8):
            result.pop()
        work = result
    if closed and work:
        work.append(work[0])
    return tuple(work)


def _points_from_shape(
    shape: ShapeDefinition,
    *,
    segment_lengths_cm: Sequence[float] | None = None,
) -> tuple[tuple[Point2D, ...], bool, float | None]:
    point = (0.0, 0.0)
    points: list[Point2D] = [point]
    heading = _heading_for_direction(shape.initial_direction) or 0.0
    segments = sorted(shape.segments, key=lambda item: item.index)
    fixed_profile = (
        tuple(float(value) for value in segment_lengths_cm)
        if segment_lengths_cm is not None
        else None
    )
    if fixed_profile is not None and len(fixed_profile) != len(segments):
        fixed_profile = None

    all_fixed = True
    resolved_lengths: list[float] = []
    for position, segment in enumerate(segments):
        # Bend definitions are the engineering source of truth.  The legacy
        # drawing_direction field is only a starting/fallback direction; many
        # 29° stair forms store it in the nearest 45° compass bucket.
        previous_has_bend = bool(
            position > 0 and segments[position - 1].bend_after_deg is not None
        )
        if position == 0 or not previous_has_bend:
            absolute = _heading_for_direction(segment.drawing_direction)
            if absolute is not None:
                heading = absolute
        if fixed_profile is not None:
            length = max(float(fixed_profile[position]), 1e-6)
        elif segment.fixed_length_cm is None:
            length = 1.0
            all_fixed = False
        else:
            length = max(float(segment.fixed_length_cm), 1e-6)
        resolved_lengths.append(length)
        radians = math.radians(heading)
        point = (point[0] + length * math.cos(radians), point[1] + length * math.sin(radians))
        points.append(point)
        if segment.bend_after_deg is not None:
            direction = str(segment.bend_direction or "RIGHT").upper()
            sign = 1.0 if direction in {"LEFT", "НАЛЯВО"} else -1.0
            heading += sign * float(segment.bend_after_deg)
    if shape.closed and points and not _points_close(points[0], points[-1], 1e-8):
        points.append(points[0])
    expected_total = sum(resolved_lengths) if all_fixed else None
    return tuple(points), all_fixed, expected_total


def _profile_from_lengths(
    profile_id: str,
    raw_lengths: Any,
    *,
    source: str,
    expected_count: int,
) -> ShapeReferenceProfile | None:
    if not isinstance(raw_lengths, (list, tuple)):
        return None
    try:
        values = tuple(float(value) for value in raw_lengths)
    except (TypeError, ValueError):
        return None
    if len(values) != expected_count or any(
        not math.isfinite(value) or value <= 0 for value in values
    ):
        return None
    return ShapeReferenceProfile(
        profile_id=str(profile_id),
        segment_lengths_cm=values,
        source=str(source),
    )


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _polyline_length(points: Sequence[Point2D]) -> float:
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _descriptors(
    points: Sequence[Point2D],
    *,
    closed: bool,
    allow_reverse: bool,
    allow_mirror: bool,
    implicit_closure: bool = False,
) -> tuple[_Descriptor, ...]:
    base = tuple(points)
    variants: list[tuple[tuple[Point2D, ...], bool, bool]] = [(base, False, False)]
    if allow_reverse:
        variants.append((tuple(reversed(base)), False, True))
    if allow_mirror:
        mirrored = tuple((x, -y) for x, y in base)
        variants.append((mirrored, True, False))
        if allow_reverse:
            variants.append((tuple(reversed(mirrored)), True, True))

    output: list[_Descriptor] = []
    seen: set[tuple] = set()
    for variant, mirrored, reversed_direction in variants:
        ratios, turns = _ratios_and_turns(
            variant,
            closed,
            implicit_closure=implicit_closure,
        )
        if not ratios:
            continue
        rotations = range(len(ratios)) if closed else range(1)
        for shift in rotations:
            shifted_ratios = _rotate(ratios, shift)
            shifted_turns = _rotate(turns, shift) if turns else turns
            descriptor = _Descriptor(
                ratios=tuple(round(value, 5) for value in shifted_ratios),
                turns=tuple(round(_normalize_angle(value), 2) for value in shifted_turns),
                mirrored=mirrored,
                reversed_direction=reversed_direction,
            )
            key = (descriptor.ratios, descriptor.turns, mirrored, reversed_direction)
            if key not in seen:
                seen.add(key)
                output.append(descriptor)
    return tuple(output)


def _ratios_and_turns(
    points: Sequence[Point2D],
    closed: bool,
    *,
    implicit_closure: bool = False,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    work = list(points)
    if (
        closed
        and not implicit_closure
        and work
        and not _points_close(work[0], work[-1], 1e-8)
    ):
        work.append(work[0])
    vectors: list[tuple[float, float]] = []
    lengths: list[float] = []
    for start, end in zip(work, work[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-8:
            continue
        vectors.append((dx, dy))
        lengths.append(length)
    if not lengths:
        return (), ()
    total = sum(lengths)
    ratios = tuple(length / total for length in lengths)
    turns: list[float] = []
    pair_count = len(vectors) if closed else len(vectors) - 1
    for index in range(max(0, pair_count)):
        left = vectors[index]
        right = vectors[(index + 1) % len(vectors)]
        turns.append(_vector_turn(left, right))
    return ratios, tuple(turns)


def _signature(descriptor: _Descriptor, closed: bool) -> str:
    ratios = ",".join(f"{value:.5f}" for value in descriptor.ratios)
    turns = ",".join(f"{value:.2f}" for value in descriptor.turns)
    return f"{'C' if closed else 'O'}|n={len(descriptor.ratios)}|r={ratios}|a={turns}"


def _descriptor_key(descriptor: _Descriptor) -> tuple:
    return descriptor.turns, descriptor.ratios


def _rotate(values: Sequence[float], shift: int) -> tuple[float, ...]:
    if not values:
        return ()
    shift %= len(values)
    return tuple(values[shift:]) + tuple(values[:shift])


def _vector_turn(left: Point2D, right: Point2D) -> float:
    cross = left[0] * right[1] - left[1] * right[0]
    dot = left[0] * right[0] + left[1] * right[1]
    return _normalize_angle(math.degrees(math.atan2(cross, dot)))


def _signed_turn(previous: Point2D, point: Point2D, following: Point2D) -> float:
    return _vector_turn(
        (point[0] - previous[0], point[1] - previous[1]),
        (following[0] - point[0], following[1] - point[1]),
    )


def _angle_distance(left: float, right: float) -> float:
    return abs(_normalize_angle(left - right))


def _normalize_angle(value: float) -> float:
    normalized = (float(value) + 180.0) % 360.0 - 180.0
    return 180.0 if abs(normalized + 180.0) < 1e-9 else normalized


def _points_close(left: Point2D, right: Point2D, tolerance: float) -> bool:
    return math.hypot(left[0] - right[0], left[1] - right[1]) <= tolerance


def _inferred_closed(points: Sequence[Point2D], endpoint_tolerance: float) -> bool:
    """Distinguish a real closure from two short open hooks that nearly meet."""

    if len(points) < 3:
        return False
    gap = math.hypot(
        points[-1][0] - points[0][0],
        points[-1][1] - points[0][1],
    )
    if gap <= 1e-8:
        return True
    lengths = [
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
        if math.hypot(right[0] - left[0], right[1] - left[1]) > 1e-8
    ]
    if not lengths:
        return False
    return gap <= min(float(endpoint_tolerance), min(lengths) * 0.05)


def _heading_for_direction(value: str | None) -> float | None:
    normalized = str(value or "").upper().replace("-", "_").replace(" ", "_")
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
