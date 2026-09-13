from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Sequence

from lion_rebar.dxf.geometry_core_v2 import (
    arc_path_from_geometry,
    polyline_path_from_geometry,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_parser import looks_like_rebar_text
from lion_rebar.dxf.vertical_shape_resolver import ShapeEvidence, derive_shape


PROXY_ANNOTATION_SOURCE = "ACAD_PROXY_GRAPHIC_REBAR_ANNOTATION"
PROXY_GEOMETRY_SOURCE = "ACAD_PROXY_GRAPHIC_REBAR_GEOMETRY"
PROXY_ASSOCIATION_CODE = "PROXY_NEAREST_PREDECESSOR_EXACT_LENGTH"
#: Owner-confirmed 2026-09-10: a fully parsed annotation (count, diameter
#: AND length all present in the text) is accepted directly from the
#: designer's declared value when no unique geometric length proof exists —
#: the same "read the specification, don't audit the drafting precision"
#: policy already applied everywhere else in the product. Any geometry from
#: the annotation's own proxy is still attached as a reference (for the
#: viewer / audit trail), never as a length requirement.
PROXY_DECLARED_LENGTH_CODE = "PROXY_TEXT_DECLARED_LENGTH"

#: Annotation grammar markers (see ``_parse_annotation`` vs.
#: ``_parse_total_count_annotation``). Used to scope the column-group
#: multiplier below to the koloni "trailing total" grammar only.
_GRAMMAR_LEAD_COUNT = "LEAD_COUNT"
_GRAMMAR_TRAILING_TOTAL_COUNT = "TRAILING_TOTAL_COUNT"

#: Owner-confirmed 2026-09-10 against a real koloni drawing: several
#: physical columns with an identical cross-section and identical
#: reinforcement (e.g. "K73, K80, K83, K88") are drawn ONCE and titled with
#: a group header ending in "N бр." (count of columns sharing that spec).
#: Every "(общо N)" total inside that group's detail is declared "за една
#: колона" (for ONE column) -- the true building-wide quantity is
#: N (header) x N (declared per-column total). Verified against the
#: designer's own drawing total: 5667.9 kg (unmultiplied, 68% of 8311 kg
#: declared) -> 8171.8 kg (multiplied, 98.3% of 8311 kg) on file
#: "23_83_koloni_ot +18.18 do +21.48.dxf".
_COLUMN_GROUP_HEADER_RE = re.compile(r"(\d+)\s*бр\.?\s*$", re.IGNORECASE)
#: A header sits visually above its detail box; annotations below a header
#: within this tolerance (drawing units) are still considered "under" it
#: rather than excluded for being marginally lower than the header itself.
_COLUMN_GROUP_ABOVE_TOLERANCE = 50.0

#: Owner-confirmed 2026-09-12: nearest-header-by-text-distance is fragile
#: when several group headers sit in the same row (a real case: two
#: annotations both fell inside a 6-column group's own drawn detail box,
#: yet were 14 drawing units closer -- under 2% -- to a NEIGHBOURING
#: 2-column group's header text, silently halving their true quantity,
#: 50 instead of 150 pieces). Some koloni groups draw their own detail as
#: a closed rectangle on the same layer; when an annotation's anchor falls
#: INSIDE one, that box's own (unambiguous) header association is used
#: FIRST, before ever falling back to text distance. Deliberately NOT
#: matched by a specific size: real boxes measured 440-502 wide x
#: ~1075-1080 tall, while every individual stirrup/bend icon on this same
#: layer measured under 190 in both dimensions -- a wide, natural gap.
#: This threshold sits in that gap; it recognises "clearly a group
#: detail box", not one designer's specific rectangle.
_COLUMN_GROUP_BOX_MIN_HEIGHT = 350.0
_COLUMN_GROUP_BOX_MIN_WIDTH = 100.0

#: Owner-confirmed 2026-09-10 against the real shaibi (shear wall) drawing:
#: a DIFFERENT grouping mechanism from koloni's single combined header.
#: Each physical wall gets its OWN plain-text label on layer
#: ``C_TXT_shaibi`` reading "Ш<N> - <W>/<H>" (cross-section); when two or
#: more walls share an IDENTICAL cross-section and sit close together they
#: share one drawn detail (real example: "Ш 4 - 25/350" / "Ш 7 - 25/350",
#: confirmed identical by the owner: "Ш7 = същата като Ш4"). The true
#: building-wide quantity for that shared detail is its own declared
#: "(общо N)" x the number of walls in the group.
#:
#: The drawing ALSO repeats some of these details as pure visual reference
#: copies elsewhere on the same sheet (a floor-plan callout area far from
#: any wall label) -- verified real distances: a legitimate detail sits
#: 100-1400 drawing units from its own wall label; a reference-only copy
#: sits 2500-4500 units away, a clean, unambiguous gap. Verified end-to-end
#: on "38_83_shaibi_+18.18 do +21.48.dxf": 2010.3 kg (57 positions, no
#: fix) -> 3273.8 kg (96.0% of the designer's declared 3411 kg, up from
#: 59%), with an honest per-diameter breakdown (all within ~12%, no
#: diameter wildly wrong) rather than a lucky total from cancelling errors.
_SHAIBI_WALL_LABEL_RE = re.compile(r"Ш\s*\d+\s*-\s*(\d+)\s*/\s*(\d+)")
_SHAIBI_WALL_LABEL_LAYER = "C_TXT_SHAIBI"
#: Wall labels sharing one detail sit within ~60 units of each other on
#: the real drawing; this leaves generous margin without merging two
#: unrelated walls that coincidentally share a cross-section elsewhere.
_SHAIBI_WALL_GROUP_CLUSTER_DISTANCE = 500.0
#: The clean gap measured above (100-1400 real vs. 2500-4500 reference-only).
_SHAIBI_WALL_GROUP_MATCH_DISTANCE = 2000.0
#: The one confirmed real visual duplicate (same detail, both ends of a
#: bend labelled separately -- owner-confirmed real case: "N16x323 (общо
#: 4)" only in Ш3, wrongly counted a second time near Ш4/Ш7 before this
#: fix) sat 230 units from its own duplicate copy; unrelated walls'
#: details sit much farther apart on this drawing's grid layout.
_SHAIBI_DUPLICATE_CONTENT_DISTANCE = 500.0

#: Owner-requested 2026-09-10 ("не видях чужда форма и са доста добре
#: изчертани" — no unfamiliar shape, well drafted): most arm_koloni/
#: arm_shaibi positions carry no ``master_shape_code`` not because their
#: bend pattern is exotic, but because the traced geometry is unreliable
#: (see PROXY_DECLARED_LENGTH_CODE). The designer's own annotation already
#: carries the full bend-segment breakdown as trailing numeric fragments
#: after the "(общо N)" marker (e.g. "N8x170 (общо 26) 51 26 51 26 8 8" —
#: verified real data: the trailing numbers close the declared total to
#: within rounding for 165/165 real positions across both drawings).
#:
#: The engine's existing, deliberate rule (``batch_geometry_report.py``,
#: repeated comment "без форма не може да се твърди какво е производството")
#: is that declared/dimension text may prove exact SEGMENT lengths but must
#: NEVER by itself prove shape FAMILY -- family needs independent evidence
#: (traced geometry or an explicit label). This proxy dialect still has an
#: independent, purely geometric family signal even though the straight
#: portions of its geometry are empty: the plugin still draws one real ARC
#: per structural bend (validated: bend-arc count correlates 1:1 with
#: declared-segment-count across all 165 real positions in both drawings,
#: zero ambiguous cases) alongside an unrelated decorative "bar end" marker
#: (a circle split into two ARCs tracing the SAME two endpoints in opposite
#: directions -- an exact duplicate, detected structurally below, never a
#: bend). The signal works in BOTH directions: >0 bend arcs proves a
#: genuine bend (F003/F004/F006/...); exactly 0 proves NO bend at all --
#: either a plain straight bar (F001, one declared segment) or the
#: engine's own F002 "ПРАВ ПРЪТ С РЕДУКЦИЯ" (straight bar with a
#: diameter-reduction splice/coupler, three declared segments, e.g.
#: "150-21-310") — F002 is still geometrically straight, just billed in
#: three parts. Confirmed against a real live-app resolution ("14N20x451"
#: -> F002, 310+21+120) after an earlier version of this fix wrongly
#: collapsed the three segments into an F001 total, losing the reduction
#: detail. See ``_count_structural_bend_arcs``/``_derive_declared_shape_evidence``.
PROXY_DECLARED_SHAPE_CODE = "PROXY_ARC_VERIFIED_DECLARED_SEGMENTS"


@dataclass(frozen=True)
class ProxyAdapterConfig:
    """Strict admission rules for known proxy annotation dialects.

    This is intentionally a dialect adapter, not a generic proxy explosion:
    every admitted layer must first be verified against a real drawing to
    confirm its proxy text fragments actually reconstruct into complete,
    correctly-associated annotations under the same three-condition check
    (complete token sequence, unique connected geometry subset, uniquely
    calibrated scale) — see ``extract_acad_proxy_rebar``. Unsupported proxy
    graphics remain visible in the import summary but never enter the rebar
    matcher as geometry.

    ``C_ARM_Gredi``/``C_arm_stylbi``/``C_Arm_kobilici`` (second designer,
    beam/column/corbel reinforcement, verified 2026-09-10 against a real
    drawing where AutoCAD's own proxy fragments carry the exact same
    position/count/N/diameter/x/length token sequence as the GA_X/GA_Y
    dialect, just on different layer names) added alongside the original
    GA_X/GA_Y dialect.

    ``arm_koloni`` added 2026-09-10: same designer, owner-confirmed real
    label semantics (``N<diameter>x<length>(общо <count>[бр.])`` — a SECOND
    grammar where the total count trails the label instead of leading it,
    used for both main column bars and ties/stirrups; see
    ``_parse_total_count_annotation``).

    ``arm_shaibi`` re-admitted 2026-09-10 (owner decision, superseding the
    earlier "keep display-only" choice from rc43's CHANGELOG
    "DISPLAY-ONLY ACAD PROXY GRAPHICS" — the underlying reliability concern
    that motivated that choice is the same duplicate-geometry issue now
    handled explicitly by the declared-length fallback below, not an
    unaddressed risk). ``tests/test_proxy_display_integration_rc43.py::
    test_confirmed_shaibi_import_keeps_display_channel_separate_and_stable``
    was updated accordingly with the real, current numbers — it is not a
    stale assumption to relax casually next time either; re-verify against
    the real fixture DXF before touching it again.

    ``C_Arm_steni`` remains unverified — do not admit without checking its
    real label format first, the same way arm_koloni's second grammar was
    discovered by inspecting real fragments before writing a parser branch.

    Geometric length verification no longer gates acceptance for ANY
    dialect above (owner decision 2026-09-10): a fully parsed annotation is
    accepted directly from its declared count/diameter/length whenever no
    unique geometric proof exists, matching the "read the specification,
    don't audit the drafting precision" policy already applied everywhere
    else in the product — see ``PROXY_DECLARED_LENGTH_CODE``,
    ``declared_length_annotation_count``.

    ``arm_koloni`` column-group multiplier (owner-confirmed 2026-09-10): the
    dialect's "(общо N)" totals are declared per ONE column ("Армировката е
    за една колона" appears directly on the real drawing). When several
    physical columns share an identical cross-section/reinforcement, the
    drawing groups them under one plain-text header ending in "N бр." (e.g.
    ``"K73, K80, K83, K88 (25/55) - 4бр."``) instead of repeating the detail.
    Every resolved koloni annotation is matched to its nearest such header
    (spatial proximity, header above the detail) and its count is multiplied
    by the header's "бр." value — see ``_COLUMN_GROUP_HEADER_RE``,
    ``_apply_column_group_multiplier``. Verified against the designer's own
    declared drawing total (98.3% match, up from 68% before the multiplier).

    ``arm_shaibi`` wall-group multiplier (owner-confirmed 2026-09-10): a
    different mechanism from koloni's single combined header -- each
    physical shear wall gets its OWN "Ш<N> - W/H" cross-section label
    (layer ``C_TXT_shaibi``); walls sharing an identical section nearby
    share one drawn detail. See ``_SHAIBI_WALL_LABEL_RE``,
    ``_apply_shaibi_wall_group_multiplier``. Verified: 96.0% of the
    designer's declared total (up from 59%), with an honest per-diameter
    breakdown, not a lucky total from cancelling errors.
    """

    source_layers: tuple[str, ...] = (
        "GA_X", "GA_Y",
        "C_ARM_GREDI", "C_ARM_STYLBI", "C_ARM_KOBILICI",
        "ARM_KOLONI", "ARM_SHAIBI",
    )
    scale_hypotheses: tuple[float, ...] = (1.0, 0.1, 10.0, 0.01, 100.0)
    exact_length_tolerance_cm: float = 0.05
    endpoint_tolerance_raw: float = 0.75
    max_preceding_proxy_entities: int = 7
    max_geometry_members: int = 6
    minimum_scale_evidence: int = 3
    minimum_scale_coverage: float = 0.80
    minimum_scale_winner_margin: int = 2


@dataclass(frozen=True)
class ProxyAnnotationSpec:
    parent_handle: str
    parent_layer: str
    modelspace_index: int
    proxy_sequence_index: int
    position_key: int
    count: int
    diameter_mm: float
    length_cm: float
    spacing_cm: float | None
    tokens: tuple[str, ...]
    fragments: tuple[DxfEntityRecord, ...]
    grammar: str = _GRAMMAR_LEAD_COUNT
    #: "N бр." of the matched column-group header, if this annotation's
    #: count was multiplied (see ``_apply_column_group_multiplier``).
    column_group_count: int = 1
    column_group_label: str | None = None
    #: The declared per-column count before multiplication, kept for audit.
    column_group_source_count: int | None = None
    #: Trailing numeric fragments after "(общо N)" — the designer's own
    #: bend-segment breakdown (e.g. (51, 26, 51, 26, 8, 8)). Populated only
    #: when EVERY trailing fragment parses as a positive number (fail
    #: closed); see ``_parse_total_count_annotation``.
    declared_segments_cm: tuple[float, ...] | None = None

    @property
    def text(self) -> str:
        return f"{self.count}N{_format_number(self.diameter_mm)}x{_format_number(self.length_cm)}"


@dataclass(frozen=True)
class ProxyGeometryMember:
    record: DxfEntityRecord
    length_raw: float
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass(frozen=True)
class ProxyGeometryGroup:
    parent_handle: str
    parent_layer: str
    modelspace_index: int
    proxy_sequence_index: int
    members: tuple[ProxyGeometryMember, ...]


@dataclass(frozen=True)
class ColumnGroupHeader:
    """A plain-text "K.., K.. (WxH) - Nбр." label from the koloni dialect
    (not proxy content — an ordinary modelspace TEXT/MTEXT entity)."""

    x: float
    y: float
    count: int
    label: str


@dataclass(frozen=True)
class ColumnGroupBox:
    """A closed rectangular detail-box outline (ordinary LWPOLYLINE, not
    proxy content) drawn around one column-group's own reinforcement
    detail. See ``_COLUMN_GROUP_BOX_MIN_HEIGHT`` for why size is not
    hard-coded. ``header`` is resolved once, at collection time, by
    nearest-header-to-box-center — the box itself never moves relative to
    its own header, unlike an individual annotation that may sit
    marginally closer to a neighbouring group's header text."""

    x0: float
    x1: float
    y0: float
    y1: float
    header: ColumnGroupHeader

    def contains(self, point: tuple[float, float]) -> bool:
        return self.x0 <= point[0] <= self.x1 and self.y0 <= point[1] <= self.y1


@dataclass(frozen=True)
class ShaibiWallSectionLabel:
    """A plain-text "Ш<N> - W/H" per-wall cross-section label from the
    shaibi dialect, layer ``C_TXT_shaibi`` (not proxy content)."""

    x: float
    y: float
    section: tuple[str, str]


@dataclass(frozen=True)
class ShaibiWallGroup:
    """One or more ``ShaibiWallSectionLabel`` sharing an identical section
    close together -- ``size`` walls draw and declare ONE shared detail."""

    x: float
    y: float
    size: int


@dataclass(frozen=True)
class ProxyAssociation:
    annotation: ProxyAnnotationSpec
    geometry_group: ProxyGeometryGroup
    members: tuple[ProxyGeometryMember, ...]
    scale_factor: float
    exact_total_cm: float
    predecessor_distance: int
    modelspace_distance: int


@dataclass
class ProxyAdapterResult:
    annotation_records: list[DxfEntityRecord] = field(default_factory=list)
    geometry_records: list[DxfEntityRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proxy_entity_count: int = 0
    text_fragment_count: int = 0
    polyline_count: int = 0
    arc_count: int = 0
    parsed_annotation_count: int = 0
    resolved_annotation_count: int = 0
    unresolved_annotation_count: int = 0
    #: Of resolved_annotation_count, how many were accepted from the
    #: designer's declared text length rather than an exact geometric
    #: length proof (owner-confirmed 2026-09-10; see
    #: PROXY_DECLARED_LENGTH_CODE).
    declared_length_annotation_count: int = 0
    #: Of resolved_annotation_count, how many had their count multiplied by
    #: a matched "K.. (WxH) - Nбр." column-group header (arm_koloni only) —
    #: see ``_apply_column_group_multiplier``.
    column_group_multiplied_count: int = 0
    #: Of declared_length_annotation_count, how many got a MASTER shape
    #: derived from their own declared bend segments, independently proven
    #: by real bend-arc geometry — see ``PROXY_DECLARED_SHAPE_CODE``.
    declared_shape_evidence_count: int = 0
    #: How many arm_shaibi annotations had their count multiplied by a
    #: matched "Ш<N> - W/H" wall group — see
    #: ``_apply_shaibi_wall_group_multiplier``.
    shaibi_wall_group_multiplied_count: int = 0
    #: How many arm_shaibi annotations were dropped as reference-only
    #: visual duplicates (either a duplicate of another annotation's exact
    #: content, or too far from any wall-group label to attribute safely).
    shaibi_wall_group_excluded_count: int = 0
    scale_factor: float | None = None
    scale_evidence_count: int = 0
    scale_evidence_by_hypothesis: dict[str, int] = field(default_factory=dict)
    position_occurrences: dict[str, int] = field(default_factory=dict)
    missing_position_keys: list[int] = field(default_factory=list)


RecordFactory = Callable[[Any], DxfEntityRecord]


def extract_acad_proxy_rebar(
    modelspace_entities: Sequence[Any],
    *,
    record_factory: RecordFactory,
    units_code: int | None,
    config: ProxyAdapterConfig | None = None,
) -> ProxyAdapterResult:
    """Recover strictly proven rebar annotations and their proxy geometry.

    The adapter uses three independent facts before it creates matcher input:

    * the proxy is on one of the admitted dialect layers;
    * its text fragments form one complete ``position/count/N/diameter/x/L``
      sequence;
    * the nearest preceding proxy group contains one and only one connected
      geometry subset whose unfolded length equals L at one uniquely calibrated
      drawing scale.

    No source entity is modified.  Derived handles retain both annotation and
    geometry proxy parents for audit and for reference-only ownership handling.
    """

    cfg = config or ProxyAdapterConfig()
    allowed_layers = {value.strip().upper() for value in cfg.source_layers}
    result = ProxyAdapterResult()
    annotations: list[ProxyAnnotationSpec] = []
    geometry_groups: list[ProxyGeometryGroup] = []
    column_group_headers: list[ColumnGroupHeader] = []
    column_group_box_bboxes: list[tuple[float, float, float, float]] = []
    shaibi_wall_labels: list[ShaibiWallSectionLabel] = []
    proxy_sequence_index = -1

    for modelspace_index, entity in enumerate(modelspace_entities):
        entity_dxftype = str(entity.dxftype()).upper()
        if entity_dxftype != "ACAD_PROXY_ENTITY":
            if entity_dxftype in {"TEXT", "MTEXT"}:
                try:
                    header_record = record_factory(entity)
                except Exception:
                    header_record = None
                if header_record is not None:
                    header = _parse_column_group_header(header_record)
                    if header is not None:
                        column_group_headers.append(header)
                    if header_record.layer.strip().upper() == _SHAIBI_WALL_LABEL_LAYER:
                        wall_label = _parse_shaibi_wall_section_label(header_record)
                        if wall_label is not None:
                            shaibi_wall_labels.append(wall_label)
            elif entity_dxftype == "LWPOLYLINE" and str(
                getattr(entity.dxf, "layer", "") or ""
            ).strip().upper() == "ARM_KOLONI":
                try:
                    box_record = record_factory(entity)
                except Exception:
                    box_record = None
                if box_record is not None:
                    bbox = _parse_column_group_box_bbox(box_record)
                    if bbox is not None:
                        column_group_box_bboxes.append(bbox)
            continue
        result.proxy_entity_count += 1
        proxy_sequence_index += 1
        parent_handle = str(getattr(entity.dxf, "handle", "") or "")
        parent_layer = str(getattr(entity.dxf, "layer", "0") or "0")
        try:
            virtual_entities = list(entity.virtual_entities())
        except Exception as exc:  # noqa: BLE001 - malformed proxy must not abort import
            result.warnings.append(
                f"Proxy {parent_handle or '?'} не може да бъде разпукнат безопасно: {exc}"
            )
            continue

        counts = Counter(str(value.dxftype()).upper() for value in virtual_entities)
        result.text_fragment_count += counts.get("TEXT", 0) + counts.get("MTEXT", 0)
        result.polyline_count += counts.get("POLYLINE", 0) + counts.get("LWPOLYLINE", 0)
        result.arc_count += counts.get("ARC", 0)

        if parent_layer.strip().upper() not in allowed_layers:
            continue

        fragment_records: list[DxfEntityRecord] = []
        member_records: list[ProxyGeometryMember] = []
        for virtual_index, virtual in enumerate(virtual_entities):
            entity_type = str(virtual.dxftype()).upper()
            if entity_type in {"TEXT", "MTEXT"}:
                try:
                    fragment = record_factory(virtual)
                except Exception:
                    continue
                virtual_layer = fragment.layer
                preserved_raw = dict(fragment.raw or {})
                fragment.handle = f"PXTXT_{parent_handle}_{virtual_index}"
                fragment.layer = parent_layer
                preserved_raw.update(
                    {
                        "source": "ACAD_PROXY_GRAPHIC_TEXT_FRAGMENT",
                        "parent_proxy_handle": parent_handle,
                        "parent_proxy_layer": parent_layer,
                        # Keep the virtual primitive's own layer for BYLAYER
                        # display resolution.  The public record layer remains
                        # the established parent layer for engineering logic.
                        "proxy_virtual_layer": virtual_layer,
                        "virtual_index": virtual_index,
                    }
                )
                fragment.raw = preserved_raw
                fragment_records.append(fragment)
                continue
            # ARC admitted alongside LINE/POLYLINE/LWPOLYLINE (2026-09-10):
            # the C_ARM_Gredi/C_arm_stylbi/C_Arm_kobilici dialect draws bar
            # bends/hooks as ARC primitives inside the proxy graphic, and
            # _geometry_member already has a correct ARC branch
            # (arc_path_from_geometry) that was simply never reached because
            # this gate excluded ARC before it got there.
            if entity_type not in {"LINE", "POLYLINE", "LWPOLYLINE", "ARC"}:
                continue
            try:
                record = record_factory(virtual)
            except Exception:
                continue
            virtual_layer = record.layer
            preserved_raw = dict(record.raw or {})
            record.handle = f"PXG_{parent_handle}_{virtual_index}_0"
            record.layer = f"ARM_PROXY_{parent_layer}"
            preserved_raw.update(
                {
                    "source": PROXY_GEOMETRY_SOURCE,
                    "parent_proxy_handle": parent_handle,
                    "parent_proxy_layer": parent_layer,
                    "proxy_virtual_layer": virtual_layer,
                    "virtual_index": virtual_index,
                }
            )
            record.raw = preserved_raw
            member = _geometry_member(record)
            if member is not None:
                member_records.append(member)

        annotation = _parse_annotation(
            parent_handle=parent_handle,
            parent_layer=parent_layer,
            modelspace_index=modelspace_index,
            proxy_sequence_index=proxy_sequence_index,
            fragments=fragment_records,
        )
        if annotation is None:
            annotation = _parse_total_count_annotation(
                parent_handle=parent_handle,
                parent_layer=parent_layer,
                modelspace_index=modelspace_index,
                proxy_sequence_index=proxy_sequence_index,
                fragments=fragment_records,
            )
        if annotation is None:
            annotation = _parse_total_count_annotation_with_leg_count(
                parent_handle=parent_handle,
                parent_layer=parent_layer,
                modelspace_index=modelspace_index,
                proxy_sequence_index=proxy_sequence_index,
                fragments=fragment_records,
            )
        if annotation is not None:
            annotations.append(annotation)
        if member_records:
            geometry_groups.append(
                ProxyGeometryGroup(
                    parent_handle=parent_handle,
                    parent_layer=parent_layer,
                    modelspace_index=modelspace_index,
                    proxy_sequence_index=proxy_sequence_index,
                    members=tuple(member_records),
                )
            )

    if column_group_headers:
        column_group_boxes = _resolve_column_group_boxes(
            column_group_box_bboxes, column_group_headers
        )
        annotations = [
            _apply_column_group_multiplier(annotation, column_group_headers, column_group_boxes)
            for annotation in annotations
        ]
        result.column_group_multiplied_count = sum(
            1 for value in annotations if value.column_group_count > 1
        )

    if shaibi_wall_labels:
        wall_groups = _cluster_shaibi_wall_groups(shaibi_wall_labels)
        before_count = len(annotations)
        annotations, shaibi_multiplied = _apply_shaibi_wall_group_multiplier(
            annotations, wall_groups
        )
        result.shaibi_wall_group_multiplied_count = shaibi_multiplied
        result.shaibi_wall_group_excluded_count = before_count - len(annotations)

    result.parsed_annotation_count = len(annotations)
    if not annotations:
        return result

    geometry_groups_by_sequence = {
        group.proxy_sequence_index: group for group in geometry_groups
    }

    associations_by_scale: dict[float, list[ProxyAssociation]] = {}
    for scale in cfg.scale_hypotheses:
        associations_by_scale[float(scale)] = _associate_annotations(
            annotations,
            geometry_groups,
            scale_factor=float(scale),
            config=cfg,
        )
    evidence = {
        float(scale): len(associations)
        for scale, associations in associations_by_scale.items()
    }
    result.scale_evidence_by_hypothesis = {
        _format_number(scale): count for scale, count in sorted(evidence.items())
    }
    selected_scale = _select_scale(evidence, len(annotations), cfg)

    association_by_annotation: dict[str, ProxyAssociation] = {}
    if selected_scale is not None:
        associations = associations_by_scale[selected_scale]
        result.scale_factor = selected_scale
        result.scale_evidence_count = len(associations)
        association_by_annotation = {
            association.annotation.parent_handle: association for association in associations
        }
    else:
        result.warnings.append(
            "Proxy адаптерът не намери еднозначно доказан мащаб от надпис–геометрия за "
            "целия чертеж; надписите с пълна декларирана информация (брой/диаметър/дължина) "
            "все пак се приемат директно от текста, без геометрично доказателство."
        )

    geometry_by_handle: dict[str, DxfEntityRecord] = {}
    for annotation in annotations:
        association = association_by_annotation.get(annotation.parent_handle)
        if association is not None:
            geometry_handles = tuple(member.record.handle for member in association.members)
            for member in association.members:
                geometry_by_handle.setdefault(member.record.handle, member.record)
            result.annotation_records.append(
                _annotation_record(annotation, association, geometry_handles)
            )
            continue
        # Owner-confirmed 2026-09-10: no unique geometric length proof for
        # this annotation (either no scale was globally proven, or this
        # specific bar's drawn geometry did not sum exactly to its declared
        # length — a known duplicate-geometry artifact of this designer's
        # plugin). The declared count/diameter/length are trusted directly.
        # Any geometry belonging to this annotation's OWN proxy (the
        # self-contained C_ARM_Gredi-style dialect) is still attached, but
        # purely as a non-exclusive reference for the viewer/audit trail.
        own_group = geometry_groups_by_sequence.get(annotation.proxy_sequence_index)
        reference_handles = (
            tuple(member.record.handle for member in own_group.members)
            if own_group is not None
            else ()
        )
        for member in (own_group.members if own_group is not None else ()):
            geometry_by_handle.setdefault(member.record.handle, member.record)
        record = _annotation_record(annotation, None, reference_handles)
        bend_arc_count = _count_structural_bend_arcs(own_group)
        record.raw["declared_shape_bend_arc_count"] = bend_arc_count
        shape_evidence = _derive_declared_shape_evidence(
            annotation, bend_arc_count, own_group
        )
        if shape_evidence is not None:
            record.raw["declared_shape_code"] = shape_evidence.shape_code
            record.raw["declared_shape_segments_cm"] = list(shape_evidence.segments)
            record.raw["declared_shape_confidence"] = shape_evidence.confidence
            record.raw["declared_shape_source"] = PROXY_DECLARED_SHAPE_CODE
            if shape_evidence.leg_directions_deg:
                record.raw["declared_shape_leg_directions_deg"] = list(
                    shape_evidence.leg_directions_deg
                )
            if shape_evidence.reduction_angle_deg is not None:
                record.raw["declared_shape_reduction_angle_deg"] = (
                    shape_evidence.reduction_angle_deg
                )
            result.declared_shape_evidence_count += 1
        result.annotation_records.append(record)
        result.declared_length_annotation_count += 1
    result.geometry_records = list(geometry_by_handle.values())
    result.resolved_annotation_count = len(result.annotation_records)
    result.unresolved_annotation_count = len(annotations) - len(result.annotation_records)

    occurrences = Counter(str(value.position_key) for value in annotations)
    result.position_occurrences = dict(sorted(occurrences.items(), key=lambda item: int(item[0])))
    unique_positions = sorted(int(value) for value in occurrences)
    if unique_positions:
        lower, upper = unique_positions[0], unique_positions[-1]
        span = upper - lower + 1
        density = len(unique_positions) / max(span, 1)
        missing = sorted(set(range(lower, upper + 1)) - set(unique_positions))
        if density >= 0.90 and len(missing) <= 5:
            result.missing_position_keys = missing
            if missing:
                result.warnings.append(
                    "Proxy позиционната последователност има липсващи ключове: "
                    + ", ".join(str(value) for value in missing)
                    + ". Те са маркирани за проверка и не са създадени автоматично."
                )

    if (
        selected_scale is not None
        and units_code == 4
        and abs(selected_scale - 0.1) > 1e-12
    ):
        result.warnings.append(
            "DXF $INSUNITS е mm, но proxy надпис–геометрия доказва мащаб "
            f"{_format_number(selected_scale)} към L в cm. Заглавката не е използвана за сляпо деление."
        )
    if result.unresolved_annotation_count:
        result.warnings.append(
            f"{result.unresolved_annotation_count} proxy армировъчни надписа останаха без "
            "еднозначна predecessor/дължинна връзка."
        )
    if result.declared_length_annotation_count:
        result.warnings.append(
            f"{result.declared_length_annotation_count} proxy армировъчни надписа са приети "
            "директно от декларираната дължина в текста (без еднозначно геометрично "
            "доказателство) — виж KNOWN_LIMITATIONS_v0.1.0_BG.md."
        )
    if result.column_group_multiplied_count:
        result.warnings.append(
            f"{result.column_group_multiplied_count} proxy армировъчни надписа (arm_koloni) са "
            "умножени по броя еднакви колони от груповото заглавие (\"... - N бр.\") — "
            "виж KNOWN_LIMITATIONS_v0.1.0_BG.md."
        )
    if result.declared_shape_evidence_count:
        result.warnings.append(
            f"{result.declared_shape_evidence_count} proxy армировъчни надписа получиха MASTER "
            "форма, изведена от собствените им декларирани сегменти (независимо потвърдени с "
            "реална дъгова геометрия) — виж KNOWN_LIMITATIONS_v0.1.0_BG.md."
        )
    return result


def _parse_annotation(
    *,
    parent_handle: str,
    parent_layer: str,
    modelspace_index: int,
    proxy_sequence_index: int,
    fragments: Sequence[DxfEntityRecord],
) -> ProxyAnnotationSpec | None:
    tokens = tuple(str(value.text or "").strip() for value in fragments)
    if len(tokens) not in {7, 8}:
        return None
    if tokens[2].strip().upper() != "N" or tokens[4].strip().lower() != "x":
        return None
    position = _positive_int(tokens[0])
    count = _positive_int(tokens[1])
    diameter = _positive_float(tokens[3])
    length = _positive_float(tokens[5])
    if position is None or count is None or diameter is None or length is None:
        return None

    spacing: float | None = None
    if len(tokens) == 8:
        if tokens[6] != "/":
            return None
        spacing = _positive_float(tokens[7])
        if spacing is None:
            return None
    elif tokens[6] not in {"", "-"}:
        return None

    rotations: list[float] = []
    for fragment in fragments:
        try:
            rotations.append(float(fragment.geometry.get("rotation", 0.0) or 0.0) % 360.0)
        except (TypeError, ValueError):
            return None
    if rotations and max(rotations) - min(rotations) > 0.01:
        return None

    annotation = f"{count}N{_format_number(diameter)}x{_format_number(length)}"
    if not looks_like_rebar_text(annotation):
        return None
    return ProxyAnnotationSpec(
        parent_handle=parent_handle,
        parent_layer=parent_layer,
        modelspace_index=modelspace_index,
        proxy_sequence_index=proxy_sequence_index,
        position_key=position,
        count=count,
        diameter_mm=diameter,
        length_cm=length,
        spacing_cm=spacing,
        tokens=tokens,
        fragments=tuple(fragments),
    )


def _parse_total_count_annotation(
    *,
    parent_handle: str,
    parent_layer: str,
    modelspace_index: int,
    proxy_sequence_index: int,
    fragments: Sequence[DxfEntityRecord],
) -> ProxyAnnotationSpec | None:
    """Second designer, second grammar (owner-confirmed 2026-09-10): a bar
    whose total count comes from a trailing ``(общо N бр.)`` / ``(общо N)``
    marker instead of a leading count token — used for both main
    reinforcement (``N25x481(общо 22бр.)``) and stirrups
    (``N8x170(общо 26)``, spacing given elsewhere, not in this label).

    Unlike ``_parse_annotation``, this proxy's fragment list is NOT expected
    to be exactly the label: the same proxy also carries unrelated nearby
    dimension/detail numbers (own hook/cross-section callouts) as further
    TEXT fragments straight after the closing bracket, confirmed real-data
    (``tests/test_acad_proxy_adapter.py``). Only the recognised prefix is
    used; nothing after the closing bracket is inspected, its rotation is
    not required to match, and it never contributes to ``tokens``.
    """

    non_empty = [
        fragment for fragment in fragments if str(fragment.text or "").strip()
    ]
    if len(non_empty) < 8:
        return None
    label = non_empty[:8]
    tokens = tuple(str(value.text or "").strip() for value in label)
    if tokens[1].strip().upper() != "N" or tokens[3].strip().lower() != "x":
        return None
    if not tokens[5].strip().lower().startswith("(общо"):
        return None
    if not tokens[7].endswith(")"):
        return None
    position = _positive_int(tokens[0])
    diameter = _positive_float(tokens[2])
    length = _positive_float(tokens[4])
    count = _positive_int(tokens[6])
    if position is None or diameter is None or length is None or count is None:
        return None

    rotations: list[float] = []
    for fragment in label:
        try:
            rotations.append(float(fragment.geometry.get("rotation", 0.0) or 0.0) % 360.0)
        except (TypeError, ValueError):
            return None
    if rotations and max(rotations) - min(rotations) > 0.01:
        return None

    annotation = f"{count}N{_format_number(diameter)}x{_format_number(length)}"
    if not looks_like_rebar_text(annotation):
        return None

    trailing_texts = [str(fragment.text or "").strip() for fragment in non_empty[8:]]
    declared_segments = _parse_declared_segments(trailing_texts)
    return ProxyAnnotationSpec(
        parent_handle=parent_handle,
        parent_layer=parent_layer,
        modelspace_index=modelspace_index,
        proxy_sequence_index=proxy_sequence_index,
        position_key=position,
        count=count,
        diameter_mm=diameter,
        length_cm=length,
        spacing_cm=None,
        tokens=tokens,
        fragments=tuple(label),
        grammar=_GRAMMAR_TRAILING_TOTAL_COUNT,
        declared_segments_cm=declared_segments,
    )


def _parse_total_count_annotation_with_leg_count(
    *,
    parent_handle: str,
    parent_layer: str,
    modelspace_index: int,
    proxy_sequence_index: int,
    fragments: Sequence[DxfEntityRecord],
) -> ProxyAnnotationSpec | None:
    """arm_shaibi dialect, third grammar variant (owner-confirmed
    2026-09-10, real example Ш4: "17n10x375 (Общо 34бр)"): an extra numeric
    fragment sits between the position and "N" -- a "per-leg" count that is
    PURELY informational and already folded into the trailing "(общо N)"
    total. Proof it is never itself the production count: a sibling proxy
    for the exact same physical detail spells the same relationship out
    literally as "2x17" (2 legs x 17 = 34) — see
    ``tests/test_acad_proxy_adapter.py``. The per-leg value is parsed only
    to be validated as a number and then discarded; "(общо N)" is always
    the authoritative count, exactly as in ``_parse_total_count_annotation``.
    """

    non_empty = [
        fragment for fragment in fragments if str(fragment.text or "").strip()
    ]
    if len(non_empty) < 9:
        return None
    label = non_empty[:9]
    tokens = tuple(str(value.text or "").strip() for value in label)
    if tokens[2].strip().upper() != "N" or tokens[4].strip().lower() != "x":
        return None
    if not tokens[6].strip().lower().startswith("(общо"):
        return None
    if not tokens[8].endswith(")"):
        return None
    position = _positive_int(tokens[0])
    leg_count = _positive_int(tokens[1])
    diameter = _positive_float(tokens[3])
    length = _positive_float(tokens[5])
    count = _positive_int(tokens[7])
    if (
        position is None
        or leg_count is None
        or diameter is None
        or length is None
        or count is None
    ):
        return None

    rotations: list[float] = []
    for fragment in label:
        try:
            rotations.append(float(fragment.geometry.get("rotation", 0.0) or 0.0) % 360.0)
        except (TypeError, ValueError):
            return None
    if rotations and max(rotations) - min(rotations) > 0.01:
        return None

    annotation = f"{count}N{_format_number(diameter)}x{_format_number(length)}"
    if not looks_like_rebar_text(annotation):
        return None

    trailing_texts = [str(fragment.text or "").strip() for fragment in non_empty[9:]]
    declared_segments = _parse_declared_segments(trailing_texts)
    return ProxyAnnotationSpec(
        parent_handle=parent_handle,
        parent_layer=parent_layer,
        modelspace_index=modelspace_index,
        proxy_sequence_index=proxy_sequence_index,
        position_key=position,
        count=count,
        diameter_mm=diameter,
        length_cm=length,
        spacing_cm=None,
        tokens=tokens,
        fragments=tuple(label),
        grammar=_GRAMMAR_TRAILING_TOTAL_COUNT,
        declared_segments_cm=declared_segments,
    )


def _parse_declared_segments(trailing_tokens: Sequence[str]) -> tuple[float, ...] | None:
    """Owner-confirmed real data (2026-09-10): the numbers straight after
    "(общо N)" are the designer's own bend-segment breakdown, not unrelated
    noise -- see ``PROXY_DECLARED_SHAPE_CODE``. Fail closed: any non-numeric
    or non-positive trailing fragment (this dialect really does carry
    unrelated detail callouts sometimes) discards the whole sequence rather
    than guessing which prefix is real."""

    if not trailing_tokens:
        return None
    values: list[float] = []
    for token in trailing_tokens:
        value = _positive_float(token)
        if value is None:
            return None
        values.append(value)
    return tuple(values)


def _parse_column_group_header(record: DxfEntityRecord) -> ColumnGroupHeader | None:
    text = str(record.text or "").strip()
    if not text:
        return None
    match = _COLUMN_GROUP_HEADER_RE.search(text)
    if match is None:
        return None
    count = _positive_int(match.group(1))
    if count is None:
        return None
    point = _xy(record.geometry.get("insert"))
    if point is None:
        return None
    return ColumnGroupHeader(x=point[0], y=point[1], count=count, label=text)


def _parse_column_group_box_bbox(
    record: DxfEntityRecord,
) -> tuple[float, float, float, float] | None:
    """Bounding box of a candidate group detail-box outline, or None if this
    LWPOLYLINE is not closed or is too small to be one (see
    ``_COLUMN_GROUP_BOX_MIN_HEIGHT``/``_COLUMN_GROUP_BOX_MIN_WIDTH`` for why
    size is checked only as "clearly bigger than a per-position icon", not
    matched against one specific dimension)."""

    if record.entity_type.upper() != "LWPOLYLINE":
        return None
    path = polyline_path_from_geometry(record.geometry)
    if path is None or not path.closed or len(path.points) < 4:
        return None
    xs = [point[0] for point in path.points]
    ys = [point[1] for point in path.points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if (x1 - x0) < _COLUMN_GROUP_BOX_MIN_WIDTH or (y1 - y0) < _COLUMN_GROUP_BOX_MIN_HEIGHT:
        return None
    return (x0, x1, y0, y1)


def _resolve_column_group_boxes(
    box_bboxes: Sequence[tuple[float, float, float, float]],
    headers: Sequence[ColumnGroupHeader],
) -> list[ColumnGroupBox]:
    """Associate each candidate box with its own nearest header (by box
    CENTER, using the same nearest-neighbour rule as
    ``_nearest_column_group_header``). A box's center-to-header distance is
    not subject to the same razor-thin ambiguity an individual annotation's
    anchor can hit, because the box is large and, in every real case
    checked, sits far closer to its own header than to any neighbour's."""

    boxes: list[ColumnGroupBox] = []
    for x0, x1, y0, y1 in box_bboxes:
        header = _nearest_column_group_header(((x0 + x1) / 2.0, (y0 + y1) / 2.0), headers)
        if header is None:
            continue
        boxes.append(ColumnGroupBox(x0=x0, x1=x1, y0=y0, y1=y1, header=header))
    return boxes


def _nearest_column_group_header(
    point: tuple[float, float],
    headers: Sequence[ColumnGroupHeader],
) -> ColumnGroupHeader | None:
    if not headers:
        return None
    above = [
        header for header in headers
        if header.y >= point[1] - _COLUMN_GROUP_ABOVE_TOLERANCE
    ]
    pool = above or headers
    return min(
        pool,
        key=lambda header: (header.x - point[0]) ** 2 + (header.y - point[1]) ** 2,
    )


def _apply_column_group_multiplier(
    annotation: ProxyAnnotationSpec,
    headers: Sequence[ColumnGroupHeader],
    boxes: Sequence[ColumnGroupBox] = (),
) -> ProxyAnnotationSpec:
    """Owner-confirmed 2026-09-10 (see ``_COLUMN_GROUP_HEADER_RE`` above):
    multiply an arm_koloni "(общо N)" total by the "N бр." of its own
    column-group header. Deliberately double-gated (grammar AND layer) so
    this never touches any other dialect, even if an unrelated drawing
    happened to contain trailing "... N бр." text somewhere.

    Owner-confirmed 2026-09-12: nearest-header-BY-TEXT-DISTANCE alone is
    fragile when several groups' headers sit in the same row (real case:
    an annotation fell 14 drawing units -- under 2% -- closer to a
    NEIGHBOURING 2-column header than to the 6-column header whose own
    detail box it was actually drawn inside, silently halving its
    quantity). When the annotation's anchor falls inside a detected
    ``ColumnGroupBox``, that box's own header wins outright -- it is never
    in this kind of near-tie, because the box itself does not move
    relative to its own header. Text-distance stays the fallback for
    annotations with no applicable box (see
    ``_COLUMN_GROUP_BOX_MIN_HEIGHT``: not every group draws one)."""

    if annotation.grammar != _GRAMMAR_TRAILING_TOTAL_COUNT:
        return annotation
    if annotation.parent_layer.strip().upper() != "ARM_KOLONI":
        return annotation
    if not annotation.fragments:
        return annotation
    anchor = _xy(annotation.fragments[0].geometry.get("insert"))
    if anchor is None:
        return annotation
    header = None
    for box in boxes:
        if box.contains(anchor):
            header = box.header
            break
    if header is None:
        header = _nearest_column_group_header(anchor, headers)
    if header is None:
        return annotation
    if header.count <= 1:
        # A single, un-shared column multiplies nothing -- but the working
        # sheet is still laid out column by column, so the header this
        # position was drawn under is recorded either way (owner-requested
        # 2026-09-12: "опис в колони - подробно колона по колона").
        # ``count`` is untouched, so nothing downstream changes quantity.
        return replace(
            annotation,
            column_group_count=header.count,
            column_group_label=header.label,
            column_group_source_count=annotation.count,
        )
    return replace(
        annotation,
        count=annotation.count * header.count,
        column_group_count=header.count,
        column_group_label=header.label,
        column_group_source_count=annotation.count,
    )


def _parse_shaibi_wall_section_label(
    record: DxfEntityRecord,
) -> ShaibiWallSectionLabel | None:
    text = str(record.text or "").strip()
    if not text:
        return None
    match = _SHAIBI_WALL_LABEL_RE.search(text)
    if match is None:
        return None
    point = _xy(record.geometry.get("insert"))
    if point is None:
        return None
    return ShaibiWallSectionLabel(
        x=point[0], y=point[1], section=(match.group(1), match.group(2))
    )


def _cluster_shaibi_wall_groups(
    labels: Sequence[ShaibiWallSectionLabel],
) -> list[ShaibiWallGroup]:
    used = [False] * len(labels)
    groups: list[ShaibiWallGroup] = []
    for i, label in enumerate(labels):
        if used[i]:
            continue
        cluster = [label]
        used[i] = True
        for j in range(i + 1, len(labels)):
            if used[j]:
                continue
            other = labels[j]
            if other.section != label.section:
                continue
            if _points_close(
                (label.x, label.y), (other.x, other.y),
                _SHAIBI_WALL_GROUP_CLUSTER_DISTANCE,
            ):
                cluster.append(other)
                used[j] = True
        groups.append(ShaibiWallGroup(x=cluster[0].x, y=cluster[0].y, size=len(cluster)))
    return groups


def _nearest_shaibi_wall_group(
    point: tuple[float, float],
    groups: Sequence[ShaibiWallGroup],
) -> tuple[ShaibiWallGroup | None, float]:
    if not groups:
        return None, math.inf
    best = min(groups, key=lambda group: math.hypot(group.x - point[0], group.y - point[1]))
    distance = math.hypot(best.x - point[0], best.y - point[1])
    return best, distance


def _shaibi_annotation_anchor(annotation: ProxyAnnotationSpec) -> tuple[float, float] | None:
    if not annotation.fragments:
        return None
    return _xy(annotation.fragments[0].geometry.get("insert"))


def _deduplicate_shaibi_visual_copies(
    annotations: Sequence[ProxyAnnotationSpec],
    wall_groups: Sequence[ShaibiWallGroup],
) -> list[ProxyAnnotationSpec]:
    """Collapse a bar shown TWICE on the drawing (e.g. both ends of one
    bend labelled separately -- real confirmed case: "N16x323 (общо 4)"
    belongs only to Ш3, not also to Ш4/Ш7) down to one. Two annotations
    with IDENTICAL declared (diameter, length, count) that sit close
    together are the SAME physical bar, not two coincidentally-identical
    ones -- distinct walls on this drawing's grid sit much farther apart.
    Keeps whichever copy is most confidently placed (closest to a real
    wall-group label)."""

    by_key: dict[tuple[float, float, int], list[ProxyAnnotationSpec]] = {}
    for annotation in annotations:
        key = (annotation.diameter_mm, annotation.length_cm, annotation.count)
        by_key.setdefault(key, []).append(annotation)

    def distance_to_nearest_group(candidate: ProxyAnnotationSpec) -> float:
        anchor = _shaibi_annotation_anchor(candidate)
        if anchor is None:
            return math.inf
        return _nearest_shaibi_wall_group(anchor, wall_groups)[1]

    deduped: list[ProxyAnnotationSpec] = []
    for items in by_key.values():
        used = [False] * len(items)
        for i, item in enumerate(items):
            if used[i]:
                continue
            cluster = [item]
            used[i] = True
            anchor_i = _shaibi_annotation_anchor(item)
            for j in range(i + 1, len(items)):
                if used[j] or anchor_i is None:
                    continue
                anchor_j = _shaibi_annotation_anchor(items[j])
                if anchor_j is None:
                    continue
                if _points_close(anchor_i, anchor_j, _SHAIBI_DUPLICATE_CONTENT_DISTANCE):
                    cluster.append(items[j])
                    used[j] = True
            deduped.append(min(cluster, key=distance_to_nearest_group))
    return deduped


def _apply_shaibi_wall_group_multiplier(
    annotations: Sequence[ProxyAnnotationSpec],
    wall_groups: Sequence[ShaibiWallGroup],
) -> tuple[list[ProxyAnnotationSpec], int]:
    """Owner-confirmed 2026-09-10 (see ``_SHAIBI_WALL_LABEL_RE`` above):
    multiply an arm_shaibi "(общо N)" total by the number of walls sharing
    its nearest "Ш<N> - W/H" cross-section group. Deliberately double-gated
    (grammar AND layer) so this never touches any other dialect. Also
    drops annotations too far from any wall-group label (>
    ``_SHAIBI_WALL_GROUP_MATCH_DISTANCE``) -- real, validated evidence that
    these are reference-only visual copies drawn elsewhere on the sheet,
    not a real, unattributed position (see module-level comment for the
    measured distance gap).

    arm_shaibi annotations that parsed via the OTHER (``LEAD_COUNT``)
    grammar are dropped entirely here, never counted. Real confirmed data:
    every genuine shaibi position eventually carries a "(общо N)" total
    somewhere on the sheet; a LEAD_COUNT-style rendering of the SAME
    physical bar (e.g. "14N12x317/10") is always a redundant partial/
    per-leg duplicate of that total (confirmed real pair: "14N12x317/10"
    next to its sibling "14 N12x317 (общо 28)" for the exact same detail —
    28 = 14 x 2 legs). Counting both double-counts the position; this is
    the same "zone-spacing duplicate" noise already established as safe to
    discard for koloni, just reached through a grammar that happens to
    still parse for this dialect."""

    eligible: list[ProxyAnnotationSpec] = []
    passthrough: list[ProxyAnnotationSpec] = []
    for annotation in annotations:
        is_shaibi = annotation.parent_layer.strip().upper() == "ARM_SHAIBI"
        if is_shaibi and annotation.grammar == _GRAMMAR_TRAILING_TOTAL_COUNT:
            eligible.append(annotation)
        elif is_shaibi and annotation.grammar == _GRAMMAR_LEAD_COUNT:
            continue
        else:
            passthrough.append(annotation)
    if not eligible:
        return passthrough, 0

    deduped = _deduplicate_shaibi_visual_copies(eligible, wall_groups)

    kept: list[ProxyAnnotationSpec] = []
    multiplied_count = 0
    for annotation in deduped:
        anchor = _shaibi_annotation_anchor(annotation)
        if anchor is None:
            kept.append(annotation)
            continue
        group, distance = _nearest_shaibi_wall_group(anchor, wall_groups)
        if group is None or distance > _SHAIBI_WALL_GROUP_MATCH_DISTANCE:
            continue
        if group.size <= 1:
            kept.append(annotation)
            continue
        kept.append(replace(annotation, count=annotation.count * group.size))
        multiplied_count += 1
    return passthrough + kept, multiplied_count


def _count_structural_bend_arcs(group: ProxyGeometryGroup | None) -> int:
    """Counts ARC members that are a genuine structural bend, excluding a
    decorative "bar end" marker drawn as a full circle split into two ARC
    entities that trace the SAME two endpoints in opposite directions (an
    exact duplicate — detected structurally here, not by a hardcoded
    radius value, so the signal survives a differently-scaled marker on a
    future drawing). See ``PROXY_DECLARED_SHAPE_CODE`` for the validation
    behind this: across 165 real positions in two real drawings, the
    remaining count was 0 for every straight/spliced bar and >0 for every
    genuinely bent one, with zero ambiguous cases."""

    if group is None:
        return 0
    arcs = [member for member in group.members if member.record.entity_type.upper() == "ARC"]
    used = [False] * len(arcs)
    bend_count = 0
    for i, member in enumerate(arcs):
        if used[i]:
            continue
        duplicate_found = False
        for j in range(i + 1, len(arcs)):
            if used[j]:
                continue
            other = arcs[j]
            if abs(member.length_raw - other.length_raw) > 1e-6:
                continue
            same_order = _points_close(member.start, other.start, 1e-6) and _points_close(
                member.end, other.end, 1e-6
            )
            reversed_order = _points_close(member.start, other.end, 1e-6) and _points_close(
                member.end, other.start, 1e-6
            )
            if same_order or reversed_order:
                used[i] = used[j] = True
                duplicate_found = True
                break
        if not duplicate_found:
            bend_count += 1
    return bend_count


#: Толеранс при проверката дали декларираните размери затварят общата
#: дължина. Размерите в този диалект са цели сантиметри, така че това е
#: защита от плаваща запетая, а не инженерен допуск.
_DECLARED_CLOSURE_TOLERANCE_CM = 0.05


#: Ъглови прагове за трисегментна форма. F002 иска НАКЛОНЕНА редукция между
#: две паралелни рамена; F003 иска огъвки от 90°. Между двете няма форма в
#: ENGINE, затова средната зона се отказва вместо да се налучква.
_THREE_LEG_PARALLEL_TOLERANCE_DEG = 5.0
_THREE_LEG_PERPENDICULAR_TOLERANCE_DEG = 5.0

#: Допуск при сверяване на декларираните размери с начертаните. Мащабът се
#: ИЗВЕЖДА от чертежа (колони рисуват 1 единица/см, шайби 2), затова
#: проверката е пропорционална.
_THREE_LEG_SCALE_TOLERANCE = 0.04


def _leg_direction_deg(leg: tuple[tuple[float, float], tuple[float, float]]) -> float | None:
    (x0, y0), (x1, y1) = leg
    dx, dy = x1 - x0, y1 - y0
    if math.hypot(dx, dy) <= 1e-9:
        return None
    return math.degrees(math.atan2(dy, dx)) % 180.0


def _angle_between_deg(first: float, second: float) -> float:
    """Ъгъл между две ПОСОКИ (не вектори): 0° и 179.99° са паралелни."""

    delta = abs(first - second) % 180.0
    return min(delta, 180.0 - delta)


def reduction_offset_angle_deg(
    offset_cm: float,
    declared_reduction_cm: float,
) -> float | None:
    """Ъгълът на редукцията, изведен от НАДПИСАНИТЕ числа на проектанта.

    Собственик, 2026-09-13: „при железата с редукция не е спазен
    проектантският надпис — отместването е точно 5, а дължината на
    редукцията е 21 точно“.

    Чертежът е схематичен: наклоненият участък е начертан 20.616 cm, а е
    надписан 21. Ъгълът, взет от НАЧЕРТАНАТА посока, дава отместване 5.095 —
    достатъчно, за да развали кръглото число на проектанта, защото
    ``master_engine_v031`` смята ``DY2 = редукция * sin(ъгъл)``.

    Затова ъгълът се извежда от двете числа, които са точни: измереното
    отместване между двете успоредни рамена (5.000 cm в реалния чертеж) и
    ДЕКЛАРИРАНАТА дължина на редукцията (21). Тогава ``DY2`` излиза точно
    5.0000.
    """

    if declared_reduction_cm <= 0 or offset_cm <= 0:
        return None
    if offset_cm >= declared_reduction_cm:
        return None
    return math.degrees(math.asin(offset_cm / declared_reduction_cm))


def _perpendicular_offset(
    line: tuple[tuple[float, float], tuple[float, float]],
    point: tuple[float, float],
) -> float | None:
    """Разстояние от точка до правата, носеща отсечката."""

    (x0, y0), (x1, y1) = line
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy)
    if norm <= 1e-9:
        return None
    return abs(dy * (point[0] - x0) - dx * (point[1] - y0)) / norm


def three_leg_shape_from_directions(
    directions: Sequence[float],
    segments: Sequence[float],
) -> str | None:
    """Коя трисегментна форма описват ТРИ измерени посоки и трите размера.

    Публична и чиста, защото я ползват две независими страни: адаптерът я
    прилага върху току-що измерената геометрия, а одобряващият гейт я
    прилага отново върху ЗАПИСАНИТЕ посоки, за да не вярва на кода на
    формата, дошъл от записа. Виж ``export/auto_gate.py``.

    Праговете идват от заключените дефиниции в ENGINE: ``F003`` иска огъвки
    от 90° и двете външни рамена по-къси от средното; ``F002`` и ``F019``
    делят силуета „две паралелни рамена и наклонен участък между тях" и се
    различават по това кой участък е дълъг -- късият среден е снаждане
    (F002), дългият среден е стълбищно рамо (F019). Всичко останало връща
    ``None``.
    """

    if len(directions) != 3 or len(segments) != 3:
        return None
    values = [float(value) for value in segments]
    if any(value <= 0 for value in values):
        return None
    outer = _angle_between_deg(float(directions[0]), float(directions[2]))
    if outer > _THREE_LEG_PARALLEL_TOLERANCE_DEG:
        return None
    middle = min(
        _angle_between_deg(float(directions[1]), float(directions[0])),
        _angle_between_deg(float(directions[1]), float(directions[2])),
    )
    if middle <= _THREE_LEG_PARALLEL_TOLERANCE_DEG:
        return None
    if abs(middle - 90.0) <= _THREE_LEG_PERPENDICULAR_TOLERANCE_DEG:
        if values[0] >= values[1] or values[2] >= values[1]:
            return None
        return "F003"
    if middle < 90.0 - _THREE_LEG_PERPENDICULAR_TOLERANCE_DEG:
        # Две форми споделят този силует -- два паралелни прави хода и
        # наклонен участък между тях. Различава ги КОЙ участък е дълъг, и
        # това не е стилистика, а различни изделия:
        #
        # ``F002`` РЕДУКЦИЯ  -- два дълги пръта, свързани с КРАТКО наклонено
        #                       снаждане (колони: [150, 21, 310]).
        # ``F019`` СТЪЛБИЩНО -- късо рамо, ДЪЛЪГ наклонен марш, късо рамо
        #     РАМО 5           (шайби: [60, 203, 60]).
        #
        # Собственик, 2026-09-13, за „4N16x323" [60, 203, 60] под 42°:
        # „Като тази за стълбищен манш." Дотогава редът минаваше като F002
        # без преглед, после -- след поправката от същия ден -- не минаваше
        # никак и оставаше без форма.
        #
        # Ъгълът НЕ участва в разграничението. В ENGINE F019 е с
        # ``angle_mode='AUTO'`` и без ``min/max_angle_deg``: наклонът идва от
        # чертежа, защото стълбищата се проектират с различен наклон.
        # Контролната позиция в ENGINE е 8N10x406 [50, 286, 70] при около
        # 29°; тази тук е под 42° -- същата форма, друг марш.
        #
        # Останалите стълбищни форми не могат да се сбъркат с тази: F012,
        # F014 и F016 имат 90-градусов завой, тоест външните им рамена НЕ са
        # паралелни, а горната проверка вече изисква да са.
        if values[1] > values[0] and values[1] > values[2]:
            return "F019"
        if values[1] >= values[0] or values[1] >= values[2]:
            # Средният участък е равен на някое от крайните -- нито ясно
            # снаждане, нито ясно рамо. FAIL-CLOSED: при оператора.
            return None
        return "F002"
    return None


def _read_declared_three_leg_shape(
    legs: Sequence[tuple[tuple[float, float], tuple[float, float]]],
    segments: Sequence[float],
    length_cm: float,
) -> tuple[str, tuple[float, float, float]] | None:
    """Определя коя трисегментна форма е деклариран прът, по НАЧЕРТАНАТА му
    геометрия, и връща ``(код, сегменти)``.

    Нулевият брой дъги НЕ доказва прав прът: този диалект чертае огъвките
    като остри върхове на полилиния, а единствените дъги в proxy-то са
    декоративният кръг в края, който ``_count_structural_bend_arcs``
    правилно неутрализира. По-ранната версия връщаше F002 за всяка
    трисегментна декларация с нула дъги и така изнасяше 20 реални U-пръта
    от чертежа на шайбите като прави пръти с редукция.

    Решава се по заключените дефиниции в ENGINE, не по предположение:

    * ``F002`` — „два хоризонтални прави участъка и НАКЛОНЕНА редукция
      между тях“: двете външни рамена са паралелни, средното е под ъгъл.
    * ``F003`` — „U-форма, при която и двете външни рамена са ПО-КЪСИ от
      средния сегмент“: огъвки от 90°.

    Дължините остават на проектанта, формата — на чертежа, и двете се
    сверяват едно с друго: начертаните дължини трябва да отговарят на
    декларираните при един общ мащаб (той се извежда, защото различните
    листове рисуват в различен мащаб).

    FAIL-CLOSED навсякъде: несъвпадащ мащаб, ъгъл извън двете зони,
    непаралелни външни рамена или пропорция, която ENGINE отнася към друга
    форма, връщат ``None`` и позицията отива при оператора.
    """

    if len(legs) != 3 or len(segments) != 3:
        return None
    declared = [float(value) for value in segments]
    if any(value <= 0 for value in declared):
        return None
    if length_cm <= 0 or abs(math.fsum(declared) - length_cm) > _DECLARED_CLOSURE_TOLERANCE_CM:
        return None

    drawn = [math.dist(start, end) for start, end in legs]
    if any(value <= 1e-9 for value in drawn):
        return None
    directions = [_leg_direction_deg(leg) for leg in legs]
    if any(value is None for value in directions):
        return None

    # Един общ мащаб трябва да обяснява и трите рамена.
    scale = math.fsum(drawn) / math.fsum(declared)
    if scale <= 0:
        return None
    for measured, stated in zip(drawn, declared):
        if abs(measured - stated * scale) > stated * scale * _THREE_LEG_SCALE_TOLERANCE:
            return None

    # Напълно колинеарен прът в три части не е описан в ENGINE (F002 иска
    # наклонена редукция), а „рамо по-дълго от средата“ ENGINE отнася към
    # F005 -- и двете връщат None от класификатора по-долу.
    code = three_leg_shape_from_directions(directions, declared)
    if code is None:
        return None
    reduction_angle = None
    if code == "F002":
        # Ъгълът се извежда от надписаните числа, не от начертаната посока --
        # виж ``reduction_offset_angle_deg``.
        offset = _perpendicular_offset(legs[0], legs[2][0])
        if offset is None:
            return None
        # Мащабът за отместването се взема от ВЪНШНИТЕ рамена. Те са
        # начертани точно колкото са надписани (150 и 310 в реалния чертеж),
        # докато наклоненият участък е схематичен -- 20.616 при надписани 21.
        # Общият мащаб би внесъл точно тази грешка обратно в отместването и
        # 5.000 би станало 5.004.
        outer_scale = (drawn[0] + drawn[2]) / (declared[0] + declared[2])
        if outer_scale <= 0:
            return None
        reduction_angle = reduction_offset_angle_deg(offset / outer_scale, declared[1])
        if reduction_angle is None:
            return None
    return (code, (declared[0], declared[1], declared[2]), reduction_angle)


def _read_declared_hook_bar(
    segments: Sequence[float],
    length_cm: float,
) -> tuple[float, float, float] | None:
    """Чете прът с две еднакви куки: декларация ``[рамо, кука, кука]``.

    ENGINE описва ``F007`` („S / C-ОБРАЗНА ФОРМА") с думите „Контролната
    дължина е 2×L1 + L2; кривината е схематична". Точно това уравнение
    затваря при 18 надписа в реалния чертеж на шайби:

        [31, 8, 8] L=47 · [21, 8, 8] L=37 · [26, 8, 8] L=42

    Всичките имат по една истинска дъга (r=4.8, завъртане 135°) -- това е
    кука, не винкел. ``derive_shape`` ги типизираше като ``F003`` -- U-форма
    с прави ъгли. Осем от тях собственикът поправи на ръка на F007; другите
    десет минаха автоматично и влязоха в описа с грешна огъвателна ведомост
    (2026-09-13, проект ``s-7``).

    Връща производствения ред ``(кука, рамо, кука)``. FAIL-CLOSED при всичко
    останало.
    """

    if len(segments) != 3 or length_cm <= 0:
        return None
    leg, hook, hook_again = (float(value) for value in segments)
    if leg <= 0 or hook <= 0:
        return None
    if hook != hook_again:
        return None
    # Куката е късото връщащо рамо; иначе числата не са това, което мислим.
    if hook >= leg:
        return None
    if abs(2 * hook + leg - length_cm) > _DECLARED_CLOSURE_TOLERANCE_CM:
        return None
    return (hook, leg, hook)


def _split_declared_closed_stirrup(
    segments: Sequence[float],
) -> tuple[float, float, float] | None:
    """Разпознава САМО формата на декларацията ``[a, b, a, b, кука(, кука)]``,
    без да проверява сметката.

    Отделена от ``_read_declared_closed_stirrup``, за да може извикващият да
    различи „това не е стреме“ (продължава по другия път) от „това е
    стреме, но числата не се връзват“ (позицията отива при оператора).
    """

    if len(segments) < 5 or len(segments) > 6:
        return None
    values = [float(value) for value in segments]
    if any(value <= 0 for value in values):
        return None
    width, height = values[0], values[1]
    if values[2] != width or values[3] != height:
        return None
    hooks = values[4:]
    if len(set(hooks)) != 1:
        return None
    hook = hooks[0]
    # Куката е късото връщащо рамо -- по определение по-къса от двете страни.
    if hook >= width or hook >= height:
        return None
    return (width, height, hook)


def _read_declared_closed_stirrup(
    segments: Sequence[float],
    length_cm: float,
) -> tuple[float, float, float] | None:
    """Чете затворено стреме от СОБСТВЕНАТА му декларация: ``[a, b, a, b,
    кука(, кука)]``.

    Този диалект не коти стремето с ``DIMENSION`` обекти някъде по листа --
    изписва четирите страни и куката направо до надписа. Затова тук няма и
    не трябва да има избор „кой размер е най-близо“: размерите са дадени,
    остава само да се провери сметката ``2*(a + b + кука) = L``.

    Проверено върху 56 от 56 огънати надписа в реалния чертеж на колони --
    всичките затварят ТОЧНО, без изключение.

    FAIL-CLOSED: декларация с формата на стреме, чиято сметка не затваря,
    връща ``None``. Позицията отива при оператора. По-ранната версия я
    подаваше на ``vertical_shape_resolver.derive_shape`` -- resolver за
    ДРУГИЯ диалект, който при липса на пространствена информация избираше
    първата подходяща комбинация и мълчаливо изнасяше грешен размер
    (реален случай: „100N8x94“ излизаше 18x18 при надписано 21x18).
    """

    if length_cm <= 0:
        return None
    read = _split_declared_closed_stirrup(segments)
    if read is None:
        return None
    width, height, hook = read
    unfolded = 2 * (width + height + hook)
    if abs(unfolded - length_cm) > _DECLARED_CLOSURE_TOLERANCE_CM:
        return None
    return (width, height, hook)


def _own_geometry_legs(
    group: ProxyGeometryGroup | None,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Правите участъци на собствената геометрия на надписа, в реда, в който
    са изчертани. Дъгите се изключват: в този диалект те са декоративният
    кръг в края на пръта, не огъвка (виж
    ``_count_structural_bend_arcs``)."""

    if group is None:
        return []
    return [
        (member.start, member.end)
        for member in group.members
        if member.record.entity_type.upper() != "ARC"
    ]


def _derive_declared_shape_evidence(
    annotation: ProxyAnnotationSpec,
    bend_arc_count: int,
    own_group: ProxyGeometryGroup | None = None,
) -> ShapeEvidence | None:
    """Owner-requested 2026-09-10 automatic-recognition fix: derive a MASTER
    shape from the designer's own declared bend segments, gated on
    independent geometric proof so declared text never proves shape family
    by itself — see ``PROXY_DECLARED_SHAPE_CODE``.

    ``bend_arc_count`` is proof in BOTH directions, validated against 165
    real positions in two real drawings with zero ambiguous cases:
    ``> 0`` proves a genuine bend (fed through ``vertical_shape_resolver.
    derive_shape`` exactly as the GA_X/GA_Y dimension-based path does);
    ``== 0`` proves NO bend at all -- the bar is straight, whatever its
    declared text happens to break the length into.

    Owner-confirmed 2026-09-10 (real live-app case, "14N20x451" resolved
    by hand as F002 with segments 310+21+120): a "150-21-310"-style 3-way
    split is NOT a plain straight bar (F001) -- it is the engine's own
    dedicated ``F002`` "ПРАВ ПРЪТ С РЕДУКЦИЯ" (straight bar with a
    diameter-reduction splice/coupler), which is still geometrically
    straight (no bend, ``bend_arc_count`` stays 0) but requires all THREE
    declared segments preserved in their original order -- collapsing
    them into one F001 total (this function's first, wrong version) loses
    the reduction detail entirely. A single declared segment (already
    equal to the total length -- nothing else it could be) stays F001.
    Any other segment count with zero bend arcs is an unrecognised
    pattern -- fail closed rather than guess."""

    segments = annotation.declared_segments_cm
    if not segments:
        return None
    if bend_arc_count == 0:
        if len(segments) == 1:
            return ShapeEvidence(
                shape_code="F001",
                segments=(annotation.length_cm,),
                source="ДЕКЛАРИРАНА ДЪЛЖИНА (нула огъващи дъги — прав прът)",
                confidence="HIGH",
            )
        if len(segments) == 3:
            # Нула дъги НЕ значи прав прът: този диалект чертае огъвките
            # като остри върхове. Формата се чете от начертаната геометрия
            # срещу заключените дефиниции в ENGINE -- виж
            # ``_read_declared_three_leg_shape``. По-ранната версия връщаше
            # F002 безусловно и изнасяше реални U-пръти като прави.
            legs = _own_geometry_legs(own_group)
            read = _read_declared_three_leg_shape(
                legs, segments, annotation.length_cm
            )
            if read is None:
                return None
            code, resolved, reduction_angle = read
            return ShapeEvidence(
                shape_code=code,
                segments=resolved,
                reduction_angle_deg=reduction_angle,
                source=(
                    "ДЕКЛАРИРАНИ РАЗМЕРИ, ФОРМА ОТ НАЧЕРТАНАТА ГЕОМЕТРИЯ "
                    "(без дъги — огъвките са остри върхове)"
                ),
                confidence="HIGH",
                leg_directions_deg=tuple(
                    value for value in (_leg_direction_deg(leg) for leg in legs)
                    if value is not None
                ),
            )
        return None
    # Затворено стреме, декларирано явно: размерите се ЧЕТАТ, не се търсят.
    # Виж ``_read_declared_closed_stirrup`` защо това не минава през
    # ``derive_shape`` -- онзи resolver избира размери по близост, а тук
    # всички биха били на едно и също място.
    declared_stirrup = _split_declared_closed_stirrup(segments)
    if declared_stirrup is not None:
        closed = _read_declared_closed_stirrup(segments, annotation.length_cm)
        if closed is None:
            # Декларация с формата на стреме, чиято сметка не затваря.
            # FAIL-CLOSED: при оператора, не при resolver-а да я гадае.
            return None
        return ShapeEvidence(
            shape_code="F006",
            segments=closed,
            source="ДЕКЛАРИРАНО ЗАТВОРЕНО СТРЕМЕ (разгъвката затваря точно)",
            confidence="HIGH",
            allowance=0.0,
            measured_count=3,
            computed_count=0,
        )
    hook_bar = _read_declared_hook_bar(segments, annotation.length_cm)
    if hook_bar is not None:
        return ShapeEvidence(
            shape_code="F007",
            segments=hook_bar,
            source=(
                "ДЕКЛАРИРАН ПРЪТ С ДВЕ КУКИ (2×L1 + L2 = L, потвърдено с дъга)"
            ),
            confidence="HIGH",
        )
    if not annotation.fragments:
        return None
    anchor = _xy(annotation.fragments[0].geometry.get("insert"))
    if anchor is None:
        return None
    dimensions = [{"measurement": value, "point": anchor} for value in segments]
    evidence = derive_shape(anchor, dimensions, annotation.length_cm)
    if evidence is None or evidence.confidence not in ("HIGH", "MEDIUM"):
        return None
    # Deliberately NOT reordered here: evidence.segments for F006 stays in
    # vertical_shape_resolver's own native (width, height, hook) order --
    # the SAME convention already used by the pre-existing dimension-hint
    # path (batch_geometry_report.py's _apply_dimension_derived_production_
    # sizes, which stores raw derive_shape() output the same way) and
    # already expected by the UI's own reorder step
    # (ui/main_window.py::_shape_dimension_overlay_input, comment "Resolver
    # suggestion schema: width, height, hook"). Reordering it HERE too
    # would double-swap hook and width by the time it reaches that UI
    # step. Whatever DOES need master_engine_v031's own (hook, side_a,
    # side_b) order (e.g. render_shape_png) must reorder at the point of
    # use, not here at storage -- this was tried once and reverted after
    # finding the UI's existing convention.
    return evidence


def _geometry_member(record: DxfEntityRecord) -> ProxyGeometryMember | None:
    entity_type = record.entity_type.upper()
    if entity_type == "LINE":
        start = _xy(record.geometry.get("start"))
        end = _xy(record.geometry.get("end"))
        if start is None or end is None or _points_close(start, end, 1e-9):
            return None
        return ProxyGeometryMember(record, math.dist(start, end), start, end)
    if entity_type in {"POLYLINE", "LWPOLYLINE"}:
        path = polyline_path_from_geometry(record.geometry)
    elif entity_type == "ARC":
        path = arc_path_from_geometry(record.geometry)
    else:
        path = None
    if path is None or path.closed or path.exact_length_raw <= 0.0:
        return None
    points = path.points
    if len(points) < 2:
        return None
    return ProxyGeometryMember(
        record,
        float(path.exact_length_raw),
        points[0],
        points[-1],
    )


def _associate_annotations(
    annotations: Sequence[ProxyAnnotationSpec],
    groups: Sequence[ProxyGeometryGroup],
    *,
    scale_factor: float,
    config: ProxyAdapterConfig,
) -> list[ProxyAssociation]:
    groups_by_sequence = sorted(groups, key=lambda value: value.proxy_sequence_index)
    result: list[ProxyAssociation] = []
    for annotation in annotations:
        # <= (not strictly <): the C_ARM_Gredi/C_arm_stylbi/C_Arm_kobilici
        # dialect (2026-09-10) draws one bar as a SINGLE self-contained proxy
        # holding both its own text fragments and its own geometry, unlike
        # GA_X/GA_Y where geometry and annotation are separate, sequential
        # proxies. Sorting descending tries the self-match (distance 0)
        # first, before falling back to an actually preceding neighbour.
        preceding = [
            group
            for group in groups_by_sequence
            if group.proxy_sequence_index <= annotation.proxy_sequence_index
            and annotation.proxy_sequence_index - group.proxy_sequence_index
            <= config.max_preceding_proxy_entities
        ]
        preceding.sort(key=lambda value: value.proxy_sequence_index, reverse=True)
        for group in preceding:
            subsets = _exact_connected_subsets(
                group.members,
                target_cm=annotation.length_cm,
                scale_factor=scale_factor,
                config=config,
            )
            if len(subsets) != 1:
                continue
            members = subsets[0]
            result.append(
                ProxyAssociation(
                    annotation=annotation,
                    geometry_group=group,
                    members=members,
                    scale_factor=scale_factor,
                    exact_total_cm=sum(value.length_raw for value in members) * scale_factor,
                    predecessor_distance=(
                        annotation.proxy_sequence_index - group.proxy_sequence_index
                    ),
                    modelspace_distance=annotation.modelspace_index - group.modelspace_index,
                )
            )
            break
    return result


def _exact_connected_subsets(
    members: Sequence[ProxyGeometryMember],
    *,
    target_cm: float,
    scale_factor: float,
    config: ProxyAdapterConfig,
) -> list[tuple[ProxyGeometryMember, ...]]:
    matches: dict[tuple[str, ...], tuple[ProxyGeometryMember, ...]] = {}
    max_size = min(len(members), config.max_geometry_members)
    for size in range(1, max_size + 1):
        for subset in itertools.combinations(members, size):
            total = sum(value.length_raw for value in subset) * scale_factor
            if abs(total - target_cm) > config.exact_length_tolerance_cm:
                continue
            ordered = _ordered_connected_members(subset, config.endpoint_tolerance_raw)
            if ordered is None:
                continue
            key = tuple(sorted(value.record.handle for value in subset))
            matches[key] = ordered
    return [matches[key] for key in sorted(matches)]


def _ordered_connected_members(
    members: Sequence[ProxyGeometryMember],
    tolerance: float,
) -> tuple[ProxyGeometryMember, ...] | None:
    if len(members) <= 1:
        return tuple(members)

    def walk(
        ordered: tuple[ProxyGeometryMember, ...],
        used: frozenset[int],
        current_end: tuple[float, float],
    ) -> tuple[ProxyGeometryMember, ...] | None:
        if len(used) == len(members):
            return ordered
        for index, member in enumerate(members):
            if index in used:
                continue
            if _points_close(current_end, member.start, tolerance):
                found = walk(ordered + (member,), used | {index}, member.end)
                if found is not None:
                    return found
            if _points_close(current_end, member.end, tolerance):
                reversed_member = ProxyGeometryMember(
                    member.record,
                    member.length_raw,
                    member.end,
                    member.start,
                )
                found = walk(ordered + (reversed_member,), used | {index}, member.start)
                if found is not None:
                    return found
        return None

    for start_index, member in enumerate(members):
        for oriented in (
            member,
            ProxyGeometryMember(member.record, member.length_raw, member.end, member.start),
        ):
            found = walk((oriented,), frozenset({start_index}), oriented.end)
            if found is not None:
                return found
    return None


def _select_scale(
    evidence: dict[float, int],
    annotation_count: int,
    config: ProxyAdapterConfig,
) -> float | None:
    if not evidence or annotation_count <= 0:
        return None
    ordered = sorted(evidence.items(), key=lambda item: (-item[1], item[0]))
    winner_scale, winner_count = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0
    if winner_count < config.minimum_scale_evidence:
        return None
    if winner_count / annotation_count < config.minimum_scale_coverage:
        return None
    if winner_count - runner_up < config.minimum_scale_winner_margin:
        return None
    return winner_scale


def _annotation_record(
    annotation: ProxyAnnotationSpec,
    association: ProxyAssociation | None,
    geometry_handles: Sequence[str],
) -> DxfEntityRecord:
    visual_points: list[list[float]] = []
    fragment_inserts: list[list[float]] = []
    heights: list[float] = []
    fragment_virtual_indices: list[int] = []
    render_fragments: list[dict[str, Any]] = []
    for fragment in annotation.fragments:
        insert = fragment.geometry.get("insert")
        if isinstance(insert, (list, tuple)) and len(insert) >= 2:
            normalized_insert = [
                float(insert[0]),
                float(insert[1]),
                float(insert[2]) if len(insert) > 2 else 0.0,
            ]
            fragment_inserts.append(normalized_insert)
            render_fragment = {
                "entity_type": fragment.entity_type,
                "text": fragment.text or "",
                "insert": normalized_insert,
                "height": float(
                    fragment.geometry.get(
                        "height", fragment.geometry.get("char_height", 1.0)
                    )
                    or 1.0
                ),
                "rotation": float(fragment.geometry.get("rotation", 0.0) or 0.0),
                "virtual_index": fragment.raw.get("virtual_index"),
                "source_layer": fragment.raw.get("proxy_virtual_layer"),
                "color_index": fragment.color_index,
                "true_color": fragment.raw.get("true_color"),
                "linetype": fragment.linetype,
            }
            for key in (
                "width_factor",
                "oblique",
                "halign",
                "valign",
                "align_point",
                "width",
                "attachment_point",
                "text_direction",
            ):
                if key in fragment.geometry:
                    render_fragment[key] = fragment.geometry[key]
            render_fragments.append(render_fragment)
        bbox = fragment.geometry.get("visual_bbox")
        if isinstance(bbox, list):
            visual_points.extend(
                [float(value[0]), float(value[1]), float(value[2]) if len(value) > 2 else 0.0]
                for value in bbox
                if isinstance(value, (list, tuple)) and len(value) >= 2
            )
        try:
            heights.append(float(fragment.geometry.get("height", 1.0) or 1.0))
        except (TypeError, ValueError):
            pass
        try:
            fragment_virtual_indices.append(int(fragment.raw.get("virtual_index")))
        except (TypeError, ValueError):
            pass
    anchor_source = visual_points or fragment_inserts
    if anchor_source:
        anchor = [
            sum(value[0] for value in anchor_source) / len(anchor_source),
            sum(value[1] for value in anchor_source) / len(anchor_source),
            0.0,
        ]
    else:
        anchor = [0.0, 0.0, 0.0]
    insert = fragment_inserts[0] if fragment_inserts else anchor
    rotation = float(
        annotation.fragments[0].geometry.get("rotation", 0.0)
        if annotation.fragments
        else 0.0
    )
    geometry_parent_handle = (
        association.geometry_group.parent_handle
        if association is not None
        else annotation.parent_handle
    )
    geometry_parent_layer = (
        association.geometry_group.parent_layer
        if association is not None
        else annotation.parent_layer
    )
    return DxfEntityRecord(
        handle=f"PXANN_{annotation.parent_handle}",
        entity_type="BLOCK_ATTR_TEXT",
        layer=annotation.parent_layer,
        geometry={
            "insert": insert,
            "height": max(heights, default=1.0),
            "rotation": rotation,
            "visual_anchor": anchor,
            "visual_bbox": visual_points,
            "attribute_inserts": fragment_inserts,
            # The full annotation text remains the engineering value.  The
            # original fragments below are exclusively for a faithful,
            # selectable viewer group and preserve their individual placement.
            "render_fragments": render_fragments,
            "parent_proxy_handle": annotation.parent_handle,
            "proxy_geometry_parent_handle": geometry_parent_handle,
            "proxy_geometry_handles": list(geometry_handles),
            "derived": True,
        },
        text=annotation.text,
        block_name="ACAD_PROXY_GRAPHIC_REBAR",
        raw={
            "source": PROXY_ANNOTATION_SOURCE,
            "association_code": (
                PROXY_ASSOCIATION_CODE if association is not None else PROXY_DECLARED_LENGTH_CODE
            ),
            "parent_proxy_handle": annotation.parent_handle,
            "parent_proxy_layer": annotation.parent_layer,
            "proxy_geometry_parent_handle": geometry_parent_handle,
            "proxy_geometry_parent_layer": geometry_parent_layer,
            "proxy_geometry_handles": list(geometry_handles),
            "position_key": annotation.position_key,
            "count": annotation.count,
            "diameter_mm": annotation.diameter_mm,
            "length_cm": annotation.length_cm,
            "spacing_cm": annotation.spacing_cm,
            "fragment_tokens": list(annotation.tokens),
            "fragment_virtual_indices": fragment_virtual_indices,
            "scale_factor": association.scale_factor if association is not None else None,
            "exact_total_cm": (
                association.exact_total_cm if association is not None else annotation.length_cm
            ),
            "predecessor_distance": (
                association.predecessor_distance if association is not None else 0
            ),
            "modelspace_distance": (
                association.modelspace_distance if association is not None else 0
            ),
            "geometry_linked": association is not None,
            "ownership_mode": "REFERENCE_ONLY",
            "column_group_count": annotation.column_group_count,
            "column_group_label": annotation.column_group_label,
            "column_group_source_count": annotation.column_group_source_count,
        },
    )


def is_proxy_rebar_annotation(record: DxfEntityRecord) -> bool:
    return str(record.raw.get("source") or "") == PROXY_ANNOTATION_SOURCE


def proxy_geometry_handles(record: DxfEntityRecord) -> tuple[str, ...]:
    if not is_proxy_rebar_annotation(record):
        return ()
    values = record.raw.get("proxy_geometry_handles")
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values if str(value))


def _xy(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _points_close(
    left: tuple[float, float],
    right: tuple[float, float],
    tolerance: float,
) -> bool:
    return math.hypot(left[0] - right[0], left[1] - right[1]) <= tolerance


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _format_number(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.6f}".rstrip("0").rstrip(".")


__all__ = [
    "PROXY_ANNOTATION_SOURCE",
    "PROXY_ASSOCIATION_CODE",
    "PROXY_DECLARED_LENGTH_CODE",
    "PROXY_DECLARED_SHAPE_CODE",
    "PROXY_GEOMETRY_SOURCE",
    "ProxyAdapterConfig",
    "ProxyAdapterResult",
    "extract_acad_proxy_rebar",
    "is_proxy_rebar_annotation",
    "proxy_geometry_handles",
]
