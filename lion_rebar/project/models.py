from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lion_rebar.config.constants import PROJECT_SCHEMA


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProjectModel:
    name: str
    root: str
    schema: str = PROJECT_SCHEMA
    version: str = "0.1.0"
    created_at: str = field(default_factory=utc_now_iso)
    modified_at: str = field(default_factory=utc_now_iso)
    source_dxf: str | None = None
    original_source_name: str | None = None
    imported_at: str | None = None
    engine_id: str | None = None
    engine_version: str | None = None
    engine_sha256: str | None = None
    units: str | None = None
    units_confidence: float | None = None
    layer_visibility: dict[str, bool] = field(default_factory=dict)
    import_summary: dict[str, Any] = field(default_factory=dict)
    selected_handle: str | None = None
    notes: str = ""
    extensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectModel":
        return cls(
            schema=str(data.get("schema", PROJECT_SCHEMA)),
            version=str(data.get("version", "0.1.0")),
            name=str(data.get("name", "Unnamed project")),
            root=str(data.get("root", "")),
            created_at=str(data.get("created_at", utc_now_iso())),
            modified_at=str(data.get("modified_at", utc_now_iso())),
            source_dxf=data.get("source_dxf"),
            original_source_name=data.get("original_source_name"),
            imported_at=data.get("imported_at"),
            engine_id=data.get("engine_id"),
            engine_version=data.get("engine_version"),
            engine_sha256=data.get("engine_sha256"),
            units=data.get("units"),
            units_confidence=data.get("units_confidence"),
            layer_visibility={str(k): bool(v) for k, v in data.get("layer_visibility", {}).items()},
            import_summary=deepcopy(data.get("import_summary", {})),
            selected_handle=data.get("selected_handle"),
            notes=str(data.get("notes", "")),
            extensions=deepcopy(data.get("extensions", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def root_path(self) -> Path:
        return Path(self.root)
