from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DxfEntityRecord:
    handle: str
    entity_type: str
    layer: str
    geometry: dict[str, Any] = field(default_factory=dict)
    text: str | None = None
    block_name: str | None = None
    color_index: int | None = None
    linetype: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DxfImportSummary:
    filename: str
    dxf_version: str
    units_code: int | None
    units_name: str
    layers: list[str]
    entity_counts: dict[str, int]
    total_entities: int
    text_entities: int
    potential_rebar_texts: int
    block_count: int
    derived_entity_count: int = 0
    reconstructed_rebar_annotations: int = 0
    reconstructed_attribute_values: int = 0
    proxy_entity_count: int = 0
    proxy_text_fragment_count: int = 0
    proxy_polyline_count: int = 0
    proxy_arc_count: int = 0
    proxy_parsed_annotation_count: int = 0
    proxy_resolved_annotation_count: int = 0
    proxy_unresolved_annotation_count: int = 0
    proxy_geometry_record_count: int = 0
    proxy_scale_factor: float | None = None
    proxy_scale_evidence_count: int = 0
    proxy_scale_evidence_by_hypothesis: dict[str, int] = field(default_factory=dict)
    proxy_position_occurrences: dict[str, int] = field(default_factory=dict)
    proxy_missing_position_keys: list[int] = field(default_factory=list)
    # Generic proxy graphics are a display-only data channel.  These counters
    # deliberately live beside (but do not alter) the strict GA_X/GA_Y
    # engineering-adapter statistics above.
    proxy_display_instance_count: int = 0
    proxy_display_discovered_primitive_count: int = 0
    proxy_display_rendered_primitive_count: int = 0
    proxy_display_suppressed_duplicate_count: int = 0
    proxy_display_unsupported_primitive_count: int = 0
    proxy_display_complete_count: int = 0
    proxy_display_partial_count: int = 0
    proxy_display_missing_graphic_count: int = 0
    proxy_display_unreadable_count: int = 0
    proxy_display_type_counts: dict[str, int] = field(default_factory=dict)
    proxy_display_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DxfImportResult:
    summary: DxfImportSummary
    entities: list[DxfEntityRecord]
    # Never pass these records to recognition, quantity or export code.  They
    # are virtual, non-destructive representations of embedded proxy graphics
    # and exist solely for the operator's drawing view.
    display_entities: list[DxfEntityRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "entities": [entity.to_dict() for entity in self.entities],
            "display_entities": [entity.to_dict() for entity in self.display_entities],
        }
