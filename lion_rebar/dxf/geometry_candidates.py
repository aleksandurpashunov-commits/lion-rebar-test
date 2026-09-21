from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.geometry_core_v2 import (
    GeometryRepresentation,
    arc_path_from_geometry,
    path_spatial_segments,
    polyline_path_from_geometry,
    semantic_path_from_polyline,
)
from lion_rebar.dxf.dimension_resolver import (
    DimensionEvidence,
    dimension_evidence_for_shape,
    points_with_segment_lengths,
)
from lion_rebar.dxf.line_chain_v015 import (
    LineChainIndex,
    NearGapBridge,
    NearGapEdge,
    audit_unique_one_gap_bridge,
)
from lion_rebar.dxf.line_chain_v015 import (
    build_connected_chains as _v015_build_connected_chains,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.proxy_adapter import proxy_geometry_handles
from lion_rebar.dxf.recognition_policy import (
    DEFAULT_RECOGNITION_POLICY,
    RecognitionPolicy,
)
from lion_rebar.dxf.shape_recognition import (
    ShapeFingerprint,
    ShapeMatch,
    fingerprint_from_points,
    match_master_shapes,
    match_shape_length_relations,
)
from lion_rebar.dxf.text_parser import parse_rebar_text
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engine.shape_index import EngineShapeIndex
from lion_rebar.manual_assignment import infer_f006_composite_segments

_REBAR_LAYER_DIAMETER_RE = re.compile(r"(?:^|[^A-Z0-9])N\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)

#: Owner-requested 2026-09-12 ("само непознати неща да излизат за преглед, а
#: не елементарни грешки"): the hard diameter rejection above it can only
#: fire when the DXF LAYER NAME encodes the diameter. The arm_koloni /
#: arm_shaibi dialect puts every diameter on ONE generic layer, so that
#: check never fires there and a coincidental length match on unrelated
#: Ø16 geometry could out-rank the truth for a declared Ø8 bar (real case:
#: "104N8x94"). This dialect does, however, name its exploded geometry
#: after the proxy that owns it -- ``PXG_<parent>_<index>_0`` -- and that
#: parent's own annotation declares a diameter. Comparing those two is a
#: diameter check that needs no layer naming convention at all.
_PROXY_GEOMETRY_HANDLE_RE = re.compile(r"^PXG_([^_]+)_\d+_\d+$")
_PROXY_ANNOTATION_SOURCE = "ACAD_PROXY_GRAPHIC_REBAR_ANNOTATION"


def _proxy_owner_handle(geometry_handle: str) -> str | None:
    match = _PROXY_GEOMETRY_HANDLE_RE.match(str(geometry_handle or ""))
    return match.group(1) if match else None


def _proxy_declared_diameters(
    entities: Sequence[DxfEntityRecord],
) -> dict[str, float]:
    """Map each proxy parent handle to the diameter its own annotation
    declares. Only unambiguous parents are kept: if one proxy somehow
    carried two annotations with different diameters, it proves nothing and
    is dropped rather than guessed at."""

    diameters: dict[str, float] = {}
    conflicting: set[str] = set()
    for record in entities:
        raw = record.raw or {}
        if raw.get("source") != _PROXY_ANNOTATION_SOURCE:
            continue
        parent = str(raw.get("parent_proxy_handle") or "")
        if not parent:
            continue
        try:
            diameter = float(raw.get("diameter_mm"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(diameter) or diameter <= 0:
            continue
        known = diameters.get(parent)
        if known is not None and abs(known - diameter) > 0.01:
            conflicting.add(parent)
            continue
        diameters[parent] = diameter
    for parent in conflicting:
        diameters.pop(parent, None)
    return diameters


def _proxy_owner_diameter_mismatch(
    shape: Sequence[GeometryPrimitive],
    declared_diameter: float,
    owner_diameters: Mapping[str, float],
) -> bool:
    """True only when every piece of this candidate provably belongs to
    proxies that declare a DIFFERENT diameter than the annotation being
    matched. Fail-open by design: unknown ownership proves nothing."""

    if not owner_diameters or declared_diameter <= 0:
        return False
    seen = False
    for primitive in shape:
        owner = _proxy_owner_handle(primitive.handle)
        if owner is None:
            return False
        diameter = owner_diameters.get(owner)
        if diameter is None:
            return False
        if abs(diameter - declared_diameter) <= 0.01:
            return False
        seen = True
    return seen
_REBAR_LAYER_KEYWORDS = (
    "ARM",
    "REBAR",
    "BAR",
    "BARS",
    "STEEL",
    "АРМ",
    "АРМАТ",
    "ЖЕЛЯЗ",
)


F006_CONTEXTUAL_RULE_CODE = "F006_COMPOSITE_STIRRUP"
F006_STRONG_CONFIDENCE = "СИЛНО СТРЕМЕ F006 — ИЗИСКВА ИЗВАДКА"
F006_HOOKED_FAMILY_CONFIDENCE = (
    "СИЛНО СЕМЕЙСТВО F006 — ИЗВИТА ОС, ДЪЛЖИНАТА ИЗИСКВА ПРОВЕРКА"
)
C_SHAPE_EXTENDED_BASIS = "ТЕСЕН ESOVE F007 РАДИУС"
PROXY_PARENT_PROVENANCE_BASIS = "PROXY PARENT PROVENANCE — ТОЧНА L"

Point2D = tuple[float, float]
Segment2D = tuple[float, float, float, float]


@dataclass(frozen=True)
class GeometryPrimitive:
    handle: str
    layer: str
    entity_type: str
    length_raw: float
    segments: tuple[Segment2D, ...]
    points: tuple[Point2D, ...]
    start: Point2D
    end: Point2D
    closed: bool = False
    recognition_points: tuple[Point2D, ...] = ()
    semantic_length_raw: float | None = None
    geometry_representation: str = GeometryRepresentation.CENTERLINE.value
    geometry_layer_role: str = "REBAR_LAYER"
    source_curve_count: int = 0
    normalized_bend_count: int = 0
    unresolved_curve_count: int = 0
    arc_radii_raw: tuple[float, ...] = ()
    hooked_closure: bool = False
    hooked_closure_gap_raw: float | None = None
    geometry_notes: tuple[str, ...] = ()


@dataclass
class GeometryCandidateContext:
    """Reusable spatial and topology indexes for one imported DXF.

    Interactive analysis may omit this context. Batch analysis builds it once
    and avoids rebuilding primitives, leader lists and endpoint graphs for every
    annotation.
    """

    entities: tuple[DxfEntityRecord, ...]
    primitives: tuple[GeometryPrimitive, ...]
    leaders: tuple[DxfEntityRecord, ...]
    line_index: LineChainIndex
    near_gap_line_index: LineChainIndex | None
    endpoint_tolerance_raw: float
    near_gap_max_raw: float
    cell_size: float
    primitive_cells: dict[tuple[int, int], tuple[int, ...]]
    primitive_by_handle: dict[str, GeometryPrimitive]
    segments_by_handle: dict[str, tuple[Segment2D, ...]]

    @classmethod
    def build(
        cls,
        entities: Sequence[DxfEntityRecord],
        *,
        policy: RecognitionPolicy | Mapping | None = None,
    ) -> "GeometryCandidateContext":
        resolved = _resolve_policy(policy)
        primitives = tuple(
            primitive
            for record in entities
            if (
                layer_role := _rebar_geometry_layer_role(
                    record.layer,
                    resolved,
                )
            )
            and (
                primitive := _primitive_from_record(
                    record,
                    geometry_layer_role=layer_role,
                )
            )
            is not None
        )
        cell_size = max(50.0, min(200.0, float(resolved.max_search_distance_raw)))
        cells_mut: dict[tuple[int, int], set[int]] = defaultdict(set)
        for index, primitive in enumerate(primitives):
            for x1, y1, x2, y2 in primitive.segments:
                gx0 = math.floor(min(x1, x2) / cell_size)
                gx1 = math.floor(max(x1, x2) / cell_size)
                gy0 = math.floor(min(y1, y2) / cell_size)
                gy1 = math.floor(max(y1, y2) / cell_size)
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        cells_mut[(gx, gy)].add(index)
        return cls(
            entities=tuple(entities),
            primitives=primitives,
            leaders=tuple(
                entity for entity in entities if entity.entity_type.upper() == "LEADER"
            ),
            line_index=LineChainIndex(
                primitives,
                endpoint_tolerance=float(resolved.endpoint_tolerance_raw),
                spatial_cell_size=cell_size,
            ),
            near_gap_line_index=(
                LineChainIndex(
                    primitives,
                    endpoint_tolerance=float(resolved.near_gap_max_raw),
                    spatial_cell_size=cell_size,
                )
                if resolved.near_gap_healing_enabled
                and float(resolved.near_gap_max_raw)
                > float(resolved.endpoint_tolerance_raw)
                else None
            ),
            endpoint_tolerance_raw=float(resolved.endpoint_tolerance_raw),
            near_gap_max_raw=float(resolved.near_gap_max_raw),
            cell_size=cell_size,
            primitive_cells={key: tuple(sorted(values)) for key, values in cells_mut.items()},
            primitive_by_handle={primitive.handle: primitive for primitive in primitives},
            segments_by_handle={primitive.handle: primitive.segments for primitive in primitives},
        )

    def nearby_primitives(
        self,
        probes: Sequence[Point2D],
        radius: float,
    ) -> tuple[GeometryPrimitive, ...]:
        if not probes:
            return ()
        radius = max(float(radius), 0.0)
        span = max(1, math.ceil(radius / self.cell_size))
        candidates: set[int] = set()
        for probe in probes:
            gx = math.floor(probe[0] / self.cell_size)
            gy = math.floor(probe[1] / self.cell_size)
            for dx in range(-span, span + 1):
                for dy in range(-span, span + 1):
                    candidates.update(self.primitive_cells.get((gx + dx, gy + dy), ()))
        return tuple(
            self.primitives[index]
            for index in sorted(candidates)
            if min(
                _distance_point_to_segments(probe, self.primitives[index].segments)
                for probe in probes
            ) <= radius
        )

    def connected_paths(
        self,
        probes: Sequence[Point2D],
        search_distance: float,
        policy: RecognitionPolicy,
    ) -> list[tuple[GeometryPrimitive, ...]]:
        per_component_limit = max(512, min(5000, int(policy.max_path_candidates) * 4))
        if abs(float(policy.endpoint_tolerance_raw) - self.endpoint_tolerance_raw) > 1e-9:
            return _connected_paths(
                list(self.primitives),
                policy,
                probe_points=probes,
                search_distance=search_distance,
            )
        return self.line_index.query(
            probe_points=probes,
            search_distance=search_distance,
            max_segments=int(policy.max_component_segments),
            max_paths_per_component=per_component_limit,
        )

    def nearby_segments(
        self,
        point: Point2D,
        *,
        radius: float = 120.0,
        limit: int = 20,
    ) -> tuple[tuple[str, Segment2D], ...]:
        primitives = self.nearby_primitives((point,), radius)
        rows: list[tuple[float, str, Segment2D]] = []
        for primitive in primitives:
            for segment in primitive.segments:
                rows.append((_distance_point_to_segment(point, segment), primitive.handle, segment))
        rows.sort(key=lambda row: (row[0], row[1], row[2]))
        return tuple((handle, segment) for _distance, handle, segment in rows[: max(1, limit)])

    def near_gap_connected_paths(
        self,
        probes: Sequence[Point2D],
        search_distance: float,
        policy: RecognitionPolicy,
    ) -> list[tuple[tuple[GeometryPrimitive, ...], NearGapBridge]]:
        """Return only paths proven to contain one unique review-only bridge."""

        if (
            not policy.near_gap_healing_enabled
            or float(policy.near_gap_max_raw) <= float(policy.endpoint_tolerance_raw)
        ):
            return []
        per_component_limit = max(512, min(5000, int(policy.max_path_candidates) * 4))
        if (
            self.near_gap_line_index is None
            or abs(float(policy.endpoint_tolerance_raw) - self.endpoint_tolerance_raw) > 1e-9
            or abs(float(policy.near_gap_max_raw) - self.near_gap_max_raw) > 1e-9
        ):
            expanded_index = LineChainIndex(
                self.primitives,
                endpoint_tolerance=float(policy.near_gap_max_raw),
                spatial_cell_size=self.cell_size,
            )
        else:
            expanded_index = self.near_gap_line_index
        expanded = expanded_index.query(
            probe_points=probes,
            search_distance=search_distance,
            max_segments=int(policy.max_component_segments),
            max_paths_per_component=per_component_limit,
        )
        return _audited_near_gap_paths(
            expanded,
            all_primitives=self.primitives,
            policy=policy,
        )

    def segments_in_rect(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
    ) -> tuple[tuple[str, Segment2D], ...]:
        gx0 = math.floor(min_x / self.cell_size)
        gx1 = math.floor(max_x / self.cell_size)
        gy0 = math.floor(min_y / self.cell_size)
        gy1 = math.floor(max_y / self.cell_size)
        indices: set[int] = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                indices.update(self.primitive_cells.get((gx, gy), ()))
        rows: list[tuple[str, Segment2D]] = []
        seen: set[tuple[str, Segment2D]] = set()
        for index in sorted(indices):
            primitive = self.primitives[index]
            for segment in primitive.segments:
                key = (primitive.handle, segment)
                if key in seen:
                    continue
                seen.add(key)
                if not (
                    max(segment[0], segment[2]) < min_x
                    or min(segment[0], segment[2]) > max_x
                    or max(segment[1], segment[3]) < min_y
                    or min(segment[1], segment[3]) > max_y
                ):
                    rows.append(key)
        return tuple(rows)


def build_geometry_candidate_context(
    entities: Sequence[DxfEntityRecord],
    *,
    policy: RecognitionPolicy | Mapping | None = None,
) -> GeometryCandidateContext:
    return GeometryCandidateContext.build(entities, policy=policy)


@dataclass(frozen=True)
class GeometryCandidate:
    rank: int
    handles: tuple[str, ...]
    layer: str
    segment_count: int
    geometry_length_raw: float
    declared_length_cm: float
    numeric_difference: float
    distance_raw: float
    layer_diameter_mm: float | None
    diameter_layer_match: bool
    score: float
    score_gap: float | None
    distance_gap_raw: float | None
    confidence: str
    basis: str
    notes: tuple[str, ...] = field(default_factory=tuple)
    centroid_distance_raw: float | None = None
    canonical_signature: str | None = None
    orientation_invariant: bool = True
    reversed_direction_equivalent: bool = True
    master_shape_code: str | None = None
    master_shape_name: str | None = None
    master_shape_revision: int | None = None
    master_shape_mirrored: bool = False
    master_shape_reversed: bool = False
    master_shape_score: float | None = None
    master_reference_profile_id: str | None = None
    master_ambiguous_shape_codes: tuple[str, ...] = field(default_factory=tuple)
    uniform_scale_factor: float | None = None
    mirror_requires_review: bool = False
    review_reason_codes: tuple[str, ...] = field(default_factory=tuple)
    effective_geometry_length_cm: float | None = None
    raw_numeric_difference: float | None = None
    contextual_rule_code: str | None = None
    contextual_rule_note: str | None = None
    near_gap_healed: bool = False
    near_gap_bridge_gap_raw: float | None = None
    near_gap_bridge_handles: tuple[str, str] = field(default_factory=tuple)
    near_gap_bridge_points: tuple[Point2D, Point2D] = field(default_factory=tuple)
    canonical_segment_lengths_cm: tuple[float, ...] = field(default_factory=tuple)
    canonical_turn_angles_deg: tuple[float, ...] = field(default_factory=tuple)
    geometry_representation: str | None = None
    geometry_layer_role: str | None = None
    source_curve_count: int = 0
    normalized_bend_count: int = 0
    unresolved_curve_count: int = 0
    source_arc_radii_raw: tuple[float, ...] = field(default_factory=tuple)
    hooked_closure: bool = False
    hooked_closure_gap_raw: float | None = None
    dimension_evidence_applied: bool = False
    dimension_master_resolved: bool = False
    dimension_original_ambiguous_shape_codes: tuple[str, ...] = field(default_factory=tuple)
    dimension_text_handles: tuple[str, ...] = field(default_factory=tuple)
    dimension_resolution_strategy: str | None = None
    dimension_total_difference_cm: float | None = None
    local_f006_printed_dimension_handles: tuple[str, ...] = field(
        default_factory=tuple
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GeometryCandidateAnalysis:
    annotation_handle: str
    label: str
    diameter_mm: float | None
    declared_length_cm: float | None
    candidates: tuple[GeometryCandidate, ...]
    status: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    policy: dict = field(default_factory=dict)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


@dataclass(frozen=True)
class _ContextualShapeOverride:
    code: str
    note: str
    points: tuple[Point2D, ...]
    effective_length_cm: float
    segment_lengths_cm: tuple[float, ...]
    turn_angles_deg: tuple[float, ...]


def find_geometry_candidates(
    selected: DxfEntityRecord,
    entities: list[DxfEntityRecord],
    *,
    max_candidates: int = 8,
    shape_definitions: Iterable[ShapeDefinition] | None = None,
    control_tests: Iterable[Mapping[str, Any]] | None = None,
    policy: RecognitionPolicy | Mapping | None = None,
    context: GeometryCandidateContext | None = None,
    shape_index: EngineShapeIndex | None = None,
    local_detail_shape_code: str | None = None,
    local_detail_rule_code: str | None = None,
    local_detail_rule_note: str | None = None,
    allowed_dimension_handles: Iterable[str] | None = None,
    excluded_dimension_handles: Iterable[str] = (),
) -> GeometryCandidateAnalysis:
    """Rank safe geometry candidates without approving a link.

    v0.1.15 applies explicit recognition laws before ranking. Geometry is
    normalized by relative turn angles and segment ratios, so translation,
    rotation and reversed drawing direction do not affect the fingerprint.
    Mirroring is accepted only when the matching MASTER shape allows it.
    """

    resolved_policy = _resolve_policy(policy)
    local_detail_shape_code = str(local_detail_shape_code or "").strip().upper() or None
    local_detail_rule_code = str(local_detail_rule_code or "").strip() or None
    local_detail_rule_note = str(local_detail_rule_note or "").strip() or None
    local_allowed_dimension_handles = (
        tuple(sorted({str(value) for value in allowed_dimension_handles if str(value)}))
        if allowed_dimension_handles is not None
        else None
    )
    local_excluded_dimension_handles = tuple(
        sorted({str(value) for value in excluded_dimension_handles if str(value)})
    )
    # Легендната геометрия вече е доказана като чертеж на спецификацията
    # (ключ ↔ спецификация ↔ точна MASTER геометрия). Само там котите, които
    # мерят напречно на участък, се изключват от A-E — виж
    # dimension_resolver._MAX_DIMENSION_AXIS_OFFSET_DEG за границата и защо.
    legend_probe = (
        str((selected.raw or {}).get("source") or "").strip().upper()
        == "POSITION_KEY_LEGEND_PROBE"
    )
    shape_definitions = tuple(shape_definitions or ())
    control_tests = tuple(control_tests or ())
    parsed = parse_rebar_text(selected.text or "")
    warnings: list[str] = []
    rejection_counts: dict[str, int] = {}

    if parsed.diameter_mm is None or parsed.length_cm is None:
        missing = []
        if parsed.diameter_mm is None:
            missing.append("диаметър")
        if parsed.length_cm is None:
            missing.append("числова дължина L")
        return GeometryCandidateAnalysis(
            annotation_handle=selected.handle,
            label=selected.text or "",
            diameter_mm=parsed.diameter_mm,
            declared_length_cm=parsed.length_cm,
            candidates=(),
            status="НЯМА ДОСТАТЪЧНО ДАННИ",
            warnings=(f"Липсва: {', '.join(missing)}.",),
            policy=resolved_policy.to_dict(),
        )

    source_entities: Sequence[DxfEntityRecord] = context.entities if context is not None else entities
    probe_points = _annotation_probe_points(
        selected,
        source_entities,
        resolved_policy,
        leader_entities=context.leaders if context is not None else None,
    )
    if not probe_points:
        return GeometryCandidateAnalysis(
            annotation_handle=selected.handle,
            label=selected.text or "",
            diameter_mm=parsed.diameter_mm,
            declared_length_cm=parsed.length_cm,
            candidates=(),
            status="ЛИПСВА КООРДИНАТА НА НАДПИСА",
            warnings=("Не може да се изчисли пространствена близост.",),
            policy=resolved_policy.to_dict(),
        )
    anchor = _points_center(probe_points)
    declared = float(parsed.length_cm)
    diameter = float(parsed.diameter_mm)
    max_search_distance = resolved_policy.search_distance_limit(declared)
    primary_search_distance = resolved_policy.primary_search_distance_limit(declared)
    max_length_difference = resolved_policy.hard_length_difference_limit(declared)

    if context is not None:
        nearby_primitives = context.nearby_primitives(probe_points, max_search_distance)
        all_primitives = context.primitives
        connected_paths = context.connected_paths(
            probe_points,
            max_search_distance,
            resolved_policy,
        )
        near_gap_paths = context.near_gap_connected_paths(
            probe_points,
            max_search_distance,
            resolved_policy,
        )
    else:
        all_primitives = tuple(
            primitive
            for record in entities
            if (
                layer_role := _rebar_geometry_layer_role(
                    record.layer,
                    resolved_policy,
                )
            )
            and (
                primitive := _primitive_from_record(
                    record,
                    geometry_layer_role=layer_role,
                )
            )
            is not None
        )
        nearby_primitives = tuple(
            primitive
            for primitive in all_primitives
            if min(
                _distance_point_to_segments(probe, primitive.segments)
                for probe in probe_points
            ) <= max_search_distance
        )
        connected_paths = _connected_paths(
            list(all_primitives),
            resolved_policy,
            probe_points=probe_points,
            search_distance=max_search_distance,
        )
        near_gap_paths = _near_gap_connected_paths(
            list(all_primitives),
            resolved_policy,
            probe_points=probe_points,
            search_distance=max_search_distance,
        )

    if not all_primitives:
        return GeometryCandidateAnalysis(
            annotation_handle=selected.handle,
            label=selected.text or "",
            diameter_mm=parsed.diameter_mm,
            declared_length_cm=parsed.length_cm,
            candidates=(),
            status="НЕ Е НАМЕРЕНА ПОДДЪРЖАНА ГЕОМЕТРИЯ",
            warnings=(
                "Не са намерени LINE/POLYLINE обекти в разпознаваем армировъчен слой.",
            ),
            policy=resolved_policy.to_dict(),
        )

    candidate_shapes: list[
        tuple[tuple[GeometryPrimitive, ...], str, NearGapBridge | None]
    ] = [
        ((primitive,), "ЕДИНИЧЕН DXF ОБЕКТ", None)
        for primitive in nearby_primitives
    ]
    proven_proxy_handles = proxy_geometry_handles(selected)
    if proven_proxy_handles:
        primitive_by_handle = (
            context.primitive_by_handle
            if context is not None
            else {primitive.handle: primitive for primitive in all_primitives}
        )
        proven_proxy_shape = tuple(
            primitive_by_handle[handle]
            for handle in proven_proxy_handles
            if handle in primitive_by_handle
        )
        if len(proven_proxy_shape) == len(proven_proxy_handles):
            # Put structural provenance first.  The ordinary spatial search
            # may rediscover the same handles, but ``seen`` below keeps one
            # auditable candidate and prevents duplicate rankings.
            candidate_shapes.insert(
                0,
                (proven_proxy_shape, PROXY_PARENT_PROVENANCE_BASIS, None),
            )
    candidate_shapes.extend(
        (component, "СВЪРЗАНИ СЕГМЕНТИ", None)
        for component in connected_paths
    )
    candidate_shapes.extend(
        (component, "КОНТРОЛИРАНО СВЪРЗАНИ СЕГМЕНТИ", bridge)
        for component, bridge in near_gap_paths
    )
    if resolved_policy.c_shape_curve_rule_enabled:
        c_shape_radius = float(resolved_policy.c_shape_curve_search_distance_raw)
        nearby_handles = {primitive.handle for primitive in nearby_primitives}
        candidate_shapes.extend(
            ((primitive,), C_SHAPE_EXTENDED_BASIS, None)
            for primitive in all_primitives
            if primitive.handle not in nearby_handles
            and _is_esove_curve_primitive(primitive, resolved_policy)
            and min(
                _distance_point_to_segments(probe, primitive.segments)
                for probe in probe_points
            )
            <= c_shape_radius
        )
    proxy_owner_diameters = _proxy_declared_diameters(entities)
    raw_rows: list[dict] = []
    seen: set[tuple[str, ...]] = set()

    for shape, basis, near_gap_bridge in candidate_shapes:
        proxy_parent_provenance = basis == PROXY_PARENT_PROVENANCE_BASIS
        handles = tuple(sorted(primitive.handle for primitive in shape if primitive.handle))
        if not handles or handles in seen:
            continue
        seen.add(handles)

        layer = _single_layer(shape)
        layer_diameter = _diameter_from_layer(layer)
        if (
            resolved_policy.reject_explicit_diameter_mismatch
            and layer_diameter is not None
            and abs(layer_diameter - diameter) > 0.01
        ):
            _reject(rejection_counts, "ГРЕШЕН ДИАМЕТЪР В СЛОЯ")
            continue
        if (
            resolved_policy.reject_explicit_diameter_mismatch
            and layer_diameter is None
            and not proxy_parent_provenance
            and _proxy_owner_diameter_mismatch(shape, diameter, proxy_owner_diameters)
        ):
            # Same rule as the layer check above, proven from proxy ownership
            # instead of a layer name (see _proxy_declared_diameters). Skipped
            # when the geometry belongs to this annotation's OWN proxy, which
            # by definition shares its diameter.
            _reject(rejection_counts, "ЧУЖД ДИАМЕТЪР ПО PROXY СОБСТВЕНОСТ")
            continue

        distance = min(
            _distance_point_to_segments(probe, primitive.segments)
            for probe in probe_points
            for primitive in shape
        )
        distance_limit = (
            float(resolved_policy.c_shape_curve_search_distance_raw)
            if basis == C_SHAPE_EXTENDED_BASIS
            else max_search_distance
        )
        if distance > distance_limit and not proxy_parent_provenance:
            _reject(rejection_counts, "ПРЕКАЛЕНО ДАЛЕЧ ОТ НАДПИСА")
            continue

        ordered_points = _ordered_shape_points(
            shape,
            resolved_policy,
            endpoint_tolerance_raw=(
                resolved_policy.near_gap_max_raw
                if near_gap_bridge is not None
                else resolved_policy.endpoint_tolerance_raw
            ),
        )
        if ordered_points is None:
            _reject(rejection_counts, "РАЗКЛОНЕНА ИЛИ НЕСВЪРЗАНА ГЕОМЕТРИЯ")
            continue

        representations = {
            primitive.geometry_representation for primitive in shape
        }
        if GeometryRepresentation.STROKED_OUTLINE.value in representations:
            _reject(
                rejection_counts,
                "ОЧЕРТАНИЕ БЕЗ ИЗВЛЕЧЕНА ОСЕВА ГЕОМЕТРИЯ",
            )
            continue
        layer_roles = {primitive.geometry_layer_role for primitive in shape}
        source_curve_count = sum(primitive.source_curve_count for primitive in shape)
        normalized_bend_count = sum(
            primitive.normalized_bend_count for primitive in shape
        )
        unresolved_curve_count = sum(
            primitive.unresolved_curve_count for primitive in shape
        )
        source_arc_radii = tuple(
            radius
            for primitive in shape
            for radius in primitive.arc_radii_raw
        )
        hooked_closure = bool(
            len(shape) == 1 and shape[0].hooked_closure
        )
        hooked_closure_gap = (
            shape[0].hooked_closure_gap_raw if hooked_closure else None
        )

        # Production-length comparison follows the physical DXF centreline:
        # straight edges use their chord, while bulge/ARC edges use exact arc
        # length.  ``semantic_length_raw`` remains topology-only evidence for
        # tangent-run reconstruction and MASTER matching.
        geometry_length = sum(primitive.length_raw for primitive in shape)
        raw_difference = abs(geometry_length - declared)
        contextual_override = _contextual_shape_override(
            selected=selected,
            diameter_mm=diameter,
            declared_length_cm=declared,
            measured_geometry_length_cm=geometry_length,
            layer=layer,
            ordered_points=ordered_points,
            source_curve_count=source_curve_count,
            normalized_bend_count=normalized_bend_count,
            unresolved_curve_count=unresolved_curve_count,
            policy=resolved_policy,
        )
        if basis == C_SHAPE_EXTENDED_BASIS and contextual_override is None:
            _reject(rejection_counts, "ДАЛЕЧНА ESOVE ГЕОМЕТРИЯ БЕЗ ПЪЛНО F007 ДОКАЗАТЕЛСТВО")
            continue
        comparison_points = contextual_override.points if contextual_override else ordered_points
        effective_geometry_length = (
            contextual_override.effective_length_cm if contextual_override else geometry_length
        )
        difference = abs(effective_geometry_length - declared)
        fingerprint = (
            fingerprint_from_points(
                comparison_points,
                closed=True if hooked_closure else None,
                implicit_closure=hooked_closure,
                policy=resolved_policy,
            )
            if unresolved_curve_count == 0
            else None
        )
        master_match = (
            match_master_shapes(
                comparison_points,
                shape_definitions,
                policy=resolved_policy,
                control_tests=control_tests,
                shape_index=shape_index,
                candidate_closed_override=(
                    True if hooked_closure else None
                ),
                implicit_closure=hooked_closure,
            )
            if unresolved_curve_count == 0
            else None
        )
        original_master_ambiguity = (
            tuple(master_match.ambiguous_shape_codes)
            if master_match is not None
            else ()
        )
        local_context_resolved = False
        if (
            local_detail_shape_code in {"F003", "F005"}
            and master_match is not None
            and len(comparison_points) == 4
        ):
            local_resolution = _resolve_local_named_u_subtype(
                comparison_points,
                target_shape_code=local_detail_shape_code,
                shape_definitions=shape_definitions,
                control_tests=control_tests,
                policy=resolved_policy,
            )
            if local_resolution is not None:
                master_match = local_resolution
                original_master_ambiguity = ()
                local_context_resolved = True
        proportion_rule_code: str | None = (
            local_detail_rule_code if local_context_resolved else None
        )
        proportion_rule_note: str | None = (
            local_detail_rule_note if local_context_resolved else None
        )
        proportion_resolved = False
        # Engineering identity is decided from the drawn U proportions before
        # any nearby printed dimension is allowed to resolve a MASTER
        # ambiguity. Both outer arms must independently support the same side;
        # a mixed or boundary case deliberately stays ambiguous.
        if (
            master_match is not None
            and master_match.ambiguous_shape_codes
            and resolved_policy.u_subtype_proportion_rule_enabled
        ):
            resolved_proportion = _resolve_declared_u_subtype_by_proportion(
                comparison_points,
                master_match=master_match,
                shape_definitions=shape_definitions,
                control_tests=control_tests,
                policy=resolved_policy,
            )
            if resolved_proportion is not None:
                master_match, proportion_rule_code, proportion_rule_note = (
                    resolved_proportion
                )
                proportion_resolved = True

        dimension_evidence: DimensionEvidence | None = None
        dimension_master_resolved = False
        if master_match is not None and master_match.ambiguous_shape_codes:
            remaining_master_ambiguity = tuple(master_match.ambiguous_shape_codes)
            resolved = _resolve_dimension_master_ambiguity(
                comparison_points,
                declared_length_cm=declared,
                annotation_handle=selected.handle,
                entities=source_entities,
                original_ambiguous_shape_codes=remaining_master_ambiguity,
                shape_definitions=shape_definitions,
                control_tests=control_tests,
                policy=resolved_policy,
                shape_index=shape_index,
                allowed_dimension_handles=local_allowed_dimension_handles,
                excluded_dimension_handles=local_excluded_dimension_handles,
                exclude_cross_axis_dimensions=legend_probe,
            )
            if resolved is not None:
                master_match, dimension_evidence = resolved
                dimension_master_resolved = True
                # Printed A-E values are the production dimensions only after
                # their MASTER formula reproduces the annotation total.  The
                # raw drawn length remains separately preserved for audit.
                effective_geometry_length = declared
                difference = dimension_evidence.total_difference_cm
        elif master_match is not None:
            restricted_dimension_shape = (
                next(
                    (
                        value
                        for value in shape_definitions
                        if str(value.code).strip().upper()
                        == str(master_match.shape_code).strip().upper()
                    ),
                    None,
                )
                if local_context_resolved or proportion_resolved
                else None
            )
            resolved = _resolve_unique_master_dimensions(
                comparison_points,
                declared_length_cm=declared,
                annotation_handle=selected.handle,
                entities=source_entities,
                master_match=master_match,
                shape_definitions=(restricted_dimension_shape,)
                if restricted_dimension_shape is not None
                else shape_definitions,
                control_tests=control_tests,
                policy=resolved_policy,
                shape_index=(
                    None if restricted_dimension_shape is not None else shape_index
                ),
                allowed_dimension_handles=local_allowed_dimension_handles,
                excluded_dimension_handles=local_excluded_dimension_handles,
                exclude_cross_axis_dimensions=legend_probe,
            )
            if resolved is not None and (
                not proportion_resolved
                or _same_u_proportion_side(
                    comparison_points,
                    resolved[1].segment_lengths_cm,
                    tolerance=resolved_policy.u_subtype_arm_ratio_tolerance,
                )
            ):
                master_match, dimension_evidence = resolved
                effective_geometry_length = declared
                difference = dimension_evidence.total_difference_cm
        scale_factor = master_match.uniform_scale_factor if master_match is not None else None
        schematic_scale = bool(
            contextual_override is None
            and master_match is not None
            and master_match.reference_profile_id is not None
            and scale_factor is not None
            and len(shape) > 1
            and resolved_policy.schematic_scale_min
            <= scale_factor
            <= resolved_policy.schematic_scale_max
            and abs(scale_factor - 1.0)
            > resolved_policy.schematic_scale_exact_tolerance
        )
        if near_gap_bridge is not None:
            if resolved_policy.near_gap_require_master_match and master_match is None:
                _reject(rejection_counts, "ПРАЗНИНА БЕЗ MASTER ФОРМА")
                continue
            if (
                resolved_policy.near_gap_require_exact_length
                and difference
                > resolved_policy.exact_length_tolerance_for_master(
                    declared,
                    getattr(master_match, "shape_code", None),
                )
            ):
                _reject(rejection_counts, "ПРАЗНИНА БЕЗ ТОЧНА L")
                continue
            if schematic_scale:
                _reject(rejection_counts, "ПРАЗНИНА В СХЕМАТИЧЕН МАЩАБ")
                continue
        if difference > max_length_difference and not schematic_scale:
            _reject(rejection_counts, "АБСУРДНА РАЗЛИКА В ДЪЛЖИНАТА")
            continue
        if schematic_scale:
            # The annotation remains the production L; raw DXF length is kept
            # separately for audit and the candidate is forced to manual review.
            effective_geometry_length = declared
            difference = 0.0

        center = _points_center(comparison_points)
        center_distance = min(
            math.hypot(probe[0] - center[0], probe[1] - center[1])
            for probe in probe_points
        )

        layer_match = layer_diameter is not None and abs(layer_diameter - diameter) <= 0.01
        relative_difference = difference / max(abs(declared), 1.0)
        normalized_distance = distance / max(abs(declared), 100.0)
        layer_penalty = 0.0 if layer_match else 0.08
        complexity_penalty = (
            0.0 if master_match is not None else max(0, len(shape) - 1) * 0.02
        )
        shape_bonus = (
            0.20
            if master_match is not None and master_match.reference_profile_id is not None
            else (0.12 if master_match is not None else 0.0)
        )
        master_fit_penalty = (
            max(0.0, float(master_match.score)) * 0.08
            if master_match is not None
            else 0.0
        )
        rule_bonus = 0.06 if contextual_override is not None else 0.0
        provenance_bonus = 10.0 if proxy_parent_provenance else 0.0
        score = (
            relative_difference * 4.0
            + normalized_distance
            + layer_penalty
            + complexity_penalty
            + master_fit_penalty
            + (
                float(resolved_policy.schematic_scale_score_penalty)
                if schematic_scale
                else 0.0
            )
            + (
                float(resolved_policy.near_gap_score_penalty)
                if near_gap_bridge is not None
                else 0.0
            )
            - shape_bonus
            - rule_bonus
            - provenance_bonus
        )

        notes: list[str] = []
        review_reason_codes: list[str] = []
        for primitive in shape:
            for note in primitive.geometry_notes:
                if note not in notes:
                    notes.append(note)
        if source_curve_count:
            semantic_geometry_length = sum(
                (
                    primitive.semantic_length_raw
                    if primitive.semantic_length_raw is not None
                    else primitive.length_raw
                )
                for primitive in shape
            )
            notes.append(
                "Сравнението по L използва точната дъгова дължина "
                f"{_fmt(geometry_length)}; нормализираната топологична "
                f"дължина {_fmt(semantic_geometry_length)} не участва като "
                "производствена L"
            )
        if normalized_bend_count:
            notes.append(
                f"Нормализирани са {normalized_bend_count} допирателни DXF дъги "
                "като инженерни огъвки; оригиналните радиуси са запазени"
            )
        if unresolved_curve_count:
            review_reason_codes.append("UNRESOLVED_CURVE_GEOMETRY")
            notes.append(
                f"{unresolved_curve_count} дъгови сегмента не могат безопасно "
                "да се сведат до праволинейна MASTER топология"
            )
        if "CURVED_CENTERLINE_ALIAS" in layer_roles:
            review_reason_codes.append("CURVED_LAYER_ALIAS")
            notes.append(
                "Слоят е допуснат чрез изричен alias за извита армировъчна ос"
            )
        if hooked_closure:
            review_reason_codes.append("HOOKED_CLOSURE")
            notes.append(
                "Формата е геометрично отворена, но близките противоположни "
                "крайни куки позволяват review-only сравнение със затворена "
                f"MASTER топология (междина {_fmt(hooked_closure_gap or 0.0)})"
            )
        if layer_diameter is None:
            notes.append("Армировъчен слой без кодиран диаметър — Ø остава за ръчна проверка")
        if len(shape) > 1:
            notes.append(f"Съставна геометрия от {len(shape)} свързани DXF обекта")
        if proxy_parent_provenance:
            review_reason_codes.append("PROXY_PARENT_PROVENANCE")
            notes.append(
                "Надписът и геометрията са възстановени от ACAD_PROXY_ENTITY "
                "чрез най-близкия предходен proxy комплект и точна обща L"
            )
        if near_gap_bridge is not None:
            review_reason_codes.append("NEAR_GAP_HEALED")
            notes.append(
                "Контролирано свързана една чертожна празнина "
                f"{_fmt(near_gap_bridge.gap_raw)} между "
                f"{near_gap_bridge.edge_handles[0]} и "
                f"{near_gap_bridge.edge_handles[1]}; строгият допуск "
                f"{_fmt(near_gap_bridge.strict_tolerance_raw)} остава непроменен"
            )
        if fingerprint is not None:
            notes.append("Ориентацията е нормализирана — завъртането и обратната посока не влияят")
        if contextual_override is not None:
            notes.append(contextual_override.note)
            notes.append(
                f"Сурова DXF дължина: {_fmt(geometry_length)}; проектна дължина след правило: {_fmt(effective_geometry_length)}"
            )
        if master_match is not None:
            mirror_note = " (огледално)" if master_match.mirrored else ""
            notes.append(
                f"Съвпадение с MASTER форма {master_match.shape_code} — {master_match.shape_name}{mirror_note}"
            )
            if len(master_match.equivalent_shape_codes) > 1:
                notes.append(
                    "ENGINE еквивалентна група: "
                    + ", ".join(master_match.equivalent_shape_codes)
                    + f"; използван каноничен код {master_match.canonical_shape_code or master_match.shape_code}"
                )
            if master_match.reference_profile_id:
                notes.append(
                    f"Използван контролен профил: {master_match.reference_profile_id}"
                )
            if master_match.required_relation_count:
                relation_error = (
                    "—"
                    if master_match.max_relation_error is None
                    else f"{master_match.max_relation_error:.4f}"
                )
                notes.append(
                    "ENGINE отношенията са изпълнени: "
                    f"{master_match.required_relation_count} задължителни "
                    f"правила, максимална относителна грешка {relation_error}"
                )
                notes.extend(
                    f"ENGINE relation: {item}"
                    for item in master_match.relation_evidence
                )
            if master_match.ambiguous_shape_codes:
                review_reason_codes.append("AMBIGUOUS_MASTER")
                notes.append(
                    "Конфликт между MASTER форми: "
                    + ", ".join(master_match.ambiguous_shape_codes)
                )
            if dimension_evidence is not None:
                prefix = (
                    "MASTER двусмислието е разрешено"
                    if dimension_master_resolved
                    else "Производствените сегменти са потвърдени"
                )
                notes.append(
                    f"{prefix} от отпечатаните размери A–E: "
                    f"{' + '.join(_fmt(value) for value in dimension_evidence.segment_lengths_cm)} "
                    f"= {_fmt(sum(dimension_evidence.segment_lengths_cm))} cm; "
                    f"източници {', '.join(dimension_evidence.text_handles)}"
                )
            if master_match.mirror_requires_review:
                review_reason_codes.append("MIRROR_REVIEW")
                notes.append(
                    "Геометрията е огледална спрямо MASTER шаблона — формата е намерена, но изисква потвърждение"
                )
        f007_exact_extended = bool(
            contextual_override is not None
            and contextual_override.code == "F007_ESOVE_CURVED_CENTERLINE"
            and master_match is not None
            and not master_match.ambiguous_shape_codes
            and difference
            <= resolved_policy.exact_length_tolerance_for_master(
                declared,
                master_match.shape_code,
            )
            and distance <= float(resolved_policy.c_shape_curve_strong_distance_raw)
        )
        if (
            distance > primary_search_distance
            and not f007_exact_extended
            and not proxy_parent_provenance
        ):
            review_reason_codes.append("FAR_ASSOCIATION")
            notes.append(
                f"Далечна връзка над основния радиус ({_fmt(primary_search_distance)}) — изисква ръчен контрол"
            )
        elif distance > primary_search_distance and not proxy_parent_provenance:
            notes.append(
                "Основният радиус е надвишен само в тесния F007 път; "
                "пълните доказателства за слой, дъга, отношение, завой, "
                "обща L и еднозначен MASTER остават задължителни"
            )
        if schematic_scale and scale_factor is not None:
            review_reason_codes.append("SCHEMATIC_SCALE")
            notes.append(
                "Равномерен схематичен мащаб "
                f"{_fmt(scale_factor)}×: DXF L={_fmt(geometry_length)}, "
                f"производствена L={_fmt(declared)}"
            )
        if difference > resolved_policy.exact_length_tolerance_for_master(
            declared,
            getattr(master_match, "shape_code", None),
        ):
            notes.append(f"Числова разлика спрямо L: {_fmt(difference)}")
            if difference <= resolved_policy.review_length_tolerance(declared):
                notes.append("Възможно символично/немащабно изчертаване — изисква визуален контрол")
        if proportion_rule_note:
            notes.append(proportion_rule_note)

        raw_rows.append(
            {
                "handles": handles,
                "layer": layer,
                "segment_count": max(1, (fingerprint.segment_count if fingerprint else len(shape))),
                "geometry_length_raw": geometry_length,
                "effective_geometry_length_cm": effective_geometry_length,
                "declared_length_cm": declared,
                "numeric_difference": difference,
                "raw_numeric_difference": raw_difference,
                "distance_raw": distance,
                "centroid_distance_raw": center_distance,
                "layer_diameter_mm": layer_diameter,
                "diameter_layer_match": layer_match,
                "score": score,
                "basis": basis,
                "notes": tuple(notes),
                "fingerprint": fingerprint,
                "master_match": master_match,
                "dimension_evidence": dimension_evidence,
                "dimension_master_resolved": dimension_master_resolved,
                "dimension_original_ambiguous_shape_codes": original_master_ambiguity,
                "contextual_override": contextual_override,
                "contextual_rule_code": proportion_rule_code,
                "contextual_rule_note": proportion_rule_note,
                "review_reason_codes": tuple(review_reason_codes),
                "schematic_scale": schematic_scale,
                "uniform_scale_factor": scale_factor,
                "near_gap_bridge": near_gap_bridge,
                "geometry_representation": (
                    next(iter(representations))
                    if len(representations) == 1
                    else "MIXED"
                ),
                "geometry_layer_role": (
                    next(iter(layer_roles))
                    if len(layer_roles) == 1
                    else "MIXED"
                ),
                "source_curve_count": source_curve_count,
                "normalized_bend_count": normalized_bend_count,
                "unresolved_curve_count": unresolved_curve_count,
                "source_arc_radii_raw": source_arc_radii,
                "hooked_closure": hooked_closure,
                "hooked_closure_gap_raw": hooked_closure_gap,
                "forced_confidence": (
                    "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА"
                    if proxy_parent_provenance
                    and master_match is not None
                    and not master_match.ambiguous_shape_codes
                    and not master_match.mirror_requires_review
                    and unresolved_curve_count == 0
                    and not schematic_scale
                    and difference
                    <= resolved_policy.exact_length_tolerance_for_master(
                        declared,
                        master_match.shape_code,
                    )
                    else None
                ),
            }
        )

    # F006 is drawn in two approved engineering representations:
    #
    # * six physically separate LINE objects (four sides + two keys); or
    # * a rounded cage (four LINE sides + four quarter ARC corners + two keys).
    #
    # An explicit one-to-one local ``стр.:`` label is stronger semantic
    # evidence than a nearby open U.  In that case the F006 topology search is
    # mandatory even when the generic matcher already found the neighbouring
    # fiba geometry.  If no valid F006 topology is found, the result is kept
    # fail-closed instead of silently borrowing the fiba.
    if local_detail_shape_code == "F006":
        f006_rows = _f006_composite_candidate_rows(
            nearby_primitives=all_primitives,
            probe_points=probe_points,
            declared=declared,
            diameter=diameter,
            shape_definitions=shape_definitions,
            policy=resolved_policy,
            max_rows=max_candidates,
        )
        if resolved_policy.rounded_f006_composite_rule_enabled:
            f006_rows.extend(
                _f006_rounded_composite_candidate_rows(
                    nearby_primitives=all_primitives,
                    probe_points=probe_points,
                    declared=declared,
                    diameter=diameter,
                    shape_definitions=shape_definitions,
                    entities=source_entities,
                    dimension_handles=local_allowed_dimension_handles or (),
                    policy=resolved_policy,
                    max_rows=max_candidates,
                    local_rule_code=local_detail_rule_code,
                    local_rule_note=local_detail_rule_note,
                )
            )
        raw_rows = f006_rows
        if not raw_rows:
            warnings.append(
                "Локалният надпис за стреме е намерен, но няма доказана F006 "
                "геометрия; съседни U-форми са изключени fail-closed."
            )
    elif not raw_rows:
        # Historical fallback for drawings without explicit local labels.
        raw_rows.extend(
            _f006_composite_candidate_rows(
                nearby_primitives=all_primitives,
                probe_points=probe_points,
                declared=declared,
                diameter=diameter,
                shape_definitions=shape_definitions,
                policy=resolved_policy,
                max_rows=max_candidates,
            )
        )

    if resolved_policy.prefer_maximal_master_chain:
        _apply_maximal_master_chain_preference(raw_rows, resolved_policy)

    raw_rows.sort(
        key=lambda row: (
            row["score"],
            row["numeric_difference"],
            row["distance_raw"],
            row["centroid_distance_raw"],
            -row["segment_count"],
            row["handles"],
        )
    )
    raw_rows = raw_rows[: max(1, max_candidates)]

    candidates: list[GeometryCandidate] = []
    for index, row in enumerate(raw_rows):
        next_row = raw_rows[index + 1] if index + 1 < len(raw_rows) else None
        score_gap = (
            math.inf if next_row is None else float(next_row["score"]) - float(row["score"])
        )
        distance_gap = (
            math.inf
            if next_row is None
            else float(next_row["distance_raw"]) - float(row["distance_raw"])
        )
        forced_confidence = row.get("forced_confidence")
        reason_codes = tuple(row.get("review_reason_codes") or ())
        candidate_notes = list(row.get("notes") or ())
        dimension_candidate_tiebreak = _dimension_evidence_candidate_advantage(
            row,
            next_row,
            resolved_policy,
        )
        if dimension_candidate_tiebreak:
            reason_codes = (*reason_codes, "DIMENSION_CANDIDATE_TIEBREAK")
            row["review_reason_codes"] = reason_codes
            candidate_notes.append(
                "Кандидатът е отделен от близката алтернатива чрез пълно "
                "уравнение на размерите A–E към общата L"
            )
        generic_strong_limit = resolved_policy.strong_distance_limit(
            max(float(row.get("declared_length_cm") or 0.0), 1.0)
        )
        contextual_override = row.get("contextual_override")
        contextual_code = str(
            row.get("contextual_rule_code")
            or getattr(contextual_override, "code", "")
            or ""
        ).upper()
        if (
            contextual_code == "F007_ESOVE_CURVED_CENTERLINE"
            and float(row.get("distance_raw") or math.inf) > generic_strong_limit
            and float(row.get("distance_raw") or math.inf)
            <= _strong_distance_limit_for_row(row, resolved_policy)
        ):
            reason_codes = (*reason_codes, "F007_EXACT_EXTENDED_ASSOCIATION")
            row["review_reason_codes"] = reason_codes
            candidate_notes.append(
                "Използван е тесният силен радиус само за напълно доказана "
                "F007 от слой ESove"
            )
        master_match = row.get("master_match")
        declared = max(float(row.get("declared_length_cm") or 0.0), 1.0)
        difference = abs(float(row.get("numeric_difference") or 0.0))
        global_exact = resolved_policy.exact_length_tolerance(declared)
        master_exact = resolved_policy.exact_length_tolerance_for_master(
            declared,
            getattr(master_match, "shape_code", None),
        )
        if difference > global_exact and difference <= master_exact:
            reason_codes = (*reason_codes, "F001_EXTENDED_EXACT_LENGTH")
            row["review_reason_codes"] = reason_codes
            candidate_notes.append(
                "Използван е отделният калибриран допуск за еднозначен "
                f"F001: Δ={_fmt(difference)} cm при допустими "
                f"{_fmt(master_exact)} cm"
            )
        if forced_confidence is None:
            if (
                "NEAR_GAP_HEALED" in reason_codes
                and "AMBIGUOUS_MASTER" in reason_codes
            ):
                forced_confidence = (
                    "КОНТРОЛИРАНА ПРАЗНИНА И КОНФЛИКТ МЕЖДУ MASTER ФОРМИ "
                    "— РЪЧНА ПРОВЕРКА"
                )
            elif "NEAR_GAP_HEALED" in reason_codes:
                forced_confidence = "ЕДНА КОНТРОЛИРАНА ПРАЗНИНА — РЪЧНА ПРОВЕРКА"
            elif "AMBIGUOUS_MASTER" in reason_codes:
                forced_confidence = "КОНФЛИКТ МЕЖДУ MASTER ФОРМИ — РЪЧНА ПРОВЕРКА"
            elif "HOOKED_CLOSURE" in reason_codes:
                master_match = row.get("master_match")
                if (
                    master_match is not None
                    and str(master_match.shape_code).upper() == "F006"
                    and int(row.get("segment_count") or 0) == 6
                    and not master_match.ambiguous_shape_codes
                    and int(row.get("unresolved_curve_count") or 0) == 0
                    and "FAR_ASSOCIATION" not in reason_codes
                ):
                    # This proves the engineering family, not the unfolded
                    # production length.  Keep it outside unattended approval.
                    forced_confidence = F006_HOOKED_FAMILY_CONFIDENCE
                else:
                    forced_confidence = (
                        "ОТВОРЕНИ КУКИ КЪМ ЗАТВОРЕНА MASTER ФОРМА "
                        "— РЪЧНА ПРОВЕРКА"
                    )
            elif "MIRROR_REVIEW" in reason_codes:
                forced_confidence = "ОГЛЕДАЛНА MASTER ФОРМА — РЪЧНА ПРОВЕРКА"
            elif "SCHEMATIC_SCALE" in reason_codes:
                forced_confidence = "СХЕМАТИЧЕН МАЩАБ — РЪЧНА ПРОВЕРКА"
            elif "FAR_ASSOCIATION" in reason_codes:
                forced_confidence = "ДАЛЕЧНА ВРЪЗКА — РЪЧНА ПРОВЕРКА"
            elif "UNRESOLVED_CURVE_GEOMETRY" in reason_codes:
                forced_confidence = "НЕРАЗРЕШЕНА ДЪГОВА ГЕОМЕТРИЯ — РЪЧНА ПРОВЕРКА"
            elif (
                "CURVED_LAYER_ALIAS" in reason_codes
                and not _exact_unique_master_row(
                    row,
                    next_row,
                    resolved_policy,
                )
            ):
                forced_confidence = (
                    "СПЕЦИАЛИЗИРАН СЛОЙ ЗА ИЗВИТА ОС — РЪЧНА ПРОВЕРКА"
                )
        confidence = str(forced_confidence or _confidence_for(row, next_row, resolved_policy))
        row["notes"] = tuple(candidate_notes)
        fingerprint: ShapeFingerprint | None = row["fingerprint"]
        master_match: ShapeMatch | None = row["master_match"]
        candidates.append(
            GeometryCandidate(
                rank=index + 1,
                handles=row["handles"],
                layer=row["layer"],
                segment_count=row["segment_count"],
                geometry_length_raw=row["geometry_length_raw"],
                declared_length_cm=row["declared_length_cm"],
                numeric_difference=row["numeric_difference"],
                distance_raw=row["distance_raw"],
                layer_diameter_mm=row["layer_diameter_mm"],
                diameter_layer_match=row["diameter_layer_match"],
                score=row["score"],
                score_gap=None if math.isinf(score_gap) else score_gap,
                distance_gap_raw=None if math.isinf(distance_gap) else distance_gap,
                confidence=confidence,
                basis=row["basis"],
                notes=row["notes"],
                centroid_distance_raw=row["centroid_distance_raw"],
                canonical_signature=fingerprint.canonical_signature if fingerprint else None,
                orientation_invariant=True,
                reversed_direction_equivalent=resolved_policy.allow_reversed_direction,
                master_shape_code=master_match.shape_code if master_match else None,
                master_shape_name=master_match.shape_name if master_match else None,
                master_shape_revision=master_match.shape_revision if master_match else None,
                master_shape_mirrored=master_match.mirrored if master_match else False,
                master_shape_reversed=master_match.reversed_direction if master_match else False,
                master_shape_score=master_match.score if master_match else None,
                master_reference_profile_id=(
                    master_match.reference_profile_id if master_match else None
                ),
                master_ambiguous_shape_codes=(
                    master_match.ambiguous_shape_codes if master_match else ()
                ),
                uniform_scale_factor=row.get("uniform_scale_factor"),
                mirror_requires_review=(
                    master_match.mirror_requires_review if master_match else False
                ),
                review_reason_codes=reason_codes,
                effective_geometry_length_cm=row.get("effective_geometry_length_cm"),
                raw_numeric_difference=row.get("raw_numeric_difference"),
                contextual_rule_code=(
                    row.get("contextual_rule_code")
                    or (row["contextual_override"].code if row.get("contextual_override") else None)
                ),
                contextual_rule_note=(
                    row.get("contextual_rule_note")
                    or (row["contextual_override"].note if row.get("contextual_override") else None)
                ),
                near_gap_healed=row.get("near_gap_bridge") is not None,
                near_gap_bridge_gap_raw=(
                    row["near_gap_bridge"].gap_raw
                    if row.get("near_gap_bridge") is not None
                    else None
                ),
                near_gap_bridge_handles=(
                    row["near_gap_bridge"].edge_handles
                    if row.get("near_gap_bridge") is not None
                    else ()
                ),
                near_gap_bridge_points=(
                    row["near_gap_bridge"].points
                    if row.get("near_gap_bridge") is not None
                    else ()
                ),
                canonical_segment_lengths_cm=(
                    tuple(row.get("canonical_segment_lengths_cm") or ())
                    or (
                        tuple(row["dimension_evidence"].segment_lengths_cm)
                        if row.get("dimension_evidence") is not None
                        else ()
                    )
                    or (
                        row["contextual_override"].segment_lengths_cm
                        if row.get("contextual_override")
                        else ()
                    )
                ),
                canonical_turn_angles_deg=(
                    tuple(row.get("canonical_turn_angles_deg") or ())
                    or (
                        row["contextual_override"].turn_angles_deg
                        if row.get("contextual_override")
                        else ()
                    )
                ),
                geometry_representation=row.get("geometry_representation"),
                geometry_layer_role=row.get("geometry_layer_role"),
                source_curve_count=int(row.get("source_curve_count") or 0),
                normalized_bend_count=int(row.get("normalized_bend_count") or 0),
                unresolved_curve_count=int(row.get("unresolved_curve_count") or 0),
                source_arc_radii_raw=tuple(
                    row.get("source_arc_radii_raw") or ()
                ),
                hooked_closure=bool(row.get("hooked_closure")),
                hooked_closure_gap_raw=row.get("hooked_closure_gap_raw"),
                dimension_evidence_applied=row.get("dimension_evidence") is not None,
                dimension_master_resolved=bool(row.get("dimension_master_resolved")),
                dimension_original_ambiguous_shape_codes=tuple(
                    row.get("dimension_original_ambiguous_shape_codes") or ()
                ),
                dimension_text_handles=(
                    tuple(row["dimension_evidence"].text_handles)
                    if row.get("dimension_evidence") is not None
                    else ()
                ),
                dimension_resolution_strategy=(
                    row["dimension_evidence"].strategy
                    if row.get("dimension_evidence") is not None
                    else None
                ),
                dimension_total_difference_cm=(
                    row["dimension_evidence"].total_difference_cm
                    if row.get("dimension_evidence") is not None
                    else None
                ),
                local_f006_printed_dimension_handles=tuple(
                    row.get("local_f006_printed_dimension_handles") or ()
                ),
            )
        )

    if not candidates:
        status = "НЯМА КАНДИДАТИ СЛЕД ПРИЛАГАНЕ НА ЗАКОНИТЕ"
    elif candidates[0].confidence in {
        "СИЛЕН КАНДИДАТ — НЕОДОБРЕН",
        "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА",
        F006_STRONG_CONFIDENCE,
        F006_HOOKED_FAMILY_CONFIDENCE,
    }:
        status = "НАМЕРЕН Е СИЛЕН КАНДИДАТ — ИЗИСКВА ВИЗУАЛНА ПРОВЕРКА"
    else:
        status = "ИМА КАНДИДАТИ ЗА РЪЧНА ПРОВЕРКА"

    warnings.append(
        "Това е ранжиране, не одобрена връзка. Позиция, стъпка и спецификация не се извеждат."
    )
    warnings.extend(resolved_policy.laws_bg())
    return GeometryCandidateAnalysis(
        annotation_handle=selected.handle,
        label=selected.text or "",
        diameter_mm=parsed.diameter_mm,
        declared_length_cm=parsed.length_cm,
        candidates=tuple(candidates),
        status=status,
        warnings=tuple(warnings),
        rejection_counts=dict(sorted(rejection_counts.items())),
        policy=resolved_policy.to_dict(),
    )


def _resolve_unique_master_dimensions(
    points: Sequence[Point2D],
    *,
    declared_length_cm: float,
    annotation_handle: str,
    entities: Sequence[DxfEntityRecord],
    master_match: ShapeMatch,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex | None,
    allowed_dimension_handles: Iterable[str] | None = None,
    excluded_dimension_handles: Iterable[str] = (),
    exclude_cross_axis_dimensions: bool = False,
) -> tuple[ShapeMatch, DimensionEvidence] | None:
    """Apply A-E to a unique family only when the same owner stays unique."""

    code = master_match.shape_code.strip().upper()
    shape = next(
        (
            value
            for value in shape_definitions
            if value.code.strip().upper() == code
        ),
        None,
    )
    if shape is None:
        return None
    evidence_rows = dimension_evidence_for_shape(
        points,
        shape,
        entities,
        declared_length_cm=declared_length_cm,
        excluded_handles=(annotation_handle, *tuple(excluded_dimension_handles)),
        allowed_handles=allowed_dimension_handles,
        exclude_cross_axis_dimensions=exclude_cross_axis_dimensions,
    )
    resolved_rows: list[tuple[float, ShapeMatch, DimensionEvidence]] = []
    for evidence in evidence_rows[:8]:
        dimension_points = points_with_segment_lengths(
            points,
            evidence.segment_lengths_cm,
        )
        if dimension_points is None:
            continue
        dimension_match = match_master_shapes(
            dimension_points,
            shape_definitions,
            policy=policy,
            control_tests=control_tests,
            shape_index=shape_index,
            candidate_closed_override=False,
        )
        if (
            dimension_match is None
            or dimension_match.ambiguous_shape_codes
            or dimension_match.shape_code.strip().upper() != code
        ):
            continue
        resolved_rows.append(
            (
                evidence.score + max(0.0, float(dimension_match.score)) * 5.0,
                dimension_match,
                evidence,
            )
        )
    return _unique_dimension_resolution(resolved_rows)


def _resolve_local_named_u_subtype(
    points: Sequence[Point2D],
    *,
    target_shape_code: str,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
) -> ShapeMatch | None:
    """Resolve an overlapping U family from a one-to-one local label.

    F003 and F005 intentionally share the same three-segment U topology.  A
    generic geometric matcher therefore cannot decide the engineering
    identity on topology alone.  The local detail resolver supplies stronger
    evidence: the explicit word ``фиби`` or ``отворен детайл`` was paired
    one-to-one with this exact full annotation before matching.  We still
    require the target MASTER definition to accept the candidate points; the
    label never creates a geometry or bypasses topology validation.
    """

    code = str(target_shape_code or "").strip().upper()
    if code not in {"F003", "F005"}:
        return None
    target = next(
        (
            shape
            for shape in shape_definitions
            if shape.active and str(shape.code).strip().upper() == code
        ),
        None,
    )
    if target is None:
        return None
    resolved = match_master_shapes(
        points,
        (target,),
        policy=policy,
        control_tests=control_tests,
        shape_index=None,
        candidate_closed_override=False,
        enforce_instance_collisions=False,
    )
    if (
        resolved is None
        or resolved.ambiguous_shape_codes
        or str(resolved.shape_code).strip().upper() != code
    ):
        return None
    return resolved


def _u_proportion_side(
    arm: float,
    middle: float,
    *,
    tolerance: float,
) -> int:
    """-1: рамене под лентата (винкели); +1: над нея (фиба); 0: в лентата."""

    if middle <= 1e-9 or arm <= 1e-9:
        return 0
    if arm > middle * (1.0 + tolerance):
        return 1
    if arm < middle * (1.0 - tolerance):
        return -1
    return 0


def _same_u_proportion_side(
    points: Sequence[Point2D],
    printed_segment_lengths: Sequence[float],
    *,
    tolerance: float,
) -> bool:
    """Печатните коти трябва да са на страната на начертаната пропорция."""

    drawn = [
        math.dist(points[index], points[index + 1])
        for index in range(len(points) - 1)
    ]
    printed = [float(value) for value in printed_segment_lengths]
    if len(drawn) != 3 or len(printed) != 3:
        return False
    resolved_tolerance = max(0.0, float(tolerance))
    drawn_sides = (
        _u_proportion_side(drawn[0], drawn[1], tolerance=resolved_tolerance),
        _u_proportion_side(drawn[2], drawn[1], tolerance=resolved_tolerance),
    )
    printed_sides = (
        _u_proportion_side(printed[0], printed[1], tolerance=resolved_tolerance),
        _u_proportion_side(printed[2], printed[1], tolerance=resolved_tolerance),
    )
    return (
        drawn_sides[0] != 0
        and drawn_sides[0] == drawn_sides[1]
        and drawn_sides == printed_sides
    )


def _resolve_declared_u_subtype_by_proportion(
    points: Sequence[Point2D],
    *,
    master_match: ShapeMatch,
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
) -> tuple[ShapeMatch, str, str] | None:
    """Owner-approved proportion rule for an ENGINE-declared distinct U pair.

    Активира се само когато ВСИЧКИ условия идват от самия ENGINE:

    * конфликтът е точно между две форми, които взаимно се декларират в
      ``recognition.distinct_from`` (v0.3.13+);
    * всяка форма декларира противоположна
      ``recognition.u_outer_arms_relation``: F003 изисква и двете
      външни рамена да са по-къси от средния сегмент, а F005 -- и двете
      да са по-дълги.

    Инженерното правило на собственика (2026-08-06): при прът с два винкела
    крайните рамене са по-къси от средната част (25-400-25); при фиба
    и двете рамена са по-дълги от средния сегмент, без изискване да са
    равни (90-18-50). Смесена посока или рамо в толерансната лента оставя
    конфликта видим. Правилото не ползва име на чертеж, позиция, рамка или
    текст -- само конкретните сегментни пропорции срещу декларацията на
    ENGINE.
    """

    codes = {
        str(value).strip().upper()
        for value in master_match.ambiguous_shape_codes
        if str(value).strip()
    }
    if len(codes) != 2:
        return None
    by_code = {
        shape.code.strip().upper(): shape
        for shape in shape_definitions
        if shape.code.strip().upper() in codes
    }
    if len(by_code) != 2:
        return None

    def _recognition(shape: ShapeDefinition) -> Mapping[str, Any]:
        recognition = shape.recognition
        return recognition if isinstance(recognition, Mapping) else {}

    def _declares_distinct(owner: ShapeDefinition, other: ShapeDefinition) -> bool:
        listed = _recognition(owner).get("distinct_from")
        if not isinstance(listed, (list, tuple)):
            return False
        return other.code.strip().upper() in {
            str(value).strip().upper() for value in listed
        }

    first_code, second_code = sorted(by_code)
    first, second = by_code[first_code], by_code[second_code]
    if not (_declares_distinct(first, second) and _declares_distinct(second, first)):
        return None
    shorter_relation = "BOTH_SHORTER_THAN_MIDDLE"
    longer_relation = "BOTH_LONGER_THAN_MIDDLE"
    relation_by_code = {
        code: str(_recognition(shape).get("u_outer_arms_relation") or "")
        .strip()
        .upper()
        for code, shape in by_code.items()
    }
    if set(relation_by_code.values()) != {shorter_relation, longer_relation}:
        # Backward compatibility for Engine v0.3.13: its repeated L1 code
        # declared the same older intent. Engine v0.3.14 no longer relies on
        # equality and carries the explicit two-arm relation above.
        equal_arm_codes = [
            code
            for code, shape in by_code.items()
            if bool(_recognition(shape).get("requires_equal_end_arms"))
        ]
        if len(equal_arm_codes) != 1:
            return None
        relation_by_code = {
            code: (
                longer_relation
                if code == equal_arm_codes[0]
                else shorter_relation
            )
            for code in by_code
        }

    segments = [
        math.dist(points[index], points[index + 1])
        for index in range(len(points) - 1)
    ]
    if len(segments) != 3:
        return None
    middle = segments[1]
    if middle <= 1e-9 or segments[0] <= 1e-9 or segments[2] <= 1e-9:
        return None
    tolerance = max(0.0, float(policy.u_subtype_arm_ratio_tolerance))
    outer_sides = (
        _u_proportion_side(segments[0], middle, tolerance=tolerance),
        _u_proportion_side(segments[2], middle, tolerance=tolerance),
    )
    if outer_sides == (1, 1):
        target_relation = longer_relation
    elif outer_sides == (-1, -1):
        target_relation = shorter_relation
    else:
        return None
    target_codes = [
        code
        for code, declared_relation in relation_by_code.items()
        if declared_relation == target_relation
    ]
    if len(target_codes) != 1:
        return None
    target_code = target_codes[0]

    target_shape = by_code[target_code]
    resolved = match_master_shapes(
        points,
        (target_shape,),
        policy=policy,
        control_tests=control_tests,
        shape_index=EngineShapeIndex.from_shapes((target_shape,)),
    )
    if resolved is None or resolved.shape_code.strip().upper() != target_code:
        return None
    rule_code = f"U_PROPORTION_{target_code}"
    relation = ">" if target_relation == longer_relation else "<"
    note = (
        f"{rule_code}: рамене {_fmt(segments[0])}/{_fmt(segments[2])} "
        f"{relation} средно {_fmt(middle)} при допуск "
        f"{tolerance:.0%} — ENGINE декларацията distinct_from/"
        f"u_outer_arms_relation избира {target_code}; смесените и "
        "граничните пропорции остават конфликт"
    )
    return resolved, rule_code, note


def _resolve_dimension_master_ambiguity(
    points: Sequence[Point2D],
    *,
    declared_length_cm: float,
    annotation_handle: str,
    entities: Sequence[DxfEntityRecord],
    original_ambiguous_shape_codes: Sequence[str],
    shape_definitions: Sequence[ShapeDefinition],
    control_tests: Sequence[Mapping[str, Any]],
    policy: RecognitionPolicy,
    shape_index: EngineShapeIndex | None,
    allowed_dimension_handles: Iterable[str] | None = None,
    excluded_dimension_handles: Iterable[str] = (),
    exclude_cross_axis_dimensions: bool = False,
) -> tuple[ShapeMatch, DimensionEvidence] | None:
    """Use strict printed A-E values only when they produce one MASTER owner."""

    ambiguous = {
        str(value).strip().upper()
        for value in original_ambiguous_shape_codes
        if str(value).strip()
    }
    if len(ambiguous) < 2:
        return None
    shapes_by_code = {
        shape.code.strip().upper(): shape
        for shape in shape_definitions
        if shape.code.strip().upper() in ambiguous
    }
    if len(shapes_by_code) < 2:
        return None

    evidence_rows: list[DimensionEvidence] = []
    for code in sorted(shapes_by_code):
        evidence_rows.extend(
            dimension_evidence_for_shape(
                points,
                shapes_by_code[code],
                entities,
                declared_length_cm=declared_length_cm,
                excluded_handles=(annotation_handle, *tuple(excluded_dimension_handles)),
                allowed_handles=allowed_dimension_handles,
                exclude_cross_axis_dimensions=exclude_cross_axis_dimensions,
            )[:8]
        )
    if not evidence_rows:
        return None

    relation_tolerance = min(float(policy.length_ratio_tolerance), 0.05)
    resolved_rows: list[tuple[float, ShapeMatch, DimensionEvidence]] = []
    seen: set[tuple[str, tuple[float, ...], tuple[str, ...]]] = set()
    for evidence in evidence_rows:
        dimension_points = points_with_segment_lengths(
            points,
            evidence.segment_lengths_cm,
        )
        if dimension_points is None:
            continue
        # A-E evidence may resolve an initial MASTER ambiguity only when the
        # proven production segment vector is fully accepted by EXACTLY ONE
        # of the originally ambiguous identities (each shape's own required
        # relations).  15+295+15=325 proves a symmetric U, not which of two
        # overlapping identities (F003 over F005) owns the bar: both accept
        # that vector, so no dimension text may erase the conflict.  A vector
        # with provably unequal arms is rejected by F005's repeated-code rule
        # and legitimately resolves to F003.
        accepting_codes = [
            code
            for code, candidate_shape in shapes_by_code.items()
            if match_shape_length_relations(
                candidate_shape,
                evidence.segment_lengths_cm,
                default_relative_tolerance=relation_tolerance,
                allow_reverse=policy.allow_reversed_direction,
            ).compatible
        ]
        if len(accepting_codes) != 1:
            continue
        dimension_match = match_master_shapes(
            dimension_points,
            shape_definitions,
            policy=policy,
            control_tests=control_tests,
            shape_index=shape_index,
            candidate_closed_override=False,
            # The single-accepting-identity gate above already proved which
            # engineering identity owns the vector; the re-run only rebuilds
            # the concrete ShapeMatch, so the mandatory instance-collision
            # expansion may step aside here.  The resolution stays auditable
            # through dimension_master_resolved / dimension_original_
            # ambiguous_shape_codes / dimension_text_handles.
            enforce_instance_collisions=False,
        )
        if (
            dimension_match is None
            or dimension_match.ambiguous_shape_codes
            or dimension_match.shape_code.strip().upper() not in ambiguous
            or dimension_match.shape_code.strip().upper() != accepting_codes[0]
        ):
            continue
        forward = tuple(round(value, 3) for value in evidence.segment_lengths_cm)
        canonical_lengths = min(forward, tuple(reversed(forward)))
        key = (
            dimension_match.shape_code.strip().upper(),
            canonical_lengths,
            tuple(sorted(evidence.text_handles)),
        )
        if key in seen:
            continue
        seen.add(key)
        combined_score = evidence.score + max(0.0, float(dimension_match.score)) * 5.0
        resolved_rows.append((combined_score, dimension_match, evidence))

    return _unique_dimension_resolution(resolved_rows)


def _unique_dimension_resolution(
    resolved_rows: Sequence[tuple[float, ShapeMatch, DimensionEvidence]],
) -> tuple[ShapeMatch, DimensionEvidence] | None:
    if not resolved_rows:
        return None
    ordered = sorted(
        resolved_rows,
        key=lambda row: (
            row[0],
            row[1].shape_code.upper(),
            tuple(round(value, 6) for value in row[2].segment_lengths_cm),
        ),
    )
    best_score, best_match, best_evidence = ordered[0]
    best_lengths = min(
        tuple(round(value, 3) for value in best_evidence.segment_lengths_cm),
        tuple(reversed(tuple(round(value, 3) for value in best_evidence.segment_lengths_cm))),
    )
    for other_score, other_match, other_evidence in ordered[1:]:
        if other_score - best_score > 8.0:
            break
        other_lengths_forward = tuple(
            round(value, 3) for value in other_evidence.segment_lengths_cm
        )
        other_lengths = min(
            other_lengths_forward,
            tuple(reversed(other_lengths_forward)),
        )
        if (
            other_match.shape_code.strip().upper()
            != best_match.shape_code.strip().upper()
            or other_lengths != best_lengths
        ):
            return None
    return best_match, best_evidence


def _apply_maximal_master_chain_preference(
    rows: list[dict],
    policy: RecognitionPolicy,
) -> None:
    """Keep partial paths visible, but prefer a recognized complete chain.

    The parent may itself expose a legitimate MASTER ambiguity.  That is still
    safer than promoting one tested child segment as if it were the complete
    reinforcement bar; the parent is routed to Review Center for resolution.
    """

    for row in rows:
        handles = set(row.get("handles") or ())
        if not handles:
            continue
        parents = [
            parent
            for parent in rows
            if parent is not row
            and parent.get("master_match") is not None
            and handles < set(parent.get("handles") or ())
            and int(parent.get("segment_count") or 0)
            > int(row.get("segment_count") or 0)
        ]
        if not parents:
            continue
        parent = min(
            parents,
            key=lambda value: (
                float(value.get("score") or 0.0),
                -int(value.get("segment_count") or 0),
            ),
        )
        missing = len(set(parent.get("handles") or ()) - handles)
        row["score"] = float(row.get("score") or 0.0) + float(
            policy.subset_chain_score_penalty
        ) + missing * 0.03
        notes = list(row.get("notes") or ())
        notes.append(
            "Непълна част от по-дълга разпозната MASTER верига — "
            "пълната форма е с предимство"
        )
        row["notes"] = tuple(notes)
        reason_codes = list(row.get("review_reason_codes") or ())
        if "PARTIAL_MASTER_CHAIN" not in reason_codes:
            reason_codes.append("PARTIAL_MASTER_CHAIN")
        row["review_reason_codes"] = tuple(reason_codes)



def _f006_composite_candidate_rows(
    *,
    nearby_primitives: Sequence[GeometryPrimitive],
    probe_points: Sequence[Point2D],
    declared: float,
    diameter: float,
    shape_definitions: Sequence[ShapeDefinition],
    policy: RecognitionPolicy,
    max_rows: int,
) -> list[dict]:
    """Return strict rectangle-plus-two-keys F006 candidates.

    The x-value in beam stirrup labels such as ``11N8x130`` is not a reliable
    unfolded bar length. For this topology the measured DXF geometry is preserved
    separately and the annotation parameter is not used as a hard length gate.
    Candidates are routed to a dedicated sampled Review Center queue.
    """

    master = next(
        (
            shape
            for shape in shape_definitions
            if shape.active
            and str(shape.state).upper() != "RETIRED"
            and str(shape.code).upper() == "F006"
            and bool(shape.closed)
        ),
        None,
    )
    if master is None or not probe_points:
        return []

    composite_search_radius = max(120.0, float(policy.max_search_distance_raw) * 1.75)
    lines = [
        primitive
        for primitive in nearby_primitives
        if primitive.entity_type.upper() == "LINE"
        and len(primitive.segments) == 1
        and primitive.handle
        and min(
            _distance_point_to_segments(probe, primitive.segments)
            for probe in probe_points
        ) <= composite_search_radius
    ]
    lines.sort(
        key=lambda primitive: (
            min(
                _distance_point_to_segments(probe, primitive.segments)
                for probe in probe_points
            ),
            primitive.handle,
        )
    )
    # Keep the search bounded. Eighteen nearby ARM lines give 18,564 six-line
    # combinations, while still covering the real Alfa stirrup neighbourhoods.
    lines = lines[:18]
    if len(lines) < 6:
        return []

    rows: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    tolerance = max(float(policy.endpoint_tolerance_raw), 1e-6)
    for group in combinations(lines, 6):
        layers = {primitive.layer for primitive in group}
        if len(layers) != 1:
            continue
        layer = next(iter(layers))
        layer_diameter = _diameter_from_layer(layer)
        if (
            policy.reject_explicit_diameter_mismatch
            and layer_diameter is not None
            and abs(layer_diameter - diameter) > 0.01
        ):
            continue

        segments = tuple(
            ((segment[0], segment[1]), (segment[2], segment[3]))
            for primitive in group
            for segment in primitive.segments
        )
        inference = infer_f006_composite_segments(segments, tolerance=tolerance)
        if inference is None:
            continue
        handles = tuple(sorted(primitive.handle for primitive in group))
        if handles in seen:
            continue
        seen.add(handles)

        distance = min(
            _distance_point_to_segments(probe, primitive.segments)
            for probe in probe_points
            for primitive in group
        )
        center = _points_center(inference.loop_points)
        center_distance = min(
            math.hypot(probe[0] - center[0], probe[1] - center[1])
            for probe in probe_points
        )
        total_length = float(sum(inference.segment_lengths))
        raw_difference = abs(total_length - declared)
        layer_match = layer_diameter is not None and abs(layer_diameter - diameter) <= 0.01
        normalized_distance = distance / max(abs(declared), 100.0)
        fit_penalty = min(1.0, float(inference.fit_score) / max(total_length, 1.0))
        layer_penalty = 0.0 if layer_match else 0.08
        score = normalized_distance + fit_penalty * 0.35 + layer_penalty - 0.20
        ratios = tuple(value / max(total_length, 1e-9) for value in inference.segment_lengths)
        fingerprint = ShapeFingerprint(
            segment_count=6,
            closed=True,
            length_ratios=ratios,
            turn_angles_deg=tuple(inference.turn_angles),
            canonical_signature=inference.signature,
        )
        master_match = ShapeMatch(
            shape_code=master.code,
            shape_name=master.name,
            shape_revision=master.revision,
            mirrored=False,
            reversed_direction=False,
            max_angle_error_deg=0.0,
            max_length_ratio_error=None,
            score=float(inference.fit_score),
            confidence="СТРОГО СЪВПАДЕНИЕ С F006 ТОПОЛОГИЯ",
        )
        note = (
            "Автоматично разпознато съставно стреме F006: четири страни на "
            "правоъгълен контур и два отделени ключа. Стойността след x в надписа "
            "не е използвана като разгъната дължина; реалната L е измерена от DXF."
        )
        rows.append(
            {
                "handles": handles,
                "layer": layer,
                "segment_count": 6,
                "geometry_length_raw": total_length,
                "effective_geometry_length_cm": total_length,
                "declared_length_cm": declared,
                # Dedicated F006 sampling, not numeric equality, controls approval.
                "numeric_difference": 0.0,
                "raw_numeric_difference": raw_difference,
                "distance_raw": distance,
                "centroid_distance_raw": center_distance,
                "layer_diameter_mm": layer_diameter,
                "diameter_layer_match": layer_match,
                "score": score,
                "basis": "СЪСТАВНО СТРЕМЕ ОТ 6 ОТДЕЛНИ LINE ОБЕКТА",
                "notes": (note, inference.note),
                "fingerprint": fingerprint,
                "master_match": master_match,
                "contextual_override": None,
                "contextual_rule_code": F006_CONTEXTUAL_RULE_CODE,
                "contextual_rule_note": note,
                "canonical_segment_lengths_cm": tuple(inference.segment_lengths),
                "canonical_turn_angles_deg": tuple(inference.turn_angles),
                "forced_confidence": F006_STRONG_CONFIDENCE,
            }
        )

    rows.sort(
        key=lambda row: (
            row["score"],
            row["distance_raw"],
            row["centroid_distance_raw"],
            row["handles"],
        )
    )
    return rows[: max(1, int(max_rows))]


def _f006_rounded_composite_candidate_rows(
    *,
    nearby_primitives: Sequence[GeometryPrimitive],
    probe_points: Sequence[Point2D],
    declared: float,
    diameter: float,
    shape_definitions: Sequence[ShapeDefinition],
    entities: Sequence[DxfEntityRecord],
    dimension_handles: Sequence[str],
    policy: RecognitionPolicy,
    max_rows: int,
    local_rule_code: str | None,
    local_rule_note: str | None,
) -> list[dict]:
    """Recognize a rounded rectangular F006 cage plus two symbolic keys.

    The designer used by the recovery-5.3 fixture draws the stirrup as four
    straight tangent runs, four separate quarter-circle ARC objects and two
    diagonal hook/key lines.  The generic connected-chain matcher correctly
    refuses this ten-object drawing because the keys attach to the middle of
    sides and the ARC primitives are not ordinary straight segments.  This
    resolver validates the engineering topology explicitly and derives the
    production six-segment vector only from locally-owned printed dimensions.
    """

    master = next(
        (
            shape
            for shape in shape_definitions
            if shape.active
            and str(shape.state).upper() != "RETIRED"
            and str(shape.code).upper() == "F006"
            and bool(shape.closed)
        ),
        None,
    )
    if master is None or not probe_points:
        return []

    search_radius = max(
        float(policy.rounded_f006_search_distance_raw),
        float(policy.max_search_distance_raw),
    )
    nearby = [
        primitive
        for primitive in nearby_primitives
        if primitive.handle
        and primitive.entity_type.upper() in {"LINE", "ARC"}
        and min(
            _distance_point_to_segments(probe, primitive.segments)
            for probe in probe_points
        )
        <= search_radius
    ]
    if len(nearby) < 10:
        return []

    # The closed rounded cage is one endpoint-connected component.  Hook/key
    # lines touch side interiors, not the cage endpoints, and stay outside the
    # component by construction.
    components = _primitive_endpoint_components(
        nearby,
        tolerance=max(float(policy.endpoint_tolerance_raw), 1e-6),
    )
    rows: list[dict] = []
    entity_map = {str(entity.handle): entity for entity in entities}
    for component in components:
        loop = tuple(component)
        if len(loop) != 8:
            continue
        line_sides = tuple(
            primitive for primitive in loop if primitive.entity_type.upper() == "LINE"
        )
        arc_corners = tuple(
            primitive for primitive in loop if primitive.entity_type.upper() == "ARC"
        )
        if len(line_sides) != 4 or len(arc_corners) != 4:
            continue
        layers = {primitive.layer for primitive in loop}
        if len(layers) != 1:
            continue
        layer = next(iter(layers))
        layer_diameter = _diameter_from_layer(layer)
        if (
            policy.reject_explicit_diameter_mismatch
            and layer_diameter is not None
            and abs(layer_diameter - diameter) > 0.01
        ):
            continue
        loop_validation = _validate_rounded_f006_loop(
            loop,
            policy=policy,
        )
        if loop_validation is None:
            continue
        orientation_a, orientation_b, loop_center = loop_validation

        loop_handles = {primitive.handle for primitive in loop}
        keys = _rounded_f006_key_pair(
            nearby,
            loop=loop,
            loop_handles=loop_handles,
            layer=layer,
            side_orientations=(orientation_a, orientation_b),
            policy=policy,
        )
        if keys is None:
            continue

        printed = _rounded_f006_printed_segments(
            declared=declared,
            dimension_handles=dimension_handles,
            entity_map=entity_map,
            loop=loop,
        )
        if printed is None:
            # Topology alone proves the family but not the production request.
            # This version deliberately remains fail-closed when the local
            # width/height/hook equation cannot be recovered uniquely.
            continue
        canonical, dimension_text_handles, printed_difference = printed
        handles = tuple(
            sorted(
                [primitive.handle for primitive in loop]
                + [primitive.handle for primitive in keys]
            )
        )
        all_parts = (*loop, *keys)
        distance = min(
            _distance_point_to_segments(probe, primitive.segments)
            for probe in probe_points
            for primitive in all_parts
        )
        center_distance = min(
            math.hypot(probe[0] - loop_center[0], probe[1] - loop_center[1])
            for probe in probe_points
        )
        raw_total = float(sum(primitive.length_raw for primitive in all_parts))
        raw_difference = abs(raw_total - declared)
        layer_match = layer_diameter is not None and abs(layer_diameter - diameter) <= 0.01
        production_total = float(sum(canonical))
        ratios = tuple(value / max(production_total, 1e-9) for value in canonical)
        signature = (
            "C|F006_ROUNDED_COMPOSITE|n=6|r="
            + ",".join(f"{value:.5f}" for value in ratios)
            + "|a=90.00,90.00,90.00,90.00"
        )
        fingerprint = ShapeFingerprint(
            segment_count=6,
            closed=True,
            length_ratios=ratios,
            turn_angles_deg=(90.0, 90.0, 90.0, 90.0),
            canonical_signature=signature,
        )
        master_match = ShapeMatch(
            shape_code=master.code,
            shape_name=master.name,
            shape_revision=master.revision,
            mirrored=False,
            reversed_direction=False,
            max_angle_error_deg=0.0,
            max_length_ratio_error=None,
            score=0.0,
            confidence="СТРОГО СЪВПАДЕНИЕ СЪС ЗАКРЪГЛЕНА F006 ТОПОЛОГИЯ",
        )
        hook = canonical[0]
        width = max(canonical[1], canonical[2])
        height = min(canonical[1], canonical[2])
        equation_note = (
            "F006_ROUNDED_PRINTED_MASTER_EQUATION: "
            f"2×({_fmt(width)}+{_fmt(height)}+{_fmt(hook)}) = "
            f"{_fmt(production_total)} cm при заявена L={_fmt(declared)} cm; "
            f"източници {', '.join(dimension_text_handles)}."
        )
        topology_note = (
            "Автоматично разпознато закръглено съставно стреме F006: "
            "четири tangent LINE страни, четири четвърт-дъги и два "
            "паралелни символични ключа. Дъгите са топологични огъвки; "
            "производствените размери идват само от локалните печатни коти."
        )
        notes = [topology_note, equation_note]
        if local_rule_note:
            notes.insert(0, local_rule_note)
        score = (
            distance / max(abs(declared), 100.0)
            + (0.0 if layer_match else 0.08)
            + printed_difference / max(abs(declared), 1.0)
            - 0.34
        )
        rows.append(
            {
                "handles": handles,
                "layer": layer,
                "segment_count": 6,
                "geometry_length_raw": raw_total,
                "effective_geometry_length_cm": production_total,
                "declared_length_cm": declared,
                "numeric_difference": printed_difference,
                "raw_numeric_difference": raw_difference,
                "distance_raw": distance,
                "centroid_distance_raw": center_distance,
                "layer_diameter_mm": layer_diameter,
                "diameter_layer_match": layer_match,
                "score": score,
                "basis": "СЪСТАВНО ЗАКРЪГЛЕНО СТРЕМЕ ОТ 4 LINE + 4 ARC + 2 КЛЮЧА",
                "notes": tuple(notes),
                "fingerprint": fingerprint,
                "master_match": master_match,
                "dimension_evidence": None,
                "dimension_master_resolved": False,
                "dimension_original_ambiguous_shape_codes": (),
                "contextual_override": None,
                "contextual_rule_code": (
                    local_rule_code or "F006_ROUNDED_COMPOSITE_STIRRUP"
                ),
                "contextual_rule_note": local_rule_note or topology_note,
                "canonical_segment_lengths_cm": tuple(canonical),
                "canonical_turn_angles_deg": (90.0, 90.0, 90.0, 90.0),
                "review_reason_codes": (),
                "schematic_scale": False,
                "uniform_scale_factor": None,
                "near_gap_bridge": None,
                "geometry_representation": GeometryRepresentation.CENTERLINE.value,
                "geometry_layer_role": "REBAR_LAYER",
                "source_curve_count": 4,
                "normalized_bend_count": 4,
                "unresolved_curve_count": 0,
                "source_arc_radii_raw": tuple(
                    radius
                    for primitive in arc_corners
                    for radius in primitive.arc_radii_raw
                ),
                "hooked_closure": False,
                "hooked_closure_gap_raw": None,
                "forced_confidence": F006_STRONG_CONFIDENCE,
                "local_f006_printed_dimension_handles": tuple(
                    dimension_text_handles
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            row["score"],
            row["distance_raw"],
            row["centroid_distance_raw"],
            row["handles"],
        )
    )
    return rows[: max(1, int(max_rows))]


def _primitive_endpoint_components(
    primitives: Sequence[GeometryPrimitive],
    *,
    tolerance: float,
) -> tuple[tuple[GeometryPrimitive, ...], ...]:
    if not primitives:
        return ()
    adjacency: list[set[int]] = [set() for _ in primitives]
    for left_index, right_index in combinations(range(len(primitives)), 2):
        left = primitives[left_index]
        right = primitives[right_index]
        if min(
            math.dist(left_point, right_point)
            for left_point in (left.start, left.end)
            for right_point in (right.start, right.end)
        ) <= tolerance:
            adjacency[left_index].add(right_index)
            adjacency[right_index].add(left_index)
    components: list[tuple[GeometryPrimitive, ...]] = []
    visited: set[int] = set()
    for start in range(len(primitives)):
        if start in visited:
            continue
        stack = [start]
        indices: list[int] = []
        visited.add(start)
        while stack:
            current = stack.pop()
            indices.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    stack.append(neighbour)
        components.append(tuple(primitives[index] for index in sorted(indices)))
    return tuple(components)


def _validate_rounded_f006_loop(
    loop: Sequence[GeometryPrimitive],
    *,
    policy: RecognitionPolicy,
) -> tuple[float, float, Point2D] | None:
    tolerance = max(float(policy.endpoint_tolerance_raw), 1e-6)
    endpoint_clusters: list[list[Point2D]] = []
    edge_nodes: list[tuple[int, int]] = []
    for primitive in loop:
        indices: list[int] = []
        for point in (primitive.start, primitive.end):
            match_index = next(
                (
                    index
                    for index, cluster in enumerate(endpoint_clusters)
                    if math.dist(point, _points_center(cluster)) <= tolerance
                ),
                None,
            )
            if match_index is None:
                endpoint_clusters.append([point])
                match_index = len(endpoint_clusters) - 1
            else:
                endpoint_clusters[match_index].append(point)
            indices.append(match_index)
        if indices[0] == indices[1]:
            return None
        edge_nodes.append((indices[0], indices[1]))
    if len(endpoint_clusters) != 8:
        return None
    degrees = [0] * len(endpoint_clusters)
    for left, right in edge_nodes:
        degrees[left] += 1
        degrees[right] += 1
    if any(value != 2 for value in degrees):
        return None

    arcs = [primitive for primitive in loop if primitive.entity_type.upper() == "ARC"]
    radii = [
        float(primitive.arc_radii_raw[0])
        for primitive in arcs
        if primitive.arc_radii_raw
    ]
    if len(radii) != 4 or min(radii) <= 0.0:
        return None
    mean_radius = sum(radii) / len(radii)
    if max(abs(value - mean_radius) / mean_radius for value in radii) > float(
        policy.rounded_f006_radius_relative_tolerance
    ):
        return None
    sweep_tolerance = float(policy.rounded_f006_arc_sweep_tolerance_deg)
    for primitive, radius in zip(arcs, radii):
        sweep = math.degrees(float(primitive.length_raw) / radius)
        if abs(sweep - 90.0) > sweep_tolerance:
            return None

    lines = [primitive for primitive in loop if primitive.entity_type.upper() == "LINE"]
    pairings = (
        ((0, 1), (2, 3)),
        ((0, 2), (1, 3)),
        ((0, 3), (1, 2)),
    )
    angle_tolerance = float(policy.rounded_f006_parallel_tolerance_deg)
    selected: tuple[float, float] | None = None
    for first_pair, second_pair in pairings:
        first_angles = [_line_angle_deg(lines[index]) for index in first_pair]
        second_angles = [_line_angle_deg(lines[index]) for index in second_pair]
        if _parallel_angle_difference(*first_angles) > angle_tolerance:
            continue
        if _parallel_angle_difference(*second_angles) > angle_tolerance:
            continue
        first_lengths = [lines[index].length_raw for index in first_pair]
        second_lengths = [lines[index].length_raw for index in second_pair]
        if not _similar_pair(first_lengths, 0.20) or not _similar_pair(
            second_lengths, 0.20
        ):
            continue
        first_angle = _mean_axis_angle(first_angles)
        second_angle = _mean_axis_angle(second_angles)
        if abs(_parallel_angle_difference(first_angle, second_angle) - 90.0) > angle_tolerance:
            continue
        selected = (first_angle, second_angle)
        break
    if selected is None:
        return None
    all_points = [point for primitive in loop for point in (primitive.start, primitive.end)]
    return selected[0], selected[1], _points_center(all_points)


def _rounded_f006_key_pair(
    primitives: Sequence[GeometryPrimitive],
    *,
    loop: Sequence[GeometryPrimitive],
    loop_handles: set[str],
    layer: str,
    side_orientations: tuple[float, float],
    policy: RecognitionPolicy,
) -> tuple[GeometryPrimitive, GeometryPrimitive] | None:
    minimum_side = min(
        primitive.length_raw
        for primitive in loop
        if primitive.entity_type.upper() == "LINE"
    )
    candidates: list[GeometryPrimitive] = []
    for primitive in primitives:
        if (
            primitive.handle in loop_handles
            or primitive.layer != layer
            or primitive.entity_type.upper() != "LINE"
        ):
            continue
        if primitive.length_raw <= 2.0 or primitive.length_raw > max(45.0, minimum_side * 0.75):
            continue
        angle = _line_angle_deg(primitive)
        if min(
            _parallel_angle_difference(angle, side_orientations[0]),
            _parallel_angle_difference(angle, side_orientations[1]),
        ) < 15.0:
            continue
        distance = _primitive_to_primitives_distance(primitive, loop)
        if distance > max(18.0, float(policy.endpoint_tolerance_raw) * 8.0):
            continue
        candidates.append(primitive)
    ranked: list[tuple[float, tuple[GeometryPrimitive, GeometryPrimitive]]] = []
    angle_tolerance = float(policy.rounded_f006_parallel_tolerance_deg)
    for first, second in combinations(candidates, 2):
        if _parallel_angle_difference(
            _line_angle_deg(first), _line_angle_deg(second)
        ) > angle_tolerance:
            continue
        if not _similar_pair([first.length_raw, second.length_raw], 0.20):
            continue
        midpoint_distance = math.dist(
            _points_center((first.start, first.end)),
            _points_center((second.start, second.end)),
        )
        if midpoint_distance <= 0.5 or midpoint_distance > max(80.0, minimum_side * 1.5):
            continue
        score = (
            _primitive_to_primitives_distance(first, loop)
            + _primitive_to_primitives_distance(second, loop)
            + abs(first.length_raw - second.length_raw)
            + midpoint_distance * 0.02
        )
        ranked.append((score, (first, second)))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1][0].handle, item[1][1].handle))
    return ranked[0][1]


def _rounded_f006_printed_segments(
    *,
    declared: float,
    dimension_handles: Sequence[str],
    entity_map: Mapping[str, DxfEntityRecord],
    loop: Sequence[GeometryPrimitive],
) -> tuple[tuple[float, ...], tuple[str, ...], float] | None:
    numeric: list[tuple[str, float, float, float]] = []
    loop_center = _points_center(
        [point for primitive in loop for point in (primitive.start, primitive.end)]
    )
    for handle in dimension_handles:
        entity = entity_map.get(str(handle))
        if entity is None or entity.entity_type.upper() not in {"TEXT", "MTEXT", "DIMENSION"}:
            continue
        match = re.fullmatch(
            r"\s*(\d+(?:[.,]\d+)?)\s*(?:CM)?\s*",
            str(entity.text or ""),
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        value = float(match.group(1).replace(",", "."))
        if value <= 0.0:
            continue
        rotation = float((entity.geometry or {}).get("rotation", 0.0) or 0.0) % 180.0
        point = _text_record_point(entity)
        distance = math.dist(point, loop_center) if point is not None else math.inf
        numeric.append((str(handle), value, rotation, distance))
    if not numeric:
        return None
    horizontal = [item for item in numeric if _parallel_angle_difference(item[2], 0.0) <= 20.0]
    vertical = [item for item in numeric if _parallel_angle_difference(item[2], 90.0) <= 20.0]
    diagonal = [
        item
        for item in numeric
        if min(
            _parallel_angle_difference(item[2], 45.0),
            _parallel_angle_difference(item[2], 135.0),
        )
        <= 20.0
    ]
    solutions: list[tuple[float, float, tuple[float, ...], tuple[str, ...]]] = []
    for width_item in horizontal:
        for height_item in vertical:
            for hook_item in diagonal:
                handles = (width_item[0], height_item[0], hook_item[0])
                if len(set(handles)) != 3:
                    continue
                printed_width = width_item[1]
                printed_height = height_item[1]
                hook = hook_item[1]
                # F006 production identity is rotation-invariant.  Preserve the
                # locked MASTER convention used by the exporter: the larger
                # rectangle side is L2 and the smaller side is L3, regardless
                # of whether the designer drew the stirrup horizontally or
                # vertically.
                width = max(printed_width, printed_height)
                height = min(printed_width, printed_height)
                canonical = (hook, width, height, width, height, hook)
                difference = abs(sum(canonical) - declared)
                if difference > 2.1:
                    continue
                spatial = width_item[3] + height_item[3] + hook_item[3]
                solutions.append((difference, spatial, canonical, handles))
    if not solutions:
        return None
    solutions.sort(
        key=lambda item: (
            item[0],
            item[1],
            tuple(round(value, 6) for value in item[2]),
            item[3],
        )
    )
    best = solutions[0]
    best_signature = tuple(round(value, 3) for value in best[2])
    for other in solutions[1:]:
        if other[0] - best[0] > 0.11 or other[1] - best[1] > 8.0:
            break
        if tuple(round(value, 3) for value in other[2]) != best_signature:
            return None
    return tuple(best[2]), tuple(best[3]), float(best[0])


def _line_angle_deg(primitive: GeometryPrimitive) -> float:
    return math.degrees(
        math.atan2(
            primitive.end[1] - primitive.start[1],
            primitive.end[0] - primitive.start[0],
        )
    ) % 180.0


def _parallel_angle_difference(left: float, right: float) -> float:
    difference = abs((float(left) - float(right)) % 180.0)
    return min(difference, 180.0 - difference)


def _mean_axis_angle(values: Sequence[float]) -> float:
    doubled_x = sum(math.cos(math.radians(value * 2.0)) for value in values)
    doubled_y = sum(math.sin(math.radians(value * 2.0)) for value in values)
    return (math.degrees(math.atan2(doubled_y, doubled_x)) / 2.0) % 180.0


def _similar_pair(values: Sequence[float], relative_tolerance: float) -> bool:
    if len(values) != 2 or min(values) <= 0.0:
        return False
    mean = sum(values) / 2.0
    return abs(values[0] - values[1]) / mean <= relative_tolerance


def _primitive_to_primitives_distance(
    primitive: GeometryPrimitive,
    others: Sequence[GeometryPrimitive],
) -> float:
    return min(
        _distance_point_to_segments(point, other.segments)
        for point in (primitive.start, primitive.end)
        for other in others
    )


def _text_record_point(record: DxfEntityRecord) -> Point2D | None:
    geometry = record.geometry or {}
    for key in ("visual_anchor", "text_midpoint", "insert", "defpoint"):
        point = _xy(geometry.get(key))
        if point is not None:
            return point
    return None

def _confidence_for(
    row: dict,
    next_row: dict | None,
    policy: RecognitionPolicy,
) -> str:
    declared = max(float(row["declared_length_cm"]), 1.0)
    score_gap = (
        math.inf
        if next_row is None
        else float(next_row["score"]) - float(row["score"])
    )
    distance_gap = (
        math.inf
        if next_row is None
        else float(next_row["distance_raw"]) - float(row["distance_raw"])
    )
    exact_tolerance = _exact_length_tolerance_for_row(row, policy)
    review_tolerance = policy.review_length_tolerance(declared)
    exact_and_close = (
        row["numeric_difference"] <= exact_tolerance
        and row["distance_raw"] <= _strong_distance_limit_for_row(row, policy)
    )

    explicit_unique = (
        score_gap >= float(policy.strong_score_gap)
        or distance_gap >= float(policy.strong_distance_advantage_raw)
    )
    if row["diameter_layer_match"] and exact_and_close and explicit_unique:
        return "СИЛЕН КАНДИДАТ — НЕОДОБРЕН"

    # Generic ARM_Bars layers intentionally carry no diameter. They may still
    # produce a strong *geometric* candidate when the annotation supplies Ø,
    # the chain matches a MASTER shape, the length is exact and the first
    # candidate is clearly separated. This remains non-approved and auditable.
    generic_layer = row["layer_diameter_mm"] is None
    generic_unique = (
        score_gap >= float(policy.generic_layer_strong_score_gap)
        or distance_gap >= float(policy.generic_layer_strong_distance_advantage_raw)
        or _dimension_evidence_candidate_advantage(row, next_row, policy)
    )
    if (
        policy.generic_layer_strong_enabled
        and generic_layer
        and row.get("master_match") is not None
        and exact_and_close
        and generic_unique
    ):
        return "СИЛЕН ПО ГЕОМЕТРИЯ — Ø ОТ НАДПИСА"

    if (
        row["numeric_difference"] <= review_tolerance
        and row["distance_raw"] <= policy.review_distance_limit(declared)
    ):
        return "КАНДИДАТ ЗА РЪЧНА ПРОВЕРКА"

    # It survived all hard laws but remains too weak for normal review.
    return "СЛАБ / РИСКОВ КАНДИДАТ"


def _exact_unique_master_row(
    row: Mapping[str, Any],
    next_row: Mapping[str, Any] | None,
    policy: RecognitionPolicy,
) -> bool:
    match = row.get("master_match")
    if match is None or getattr(match, "ambiguous_shape_codes", ()):
        return False
    declared = max(float(row.get("declared_length_cm") or 0.0), 1.0)
    raw_difference = row.get("numeric_difference")
    raw_distance = row.get("distance_raw")
    difference = (
        math.inf if raw_difference is None else float(raw_difference)
    )
    distance = math.inf if raw_distance is None else float(raw_distance)
    if (
        difference > _exact_length_tolerance_for_row(row, policy)
        or distance > _strong_distance_limit_for_row(row, policy)
    ):
        return False
    if next_row is None:
        return True
    score_gap = float(next_row.get("score", 0.0)) - float(
        row.get("score", 0.0)
    )
    distance_gap = float(next_row.get("distance_raw", 0.0)) - float(
        row.get("distance_raw", 0.0)
    )
    return (
        score_gap >= float(policy.generic_layer_strong_score_gap)
        or distance_gap
        >= float(policy.generic_layer_strong_distance_advantage_raw)
        or _dimension_evidence_candidate_advantage(row, next_row, policy)
    )


def _strong_distance_limit_for_row(
    row: Mapping[str, Any],
    policy: RecognitionPolicy,
) -> float:
    declared = max(float(row.get("declared_length_cm") or 0.0), 1.0)
    generic_limit = policy.strong_distance_limit(declared)
    contextual_override = row.get("contextual_override")
    contextual_code = str(
        row.get("contextual_rule_code")
        or getattr(contextual_override, "code", "")
        or ""
    ).strip().upper()
    if contextual_code != "F007_ESOVE_CURVED_CENTERLINE":
        return generic_limit
    return min(
        float(policy.c_shape_curve_search_distance_raw),
        max(generic_limit, float(policy.c_shape_curve_strong_distance_raw)),
    )


def _exact_length_tolerance_for_row(
    row: Mapping[str, Any],
    policy: RecognitionPolicy,
) -> float:
    declared = max(float(row.get("declared_length_cm") or 0.0), 1.0)
    match = row.get("master_match")
    return policy.exact_length_tolerance_for_master(
        declared,
        getattr(match, "shape_code", None),
    )


def _dimension_evidence_candidate_advantage(
    row: Mapping[str, Any],
    next_row: Mapping[str, Any] | None,
    policy: RecognitionPolicy,
) -> bool:
    """Resolve one local candidate tie only with asymmetric A-E evidence."""

    if not policy.dimension_candidate_tiebreak_enabled or next_row is None:
        return False
    evidence = row.get("dimension_evidence")
    next_evidence = next_row.get("dimension_evidence")
    row_has_evidence = bool(row.get("dimension_evidence_applied")) or evidence is not None
    next_has_evidence = (
        bool(next_row.get("dimension_evidence_applied"))
        or next_evidence is not None
    )
    if not row_has_evidence or next_has_evidence:
        return False
    total_difference = row.get("dimension_total_difference_cm")
    if total_difference is None and evidence is not None:
        total_difference = getattr(evidence, "total_difference_cm", None)
    try:
        if total_difference is None or abs(float(total_difference)) > float(
            policy.dimension_candidate_tiebreak_total_tolerance_cm
        ):
            return False
    except (TypeError, ValueError):
        return False

    match = row.get("master_match")
    next_match = next_row.get("master_match")
    if match is None or next_match is None:
        return False
    if getattr(match, "ambiguous_shape_codes", ()) or getattr(
        next_match, "ambiguous_shape_codes", ()
    ):
        return False
    if str(getattr(match, "shape_code", "")).upper() != str(
        getattr(next_match, "shape_code", "")
    ).upper():
        return False
    if int(row.get("segment_count") or 0) != int(
        next_row.get("segment_count") or 0
    ):
        return False

    declared = max(float(row.get("declared_length_cm") or 0.0), 1.0)
    try:
        difference = abs(float(row.get("numeric_difference")))
        score = float(row.get("score"))
        next_score = float(next_row.get("score"))
        distance = float(row.get("distance_raw"))
        next_distance = float(next_row.get("distance_raw"))
    except (TypeError, ValueError):
        return False
    if difference > _exact_length_tolerance_for_row(row, policy):
        return False
    if distance > _strong_distance_limit_for_row(row, policy):
        return False
    if not (score < next_score and distance < next_distance):
        return False

    blockers = {
        "AMBIGUOUS_MASTER",
        "FAR_ASSOCIATION",
        "HOOKED_CLOSURE",
        "MIRROR_REVIEW",
        "NEAR_GAP_HEALED",
        "SCHEMATIC_SCALE",
        "UNRESOLVED_CURVE_GEOMETRY",
    }
    return not blockers.intersection(
        str(value) for value in row.get("review_reason_codes", ())
    )


def _primitive_from_record(
    record: DxfEntityRecord,
    *,
    geometry_layer_role: str = "REBAR_LAYER",
) -> GeometryPrimitive | None:
    entity_type = record.entity_type.upper()
    if entity_type == "LINE":
        start = _xy(record.geometry.get("start"))
        end = _xy(record.geometry.get("end"))
        if start is None or end is None:
            return None
        points = (start, end)
        recognition_points = points
        closed = False
        segments = (
            (start[0], start[1], end[0], end[1]),
        )
        length = math.dist(start, end)
        semantic_length = length
        representation = GeometryRepresentation.CENTERLINE.value
        source_curve_count = 0
        normalized_bend_count = 0
        unresolved_curve_count = 0
        arc_radii: tuple[float, ...] = ()
        hooked_closure = False
        hooked_closure_gap = None
        geometry_notes: tuple[str, ...] = ()
    elif entity_type in {"LWPOLYLINE", "POLYLINE", "ARC"}:
        path = (
            arc_path_from_geometry(record.geometry)
            if entity_type == "ARC"
            else polyline_path_from_geometry(record.geometry)
        )
        if path is None:
            return None
        semantic = semantic_path_from_polyline(path)
        points = path.points
        recognition_points = semantic.points
        closed = path.closed
        segments = path_spatial_segments(path)
        length = path.exact_length_raw
        semantic_length = semantic.semantic_length_raw
        representation = semantic.representation.value
        source_curve_count = semantic.source_curve_count
        normalized_bend_count = semantic.collapsed_arc_count
        unresolved_curve_count = semantic.unresolved_arc_count
        arc_radii = semantic.arc_radii_raw
        hooked_closure = semantic.hooked_closure
        hooked_closure_gap = semantic.hooked_closure_gap_raw
        geometry_notes_list: list[str] = []
        if source_curve_count:
            geometry_notes_list.append(
                f"DXF обектът съдържа {source_curve_count} дъгови сегмента; "
                f"точна дъгова L={_fmt(length)}, топологична "
                f"L={_fmt(semantic_length)}; радиусите са запазени"
            )
        if (
            semantic.representation
            is GeometryRepresentation.STROKED_OUTLINE
        ):
            geometry_notes_list.append(
                "Затворената геометрия прилича на очертание на прът; "
                "не е извлечена автоматично осева линия"
            )
        if hooked_closure:
            geometry_notes_list.append(
                "Разпозната е отворена форма с два близки противоположни "
                "крайни hook сегмента; допуска се само review-only сравнение "
                "със затворена MASTER топология"
            )
        geometry_notes = tuple(geometry_notes_list)
    else:
        return None

    if not segments:
        return None
    return GeometryPrimitive(
        handle=record.handle,
        layer=record.layer,
        entity_type=record.entity_type,
        length_raw=length,
        segments=segments,
        points=points,
        start=points[0],
        end=points[-1],
        closed=closed,
        recognition_points=recognition_points,
        semantic_length_raw=semantic_length,
        geometry_representation=representation,
        geometry_layer_role=geometry_layer_role,
        source_curve_count=source_curve_count,
        normalized_bend_count=normalized_bend_count,
        unresolved_curve_count=unresolved_curve_count,
        arc_radii_raw=arc_radii,
        hooked_closure=hooked_closure,
        hooked_closure_gap_raw=hooked_closure_gap,
        geometry_notes=geometry_notes,
    )


def _connected_paths(
    primitives: list[GeometryPrimitive],
    policy: RecognitionPolicy,
    *,
    probe_points: Sequence[Point2D] | None = None,
    search_distance: float | None = None,
) -> list[tuple[GeometryPrimitive, ...]]:
    """Build reliable virtual polylines from separate LINE objects.

    v0.1.14 applied max_path_candidates to the entire DXF layer. Once the cap
    was reached, identical shapes later in the file were never generated. The
    v0.1.15 builder uses a spatial endpoint graph and searches only components
    close to the selected annotation, with a separate cap per component.
    """

    per_component_limit = max(
        512,
        min(5000, int(policy.max_path_candidates) * 4),
    )
    return _v015_build_connected_chains(
        primitives,
        endpoint_tolerance=float(policy.endpoint_tolerance_raw),
        max_segments=int(policy.max_component_segments),
        max_paths_per_component=per_component_limit,
        probe_points=probe_points,
        search_distance=search_distance,
    )


def _near_gap_connected_paths(
    primitives: list[GeometryPrimitive],
    policy: RecognitionPolicy,
    *,
    probe_points: Sequence[Point2D] | None = None,
    search_distance: float | None = None,
) -> list[tuple[tuple[GeometryPrimitive, ...], NearGapBridge]]:
    if (
        not policy.near_gap_healing_enabled
        or float(policy.near_gap_max_raw) <= float(policy.endpoint_tolerance_raw)
    ):
        return []
    per_component_limit = max(
        512,
        min(5000, int(policy.max_path_candidates) * 4),
    )
    expanded = _v015_build_connected_chains(
        primitives,
        endpoint_tolerance=float(policy.near_gap_max_raw),
        max_segments=int(policy.max_component_segments),
        max_paths_per_component=per_component_limit,
        probe_points=probe_points,
        search_distance=search_distance,
    )
    return _audited_near_gap_paths(
        expanded,
        all_primitives=primitives,
        policy=policy,
    )


def _audited_near_gap_paths(
    paths: Sequence[tuple[GeometryPrimitive, ...]],
    *,
    all_primitives: Sequence[GeometryPrimitive],
    policy: RecognitionPolicy,
) -> list[tuple[tuple[GeometryPrimitive, ...], NearGapBridge]]:
    neighbourhood = tuple(
        edge
        for primitive in all_primitives
        if (edge := _near_gap_edge(primitive)) is not None
    )
    output: list[tuple[tuple[GeometryPrimitive, ...], NearGapBridge]] = []
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        handles = tuple(sorted(primitive.handle for primitive in path if primitive.handle))
        if not handles or handles in seen:
            continue
        edges = tuple(
            edge
            for primitive in path
            if (edge := _near_gap_edge(primitive)) is not None
        )
        if len(edges) != len(path):
            continue
        bridge = audit_unique_one_gap_bridge(
            edges,
            strict_tolerance_raw=float(policy.endpoint_tolerance_raw),
            max_gap_raw=float(policy.near_gap_max_raw),
            neighbourhood_edges=neighbourhood,
        )
        if bridge is None:
            continue
        seen.add(handles)
        output.append((tuple(path), bridge))
    return output


def _near_gap_edge(primitive: GeometryPrimitive) -> NearGapEdge | None:
    if primitive.entity_type.upper() != "LINE" or not primitive.handle or not primitive.layer:
        return None
    return NearGapEdge(
        handle=primitive.handle,
        layer=primitive.layer,
        start=primitive.start,
        end=primitive.end,
    )


def _ordered_shape_points(
    shape: Sequence[GeometryPrimitive],
    policy: RecognitionPolicy,
    *,
    endpoint_tolerance_raw: float | None = None,
) -> tuple[Point2D, ...] | None:
    if not shape:
        return None
    if len(shape) == 1:
        return shape[0].recognition_points or shape[0].points

    tolerance = (
        float(policy.endpoint_tolerance_raw)
        if endpoint_tolerance_raw is None
        else max(float(endpoint_tolerance_raw), 1e-9)
    )
    adjacency: list[set[int]] = [set() for _ in shape]
    for left_index, left in enumerate(shape):
        for right_index in range(left_index + 1, len(shape)):
            if _share_endpoint(left, shape[right_index], tolerance):
                adjacency[left_index].add(right_index)
                adjacency[right_index].add(left_index)

    if not _is_connected(adjacency):
        return None
    if policy.reject_branched_candidate and any(len(neighbours) > 2 for neighbours in adjacency):
        return None

    endpoints = [index for index, neighbours in enumerate(adjacency) if len(neighbours) == 1]
    if len(endpoints) not in {0, 2}:
        return None
    start_index = min(endpoints, key=lambda index: shape[index].handle) if endpoints else 0

    order: list[int] = []
    previous: int | None = None
    current = start_index
    while True:
        order.append(current)
        next_values = sorted(adjacency[current] - ({previous} if previous is not None else set()))
        if not next_values:
            break
        if len(next_values) > 1:
            return None
        previous, current = current, next_values[0]
        if current == start_index:
            break
        if current in order:
            return None
    if len(order) != len(shape):
        return None

    # Try both orientations of the first primitive and keep the coherent chain.
    for reverse_first in (False, True):
        first_source = (
            shape[order[0]].recognition_points
            or shape[order[0]].points
        )
        first_points = tuple(reversed(first_source)) if reverse_first else first_source
        points = list(first_points)
        coherent = True
        for index in order[1:]:
            primitive_points = (
                shape[index].recognition_points
                or shape[index].points
            )
            if _points_close(points[-1], primitive_points[0], tolerance):
                oriented = primitive_points
            elif _points_close(points[-1], primitive_points[-1], tolerance):
                oriented = tuple(reversed(primitive_points))
            else:
                coherent = False
                break
            points.extend(oriented[1:])
        if coherent:
            if not endpoints and not _points_close(points[0], points[-1], tolerance):
                continue
            return tuple(points)
    return None


def _is_connected(adjacency: Sequence[set[int]]) -> bool:
    if not adjacency:
        return False
    visited: set[int] = set()
    stack = [0]
    while stack:
        index = stack.pop()
        if index in visited:
            continue
        visited.add(index)
        stack.extend(adjacency[index] - visited)
    return len(visited) == len(adjacency)


def _share_endpoint(left: GeometryPrimitive, right: GeometryPrimitive, tolerance: float) -> bool:
    return any(
        _points_close(point_left, point_right, tolerance)
        for point_left in (left.start, left.end)
        for point_right in (right.start, right.end)
    )


def _contextual_shape_override(
    *,
    selected: DxfEntityRecord,
    diameter_mm: float,
    declared_length_cm: float,
    measured_geometry_length_cm: float,
    layer: str,
    ordered_points: Sequence[Point2D],
    source_curve_count: int,
    normalized_bend_count: int,
    unresolved_curve_count: int,
    policy: RecognitionPolicy,
) -> _ContextualShapeOverride | None:
    """Apply only explicit, owner-approved project conventions.

    The approved conventions concern the designer's C/S punching bars and
    middle N8 beam bars.  Both rules are deliberately narrow and auditable.
    """

    c_shape = _c_shape_curve_override(
        declared_length_cm=declared_length_cm,
        measured_geometry_length_cm=measured_geometry_length_cm,
        layer=layer,
        ordered_points=ordered_points,
        source_curve_count=source_curve_count,
        normalized_bend_count=normalized_bend_count,
        unresolved_curve_count=unresolved_curve_count,
        policy=policy,
    )
    if c_shape is not None:
        return c_shape

    if not policy.beam_n8_middle_rule_enabled or abs(diameter_mm - 8.0) > 0.01:
        return None
    normalized_layer = (layer or "").upper().replace("-", "_").replace(" ", "_")
    if "ARM_BARS" not in normalized_layer:
        return None
    if len(ordered_points) != 3:
        return None

    lengths = _point_chain_lengths(ordered_points)
    if len(lengths) != 2 or min(lengths) <= 1e-9:
        return None
    long_index = 0 if lengths[0] >= lengths[1] else 1
    short_index = 1 - long_index
    hook = float(policy.beam_n8_hook_length_cm)
    expected_long = float(declared_length_cm) - hook
    if expected_long <= 0:
        return None
    long_tolerance = max(
        float(policy.beam_n8_long_segment_tolerance_cm),
        expected_long * 0.01,
    )
    if abs(lengths[long_index] - expected_long) > long_tolerance:
        return None
    if lengths[short_index] > expected_long * float(policy.beam_n8_short_segment_max_ratio):
        return None

    raw_turn = _signed_turn_deg(ordered_points[0], ordered_points[1], ordered_points[2])
    abs_turn = abs(raw_turn)
    if not (
        float(policy.beam_n8_min_raw_turn_deg)
        <= abs_turn
        <= float(policy.beam_n8_max_raw_turn_deg)
    ):
        return None

    canonical_lengths = [0.0, 0.0]
    canonical_lengths[long_index] = expected_long
    canonical_lengths[short_index] = hook
    canonical_points = _canonical_two_segment_points(
        ordered_points,
        tuple(canonical_lengths),
        turn_sign=1.0 if raw_turn >= 0 else -1.0,
    )
    return _ContextualShapeOverride(
        code="BEAM_MIDDLE_N8_15CM_90",
        note=(
            "Приложено е одобрено проектно правило за средно желязо N8 в греда: "
            f"{_fmt(expected_long)} + {_fmt(hook)} cm, винкел 90°. "
            "Наклонът на късото рамо в DXF се приема за символично изчертаване."
        ),
        points=canonical_points,
        effective_length_cm=float(declared_length_cm),
        segment_lengths_cm=tuple(canonical_lengths),
        turn_angles_deg=(90.0,),
    )


def _c_shape_curve_override(
    *,
    declared_length_cm: float,
    measured_geometry_length_cm: float,
    layer: str,
    ordered_points: Sequence[Point2D],
    source_curve_count: int,
    normalized_bend_count: int,
    unresolved_curve_count: int,
    policy: RecognitionPolicy,
) -> _ContextualShapeOverride | None:
    """Restore the production F007 L1-L2-L1 topology from an ESove symbol."""

    if not policy.c_shape_curve_rule_enabled:
        return None
    if source_curve_count != 1 or normalized_bend_count != 1:
        return None
    if unresolved_curve_count != 0 or len(ordered_points) != 3:
        return None

    normalized_layer = re.sub(r"[^A-ZА-Я0-9]+", "", (layer or "").upper())
    c_aliases = tuple(
        token
        for value in policy.curved_centerline_layer_aliases
        if (
            token := re.sub(r"[^A-ZА-Я0-9]+", "", str(value).upper())
        ).startswith(("ESOV", "ЕСОВ"))
    )
    if not normalized_layer or not any(
        normalized_layer.startswith(alias) for alias in c_aliases
    ):
        return None

    declared = float(declared_length_cm)
    measured = float(measured_geometry_length_cm)
    if declared <= 0 or not math.isfinite(measured):
        return None
    if abs(measured - declared) > float(policy.c_shape_curve_total_tolerance_cm):
        return None

    visible_lengths = _point_chain_lengths(ordered_points)
    if len(visible_lengths) != 2 or min(visible_lengths) <= 1e-9:
        return None
    visible_ratio = max(visible_lengths) / min(visible_lengths)
    expected_ratio = float(policy.c_shape_curve_long_to_short_ratio)
    ratio_tolerance = float(policy.c_shape_curve_ratio_tolerance)
    if expected_ratio <= 0 or abs(visible_ratio / expected_ratio - 1.0) > ratio_tolerance:
        return None

    raw_turn = _signed_turn_deg(
        ordered_points[0],
        ordered_points[1],
        ordered_points[2],
    )
    expected_turn = float(policy.c_shape_curve_turn_deg)
    if abs(abs(raw_turn) - expected_turn) > float(
        policy.c_shape_curve_turn_tolerance_deg
    ):
        return None

    # The approved F007 production relation is L1 + L2 + L1 with L2=2*L1.
    # Therefore L1=L/4 and L2=L/2.  No observed DXF segment is silently used
    # as a production dimension; the annotation remains the total cut length.
    l1 = declared / 4.0
    canonical_lengths = (l1, 2.0 * l1, l1)
    turn_sign = 1.0 if raw_turn >= 0 else -1.0
    canonical_turns = (turn_sign * 90.0, turn_sign * 135.0)
    canonical_points = _canonical_chain_points(
        ordered_points,
        canonical_lengths,
        canonical_turns,
    )
    return _ContextualShapeOverride(
        code="F007_ESOVE_CURVED_CENTERLINE",
        note=(
            "Приложено е одобрено правило за C/S-форма от слой ESove: "
            f"обща L={_fmt(declared)} cm е възстановена като "
            f"{_fmt(l1)} + {_fmt(2.0 * l1)} + {_fmt(l1)} cm (F007). "
            "Една допирателна DXF дъга е третирана като огъвка, а не като "
            "отделен производствен прав участък."
        ),
        points=canonical_points,
        effective_length_cm=declared,
        segment_lengths_cm=canonical_lengths,
        turn_angles_deg=(90.0, 135.0),
    )


def _point_chain_lengths(points: Sequence[Point2D]) -> tuple[float, ...]:
    return tuple(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _signed_turn_deg(left: Point2D, middle: Point2D, right: Point2D) -> float:
    first = math.atan2(middle[1] - left[1], middle[0] - left[0])
    second = math.atan2(right[1] - middle[1], right[0] - middle[0])
    delta = math.degrees(second - first)
    while delta <= -180.0:
        delta += 360.0
    while delta > 180.0:
        delta -= 360.0
    return delta


def _canonical_two_segment_points(
    raw_points: Sequence[Point2D],
    lengths: tuple[float, float],
    *,
    turn_sign: float,
) -> tuple[Point2D, ...]:
    start = raw_points[0]
    heading = math.atan2(
        raw_points[1][1] - raw_points[0][1],
        raw_points[1][0] - raw_points[0][0],
    )
    middle = (
        start[0] + lengths[0] * math.cos(heading),
        start[1] + lengths[0] * math.sin(heading),
    )
    second_heading = heading + turn_sign * math.pi / 2.0
    end = (
        middle[0] + lengths[1] * math.cos(second_heading),
        middle[1] + lengths[1] * math.sin(second_heading),
    )
    return start, middle, end


def _canonical_chain_points(
    raw_points: Sequence[Point2D],
    lengths: Sequence[float],
    turns_deg: Sequence[float],
) -> tuple[Point2D, ...]:
    if len(lengths) < 1 or len(turns_deg) != len(lengths) - 1:
        return ()
    start = raw_points[0]
    heading = math.atan2(
        raw_points[1][1] - raw_points[0][1],
        raw_points[1][0] - raw_points[0][0],
    )
    result = [start]
    current = start
    for index, length in enumerate(lengths):
        current = (
            current[0] + float(length) * math.cos(heading),
            current[1] + float(length) * math.sin(heading),
        )
        result.append(current)
        if index < len(turns_deg):
            heading += math.radians(float(turns_deg[index]))
    return tuple(result)


def _annotation_probe_points(
    record: DxfEntityRecord,
    entities: Sequence[DxfEntityRecord],
    policy: RecognitionPolicy,
    *,
    leader_entities: Sequence[DxfEntityRecord] | None = None,
) -> tuple[Point2D, ...]:
    points: list[Point2D] = []

    def add(value) -> None:
        point = _xy(value)
        if point is not None and not any(_points_close(point, existing, 1e-8) for existing in points):
            points.append(point)

    if policy.use_visual_text_extent:
        add(record.geometry.get("visual_anchor"))
        raw_bbox = record.geometry.get("visual_bbox")
        if isinstance(raw_bbox, list):
            for value in raw_bbox:
                add(value)
        raw_attribute_inserts = record.geometry.get("attribute_inserts")
        if isinstance(raw_attribute_inserts, list):
            for value in raw_attribute_inserts:
                add(value)
    for key in ("insert", "location", "center", "start"):
        add(record.geometry.get(key))

    if policy.use_attached_leader_points and points:
        height = _annotation_height(record)
        attach_limit = max(
            float(policy.leader_attach_distance_raw),
            height * float(policy.leader_attach_height_ratio),
        )
        leaders = leader_entities if leader_entities is not None else entities
        for entity in leaders:
            if entity.entity_type.upper() != "LEADER":
                continue
            vertices = entity.geometry.get("vertices")
            if not isinstance(vertices, list):
                continue
            parsed = [point for value in vertices if (point := _xy(value)) is not None]
            if not parsed:
                continue
            endpoints = (parsed[0], parsed[-1])
            if min(
                math.hypot(endpoint[0] - probe[0], endpoint[1] - probe[1])
                for endpoint in endpoints
                for probe in points
            ) <= attach_limit:
                for point in parsed:
                    add(point)
    return tuple(points)


def _annotation_height(record: DxfEntityRecord) -> float:
    for key in ("height", "char_height", "estimated_text_height"):
        try:
            value = float(record.geometry.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 1.0


def _record_anchor(record: DxfEntityRecord) -> Point2D | None:
    for key in ("visual_anchor", "insert", "location", "center", "start"):
        point = _xy(record.geometry.get(key))
        if point is not None:
            return point
    return None


def _distance_point_to_segments(point: Point2D, segments: Iterable[Segment2D]) -> float:
    distances = [_distance_point_to_segment(point, segment) for segment in segments]
    return min(distances) if distances else math.inf


def _distance_point_to_segment(point: Point2D, segment: Segment2D) -> float:
    px, py = point
    x1, y1, x2, y2 = segment
    dx, dy = x2 - x1, y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / denominator
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _points_center(points: Sequence[Point2D]) -> Point2D:
    if not points:
        return 0.0, 0.0
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _single_layer(shape: Sequence[GeometryPrimitive]) -> str:
    layers = sorted({primitive.layer for primitive in shape})
    return layers[0] if len(layers) == 1 else " + ".join(layers)


def _diameter_from_layer(layer: str) -> float | None:
    match = _REBAR_LAYER_DIAMETER_RE.search(layer or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _rebar_geometry_layer_role(
    layer: str,
    policy: RecognitionPolicy | None = None,
) -> str | None:
    resolved = policy or DEFAULT_RECOGNITION_POLICY
    # Обявеният от оператора слой бие речника: декларацията е за ТОЗИ чертеж и
    # е дадена от човек, видял доказателството (виж
    # ``RecognitionPolicy.declared_rebar_geometry_layers``). Сравнява се цялото
    # име, не подниз, за да не хване съседен слой по случайност.
    declared = {
        str(name).strip().upper()
        for name in resolved.declared_rebar_geometry_layers
        if str(name).strip()
    }
    if declared and str(layer or "").strip().upper() in declared:
        return "DECLARED_REBAR_LAYER"
    normalized = (layer or "").upper().replace("_", " ").replace("-", " ")
    if _diameter_from_layer(layer) is not None:
        return "DIAMETER_LAYER"
    if any(keyword in normalized for keyword in _REBAR_LAYER_KEYWORDS):
        return "REBAR_LAYER"
    words = tuple(re.findall(r"[A-ZА-Я0-9]+", normalized))
    aliases = tuple(
        str(alias).strip().upper()
        for alias in resolved.curved_centerline_layer_aliases
        if str(alias).strip()
    )
    if any(
        word.startswith(alias)
        for word in words
        for alias in aliases
    ):
        return "CURVED_CENTERLINE_ALIAS"
    return None


def _is_esove_curve_primitive(
    primitive: GeometryPrimitive,
    policy: RecognitionPolicy,
) -> bool:
    if primitive.entity_type.upper() not in {"LWPOLYLINE", "POLYLINE", "ARC"}:
        return False
    if primitive.source_curve_count < 1:
        return False
    normalized_layer = re.sub(r"[^A-ZА-Я0-9]+", "", primitive.layer.upper())
    aliases = tuple(
        token
        for value in policy.curved_centerline_layer_aliases
        if (
            token := re.sub(r"[^A-ZА-Я0-9]+", "", str(value).upper())
        ).startswith(("ESOV", "ЕСОВ"))
    )
    return bool(normalized_layer and any(normalized_layer.startswith(alias) for alias in aliases))


def _is_rebar_geometry_layer(
    layer: str,
    policy: RecognitionPolicy | None = None,
) -> bool:
    return _rebar_geometry_layer_role(layer, policy) is not None


def _xy(value) -> Point2D | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _points_close(left: Point2D, right: Point2D, tolerance: float) -> bool:
    return math.hypot(left[0] - right[0], left[1] - right[1]) <= tolerance


def _reject(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _resolve_policy(value: RecognitionPolicy | Mapping | None) -> RecognitionPolicy:
    if isinstance(value, RecognitionPolicy):
        return value
    if isinstance(value, Mapping):
        return RecognitionPolicy.from_settings(value)
    return DEFAULT_RECOGNITION_POLICY


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")
