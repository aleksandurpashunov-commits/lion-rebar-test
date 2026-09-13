from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from lion_rebar.config.constants import ENGINE_SCHEMA


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean_none(item) for item in value]
    return value


@dataclass
class SourceAsset:
    role: str
    filename: str
    sha256: str
    relative_path: str | None = None
    imported_at: str = field(default_factory=utc_now_iso)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceAsset":
        return cls(
            role=str(data.get("role", "OTHER")),
            filename=str(data.get("filename", "")),
            sha256=str(data.get("sha256", "")),
            relative_path=data.get("relative_path"),
            imported_at=str(data.get("imported_at", utc_now_iso())),
            notes=str(data.get("notes", "")),
        )


@dataclass
class SegmentDefinition:
    index: int
    length_code: str
    segment_type: str = "LINE"
    fixed_length_cm: float | None = None
    bend_after_deg: float | None = None
    bend_direction: str | None = None
    geometry_method: str = "DIRECT_LENGTH"
    projection_x_code: str | None = None
    projection_y_code: str | None = None
    radius_cm: float | None = None
    required: bool = True
    drawing_direction: str | None = None
    dimension_position: str = "AUTO"
    dimension_offset: float = 6.0
    angle_mode: str = "AUTO"
    angle_parameter_code: str | None = None
    min_angle_deg: float | None = None
    max_angle_deg: float | None = None
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SegmentDefinition":
        return cls(
            index=int(data.get("index", 0)),
            length_code=str(data.get("length_code", "")),
            segment_type=str(data.get("segment_type", "LINE")),
            fixed_length_cm=_optional_float(data.get("fixed_length_cm")),
            bend_after_deg=_optional_float(data.get("bend_after_deg")),
            bend_direction=data.get("bend_direction"),
            geometry_method=str(data.get("geometry_method", "DIRECT_LENGTH")),
            projection_x_code=data.get("projection_x_code"),
            projection_y_code=data.get("projection_y_code"),
            radius_cm=_optional_float(data.get("radius_cm")),
            required=bool(data.get("required", True)),
            drawing_direction=data.get("drawing_direction"),
            dimension_position=str(data.get("dimension_position", "AUTO")),
            dimension_offset=float(data.get("dimension_offset", 6.0) or 6.0),
            angle_mode=str(data.get("angle_mode", "AUTO")),
            angle_parameter_code=data.get("angle_parameter_code"),
            min_angle_deg=_optional_float(data.get("min_angle_deg")),
            max_angle_deg=_optional_float(data.get("max_angle_deg")),
            notes=str(data.get("notes", "")),
            raw=deepcopy(data.get("raw", {})),
        )


@dataclass
class ParameterRelation:
    """Engineering relation between two segment lengths.

    ``left_segment`` and ``right_segment`` are the stable one-based segment
    indexes from the ENGINE definition.  ``EQUAL`` means
    ``left == right``.  ``RATIO`` means ``left == ratio * right``.

    Repeated ``length_code`` values and ``ShapeDefinition.symmetric`` already
    imply equality relations, so old ENGINE JSON files gain the stronger
    semantics without a migration.  This explicit model is for relations that
    cannot be expressed by those two legacy fields.
    """

    kind: str = "EQUAL"
    left_segment: int = 0
    right_segment: int = 0
    ratio: float = 1.0
    tolerance: float | None = None
    required: bool = True
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParameterRelation":
        raw_segments = data.get("segments")
        left = data.get("left_segment", data.get("left"))
        right = data.get("right_segment", data.get("right"))
        if isinstance(raw_segments, (list, tuple)) and len(raw_segments) >= 2:
            left = raw_segments[0]
            right = raw_segments[1]
        return cls(
            kind=str(data.get("kind", data.get("relation", "EQUAL"))),
            left_segment=_optional_int(left),
            right_segment=_optional_int(right),
            ratio=float(data.get("ratio", 1.0) or 1.0),
            tolerance=_optional_float(data.get("tolerance")),
            required=bool(data.get("required", True)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ShapeDefinition:
    code: str
    name: str
    category: str = ""
    state: str = "DRAFT"
    revision: int = 1
    locked: bool = False
    active: bool = True
    initial_direction: str = "RIGHT"
    closed: bool = False
    symmetric: bool = False
    mirror_allowed: bool = False
    template_orientation: str = ""
    notes: str = ""
    segments: list[SegmentDefinition] = field(default_factory=list)
    rendering: dict[str, Any] = field(default_factory=dict)
    recognition: dict[str, Any] = field(default_factory=dict)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    family_id: str = ""
    alias_of: str | None = None
    parameter_relations: list[ParameterRelation] = field(default_factory=list)
    legacy_raw: dict[str, Any] = field(default_factory=dict)
    controlled_revision: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShapeDefinition":
        return cls(
            code=str(data.get("code", "")),
            name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            state=str(data.get("state", "DRAFT")),
            revision=int(data.get("revision", 1)),
            locked=bool(data.get("locked", False)),
            active=bool(data.get("active", True)),
            initial_direction=str(data.get("initial_direction", "RIGHT")),
            closed=bool(data.get("closed", False)),
            symmetric=bool(data.get("symmetric", False)),
            mirror_allowed=bool(data.get("mirror_allowed", False)),
            template_orientation=str(data.get("template_orientation", "")),
            notes=str(data.get("notes", "")),
            segments=[SegmentDefinition.from_dict(item) for item in data.get("segments", [])],
            rendering=deepcopy(data.get("rendering", {})),
            recognition=deepcopy(data.get("recognition", {})),
            dimensions=deepcopy(data.get("dimensions", [])),
            tests=deepcopy(data.get("tests", [])),
            family_id=str(data.get("family_id", "")),
            alias_of=(
                str(data.get("alias_of")).strip()
                if data.get("alias_of") not in (None, "")
                else None
            ),
            parameter_relations=[
                ParameterRelation.from_dict(item)
                for item in data.get("parameter_relations", [])
                if isinstance(item, dict)
            ],
            legacy_raw=deepcopy(data.get("legacy_raw", {})),
            controlled_revision=deepcopy(data.get("controlled_revision")),
        )

    def clone(self) -> "ShapeDefinition":
        return ShapeDefinition.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return _clean_none(asdict(self))


@dataclass
class RuleDefinition:
    code: str
    name: str
    scope: str
    priority: int = 100
    enabled: bool = True
    when: dict[str, Any] = field(default_factory=dict)
    then: list[dict[str, Any]] = field(default_factory=list)
    requires_manual_approval: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleDefinition":
        return cls(
            code=str(data.get("code", "")),
            name=str(data.get("name", "")),
            scope=str(data.get("scope", "GENERAL")),
            priority=int(data.get("priority", 100)),
            enabled=bool(data.get("enabled", True)),
            when=deepcopy(data.get("when", {})),
            then=deepcopy(data.get("then", [])),
            requires_manual_approval=bool(data.get("requires_manual_approval", False)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class CategoryDefinition:
    code: str
    sheet_name: str
    aliases: list[str] = field(default_factory=list)
    enabled: bool = True
    sort_order: int = 100

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CategoryDefinition":
        return cls(
            code=str(data.get("code", "")),
            sheet_name=str(data.get("sheet_name", "")),
            aliases=[str(item) for item in data.get("aliases", [])],
            enabled=bool(data.get("enabled", True)),
            sort_order=int(data.get("sort_order", 100)),
        )


@dataclass
class EnginePackage:
    engine_id: str
    title: str
    version: str
    state: str = "DRAFT"
    schema: str = ENGINE_SCHEMA
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    published_at: str | None = None
    approved_by: str | None = None
    approval_note: str = ""
    parent_version: str | None = None
    content_sha256: str | None = None
    production_ready: bool = False
    shapes: list[ShapeDefinition] = field(default_factory=list)
    rules: list[RuleDefinition] = field(default_factory=list)
    categories: list[CategoryDefinition] = field(default_factory=list)
    position_statuses: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    tests: list[dict[str, Any]] = field(default_factory=list)
    source_assets: list[SourceAsset] = field(default_factory=list)
    change_log: list[dict[str, Any]] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnginePackage":
        return cls(
            schema=str(data.get("schema", ENGINE_SCHEMA)),
            engine_id=str(data.get("engine_id", "lion-rebar-engine")),
            title=str(data.get("title", "LION REBAR ENGINE")),
            version=str(data.get("version", "0.0.0")),
            state=str(data.get("state", "DRAFT")),
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            published_at=data.get("published_at"),
            approved_by=data.get("approved_by"),
            approval_note=str(data.get("approval_note", "")),
            parent_version=data.get("parent_version"),
            content_sha256=data.get("content_sha256"),
            production_ready=bool(data.get("production_ready", False)),
            shapes=[ShapeDefinition.from_dict(item) for item in data.get("shapes", [])],
            rules=[RuleDefinition.from_dict(item) for item in data.get("rules", [])],
            categories=[CategoryDefinition.from_dict(item) for item in data.get("categories", [])],
            position_statuses=[str(item) for item in data.get("position_statuses", [])],
            settings=deepcopy(data.get("settings", {})),
            tests=deepcopy(data.get("tests", [])),
            source_assets=[SourceAsset.from_dict(item) for item in data.get("source_assets", [])],
            change_log=deepcopy(data.get("change_log", [])),
            extensions=deepcopy(data.get("extensions", {})),
        )

    def to_dict(self, include_checksum: bool = True) -> dict[str, Any]:
        data = _clean_none(asdict(self))
        if not include_checksum:
            data.pop("content_sha256", None)
        return data

    def clone_as_draft(self, new_version: str, created_by: str = "OWNER") -> "EnginePackage":
        clone = EnginePackage.from_dict(self.to_dict())
        clone.version = new_version
        clone.state = "DRAFT"
        clone.parent_version = self.version
        clone.created_at = utc_now_iso()
        clone.updated_at = clone.created_at
        clone.published_at = None
        clone.approved_by = None
        clone.approval_note = ""
        clone.content_sha256 = None
        clone.change_log.append(
            {
                "at": clone.created_at,
                "action": "CLONE_AS_DRAFT",
                "by": created_by,
                "from_version": self.version,
                "to_version": new_version,
            }
        )
        return clone

    def find_shape(self, code: str) -> ShapeDefinition | None:
        normalized = code.strip().upper()
        return next((shape for shape in self.shapes if shape.code.upper() == normalized), None)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
