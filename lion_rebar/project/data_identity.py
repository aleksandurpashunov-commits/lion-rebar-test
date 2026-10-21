from __future__ import annotations

"""Project/source/run identity and stale-state isolation.

The contract is deliberately small and file based.  A project keeps one stable
``project_id``.  Every imported DXF starts a new ``source_generation_id`` and
every batch analysis starts a new ``analysis_run_id``.  Derived payloads carry
these values so a row, approval or ledger from another source/run cannot be
accepted merely because an annotation handle happens to be identical.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, MutableMapping
from uuid import uuid4

from lion_rebar.project.models import ProjectModel
from lion_rebar.utils.files import sha256_file
from lion_rebar.utils.process_lock import LOCK_SUFFIX

IDENTITY_KEY = "data_identity"
IDENTITY_SCHEMA = "lion-rebar-data-identity/v1"


@dataclass(frozen=True)
class DataIdentity:
    project_id: str
    source_sha256: str
    source_generation_id: str
    analysis_run_id: str = ""
    schema: str = IDENTITY_SCHEMA

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _uuid() -> str:
    return str(uuid4())


def ensure_project_identity(project: ProjectModel) -> bool:
    """Ensure the project has a stable identity; return whether it changed."""

    raw = project.extensions.get(IDENTITY_KEY)
    identity = dict(raw) if isinstance(raw, Mapping) else {}
    changed = False
    if identity.get("schema") != IDENTITY_SCHEMA:
        identity["schema"] = IDENTITY_SCHEMA
        changed = True
    if not str(identity.get("project_id") or "").strip():
        identity["project_id"] = _uuid()
        changed = True
    source_sha = str(project.extensions.get("source_sha256") or identity.get("source_sha256") or "")
    if source_sha != str(identity.get("source_sha256") or ""):
        identity["source_sha256"] = source_sha
        changed = True
    if project.source_dxf and not str(identity.get("source_generation_id") or "").strip():
        # Legacy migration: the current source receives one generation id.  Old
        # reports remain untrusted because they do not carry this id.
        identity["source_generation_id"] = _uuid()
        identity["source_generation_started_at"] = _now()
        changed = True
    identity.setdefault("source_generation_id", "")
    identity.setdefault("analysis_run_id", "")
    identity.setdefault("strict", True)
    if raw != identity:
        changed = True
    project.extensions[IDENTITY_KEY] = identity
    return changed


def current_identity(project: ProjectModel) -> DataIdentity:
    ensure_project_identity(project)
    raw = project.extensions.get(IDENTITY_KEY)
    assert isinstance(raw, Mapping)
    return DataIdentity(
        project_id=str(raw.get("project_id") or ""),
        source_sha256=str(raw.get("source_sha256") or ""),
        source_generation_id=str(raw.get("source_generation_id") or ""),
        analysis_run_id=str(raw.get("analysis_run_id") or ""),
    )


def identity_from_project_payload(payload: Mapping[str, Any]) -> DataIdentity | None:
    extensions = payload.get("extensions")
    raw = extensions.get(IDENTITY_KEY) if isinstance(extensions, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    project_id = str(raw.get("project_id") or "")
    source_generation_id = str(raw.get("source_generation_id") or "")
    if not project_id or not source_generation_id:
        return None
    return DataIdentity(
        project_id=project_id,
        source_sha256=str(raw.get("source_sha256") or payload.get("source_sha256") or ""),
        source_generation_id=source_generation_id,
        analysis_run_id=str(raw.get("analysis_run_id") or ""),
    )


def begin_source_generation(project: ProjectModel, *, source_sha256: str) -> DataIdentity:
    ensure_project_identity(project)
    raw = dict(project.extensions[IDENTITY_KEY])
    raw.update(
        {
            "schema": IDENTITY_SCHEMA,
            "source_sha256": str(source_sha256),
            "source_generation_id": _uuid(),
            "source_generation_started_at": _now(),
            "analysis_run_id": "",
            "analysis_run_started_at": "",
            "strict": True,
        }
    )
    project.extensions[IDENTITY_KEY] = raw
    project.extensions["source_sha256"] = str(source_sha256)
    return current_identity(project)



def begin_analysis_run(project: ProjectModel) -> DataIdentity:
    ensure_project_identity(project)
    raw = dict(project.extensions[IDENTITY_KEY])
    if not str(raw.get("source_generation_id") or ""):
        raise ValueError("Проектът няма активна source generation. Импортирай DXF отново.")
    raw["analysis_run_id"] = _uuid()
    raw["analysis_run_started_at"] = _now()
    project.extensions[IDENTITY_KEY] = raw
    return current_identity(project)


def stamp_payload(payload: MutableMapping[str, Any], identity: DataIdentity) -> MutableMapping[str, Any]:
    payload[IDENTITY_KEY] = identity.to_dict()
    return payload


def payload_identity(payload: Mapping[str, Any] | None) -> DataIdentity | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get(IDENTITY_KEY)
    # Some internal callers already hold the identity object itself (for
    # example a ledger/freeze row) rather than a complete payload containing
    # ``data_identity``.  Accept both representations so the comparison helper
    # cannot silently classify a valid direct identity mapping as missing.
    if not isinstance(raw, Mapping):
        raw = payload
    project_id = str(raw.get("project_id") or "")
    source_generation_id = str(raw.get("source_generation_id") or "")
    if not project_id or not source_generation_id:
        return None
    return DataIdentity(
        project_id=project_id,
        source_sha256=str(raw.get("source_sha256") or ""),
        source_generation_id=source_generation_id,
        analysis_run_id=str(raw.get("analysis_run_id") or ""),
    )


def mismatch_reason(
    candidate: DataIdentity | Mapping[str, Any] | None,
    current: DataIdentity,
    *,
    require_run: bool = False,
) -> str:
    identity = candidate if isinstance(candidate, DataIdentity) else payload_identity(candidate)
    if identity is None:
        return "MISSING_DATA_IDENTITY"
    if identity.project_id != current.project_id:
        return "PROJECT_ID_MISMATCH"
    if identity.source_generation_id != current.source_generation_id:
        return "SOURCE_GENERATION_MISMATCH"
    if current.source_sha256 and identity.source_sha256 != current.source_sha256:
        return "SOURCE_SHA256_MISMATCH"
    if require_run:
        if not identity.analysis_run_id or not current.analysis_run_id:
            return "MISSING_ANALYSIS_RUN_ID"
        if identity.analysis_run_id != current.analysis_run_id:
            return "ANALYSIS_RUN_MISMATCH"
    return ""


def archive_derived_working_state(project_root: Path, *, generation_id: str | None) -> Path | None:
    """Move all source-derived working files aside before a new DXF import.

    ``project.json`` and the archive tree itself stay in place.  Moving rather
    than copying guarantees that stale state cannot be discovered by normal
    working-file lookups.
    """

    working = project_root / "working"
    if not working.is_dir():
        return None
    movable = [
        child
        for child in working.iterdir()
        if child.name not in {"project.json", "source_generations"}
        and not child.name.endswith(".tmp")
        # Файловете-ключалки не са производно състояние, а средство за
        # заключване между процеси. Преместването им в архива при нов импорт е
        # безсмислено, а в Windows и невъзможно, докато някой ги държи.
        and not child.name.endswith(LOCK_SUFFIX)
    ]
    if not movable:
        return None
    token = str(generation_id or "legacy").replace("/", "_")
    archive = working / "source_generations" / f"{token}_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}"
    archive.mkdir(parents=True, exist_ok=False)
    manifest: list[dict[str, Any]] = []
    for child in movable:
        target = archive / child.name
        if child.is_file():
            item = {"name": child.name, "sha256": sha256_file(child), "kind": "file"}
        else:
            item = {"name": child.name, "kind": "directory"}
        shutil.move(str(child), str(target))
        manifest.append(item)
    (archive / "archive_manifest.json").write_text(
        json.dumps(
            {
                "schema": "lion-rebar-source-generation-archive/v1",
                "archived_at": _now(),
                "source_generation_id": generation_id or "",
                "items": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return archive
