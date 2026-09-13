from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from lion_rebar.dxf.geometry_candidates import (
    GeometryCandidate,
    GeometryCandidateAnalysis,
    GeometryCandidateContext,
    find_geometry_candidates,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.text_parser import parse_rebar_text
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex

_POSITION_KEY_RE = re.compile(r"^\s*(\d{1,3}[A-ZА-Я](?:\*)?)\s*$", re.IGNORECASE)
_LEGEND_SPEC_RE = re.compile(
    r"^\s*N\s*(\d+(?:[.,]\d+)?)\s*(?:-\s*)?L\s*=\s*(\d+(?:[.,]\d+)?)\s*$",
    re.IGNORECASE,
)

# A key/spec pair must beat the runner-up by a visible drawing-space margin.
# This keeps compact, column-aligned legends usable while preserving the
# fail-closed behaviour for real ties or near-ties.
_KEY_SPEC_SEPARATION_MARGIN_RAW = 5.0
_SHARED_FAMILY_BASIS = "POSITION_KEY_LEGEND_SHARED_GEOMETRY_FAMILY"


@dataclass(frozen=True)
class PositionKeyLegendEntry:
    key: str
    legend_circle_handle: str
    legend_spec_handle: str
    legend_spec_text: str
    diameter_mm: float
    declared_length_cm: float
    occurrence_count: int
    probe_annotation: DxfEntityRecord
    analysis: GeometryCandidateAnalysis

    @property
    def candidate(self) -> GeometryCandidate:
        return self.analysis.candidates[0]


@dataclass(frozen=True)
class PositionKeyLegendIndex:
    entries_by_key: Mapping[str, PositionKeyLegendEntry]
    circles_by_key: Mapping[str, tuple[DxfEntityRecord, ...]]
    entities_by_handle: Mapping[str, DxfEntityRecord]
    protection_only_geometry_handles: frozenset[str] = field(
        default_factory=frozenset
    )

    @property
    def protected_geometry_handles(self) -> frozenset[str]:
        """Geometry shared by legend entries, never local row ownership."""

        resolved_handles = {
            str(handle)
            for entry in self.entries_by_key.values()
            for handle in entry.candidate.handles
            if str(handle)
        }
        return frozenset(resolved_handles.union(self.protection_only_geometry_handles))


@dataclass(frozen=True)
class PositionKeyResolution:
    code: str
    annotation_handle: str
    key: str
    table_circle_handle: str
    table_circle_distance_raw: float
    legend: PositionKeyLegendEntry

    @property
    def candidate(self) -> GeometryCandidate:
        return self.legend.candidate


def build_position_key_legend_index(
    entities: Sequence[DxfEntityRecord],
    *,
    shape_definitions: Iterable[ShapeDefinition],
    control_tests: Iterable[Mapping[str, object]],
    policy: RecognitionPolicy,
    context: GeometryCandidateContext,
    shape_index: EngineShapeIndex,
) -> PositionKeyLegendIndex:
    """Build only unambiguous ``position key -> legend geometry`` entries.

    A valid entry requires one key TEXT on the configured circle layer, one
    parsed ``N.. L=..`` legend specification inside the narrow legend radius,
    and one strong, exact, unambiguous MASTER candidate beside that
    specification.  Missing or duplicate evidence removes the key from the
    index instead of guessing.
    """

    entities_by_handle = {str(entity.handle): entity for entity in entities}
    if not policy.position_key_legend_rule_enabled:
        return PositionKeyLegendIndex({}, {}, entities_by_handle)

    key_layers = _normalized_aliases(policy.position_key_circle_layer_aliases)
    spec_layers = _normalized_aliases(policy.position_key_spec_layer_aliases)
    circles_by_key_mut: dict[str, list[DxfEntityRecord]] = {}
    specs: list[tuple[DxfEntityRecord, float, float]] = []

    for entity in entities:
        entity_type = str(entity.entity_type or "").upper()
        layer = _normalize_token(entity.layer)
        if entity_type == "TEXT" and layer in key_layers:
            key = _parse_position_key(entity.text)
            if key:
                circles_by_key_mut.setdefault(key, []).append(entity)
        if entity_type in {"TEXT", "MTEXT"} and layer in spec_layers:
            parsed = _parse_legend_spec(entity.text)
            if parsed is not None:
                specs.append((entity, parsed[0], parsed[1]))

    circles_by_key = {
        key: tuple(sorted(values, key=lambda value: str(value.handle)))
        for key, values in circles_by_key_mut.items()
    }
    associations_by_key: dict[
        str,
        list[tuple[DxfEntityRecord, DxfEntityRecord, float, float]],
    ] = {}
    radius = float(policy.position_key_legend_radius_raw)
    association_search_radius = radius + _KEY_SPEC_SEPARATION_MARGIN_RAW
    unresolved_legend_spec_handles: set[str] = set()
    all_key_records = tuple(
        (key, record)
        for key, records in circles_by_key.items()
        for record in records
    )
    for key, circles in circles_by_key.items():
        for circle in circles:
            anchor = _position_key_anchor(circle)
            if anchor is None:
                continue
            nearby = sorted(
                (
                    _distance(anchor, spec_anchor),
                    str(spec.handle),
                    spec,
                    diameter,
                    length,
                )
                for spec, diameter, length in specs
                if (
                    (spec_anchor := _record_anchor(spec)) is not None
                    and _distance(anchor, spec_anchor) <= association_search_radius
                )
            )
            nearest = _unambiguous_nearest(nearby, max_distance=radius)
            if nearest is None:
                if (
                    nearby
                    and float(nearby[0][0]) <= radius
                    and len(nearby) > 1
                    and float(nearby[1][0]) - float(nearby[0][0])
                    < _KEY_SPEC_SEPARATION_MARGIN_RAW
                ):
                    unresolved_legend_spec_handles.update(
                        str(value[2].handle)
                        for value in nearby[:2]
                        if float(value[0]) <= radius
                    )
                continue
            _distance_to_spec, _spec_handle, spec, diameter, length = nearest
            spec_anchor = _record_anchor(spec)
            if spec_anchor is None:
                continue

            # Mutual-nearest is required.  A visually compact legend can put
            # several specs inside the radius, but the selected spec must also
            # point back to this exact key with the same separation guarantee.
            nearby_keys = sorted(
                (
                    _distance(spec_anchor, key_anchor),
                    str(key_record.handle),
                    other_key,
                    key_record,
                )
                for other_key, key_record in all_key_records
                if (
                    (key_anchor := _position_key_anchor(key_record)) is not None
                    and _distance(spec_anchor, key_anchor)
                    <= association_search_radius
                )
            )
            nearest_key = _unambiguous_nearest(
                nearby_keys,
                max_distance=radius,
            )
            if nearest_key is None or str(nearest_key[3].handle) != str(circle.handle):
                unresolved_legend_spec_handles.add(str(spec.handle))
                continue
            associations_by_key.setdefault(key, []).append(
                (circle, spec, diameter, length)
            )

    # One legend spec must not silently define two different global keys.
    spec_use_count: dict[str, int] = {}
    for values in associations_by_key.values():
        for _circle, spec, _diameter, _length in values:
            handle = str(spec.handle)
            spec_use_count[handle] = spec_use_count.get(handle, 0) + 1

    definitions = tuple(shape_definitions)
    tests = tuple(control_tests)
    unique_associations: dict[
        str,
        tuple[DxfEntityRecord, DxfEntityRecord, float, float],
    ] = {}
    probes: dict[str, DxfEntityRecord] = {}
    analyses: dict[str, GeometryCandidateAnalysis] = {}
    for key, associations in sorted(associations_by_key.items()):
        if len(associations) != 1:
            unresolved_legend_spec_handles.update(
                str(spec.handle)
                for _circle, spec, _diameter, _length in associations
            )
            continue
        circle, spec, diameter, length = associations[0]
        if spec_use_count.get(str(spec.handle), 0) != 1:
            unresolved_legend_spec_handles.add(str(spec.handle))
            continue
        probe = _legend_probe(
            spec,
            diameter=diameter,
            length=length,
            key=key,
            circle_handle=str(circle.handle),
        )
        analysis = find_geometry_candidates(
            probe,
            entities,
            max_candidates=8,
            shape_definitions=definitions,
            control_tests=tests,
            policy=policy,
            context=context,
            shape_index=shape_index,
        )
        unique_associations[key] = (circle, spec, diameter, length)
        probes[key] = probe
        analyses[key] = analysis

    # Geometry printed as part of a recognized global legend must stay out of
    # manual row ownership even when the key/spec entry is later rejected as a
    # duplicate or proximity ambiguity.  Reserve only one strict, exact
    # candidate identity per spec; competing exact identities fail closed and
    # do not sweep unrelated nearby rebar into this protection set.
    protection_only_handles: set[str] = set()
    analysed_spec_handles: set[str] = set()
    for key, (_circle, spec, _diameter, length) in unique_associations.items():
        analysed_spec_handles.add(str(spec.handle))
        protection_only_handles.update(
            _strict_protection_candidate_handles(
                analyses[key],
                declared_length=float(length),
                policy=policy,
            )
        )
    for spec, diameter, length in specs:
        spec_handle = str(spec.handle)
        if (
            spec_handle not in unresolved_legend_spec_handles
            or spec_handle in analysed_spec_handles
        ):
            continue
        protection_probe = _legend_probe(
            spec,
            diameter=diameter,
            length=length,
        )
        protection_analysis = find_geometry_candidates(
            protection_probe,
            entities,
            max_candidates=8,
            shape_definitions=definitions,
            control_tests=tests,
            policy=policy,
            context=context,
            shape_index=shape_index,
        )
        protection_only_handles.update(
            _strict_protection_candidate_handles(
                protection_analysis,
                declared_length=float(length),
                policy=policy,
            )
        )
        analysed_spec_handles.add(spec_handle)

    # Some real vertical legends print several diameter variants in one tight
    # row and show their common L-shape once.  The first spec is close enough to
    # anchor the geometry, while the following specs are intentionally farther
    # away.  Promote the shared candidate only when it is the *single* exact,
    # physical, common candidate for every member of an unambiguous equal-L
    # family.  Otherwise keep the normal fail-closed result.
    family_analyses = _shared_family_analyses(
        unique_associations,
        analyses,
        policy=policy,
    )

    entries: dict[str, PositionKeyLegendEntry] = {}
    for key, association in sorted(unique_associations.items()):
        circle, spec, diameter, length = association
        analysis = family_analyses.get(key, analyses[key])
        if key not in family_analyses and not _legend_analysis_is_exact(analysis, policy):
            continue
        entries[key] = PositionKeyLegendEntry(
            key=key,
            legend_circle_handle=str(circle.handle),
            legend_spec_handle=str(spec.handle),
            legend_spec_text=str(spec.text or ""),
            diameter_mm=diameter,
            declared_length_cm=length,
            occurrence_count=len(circles_by_key.get(key, ())),
            probe_annotation=probes[key],
            analysis=analysis,
        )

    return PositionKeyLegendIndex(
        entries,
        circles_by_key,
        entities_by_handle,
        protection_only_geometry_handles=frozenset(protection_only_handles),
    )


def _unambiguous_nearest(
    values: Sequence[tuple],
    *,
    max_distance: float | None = None,
) -> tuple | None:
    """Return the nearest row only when the runner-up is clearly separated."""

    if not values:
        return None
    first = values[0]
    if max_distance is not None and float(first[0]) > float(max_distance):
        return None
    if len(values) > 1:
        try:
            margin = float(values[1][0]) - float(first[0])
        except (TypeError, ValueError, IndexError):
            return None
        if margin < _KEY_SPEC_SEPARATION_MARGIN_RAW:
            return None
    return first


def _shared_family_analyses(
    associations: Mapping[
        str,
        tuple[DxfEntityRecord, DxfEntityRecord, float, float],
    ],
    analyses: Mapping[str, GeometryCandidateAnalysis],
    *,
    policy: RecognitionPolicy,
) -> dict[str, GeometryCandidateAnalysis]:
    """Resolve compact equal-L legend rows that share one drawn geometry."""

    remaining = set(associations)
    families: list[set[str]] = []
    radius = float(policy.position_key_legend_radius_raw)
    value_tolerance = float(policy.position_key_value_tolerance)
    while remaining:
        seed = remaining.pop()
        component = {seed}
        pending = [seed]
        while pending:
            current = pending.pop()
            _circle, current_spec, _diameter, current_length = associations[current]
            current_anchor = _record_anchor(current_spec)
            if current_anchor is None:
                continue
            linked: list[str] = []
            for other in remaining:
                _other_circle, other_spec, _other_diameter, other_length = associations[other]
                other_anchor = _record_anchor(other_spec)
                if (
                    other_anchor is not None
                    and abs(float(current_length) - float(other_length)) <= value_tolerance
                    and _distance(current_anchor, other_anchor) <= radius
                ):
                    linked.append(other)
            for other in linked:
                remaining.remove(other)
                component.add(other)
                pending.append(other)
        if len(component) >= 2:
            families.append(component)

    promoted: dict[str, GeometryCandidateAnalysis] = {}
    for family in families:
        exact_candidates: dict[str, dict[tuple, GeometryCandidate]] = {}
        for key in family:
            declared_length = float(associations[key][3])
            by_identity: dict[tuple, GeometryCandidate] = {}
            for candidate in analyses[key].candidates:
                if not _shared_family_candidate_is_exact(
                    candidate,
                    declared_length=declared_length,
                    policy=policy,
                ):
                    continue
                by_identity.setdefault(
                    _shared_candidate_identity(candidate),
                    candidate,
                )
            exact_candidates[key] = by_identity
        if any(not values for values in exact_candidates.values()):
            continue
        common = set.intersection(
            *(set(values) for values in exact_candidates.values())
        )
        if len(common) != 1:
            continue
        identity = next(iter(common))
        if any(
            _shared_identity_has_preceding_distinct_exact_candidate(
                analyses[key],
                identity=identity,
                declared_length=float(associations[key][3]),
                policy=policy,
            )
            for key in family
        ):
            # Never replace stronger exact per-member geometry with the farther
            # common family candidate.
            continue
        anchor_keys = [
            key
            for key in family
            if analyses[key].candidates
            and _shared_candidate_identity(analyses[key].candidates[0]) == identity
            and _legend_analysis_is_exact(analyses[key], policy)
        ]
        if len(anchor_keys) != 1:
            # Exactly one close anchor is required.  Zero is unsupported; two
            # competing anchors mean the drawing does not prove one shared row.
            continue
        anchor_key = anchor_keys[0]
        family_keys = ", ".join(sorted(family))
        for key in family:
            analysis = analyses[key]
            candidate = exact_candidates[key][identity]
            note = (
                f"{_SHARED_FAMILY_BASIS}: ключове {family_keys} имат еднаква "
                f"L={associations[key][3]:g} и единствен общ точен MASTER "
                f"кандидат {', '.join(candidate.handles)}, закотвен от {anchor_key}."
            )
            promoted_candidate = replace(
                candidate,
                rank=1,
                confidence="СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА",
                basis=(
                    f"{candidate.basis}; {_SHARED_FAMILY_BASIS}"
                    if candidate.basis
                    else _SHARED_FAMILY_BASIS
                ),
                notes=(*candidate.notes, note),
                review_reason_codes=tuple(
                    code
                    for code in candidate.review_reason_codes
                    if code != "FAR_ASSOCIATION"
                ),
            )
            promoted[key] = replace(
                analysis,
                candidates=(promoted_candidate,),
                status="НАМЕРЕН Е СИЛЕН КАНДИДАТ — ИЗИСКВА ВИЗУАЛНА ПРОВЕРКА",
                warnings=(*analysis.warnings, note),
            )
    return promoted


def _shared_identity_has_preceding_distinct_exact_candidate(
    analysis: GeometryCandidateAnalysis,
    *,
    identity: tuple,
    declared_length: float,
    policy: RecognitionPolicy,
) -> bool:
    """Detect exact candidates ranked ahead of the proposed shared identity."""

    for candidate in analysis.candidates:
        candidate_identity = _shared_candidate_identity(candidate)
        if candidate_identity == identity:
            return False
        if _shared_family_candidate_is_exact(
            candidate,
            declared_length=declared_length,
            policy=policy,
        ):
            return True
    return False


def _legend_probe(
    spec: DxfEntityRecord,
    *,
    diameter: float,
    length: float,
    key: str | None = None,
    circle_handle: str | None = None,
) -> DxfEntityRecord:
    suffix = key or "PROTECTION"
    raw = {
        "source": "POSITION_KEY_LEGEND_PROBE",
        "legend_spec_handle": str(spec.handle),
    }
    if key is not None:
        raw["position_key"] = key
    if circle_handle is not None:
        raw["legend_circle_handle"] = circle_handle
    return DxfEntityRecord(
        handle=f"{spec.handle}::POSITION_KEY_PROBE::{suffix}",
        entity_type=spec.entity_type,
        layer=spec.layer,
        geometry=dict(spec.geometry),
        text=f"1N{_format_number(diameter)}x{_format_number(length)}",
        color_index=spec.color_index,
        linetype=spec.linetype,
        raw=raw,
    )


def _strict_protection_candidate_handles(
    analysis: GeometryCandidateAnalysis,
    *,
    declared_length: float,
    policy: RecognitionPolicy,
) -> frozenset[str]:
    """Reserve one proven physical candidate, never an ambiguous nearby set."""

    if not _legend_analysis_is_exact(analysis, policy):
        return frozenset()
    candidates_by_identity: dict[tuple, GeometryCandidate] = {}
    for candidate in analysis.candidates:
        if _strict_protection_candidate_is_exact(
            candidate,
            declared_length=declared_length,
            policy=policy,
        ):
            candidates_by_identity.setdefault(
                _shared_candidate_identity(candidate),
                candidate,
            )
    if len(candidates_by_identity) != 1:
        return frozenset()
    identity, candidate = next(iter(candidates_by_identity.items()))
    if _shared_candidate_identity(analysis.candidates[0]) != identity:
        return frozenset()
    if float(candidate.distance_raw) > float(
        policy.position_key_legend_geometry_max_distance_raw
    ):
        return frozenset()
    return frozenset(str(handle) for handle in candidate.handles if str(handle))


def _strict_protection_candidate_is_exact(
    candidate: GeometryCandidate,
    *,
    declared_length: float,
    policy: RecognitionPolicy,
) -> bool:
    """Strict physical evidence used only to lock rejected legend geometry."""

    if not candidate.handles or not str(candidate.master_shape_code or "").strip():
        return False
    if candidate.master_ambiguous_shape_codes:
        return False
    if (
        candidate.uniform_scale_factor is not None
        and not math.isclose(
            float(candidate.uniform_scale_factor),
            1.0,
            rel_tol=1e-6,
            abs_tol=1e-6,
        )
    ):
        return False
    if candidate.review_reason_codes or candidate.unresolved_curve_count:
        return False
    if float(candidate.distance_raw) > float(
        policy.position_key_legend_geometry_max_distance_raw
    ):
        return False
    raw_difference = candidate.raw_numeric_difference
    if raw_difference is None:
        raw_difference = float(candidate.geometry_length_raw) - float(declared_length)
    return abs(float(raw_difference)) <= float(
        policy.position_key_legend_length_tolerance_cm
    )


def _shared_family_candidate_is_exact(
    candidate: GeometryCandidate,
    *,
    declared_length: float,
    policy: RecognitionPolicy,
) -> bool:
    if not candidate.handles or not str(candidate.master_shape_code or "").strip():
        return False
    if candidate.master_ambiguous_shape_codes:
        return False
    if (
        candidate.uniform_scale_factor is not None
        and not math.isclose(
            float(candidate.uniform_scale_factor),
            1.0,
            rel_tol=1e-6,
            abs_tol=1e-6,
        )
    ):
        return False
    if set(candidate.review_reason_codes) - {"FAR_ASSOCIATION"}:
        return False
    if candidate.unresolved_curve_count:
        return False
    raw_difference = candidate.raw_numeric_difference
    if raw_difference is None:
        raw_difference = float(candidate.geometry_length_raw) - float(declared_length)
    if abs(float(raw_difference)) > float(
        policy.position_key_legend_length_tolerance_cm
    ):
        return False
    if not candidate.canonical_segment_lengths_cm:
        return False
    return True


def _shared_candidate_identity(candidate: GeometryCandidate) -> tuple:
    return (
        tuple(candidate.handles),
        str(candidate.master_shape_code or ""),
        str(candidate.canonical_signature or ""),
        tuple(round(float(value), 6) for value in candidate.canonical_segment_lengths_cm),
    )


def resolve_position_key_annotation(
    annotation: DxfEntityRecord,
    index: PositionKeyLegendIndex,
    *,
    policy: RecognitionPolicy,
) -> PositionKeyResolution | None:
    """Resolve one table annotation through a proven global position key.

    The table block must carry exact BJ/FI/L data, use one of the explicitly
    configured ARM_N block families and have exactly one matching key circle
    inside the narrow table radius.  The resolver returns a legend reference,
    not local geometry ownership.
    """

    if not policy.position_key_legend_rule_enabled:
        return None
    if str(annotation.entity_type or "").upper() != "BLOCK_ATTR_TEXT":
        return None
    if str(annotation.raw.get("source", "")).upper() != "INSERT_ATTRIBUTES":
        return None

    block_aliases = _normalized_aliases(policy.position_key_table_block_aliases)
    layer_aliases = _normalized_aliases(policy.position_key_table_layer_aliases)
    block_name = _normalize_token(
        annotation.block_name or annotation.raw.get("block_name") or ""
    )
    if (
        block_name not in block_aliases
        or _normalize_token(annotation.layer) not in layer_aliases
    ):
        return None

    parsed = parse_rebar_text(annotation.text or "")
    if (
        parsed.count is None
        or parsed.diameter_mm is None
        or parsed.length_cm is None
    ):
        return None
    value_tolerance = float(policy.position_key_value_tolerance)
    matching_entries = [
        entry
        for entry in index.entries_by_key.values()
        if abs(float(parsed.diameter_mm) - entry.diameter_mm) <= value_tolerance
        and abs(float(parsed.length_cm) - entry.declared_length_cm) <= value_tolerance
    ]
    if len(matching_entries) != 1:
        return None
    entry = matching_entries[0]

    parent_handle = str(annotation.raw.get("parent_insert_handle", ""))
    parent = index.entities_by_handle.get(parent_handle)
    if parent is None or str(parent.entity_type or "").upper() != "INSERT":
        return None
    if (
        _normalize_token(parent.block_name) not in block_aliases
        or _normalize_token(parent.layer) not in layer_aliases
    ):
        return None
    parent_anchor = _record_anchor(parent)
    if parent_anchor is None:
        return None

    table_matches: list[tuple[float, DxfEntityRecord]] = []
    for circle in index.circles_by_key.get(entry.key, ()):
        if str(circle.handle) == entry.legend_circle_handle:
            continue
        circle_anchor = _record_anchor(circle)
        if circle_anchor is None:
            continue
        distance = _distance(parent_anchor, circle_anchor)
        if distance <= float(policy.position_key_table_radius_raw):
            table_matches.append((distance, circle))
    if len(table_matches) != 1:
        return None
    distance, circle = table_matches[0]
    return PositionKeyResolution(
        code="POSITION_KEY_LEGEND_REFERENCE",
        annotation_handle=str(annotation.handle),
        key=entry.key,
        table_circle_handle=str(circle.handle),
        table_circle_distance_raw=distance,
        legend=entry,
    )


def _legend_analysis_is_exact(
    analysis: GeometryCandidateAnalysis,
    policy: RecognitionPolicy,
) -> bool:
    if not analysis.candidates:
        return False
    top = analysis.candidates[0]
    if top.confidence not in {
        "СИЛЕН КАНДИДАТ — НЕОДОБРЕН",
        "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА",
    }:
        return False
    if not top.handles or not str(top.master_shape_code or "").strip():
        return False
    if top.master_ambiguous_shape_codes or top.review_reason_codes:
        return False
    if abs(float(top.numeric_difference)) > float(
        policy.position_key_legend_length_tolerance_cm
    ):
        return False
    return float(top.distance_raw) <= float(
        policy.position_key_legend_geometry_max_distance_raw
    )


def _parse_position_key(value: object) -> str | None:
    match = _POSITION_KEY_RE.fullmatch(str(value or ""))
    if match is None:
        return None
    return match.group(1).upper().replace(" ", "")


def _parse_legend_spec(value: object) -> tuple[float, float] | None:
    match = _LEGEND_SPEC_RE.fullmatch(str(value or ""))
    if match is None:
        return None
    return (
        float(match.group(1).replace(",", ".")),
        float(match.group(2).replace(",", ".")),
    )


def _normalized_aliases(values: Sequence[str]) -> frozenset[str]:
    return frozenset(_normalize_token(value) for value in values if str(value).strip())


def _normalize_token(value: object) -> str:
    return str(value or "").strip().upper()


def _record_anchor(record: DxfEntityRecord) -> tuple[float, float] | None:
    for key in ("insert", "visual_anchor", "location", "center", "start"):
        raw = record.geometry.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                return float(raw[0]), float(raw[1])
            except (TypeError, ValueError):
                continue
    return None


def _position_key_anchor(record: DxfEntityRecord) -> tuple[float, float] | None:
    """Use the rendered key centre before the TEXT insertion baseline.

    In compact vertical legends the insertion point of ``3a*``/``3b*`` can
    sit close to the neighbouring specification even though the visible key
    circle is correctly aligned with its own row.  ``visual_anchor`` is the
    DXF importer's conservative rendered-text anchor; older fixtures without
    it retain the historical insertion-point fallback.
    """

    for key in ("visual_anchor", "center", "insert", "location", "start"):
        raw = record.geometry.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                return float(raw[0]), float(raw[1])
            except (TypeError, ValueError):
                continue
    return None


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
