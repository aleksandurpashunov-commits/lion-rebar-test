from __future__ import annotations

from dataclasses import dataclass

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_parser import ParsedRebarText, parse_rebar_text


@dataclass(frozen=True)
class NearbyCandidate:
    """Kept for project compatibility; v0.2 does not create proximity candidates."""

    value: str
    handle: str
    distance: float
    basis: str


@dataclass(frozen=True)
class RebarAnnotationAnalysis:
    parsed: ParsedRebarText
    position_candidate: NearbyCandidate | None = None
    spacing_candidate: NearbyCandidate | None = None
    geometry_linked: bool = False
    source_kind: str = "TEXT"
    source_description: str = "Самостоятелен DXF текст"
    has_position_attribute: bool = False
    has_spacing_attribute: bool = False

    @property
    def recognized(self) -> bool:
        return self.parsed.matched_pattern is not None

    @property
    def partial(self) -> bool:
        return self.recognized and (
            self.parsed.count is None
            or self.parsed.length_cm is None
            or bool(self.parsed.base_length_symbols)
            or not self.geometry_linked
        )


def analyze_rebar_annotation(
    selected: DxfEntityRecord,
    entities: list[DxfEntityRecord],
) -> RebarAnnotationAnalysis:
    del entities  # No distance-based association is allowed in v0.2.
    parsed = parse_rebar_text(selected.text or "")

    source = str(selected.raw.get("source", "")) if selected.raw else ""
    if source == "INSERT_ATTRIBUTES":
        tags = selected.geometry.get("attribute_tags", [])
        tag_text = ", ".join(str(tag) for tag in tags) or "BJ, FI, L"
        return RebarAnnotationAnalysis(
            parsed=parsed,
            geometry_linked=False,
            source_kind="BLOCK_ATTRIBUTES",
            source_description=(
                f"BLOCK {selected.block_name or '—'} → ATTRIB {tag_text}; "
                "стойностите са прочетени директно, без пространствено предположение"
            ),
            has_position_attribute=False,
            has_spacing_attribute=False,
        )

    return RebarAnnotationAnalysis(
        parsed=parsed,
        geometry_linked=False,
        source_kind="TEXT",
        source_description="Самостоятелен DXF TEXT/MTEXT; няма автоматична връзка по близост",
        has_position_attribute=False,
        has_spacing_attribute=False,
    )
