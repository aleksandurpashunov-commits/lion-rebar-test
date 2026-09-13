from __future__ import annotations

import csv
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence, Any, Iterable

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.auto_gate import auto_classification_gate_passes
from lion_rebar.project.approval_origin import is_auto_approval, is_manual_approval
from lion_rebar.project.final_control_lifecycle import (
    STATUS_VALID as FREEZE_STATUS_VALID,
)
from lion_rebar.project.non_production import (
    is_valid_non_production_decision,
)
from lion_rebar.export.operator_areas import (
    KIND_BEAM,
    KIND_GROUP,
    OPERATOR_AREA_BEAM_METHOD,
    area_for_point,
    load_operator_areas,
)
from lion_rebar.export.beam_decisions import (
    OPERATOR_METHOD as OPERATOR_BEAM_METHOD,
    load_beam_decisions,
)
from lion_rebar.project.data_identity import (
    DataIdentity,
    IDENTITY_KEY,
    identity_from_project_payload,
    mismatch_reason,
    stamp_payload,
)
from lion_rebar.utils.files import sha256_file
from lion_rebar.review_center_model import (
    ACTIONABLE_CLASS,
    final_confirmation_eligible,
    is_position_key_legend_reference,
    is_missing_master_row,
)

_PROBLEM_DECISIONS = {
    "PROBLEM_RECORDED", "FLAGGED", "NO_VALID_CANDIDATE", "MISSING_MASTER"
}
_EXPLICIT_EXCLUSION_DECISIONS = {"EXCLUDED_BY_ENGINEER"}
_TERMINAL_NON_PRODUCTION_DECISIONS = {"NON_PRODUCTION_POSITION"}
_REVOKING_DECISIONS = (
    _PROBLEM_DECISIONS
    | _EXPLICIT_EXCLUSION_DECISIONS
    | _TERMINAL_NON_PRODUCTION_DECISIONS
)


@dataclass
class IntegrityIssue:
    code: str
    severity: str
    message: str
    annotation_handles: list[str] = field(default_factory=list)
    geometry_handles: list[str] = field(default_factory=list)
    repaired: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrityResult:
    project_name: str
    generated_at: str
    applied: bool
    approved_before: int
    approved_after: int
    stale_approvals_revoked: int
    duplicate_groups_found: int
    duplicate_links_repaired: int
    duplicate_links_removed: int
    duplicate_groups_remaining: int
    actionable_positions: int | None
    explicit_exclusions: int
    intrinsic_missing_master: int
    accounted_positions: int | None
    unresolved_positions: int | None
    safe_confirmation_pending: int | None
    engineering_decisions_pending: int | None
    integrity_passed: bool
    full_specification_complete: bool
    report_path: str | None
    backup_directory: str | None
    ledger_path: str | None
    non_production_positions: int = 0
    engineer_resolved_without_geometry_count: int = 0
    identity_failures: int = 0
    beam_assignments_frozen: int = 0
    vertical_assignments_frozen: int = 0
    vertical_sections_frozen: int = 0
    freeze_required: bool = False
    freeze_manifest_path: str | None = None
    export_preflight_error: str = ""
    data_identity: dict[str, Any] = field(default_factory=dict)
    issues: list[IntegrityIssue] = field(default_factory=list)
    #: Автоматична "silent wrong" метрика (т.15) — попълва се, ако вече е
    #: изчислена от пакетния анализ (``export.silent_wrong_metrics``).
    #: Никога не блокира Финалния контрол — липсва ли, остава празен речник.
    silent_wrong_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = [asdict(issue) for issue in self.issues]
        return payload


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _geometry_key(link: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(value) for value in link.get("geometry_handles", []) if str(value)))


def _entity_map(extracted_payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(extracted_payload, dict):
        return {}
    entities = extracted_payload.get("entities", [])
    if not isinstance(entities, list):
        return {}
    return {
        str(entity.get("handle") or ""): dict(entity)
        for entity in entities
        if isinstance(entity, dict) and str(entity.get("handle") or "")
    }


def _annotation_point(entity: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(entity, dict):
        return None
    geometry = entity.get("geometry")
    if not isinstance(geometry, dict):
        return None
    value = geometry.get("visual_anchor") or geometry.get("insert")
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _line_points(entity: dict[str, Any] | None) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if not isinstance(entity, dict) or str(entity.get("entity_type") or "").upper() != "LINE":
        return None
    geometry = entity.get("geometry")
    if not isinstance(geometry, dict):
        return None
    start = geometry.get("start")
    end = geometry.get("end")
    if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)):
        return None
    if len(start) < 2 or len(end) < 2:
        return None
    try:
        return (float(start[0]), float(start[1])), (float(end[0]), float(end[1]))
    except (TypeError, ValueError):
        return None


def _line_length(entity: dict[str, Any] | None) -> float | None:
    points = _line_points(entity)
    if points is None:
        return None
    (x1, y1), (x2, y2) = points
    return math.hypot(x2 - x1, y2 - y1)


def _point_segment_distance(
    point: tuple[float, float],
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    px, py = point
    (x1, y1), (x2, y2) = segment
    dx = x2 - x1
    dy = y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / denominator
    t = max(0.0, min(1.0, t))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return math.hypot(px - qx, py - qy)


def _distance_to_entity(annotation: dict[str, Any] | None, geometry: dict[str, Any] | None) -> float | None:
    point = _annotation_point(annotation)
    segment = _line_points(geometry)
    if point is None or segment is None:
        return None
    return _point_segment_distance(point, segment)


def _duplicate_groups(links: Iterable[dict[str, Any]]) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for link in links:
        if is_position_key_legend_reference(link):
            continue
        key = _geometry_key(link)
        if not key:
            continue
        groups.setdefault(key, []).append(link)
    return {key: values for key, values in groups.items() if len(values) > 1}


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _find_safe_single_line_replacement(
    *,
    link: dict[str, Any],
    entity_by_handle: dict[str, dict[str, Any]],
    unavailable_handles: set[str],
    shared_handle: str,
) -> tuple[str, dict[str, Any], float, float] | None:
    annotation = entity_by_handle.get(str(link.get("annotation_handle") or ""))
    annotation_point = _annotation_point(annotation)
    declared = _safe_float(link.get("declared_length_cm"))
    layer = str(link.get("geometry_layer") or "")
    if annotation_point is None or declared is None or declared <= 0 or not layer:
        return None

    tolerance = max(1.0, declared * 0.005)
    candidates: list[tuple[float, str, dict[str, Any], float]] = []
    for handle, entity in entity_by_handle.items():
        if handle == shared_handle or handle in unavailable_handles:
            continue
        if str(entity.get("entity_type") or "").upper() != "LINE":
            continue
        if str(entity.get("layer") or "") != layer:
            continue
        length = _line_length(entity)
        segment = _line_points(entity)
        if length is None or segment is None or abs(length - declared) > tolerance:
            continue
        distance = _point_segment_distance(annotation_point, segment)
        if distance > 60.0:
            continue
        candidates.append((distance, handle, entity, length))

    if not candidates:
        return None
    candidates.sort(key=lambda value: (value[0], value[1]))
    best = candidates[0]
    if len(candidates) > 1 and candidates[1][0] - best[0] < 8.0:
        return None
    return best[1], best[2], best[3], best[0]


def _backup_files(working_dir: Path, files: Iterable[Path]) -> Path:
    backup_dir = working_dir / "integrity_backups" / _stamp()
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in files:
        if path.is_file():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def _load_report_rows(
    report_path: Path | None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    if report_path is None or not report_path.is_file():
        return [], None, {}
    payload = _read_json(report_path, default={})
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return [], str(report_path), payload if isinstance(payload, dict) else {}
    return (
        [dict(row) for row in payload["rows"] if isinstance(row, dict)],
        str(report_path),
        dict(payload),
    )



def _dxf_records(extracted_payload: Any) -> list[DxfEntityRecord]:
    if not isinstance(extracted_payload, dict):
        return []
    records: list[DxfEntityRecord] = []
    for raw in extracted_payload.get("entities", []):
        if not isinstance(raw, dict) or not str(raw.get("handle") or ""):
            continue
        records.append(
            DxfEntityRecord(
                handle=str(raw.get("handle") or ""),
                entity_type=str(raw.get("entity_type") or ""),
                layer=str(raw.get("layer") or ""),
                geometry=dict(raw.get("geometry") or {}),
                text=raw.get("text"),
                block_name=raw.get("block_name"),
                color_index=raw.get("color_index"),
                linetype=raw.get("linetype"),
                raw=dict(raw.get("raw") or {}),
            )
        )
    return records


def _freeze_beam_assignments(
    *,
    rows: list[dict[str, Any]],
    links_by_annotation: dict[str, dict[str, Any]],
    extracted_payload: Any,
    project_root: Path,
    identity: DataIdentity,
) -> tuple[dict[str, dict[str, Any]], list[IntegrityIssue]]:
    """Resolve beam ownership once during Final Control.

    Excel/DXF export consumes these frozen values rather than rerunning the
    spatial heuristic.  This prevents a later code path or mutable input from
    silently moving one stirrup/position to another beam.
    """

    operator_beam_decisions = load_beam_decisions(project_root, identity)
    # Рамките, начертани ПРЕДИ анализа (виж ``export/operator_areas.py``).
    # За разлика от решенията по-горе, които сочат една позиция по handle,
    # областта е правоъгълник: какво е попаднало вътре се решава тук, наново,
    # при всеки Финален контрол. Затова добавена позиция влиза в групата си
    # сама, без операторът да пипа рамката повторно.
    operator_areas = load_operator_areas(project_root, identity)
    beam_labels_by_handle: dict[str, Any] = {}

    target_handles = {
        str(row.get("annotation_handle") or "")
        for row in rows
        if str(row.get("annotation_class") or "") == ACTIONABLE_CLASS
        and str(row.get("annotation_handle") or "") in links_by_annotation
    }
    # A project may be complete with every actionable row terminally marked as
    # NON_PRODUCTION_POSITION.  There is then no production position whose
    # frame/beam ownership must be frozen.
    if not target_handles:
        return {}, []

    records = _dxf_records(extracted_payload)
    if not records:
        return {}, [
            IntegrityIssue(
                code="BEAM_FREEZE_NO_ENTITIES",
                severity="ERROR",
                message="Extracted DXF entities are unavailable; beam ownership cannot be frozen.",
            )
        ]
    from lion_rebar.export.excel_export import (  # lazy: avoid module cycle at import time
        _assign_beam_label,
        _assign_vertical_section,
        _detect_beam_labels,
        _detect_vertical_section_labels,
        _entity_insert,
    )
    from lion_rebar.export.frame_decisions import load_frame_decisions
    from lion_rebar.export.frame_detection import (
        apply_operator_frame_decisions,
        assign_point_to_frame,
        detect_drawing_frames,
        unresolved_frames,
    )

    frames = apply_operator_frame_decisions(
        detect_drawing_frames(records),
        load_frame_decisions(project_root, identity),
    )
    if not frames:
        return {}, [
            IntegrityIssue(
                code="BEAM_FREEZE_NO_FRAMES",
                severity="ERROR",
                message=(
                    "Не е открита стандартна рамка „ЧЕРТЕЖ:“ и няма точно едно "
                    "недвусмислено заглавие за еднолистов чертеж. Frame/beam "
                    "решенията не могат да бъдат замразени."
                ),
            )
        ]
    unresolved = unresolved_frames(frames)
    if unresolved:
        return {}, [
            IntegrityIssue(
                code="FRAME_CATEGORY_UNRESOLVED",
                severity="ERROR",
                message=(
                    "Има чертожна рамка с неопределена категория. Избери "
                    "категорията на рамката и пусни Финален контрол отново."
                ),
                details={
                    "frames": [
                        {
                            "index": int(frame.index),
                            "marker_handle": str(frame.marker_handle or ""),
                            "title": str(frame.combined_title or ""),
                        }
                        for frame in unresolved
                    ]
                },
            )
        ]
    frame_map = {int(frame.index): frame for frame in frames}
    labels = _detect_beam_labels(records, frames)
    try:
        vertical_labels = _detect_vertical_section_labels(records, frames)
    except Exception as exc:
        return {}, [
            IntegrityIssue(
                code="VERTICAL_SECTION_DETECTION_FAILED",
                severity="ERROR",
                message=f"Vertical title/divider detection failed: {exc}",
            )
        ]
    entity_map = {record.handle: record for record in records}
    assignments: dict[str, dict[str, Any]] = {}
    issues: list[IntegrityIssue] = []
    used_vertical_sections: set[tuple[int, str, str]] = set()
    beam_labels_by_handle = {label.handle: label for label in labels}

    for row in rows:
        if str(row.get("annotation_class") or "") != ACTIONABLE_CLASS:
            continue
        handle = str(row.get("annotation_handle") or "")
        if handle not in links_by_annotation:
            continue
        annotation = entity_map.get(handle)
        point = _entity_insert(annotation)
        if point is None:
            issues.append(
                IntegrityIssue(
                    code="BEAM_FREEZE_MISSING_ANNOTATION_POINT",
                    severity="ERROR",
                    message="An approved position has no current DXF insertion point.",
                    annotation_handles=[handle],
                )
            )
            continue
        frame_index = assign_point_to_frame(point[0], point[1], frames)
        if frame_index is None:
            issues.append(
                IntegrityIssue(
                    code="BEAM_FREEZE_MISSING_FRAME",
                    severity="ERROR",
                    message="An approved position cannot be assigned to a drawing frame.",
                    annotation_handles=[handle],
                )
            )
            continue
        frame = frame_map.get(int(frame_index))
        if frame is None:
            issues.append(
                IntegrityIssue(
                    code="BEAM_FREEZE_FRAME_LOOKUP_FAILED",
                    severity="ERROR",
                    message="The resolved drawing frame is missing from the frame map.",
                    annotation_handles=[handle],
                )
            )
            continue
        frozen: dict[str, Any] = {
            "frame_index": int(frame_index),
            "frame_category_code": str(frame.category_code or ""),
            "frame_title_text": str(frame.combined_title or ""),
            "beam_assignment_frozen": True,
            "beam_name": "",
            "beam_label_handle": "",
            "beam_assignment_method": "FINAL_CONTROL_NON_BEAM",
            "beam_assignment_score": "",
            "beam_assignment_margin": "",
            "vertical_assignment_frozen": False,
            "vertical_section_title": "",
            "vertical_section_label_handle": "",
            "vertical_section_divider_handle": "",
            "vertical_section_kind": "",
            "vertical_section_order": "",
            "vertical_assignment_method": "FINAL_CONTROL_NON_VERTICAL",
            "vertical_assignment_score": "",
            "vertical_assignment_margin": "",
            # Заглавието на групата колони ("К23, К24 (35/35) - 2бр."), за да
            # може листът с колоните да се подрежда група по група, както
            # гредите се подреждат по ``beam_name``, а вертикалите по
            # ``vertical_section_title``. Без него Excel export-ът чете празен
            # етикет от ledger-а и изсипва всички позиции накуп.
            "column_group_label": str(row.get("column_group_label") or ""),
        }
        # Ако автоматиката не е намерила групов етикет, го взимаме от
        # операторска област. Точно това е случаят при шайбите: заглавията
        # („Ш 3-25/600") съществуват, но затворените контури, по които
        # ``proxy_adapter`` разпознава групите при колоните, там ги няма.
        # Само попълва празно — доказан автоматичен етикет не се пипа.
        if not frozen["column_group_label"]:
            group_area = area_for_point(
                operator_areas,
                point[0],
                point[1],
                kind=KIND_GROUP,
                frame_index=int(frame_index),
            )
            if group_area is not None:
                # Произходът не се дописва в ledger-а: схемата на замразения
                # артефакт не се пипа мимоходом. Кой, кога и защо е начертал
                # областта стои в ``operator_group_areas.json`` и в одитния
                # ѝ файл, вързани със същата identity.
                frozen["column_group_label"] = group_area.title_text
        if str(frame.category_code or "") == "BEAMS":
            assignment = _assign_beam_label(point[0], point[1], int(frame_index), labels)
            if assignment is None:
                # Първо рамката, начертана преди анализа: тя покрива цяла
                # група наведнъж, затова е по-евтина за оператора от решение
                # за всяка позиция поотделно. Достига се само след като
                # автоматиката се е отказала — доказана връзка не се отменя.
                beam_area = area_for_point(
                    operator_areas,
                    point[0],
                    point[1],
                    kind=KIND_BEAM,
                    frame_index=int(frame_index),
                )
                area_label = (
                    beam_labels_by_handle.get(beam_area.title_handle)
                    if beam_area is not None
                    else None
                )
                if area_label is not None:
                    frozen.update(
                        {
                            "beam_name": area_label.title,
                            "beam_label_handle": area_label.handle,
                            "beam_assignment_method": OPERATOR_AREA_BEAM_METHOD,
                            "beam_assignment_score": "",
                            "beam_assignment_margin": "",
                        }
                    )
                    assignments[handle] = frozen
                    continue
                decided_label = beam_labels_by_handle.get(
                    operator_beam_decisions.get(handle, "")
                )
                if decided_label is not None:
                    frozen.update(
                        {
                            "beam_name": decided_label.title,
                            "beam_label_handle": decided_label.handle,
                            "beam_assignment_method": OPERATOR_BEAM_METHOD,
                            "beam_assignment_score": "",
                            "beam_assignment_margin": "",
                        }
                    )
                    assignments[handle] = frozen
                    continue
                issues.append(
                    IntegrityIssue(
                        code="BEAM_ASSIGNMENT_NOT_UNIQUE",
                        severity="ERROR",
                        message=(
                            "No beam title lies in the accepted window for this "
                            "approved position, so its beam cannot be proven. "
                            "Record an operator beam decision for it and run "
                            "Final Control again."
                        ),
                        annotation_handles=[handle],
                    )
                )
                continue
            frozen.update(
                {
                    "beam_name": assignment.label.title,
                    "beam_label_handle": assignment.label.handle,
                    "beam_assignment_method": assignment.method,
                    "beam_assignment_score": assignment.score,
                    "beam_assignment_margin": assignment.margin,
                }
            )
        elif str(frame.category_code or "") == "VERTICALS":
            vertical_assignment = _assign_vertical_section(
                point[0],
                point[1],
                int(frame_index),
                vertical_labels,
            )
            if vertical_assignment is None:
                issues.append(
                    IntegrityIssue(
                        code="VERTICAL_SECTION_ASSIGNMENT_NOT_UNIQUE",
                        severity="ERROR",
                        message=(
                            "An approved vertical position cannot be assigned "
                            "uniquely to one explicit title/divider section."
                        ),
                        annotation_handles=[handle],
                    )
                )
                continue
            section = vertical_assignment.section
            frozen.update(
                {
                    "vertical_assignment_frozen": True,
                    "vertical_section_title": section.title,
                    "vertical_section_label_handle": section.title_handle,
                    "vertical_section_divider_handle": section.divider_handle,
                    "vertical_section_kind": section.kind,
                    "vertical_section_order": section.order,
                    "vertical_assignment_method": vertical_assignment.method,
                    "vertical_assignment_score": vertical_assignment.score,
                    "vertical_assignment_margin": vertical_assignment.margin,
                }
            )
            used_vertical_sections.add(
                (
                    int(section.frame_index),
                    section.title_handle,
                    section.divider_handle,
                )
            )
        assignments[handle] = frozen
    for section in vertical_labels:
        section_key = (
            int(section.frame_index),
            section.title_handle,
            section.divider_handle,
        )
        if section_key in used_vertical_sections:
            continue
        issues.append(
            IntegrityIssue(
                code="VERTICAL_SECTION_WITHOUT_POSITION",
                severity="ERROR",
                message=(
                    "An explicit vertical title/divider section has no approved "
                    "position and cannot be frozen."
                ),
                details={
                    "frame_index": int(section.frame_index),
                    "title": section.title,
                    "title_handle": section.title_handle,
                    "divider_handle": section.divider_handle,
                },
            )
        )
    return assignments, issues


def _geometry_provenance(link: dict[str, Any] | None) -> tuple[int, str]:
    handles = [str(value) for value in (link or {}).get("geometry_handles", []) if str(value)]
    if handles:
        return len(handles), "DXF_HANDLES"
    marker = str((link or {}).get("geometry_provenance") or "").strip().upper()
    if marker:
        return 0, marker
    if bool((link or {}).get("geometry_optional_by_engineer")):
        return 0, "NONE_ENGINEER_OVERRIDE"
    return 0, "NONE_UNCLASSIFIED"


def _stage_final_control_batch_report(
    *,
    root: Path,
    working_dir: Path,
    report_path: Path,
) -> Path:
    """Copy the exact selected report into the project before it is frozen.

    Cloud-first review folders remain supported for active work, but a final
    production freeze must be self-contained.  The staged bytes are verified
    before the external source can disappear or be replaced.
    """

    source = report_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Selected batch report is missing: {source}")
    target_dir = working_dir / "final_control_sources"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "batch_report.json"
    if source == target.resolve():
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != sha256_file(source):
        temporary.unlink(missing_ok=True)
        raise OSError("Staged batch report SHA-256 does not match the selected report.")
    temporary.replace(target)
    return target


def _build_freeze_manifest(
    *,
    root: Path,
    manifest_path: Path,
    files: list[tuple[str, Path]],
    identity: DataIdentity,
    frames: Sequence[Any] = (),
) -> dict[str, Any]:
    """Freeze every input/output accepted by Final Control.

    Missing artifacts and paths outside the project are hard errors.  External
    cloud reports are staged into ``working/final_control_sources`` first, so
    the production freeze remains self-contained.  A manifest containing
    ``exists: false`` would look auditable while proving nothing and is
    deliberately impossible to create.
    """

    from lion_rebar.export.frozen_export_config import (
        config_fingerprint,
        mapping_from_frames,
        write_frozen_config,
    )

    mapping = mapping_from_frames(frames)
    config_path = write_frozen_config(root, mapping)
    export_config_fingerprint = config_fingerprint(mapping)
    files = [*files, ("EXPORT_CONFIG", config_path)]

    root_resolved = root.resolve()
    artifacts: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    seen_paths: set[Path] = set()
    for raw_role, path in files:
        role = str(raw_role or "").strip().upper()
        if not role or role in seen_roles:
            raise ValueError(f"Duplicate or empty freeze artifact role: {raw_role!r}")
        resolved = path.resolve()
        try:
            relative = str(resolved.relative_to(root_resolved))
            external = False
        except ValueError as exc:
            raise ValueError(
                f"Freeze artifact is outside the project root: {resolved}"
            ) from exc
        if resolved in seen_paths:
            raise ValueError(f"Duplicate freeze artifact path: {relative}")
        if not resolved.is_file():
            raise FileNotFoundError(f"Required freeze artifact is missing: {relative}")
        seen_roles.add(role)
        seen_paths.add(resolved)
        artifacts.append(
            {
                "role": role,
                "path": relative,
                "external": external,
                "exists": True,
                "sha256": sha256_file(resolved),
            }
        )
    payload: dict[str, Any] = {
        "schema": "lion-rebar-final-control-freeze/v1",
        "export_config_fingerprint": export_config_fingerprint,
        # Явен статус на замразяването. Export-ът разрешава САМО точно
        # "VALID"; липсващ статус се чете като INVALID. До Windows приемането
        # на rc2 freeze манифестът не записваше това поле изобщо, затова
        # export-ът блокираше веднага след успешен Финален контрол.
        "status": FREEZE_STATUS_VALID,
        "generated_at": _now(),
        IDENTITY_KEY: identity.to_dict(),
        "artifacts": artifacts,
    }
    _write_json_atomic(manifest_path, payload)
    return payload


class FinalExportPreflightError(RuntimeError):
    """The exact production context could not be loaded after Final Control."""


def _run_final_export_preflight(
    *,
    root: Path,
    project_payload: dict[str, Any],
    expected_position_count: int,
) -> None:
    """Run the same locked-data loader used immediately before Excel export.

    This deliberately runs only after the integrity report and VALID freeze
    manifest exist, because that is the exact state consumed by Export. Any
    failure removes the manifest in the caller and turns Final Control red.
    """

    try:
        from lion_rebar.engine.models import EnginePackage
        from lion_rebar.export.excel_export import (
            load_export_context,
            load_pinned_engine,
        )
        from lion_rebar.project.models import ProjectModel

        project = ProjectModel.from_dict(project_payload)
        project.root = str(root)
        relative = str(project.extensions.get("engine_snapshot") or "").strip()
        if not relative:
            raise ValueError(
                "Проектът няма заключен Engine snapshot за производствения Export."
            )
        snapshot_path = root / relative
        snapshot_payload = _read_json(snapshot_path, default={})
        if not isinstance(snapshot_payload, dict) or not snapshot_payload:
            raise ValueError(f"Engine snapshot-ът липсва или е невалиден: {snapshot_path}")
        fallback = EnginePackage.from_dict(snapshot_payload)
        engine = load_pinned_engine(project, fallback)
        _frames, positions = load_export_context(project, (), engine)
        if len(positions) != int(expected_position_count):
            raise ValueError(
                "Export preflight върна различен брой производствени позиции "
                f"({len(positions)}/{expected_position_count})."
            )
    except FinalExportPreflightError:
        raise
    except Exception as exc:
        raise FinalExportPreflightError(str(exc)) from exc


def _write_ledger(
    *,
    path: Path,
    rows: list[dict[str, Any]],
    links_by_annotation: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    identity: DataIdentity | None = None,
    beam_assignments: dict[str, dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    beam_assignments = beam_assignments or {}
    identity_payload = identity.to_dict() if identity is not None else {}
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "annotation_handle",
                "original_annotation_text",
                "resolved_annotation_text",
                "final_status",
                "decision_source",
                "action_class",
                "decision_reason",
                "shape_code",
                "shape_name",
                "production_count",
                "production_diameter_mm",
                "geometry_handles",
                "geometry_handles_count",
                "geometry_provenance",
                "declared_length_cm",
                "production_length_cm",
                "production_segments_cm",
                "production_length_source",
                "project_id",
                "source_sha256",
                "source_generation_id",
                "analysis_run_id",
                "frame_index",
                "frame_category_code",
                "frame_title_text",
                "beam_assignment_frozen",
                "beam_name",
                "beam_label_handle",
                "beam_assignment_method",
                "beam_assignment_score",
                "beam_assignment_margin",
                "vertical_assignment_frozen",
                "vertical_section_title",
                "vertical_section_label_handle",
                "vertical_section_divider_handle",
                "vertical_section_kind",
                "vertical_section_order",
                "vertical_assignment_method",
                "vertical_assignment_score",
                "vertical_assignment_margin",
                "column_group_label",
            ],
        )
        writer.writeheader()
        for row in rows:
            if str(row.get("annotation_class") or "") != ACTIONABLE_CLASS:
                continue
            handle = str(row.get("annotation_handle") or "")
            link = links_by_annotation.get(handle)
            decision = decisions.get(handle, {})
            code = str(decision.get("decision") or "")
            if link is not None:
                final_status = "APPROVED"
            elif (
                code in _TERMINAL_NON_PRODUCTION_DECISIONS
                and is_valid_non_production_decision(decision)
            ):
                final_status = "NON_PRODUCTION_POSITION"
            elif code in _TERMINAL_NON_PRODUCTION_DECISIONS:
                final_status = "PROBLEM_UNRESOLVED"
            elif code in _EXPLICIT_EXCLUSION_DECISIONS:
                final_status = "EXCLUDED_BY_ENGINEER"
            elif code in _PROBLEM_DECISIONS or is_missing_master_row(row):
                final_status = "PROBLEM_UNRESOLVED"
            else:
                final_status = "UNRESOLVED"
            geometry_count, geometry_provenance = _geometry_provenance(link)
            frozen = beam_assignments.get(handle, {})
            writer.writerow(
                {
                    "annotation_handle": handle,
                    "original_annotation_text": (link or {}).get("source_annotation_text") or row.get("annotation_text"),
                    "resolved_annotation_text": (link or {}).get("resolved_annotation_text") or (link or {}).get("annotation_text") or row.get("annotation_text"),
                    "final_status": final_status,
                    "decision_source": (link or {}).get("approved_by") or decision.get("action_class") or "",
                    "action_class": ((link or {}).get("engineer_decision") or {}).get("action_class") or decision.get("action_class") or "",
                    "decision_reason": ((link or {}).get("engineer_decision") or {}).get("reason") or decision.get("reason") or decision.get("reason_code") or "",
                    "shape_code": (link or {}).get("master_shape_code") or row.get("master_shape_code") or "",
                    "shape_name": (link or {}).get("master_shape_name") or row.get("master_shape_name") or "",
                    "production_count": (link or {}).get("production_count") or (link or {}).get("annotation_count") or row.get("annotation_count") or "",
                    "production_diameter_mm": (link or {}).get("production_diameter_mm") or (link or {}).get("diameter_mm") or row.get("diameter_mm") or "",
                    "geometry_handles": ",".join((link or {}).get("geometry_handles", [])),
                    "geometry_handles_count": geometry_count,
                    "geometry_provenance": geometry_provenance,
                    "declared_length_cm": row.get("declared_length_cm"),
                    "production_length_cm": (link or {}).get("production_length_cm", row.get("declared_length_cm")),
                    "production_segments_cm": ",".join(str(value) for value in ((link or {}).get("production_segment_lengths_cm") or [])),
                    "production_length_source": (link or {}).get("production_length_source", ""),
                    "project_id": identity_payload.get("project_id", ""),
                    "source_sha256": identity_payload.get("source_sha256", ""),
                    "source_generation_id": identity_payload.get("source_generation_id", ""),
                    "analysis_run_id": identity_payload.get("analysis_run_id", ""),
                    "frame_index": frozen.get("frame_index", ""),
                    "frame_category_code": frozen.get("frame_category_code", ""),
                    "frame_title_text": frozen.get("frame_title_text", ""),
                    "beam_assignment_frozen": int(bool(frozen.get("beam_assignment_frozen"))),
                    "beam_name": frozen.get("beam_name", ""),
                    "beam_label_handle": frozen.get("beam_label_handle", ""),
                    "beam_assignment_method": frozen.get("beam_assignment_method", ""),
                    "beam_assignment_score": frozen.get("beam_assignment_score", ""),
                    "beam_assignment_margin": frozen.get("beam_assignment_margin", ""),
                    "vertical_assignment_frozen": int(
                        bool(frozen.get("vertical_assignment_frozen"))
                    ),
                    "vertical_section_title": frozen.get(
                        "vertical_section_title",
                        "",
                    ),
                    "vertical_section_label_handle": frozen.get(
                        "vertical_section_label_handle",
                        "",
                    ),
                    "vertical_section_divider_handle": frozen.get(
                        "vertical_section_divider_handle",
                        "",
                    ),
                    "vertical_section_kind": frozen.get(
                        "vertical_section_kind",
                        "",
                    ),
                    "vertical_section_order": frozen.get(
                        "vertical_section_order",
                        "",
                    ),
                    "vertical_assignment_method": frozen.get(
                        "vertical_assignment_method",
                        "",
                    ),
                    "vertical_assignment_score": frozen.get(
                        "vertical_assignment_score",
                        "",
                    ),
                    "vertical_assignment_margin": frozen.get(
                        "vertical_assignment_margin",
                        "",
                    ),
                    "column_group_label": frozen.get("column_group_label", ""),
                }
            )


def reconcile_project_integrity(
    project_root: Path | str,
    *,
    report_path: Path | str | None = None,
    apply: bool = True,
) -> IntegrityResult:
    """Audit and optionally repair project decision/approval consistency.

    The repair is intentionally conservative. Explicit Review Center rejection
    decisions always revoke stale approvals. Exact duplicate one-line geometry
    may be reassigned only when a unique, unused, same-layer, same-length line is
    clearly closer to the duplicate annotation. Everything else remains blocked
    and is written to the integrity report for manual resolution.
    """

    root = Path(project_root)
    working_dir = root / "working"
    approved_path = working_dir / "approved_geometry_links.json"
    state_path = working_dir / "review_center_state.json"
    problems_path = working_dir / "review_problems.json"
    extracted_path = working_dir / "extracted_entities.json"
    project_path = working_dir / "project.json"

    approved_payload = _read_json(
        approved_path,
        default={"schema": "lion-rebar-approved-geometry-links/v1", "links": [], "history": []},
    )
    if not isinstance(approved_payload, dict):
        approved_payload = {"schema": "lion-rebar-approved-geometry-links/v1", "links": [], "history": []}
    links = [dict(value) for value in approved_payload.get("links", []) if isinstance(value, dict)]
    history = [dict(value) for value in approved_payload.get("history", []) if isinstance(value, dict)]
    state = _read_json(state_path, default={})
    decisions_raw = state.get("decisions", {}) if isinstance(state, dict) else {}
    decisions: dict[str, dict[str, Any]] = {
        str(handle): dict(value)
        for handle, value in decisions_raw.items()
        if isinstance(value, dict)
    }
    project_payload = _read_json(project_path, default={})
    project_name = str(
        approved_payload.get("project_name")
        or (project_payload.get("name") if isinstance(project_payload, dict) else "")
        or root.name
    )
    extracted_payload = _read_json(extracted_path, default={})
    entity_by_handle = _entity_map(extracted_payload)
    rows, resolved_report_path, report_payload = _load_report_rows(
        Path(report_path) if report_path else None
    )

    issues: list[IntegrityIssue] = []
    approved_before = len(links)
    timestamp = _now()
    current_data_identity = (
        identity_from_project_payload(project_payload)
        if isinstance(project_payload, dict)
        else None
    )
    identity_failures = 0
    report_identity_valid = True
    if current_data_identity is not None:
        if not current_data_identity.analysis_run_id:
            identity_failures += 1
            report_identity_valid = False
            issues.append(
                IntegrityIssue(
                    code="MISSING_CURRENT_ANALYSIS_RUN",
                    severity="ERROR",
                    message="Project data identity has no active analysis_run_id.",
                )
            )
        report_reason = mismatch_reason(
            report_payload, current_data_identity, require_run=True
        )
        if report_reason:
            identity_failures += 1
            report_identity_valid = False
            issues.append(
                IntegrityIssue(
                    code="STALE_BATCH_REPORT",
                    severity="ERROR",
                    message="The selected batch report is not from the current project/source/run.",
                    details={"reason": report_reason},
                )
            )
        extracted_reason = mismatch_reason(
            extracted_payload, current_data_identity, require_run=False
        )
        if extracted_reason:
            identity_failures += 1
            issues.append(
                IntegrityIssue(
                    code="STALE_EXTRACTED_ENTITIES",
                    severity="ERROR",
                    message="Extracted entities are not from the current source generation.",
                    details={"reason": extracted_reason},
                )
            )
        source_relative = str(project_payload.get("source_dxf") or "")
        source_path = root / source_relative if source_relative else root / "source" / "original.dxf"
        if (
            not source_path.is_file()
            or sha256_file(source_path) != current_data_identity.source_sha256
        ):
            identity_failures += 1
            issues.append(
                IntegrityIssue(
                    code="SOURCE_DXF_IDENTITY_MISMATCH",
                    severity="ERROR",
                    message="The current source DXF does not match project data identity.",
                )
            )

    rows_by_handle: dict[str, dict[str, Any]] = {
        str(row.get("annotation_handle") or ""): row
        for row in rows
        if isinstance(row, dict)
    }
    current_report_sha256 = (
        sha256_file(Path(resolved_report_path))
        if resolved_report_path and Path(resolved_report_path).is_file()
        else ""
    )
    canonical_staged_report = (
        working_dir / "final_control_sources" / "batch_report.json"
    )
    selected_report_is_canonical_staged = False
    if resolved_report_path:
        try:
            selected_report = Path(resolved_report_path)
            selected_report_is_canonical_staged = (
                selected_report.absolute() == canonical_staged_report.absolute()
                and selected_report.resolve(strict=True)
                == canonical_staged_report.resolve(strict=True)
            )
        except (OSError, RuntimeError):
            # Path identity is provenance evidence. If it cannot be resolved,
            # the canonical staging exception must remain closed.
            selected_report_is_canonical_staged = False
    if current_data_identity is not None and report_identity_valid:
        stale_row_handles: list[str] = []
        stale_row_reasons: dict[str, str] = {}
        for handle, row in rows_by_handle.items():
            reason = mismatch_reason(row, current_data_identity, require_run=True)
            if reason:
                stale_row_handles.append(handle)
                stale_row_reasons[handle] = reason
        if stale_row_handles:
            identity_failures += 1
            report_identity_valid = False
            issues.append(
                IntegrityIssue(
                    code="MIXED_IDENTITY_REPORT_ROWS",
                    severity="ERROR",
                    message=(
                        "The batch report contains rows outside the current "
                        "project/source/run identity."
                    ),
                    annotation_handles=sorted(stale_row_handles),
                    details={"reasons": stale_row_reasons},
                )
            )

    # Review decisions can revoke approvals and can terminally exclude a row.
    # Therefore the state file is source-derived input and must never be trusted
    # across project/source generations.  Legacy state may be migrated only when
    # every decision points to a row in the current, identity-validated report.
    state_identity_migratable = False
    if current_data_identity is not None:
        state_reason = mismatch_reason(state, current_data_identity, require_run=False)
        if state_reason == "MISSING_DATA_IDENTITY":
            decision_handles = {str(value) for value in decisions if str(value)}
            if not decision_handles or (report_identity_valid and decision_handles <= set(rows_by_handle)):
                state_identity_migratable = True
            else:
                identity_failures += 1
                issues.append(
                    IntegrityIssue(
                        code="STALE_REVIEW_STATE",
                        severity="ERROR",
                        message=(
                            "Review Center state has no data identity and contains decisions "
                            "that are not present in the current batch report."
                        ),
                        annotation_handles=sorted(decision_handles - set(rows_by_handle)),
                        details={"reason": state_reason},
                    )
                )
        elif state_reason:
            identity_failures += 1
            issues.append(
                IntegrityIssue(
                    code="STALE_REVIEW_STATE",
                    severity="ERROR",
                    message="Review Center state belongs to another project/source generation.",
                    details={"reason": state_reason},
                )
            )

    effective_apply = bool(apply and identity_failures == 0)

    def _link_invalid_reason(link: dict[str, Any]) -> str:
        handle = str(link.get("annotation_handle") or "")
        row = rows_by_handle.get(handle)
        if report_identity_valid and rows_by_handle and row is None:
            return "LINK_NO_CURRENT_REPORT_ROW"

        geometry_handles = list(_geometry_key(link))
        if (
            current_data_identity is not None
            and geometry_handles
            and any(value not in entity_by_handle for value in geometry_handles)
        ):
            return "LINK_GEOMETRY_NOT_IN_CURRENT_SOURCE"
        if not geometry_handles:
            _count, provenance = _geometry_provenance(link)
            if provenance not in {"NONE_ENGINEER_OVERRIDE", "NONE_ENGINEER_CORRECTION"}:
                return "LINK_WITHOUT_GEOMETRY_PROVENANCE"

        if current_data_identity is not None:
            if is_auto_approval(link):
                identity_reason = mismatch_reason(
                    link, current_data_identity, require_run=True
                )
                if identity_reason:
                    return f"AUTO_{identity_reason}"
            elif is_manual_approval(link):
                identity_reason = mismatch_reason(
                    link, current_data_identity, require_run=False
                )
                if identity_reason == "MISSING_DATA_IDENTITY" and row is not None:
                    # Safe legacy migration: the owner decision is attached to a
                    # current report row and its geometry exists in this source.
                    stamp_payload(link, current_data_identity)
                    link["identity_migrated_at"] = timestamp
                    link["identity_migration_source"] = "FINAL_CONTROL_CURRENT_ROW"
                elif identity_reason:
                    return f"MANUAL_{identity_reason}"
            else:
                identity_reason = mismatch_reason(
                    link, current_data_identity, require_run=False
                )
                if identity_reason:
                    return f"UNKNOWN_ORIGIN_{identity_reason}"

        if is_auto_approval(link):
            if row is not None:
                if not auto_classification_gate_passes(row):
                    return "AUTO_LINK_ROW_FAILS_GATE"
            for key in (
                "source_report_sha256",
                "source_dxf_sha256",
                "engine_hash",
                "matcher_sha256",
                "rules_sha256",
            ):
                if not str(link.get(key) or ""):
                    return "AUTO_LINK_MISSING_PROVENANCE"
            if (
                current_report_sha256
                and str(link.get("source_report_sha256") or "")
                != current_report_sha256
            ):
                return "AUTO_LINK_REPORT_HASH_MISMATCH"
            auto_gate = link.get("auto_gate")
            if not isinstance(auto_gate, dict):
                return "AUTO_LINK_MISSING_GATE_AUDIT"
            if resolved_report_path:
                recorded_report_name = str(auto_gate.get("report") or "")
                selected_report_name = Path(resolved_report_path).name
                report_name_matches = recorded_report_name == selected_report_name
                canonical_stage_hash_proved = bool(
                    selected_report_is_canonical_staged
                    and recorded_report_name
                    and current_report_sha256
                    and str(link.get("source_report_sha256") or "")
                    == current_report_sha256
                )
                if not report_name_matches and not canonical_stage_hash_proved:
                    return "AUTO_LINK_REPORT_NAME_MISMATCH"
            if current_data_identity is not None and (
                str(link.get("source_dxf_sha256") or "")
                != current_data_identity.source_sha256
            ):
                return "AUTO_LINK_SOURCE_DXF_HASH_MISMATCH"
        return ""

    retained: list[dict[str, Any]] = []
    revoked = 0
    for link in links:
        annotation = str(link.get("annotation_handle") or "")
        invalid_reason = _link_invalid_reason(link)
        if invalid_reason:
            revoked += 1
            issues.append(
                IntegrityIssue(
                    code="STALE_OR_FOREIGN_LINK_REVOKED",
                    severity="ERROR",
                    message=(
                        "An approved link does not belong to the current source/run "
                        "or points to missing current geometry."
                    ),
                    annotation_handles=[annotation],
                    geometry_handles=list(_geometry_key(link)),
                    repaired=effective_apply,
                    details={"reason": invalid_reason},
                )
            )
            archived = dict(link)
            archived.update(
                {
                    "revoked_at": timestamp,
                    "revoked_by": "FINAL_INTEGRITY",
                    "revocation_decision": invalid_reason,
                    "revocation_reason": invalid_reason,
                }
            )
            history.append(archived)
            continue
        decision = decisions.get(annotation, {})
        decision_code = str(decision.get("decision") or "")
        if decision_code in _REVOKING_DECISIONS:
            revoked += 1
            issues.append(
                IntegrityIssue(
                    code="STALE_APPROVAL_REVOKED",
                    severity="ERROR",
                    message="A later Review Center problem/exclusion overrode a stale approved link.",
                    annotation_handles=[annotation],
                    geometry_handles=list(_geometry_key(link)),
                    repaired=effective_apply,
                    details={"decision": decision_code, "reason": decision.get("reason")},
                )
            )
            archived = dict(link)
            archived.update(
                {
                    "revoked_at": timestamp,
                    "revoked_by": "FINAL_INTEGRITY",
                    "revocation_decision": decision_code,
                    "revocation_reason": decision.get("reason") or decision.get("reason_code"),
                }
            )
            history.append(archived)
            continue
        retained.append(link)
    links = retained

    duplicate_groups = _duplicate_groups(links)
    duplicates_found = len(duplicate_groups)
    duplicate_repaired = 0
    duplicate_removed = 0

    all_used_handles = {
        handle
        for link in links
        for handle in _geometry_key(link)
    }

    for geometry_key, group in duplicate_groups.items():
        if len(geometry_key) != 1 or any(int(link.get("segment_count") or 0) != 1 for link in group):
            issues.append(
                IntegrityIssue(
                    code="DUPLICATE_GEOMETRY_UNRESOLVED",
                    severity="ERROR",
                    message="The same multi-segment geometry is approved for multiple annotations.",
                    annotation_handles=[str(link.get("annotation_handle") or "") for link in group],
                    geometry_handles=list(geometry_key),
                    repaired=False,
                )
            )
            continue

        shared_handle = geometry_key[0]
        shared_entity = entity_by_handle.get(shared_handle)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for link in group:
            annotation = entity_by_handle.get(str(link.get("annotation_handle") or ""))
            distance = _distance_to_entity(annotation, shared_entity)
            ranked.append((distance if distance is not None else float("inf"), link))
        ranked.sort(key=lambda value: (value[0], str(value[1].get("annotation_handle") or "")))
        keeper = ranked[0][1]
        keeper_annotation = str(keeper.get("annotation_handle") or "")

        for _distance, duplicate_link in ranked[1:]:
            duplicate_annotation = str(duplicate_link.get("annotation_handle") or "")
            unavailable = set(all_used_handles)
            unavailable.discard(shared_handle)
            replacement = _find_safe_single_line_replacement(
                link=duplicate_link,
                entity_by_handle=entity_by_handle,
                unavailable_handles=unavailable,
                shared_handle=shared_handle,
            )
            if replacement is not None:
                new_handle, new_entity, new_length, new_distance = replacement
                old = dict(duplicate_link)
                old.update(
                    {
                        "replaced_at": timestamp,
                        "replaced_by": "FINAL_INTEGRITY",
                        "replacement_reason": "DUPLICATE_GEOMETRY_SAFE_ALTERNATIVE",
                    }
                )
                history.append(old)
                declared = _safe_float(duplicate_link.get("declared_length_cm")) or new_length
                duplicate_link["geometry_handles"] = [new_handle]
                duplicate_link["geometry_layer"] = str(new_entity.get("layer") or duplicate_link.get("geometry_layer") or "")
                duplicate_link["geometry_length_raw"] = new_length
                duplicate_link["effective_geometry_length_cm"] = new_length
                duplicate_link["numeric_difference"] = abs(new_length - declared)
                duplicate_link["raw_numeric_difference"] = abs(new_length - declared)
                duplicate_link["distance_raw"] = new_distance
                duplicate_link["integrity_repaired_at"] = timestamp
                duplicate_link["integrity_repair_code"] = "DUPLICATE_GEOMETRY_SAFE_ALTERNATIVE"
                duplicate_link["integrity_previous_geometry_handles"] = [shared_handle]
                all_used_handles.add(new_handle)
                duplicate_repaired += 1
                issues.append(
                    IntegrityIssue(
                        code="DUPLICATE_GEOMETRY_REASSIGNED",
                        severity="WARNING",
                        message="Duplicate approval was reassigned to a unique same-layer, same-length nearby line.",
                        annotation_handles=[duplicate_annotation, keeper_annotation],
                        geometry_handles=[shared_handle, new_handle],
                        repaired=effective_apply,
                        details={
                            "kept_owner": keeper_annotation,
                            "reassigned_annotation": duplicate_annotation,
                            "new_geometry_handle": new_handle,
                            "new_distance_raw": new_distance,
                        },
                    )
                )
            else:
                duplicate_removed += 1
                issues.append(
                    IntegrityIssue(
                        code="DUPLICATE_GEOMETRY_REMOVED",
                        severity="ERROR",
                        message="Duplicate approval had no uniquely safe alternative and was removed from the approved set.",
                        annotation_handles=[duplicate_annotation, keeper_annotation],
                        geometry_handles=[shared_handle],
                        repaired=effective_apply,
                    )
                )
                old = dict(duplicate_link)
                old.update(
                    {
                        "revoked_at": timestamp,
                        "revoked_by": "FINAL_INTEGRITY",
                        "revocation_reason": "DUPLICATE_GEOMETRY_NO_SAFE_ALTERNATIVE",
                    }
                )
                history.append(old)
                links = [value for value in links if value is not duplicate_link]

    duplicates_remaining = len(_duplicate_groups(links))
    links_by_annotation = {
        str(link.get("annotation_handle") or ""): link
        for link in links
        if str(link.get("annotation_handle") or "")
    }

    actionable_rows = [row for row in rows if str(row.get("annotation_class") or "") == ACTIONABLE_CLASS]
    actionable_handles = {str(row.get("annotation_handle") or "") for row in actionable_rows}
    explicit_excluded_handles = {
        handle
        for handle, decision in decisions.items()
        if str(decision.get("decision") or "") in _EXPLICIT_EXCLUSION_DECISIONS
    }
    requested_non_production_handles = {
        handle
        for handle, decision in decisions.items()
        if str(decision.get("decision") or "") in _TERMINAL_NON_PRODUCTION_DECISIONS
    }
    non_production_handles: set[str] = set()
    invalid_non_production_handles: set[str] = set()
    for handle in requested_non_production_handles & actionable_handles:
        decision = decisions.get(handle, {})
        valid = is_valid_non_production_decision(decision)
        if valid:
            non_production_handles.add(handle)
        else:
            invalid_non_production_handles.add(handle)
    if invalid_non_production_handles:
        issues.append(
            IntegrityIssue(
                code="NON_PRODUCTION_EVIDENCE_INVALID",
                severity="ERROR",
                message=(
                    "A non-production decision is missing EXCLUSION/reason/note/evidence "
                    "and cannot remove a row from the production specification."
                ),
                annotation_handles=sorted(invalid_non_production_handles),
            )
        )
    # A terminal exclusion must refer to a current actionable row; old decisions
    # for annotations absent from the current report are audit history only.
    explicit_excluded_handles &= actionable_handles
    approved_handles = set(links_by_annotation)
    # A row that originally lacked MASTER is no longer intrinsically missing
    # once an engineer has supplied a complete approved production link or has
    # evidenced that it is not a real production position.
    intrinsic_missing_handles = {
        str(row.get("annotation_handle") or "")
        for row in actionable_rows
        if is_missing_master_row(row)
        and str(row.get("annotation_handle") or "") not in approved_handles
        and str(row.get("annotation_handle") or "") not in non_production_handles
    }
    accounted_handles = (
        approved_handles | explicit_excluded_handles | non_production_handles
    )
    unresolved_handles = actionable_handles - accounted_handles if actionable_rows else set()

    safe_pending_handles: set[str] = set()
    engineering_pending_handles: set[str] = set()
    for handle in unresolved_handles:
        row = rows_by_handle.get(handle, {})
        decision_code = str((decisions.get(handle) or {}).get("decision") or "")
        if decision_code in _PROBLEM_DECISIONS or not final_confirmation_eligible(row):
            engineering_pending_handles.add(handle)
        else:
            safe_pending_handles.add(handle)

    if safe_pending_handles:
        issues.append(
            IntegrityIssue(
                code="SAFE_CONFIRMATION_PENDING",
                severity="WARNING",
                message=(
                    "CONTROL positions remain technically safe but require explicit final USER confirmation."
                ),
                annotation_handles=sorted(safe_pending_handles),
                repaired=False,
            )
        )
    if engineering_pending_handles:
        issues.append(
            IntegrityIssue(
                code="ENGINEERING_DECISIONS_PENDING",
                severity="ERROR",
                message=(
                    "Positions still require a complete engineer decision: form, count, diameter, length and segments."
                ),
                annotation_handles=sorted(engineering_pending_handles),
                repaired=False,
            )
        )

    engineer_resolved_without_geometry_count = sum(
        1
        for link in links
        if _geometry_provenance(link)[0] == 0
        and _geometry_provenance(link)[1]
        in {"NONE_ENGINEER_OVERRIDE", "NONE_ENGINEER_CORRECTION"}
    )
    if engineer_resolved_without_geometry_count:
        issues.append(
            IntegrityIssue(
                code="ENGINEER_RESOLVED_WITHOUT_GEOMETRY",
                severity="WARNING",
                message=(
                    "Production positions without DXF geometry are present and are "
                    "supported only by an explicit owner engineer decision."
                ),
                details={"count": engineer_resolved_without_geometry_count},
            )
        )

    beam_assignments: dict[str, dict[str, Any]] = {}
    beam_issues: list[IntegrityIssue] = []
    pre_beam_complete = (
        duplicates_remaining == 0
        and not unresolved_handles
        and identity_failures == 0
        and not explicit_excluded_handles
        and not intrinsic_missing_handles
    )
    if current_data_identity is not None and rows and pre_beam_complete:
        beam_assignments, beam_issues = _freeze_beam_assignments(
            rows=rows,
            links_by_annotation=links_by_annotation,
            extracted_payload=extracted_payload,
            project_root=root,
            identity=current_data_identity,
        )
        issues.extend(beam_issues)
    beam_failure_count = sum(issue.severity == "ERROR" for issue in beam_issues)
    vertical_assignments_frozen = sum(
        bool(frozen.get("vertical_assignment_frozen"))
        for frozen in beam_assignments.values()
    )
    vertical_sections_frozen = len(
        {
            (
                frozen.get("frame_index"),
                frozen.get("vertical_section_label_handle"),
                frozen.get("vertical_section_divider_handle"),
            )
            for frozen in beam_assignments.values()
            if frozen.get("vertical_assignment_frozen")
        }
    )

    identity_migrated_count = sum(
        str(link.get("identity_migrated_at") or "") == timestamp for link in links
    )
    changed = (
        revoked > 0
        or duplicate_repaired > 0
        or duplicate_removed > 0
        or identity_migrated_count > 0
    )
    backup_dir: Path | None = None
    if effective_apply:
        if changed or state_identity_migratable or not state_path.is_file():
            backup_dir = _backup_files(
                working_dir,
                [approved_path, state_path, problems_path, extracted_path, project_path],
            )
        if current_data_identity is not None:
            state_payload = dict(state) if isinstance(state, dict) else {}
            state_payload.setdefault("schema", "lion-rebar-review-center-state/v1")
            state_payload["decisions"] = decisions
            state_payload["updated_at"] = timestamp
            if state_identity_migratable:
                state_payload["identity_migrated_at"] = timestamp
                state_payload["identity_migration_source"] = "FINAL_CONTROL_CURRENT_REPORT_ROWS"
            stamp_payload(state_payload, current_data_identity)
            _write_json_atomic(state_path, state_payload)
        approved_payload["schema"] = "lion-rebar-approved-geometry-links/v1"
        approved_payload["project_name"] = project_name
        approved_payload["links"] = links
        approved_payload["history"] = history
        approved_payload["updated_at"] = timestamp
        approved_payload["last_integrity_reconciliation"] = {
            "at": timestamp,
            "revoked_stale_approvals": revoked,
            "duplicate_groups_found": duplicates_found,
            "duplicate_links_repaired": duplicate_repaired,
            "duplicate_links_removed": duplicate_removed,
            "identity_migrated_links": identity_migrated_count,
        }
        if current_data_identity is not None:
            stamp_payload(approved_payload, current_data_identity)
        _write_json_atomic(approved_path, approved_payload)

    ledger_path: Path | None = None
    if rows:
        ledger_path = working_dir / "final_integrity_ledger.csv"
        if effective_apply:
            _write_ledger(
                path=ledger_path,
                rows=rows,
                links_by_annotation=links_by_annotation,
                decisions=decisions,
                identity=current_data_identity,
                beam_assignments=beam_assignments,
            )

    integrity_passed = (
        duplicates_remaining == 0
        and not unresolved_handles
        and identity_failures == 0
        and beam_failure_count == 0
    )
    # Legacy EXCLUDED_BY_ENGINEER and intrinsic missing-MASTER still block a
    # complete production export.  NON_PRODUCTION_POSITION is different: it is
    # a terminal, evidenced statement that the row is not part of the design.
    blocking_excluded_handles = explicit_excluded_handles | intrinsic_missing_handles
    full_complete = integrity_passed and not blocking_excluded_handles
    freeze_manifest_path = working_dir / "final_control_freeze.json"
    freeze_required = bool(
        effective_apply
        and full_complete
        and current_data_identity is not None
        and ledger_path is not None
    )
    try:
        from lion_rebar.export.silent_wrong_metrics import read_silent_wrong_metrics

        silent_wrong_metrics = read_silent_wrong_metrics(root) or {}
    except Exception:  # noqa: BLE001 - diagnostic only, never blocks Final Control
        silent_wrong_metrics = {}
    result = IntegrityResult(
        project_name=project_name,
        generated_at=timestamp,
        applied=effective_apply,
        approved_before=approved_before,
        approved_after=len(links),
        stale_approvals_revoked=revoked,
        duplicate_groups_found=duplicates_found,
        duplicate_links_repaired=duplicate_repaired,
        duplicate_links_removed=duplicate_removed,
        duplicate_groups_remaining=duplicates_remaining,
        actionable_positions=len(actionable_rows) if actionable_rows else None,
        explicit_exclusions=len(explicit_excluded_handles),
        intrinsic_missing_master=len(intrinsic_missing_handles),
        accounted_positions=len(accounted_handles) if actionable_rows else None,
        unresolved_positions=len(unresolved_handles) if actionable_rows else None,
        safe_confirmation_pending=len(safe_pending_handles) if actionable_rows else None,
        engineering_decisions_pending=len(engineering_pending_handles) if actionable_rows else None,
        integrity_passed=integrity_passed,
        full_specification_complete=full_complete,
        report_path=resolved_report_path,
        backup_directory=str(backup_dir) if backup_dir else None,
        ledger_path=str(ledger_path) if ledger_path else None,
        non_production_positions=len(non_production_handles),
        engineer_resolved_without_geometry_count=engineer_resolved_without_geometry_count,
        identity_failures=identity_failures,
        beam_assignments_frozen=len(beam_assignments),
        vertical_assignments_frozen=vertical_assignments_frozen,
        vertical_sections_frozen=vertical_sections_frozen,
        freeze_required=freeze_required,
        freeze_manifest_path=(str(freeze_manifest_path) if freeze_required else None),
        data_identity=(
            current_data_identity.to_dict() if current_data_identity is not None else {}
        ),
        issues=issues,
        silent_wrong_metrics=silent_wrong_metrics,
    )
    if apply:
        integrity_report_path = working_dir / "final_integrity_report.json"
        if freeze_required and current_data_identity is not None and ledger_path is not None:
            source_relative = str(project_payload.get("source_dxf") or "")
            source_path = (
                root / source_relative
                if source_relative
                else root / "source" / "original.dxf"
            )
            try:
                if not resolved_report_path:
                    raise FileNotFoundError("Final Control has no selected batch report.")
                project_extensions = (
                    project_payload.get("extensions")
                    if isinstance(project_payload.get("extensions"), dict)
                    else {}
                )
                engine_relative = str(
                    project_extensions.get("engine_snapshot") or ""
                ).strip()
                if not engine_relative:
                    raise FileNotFoundError(
                        "Final Control has no pinned Engine snapshot."
                    )
                engine_snapshot_path = root / engine_relative
                staged_report_path = _stage_final_control_batch_report(
                    root=root,
                    working_dir=working_dir,
                    report_path=Path(resolved_report_path),
                )
                # The integrity report and freeze manifest refer only to the
                # self-contained copy inside the project.
                result.report_path = str(staged_report_path)
                _write_json_atomic(integrity_report_path, result.to_dict())
                # Рамките се откриват наново от вече проверените обекти, за
                # да се замрази съответствието рамка -> категория/лист.
                from lion_rebar.dxf.importer import DxfImporter
                from lion_rebar.export.frame_decisions import load_frame_decisions
                from lion_rebar.export.frame_detection import (
                    apply_operator_frame_decisions,
                    detect_drawing_frames,
                    unresolved_frames,
                )

                detected_frames = detect_drawing_frames(
                    DxfImporter().import_file(Path(source_path)).entities
                )
                frozen_frames = apply_operator_frame_decisions(
                    detected_frames,
                    load_frame_decisions(root, current_data_identity),
                )
                unresolved = unresolved_frames(frozen_frames)
                if unresolved:
                    titles = ", ".join(
                        frame.combined_title or f"рамка {frame.index}"
                        for frame in unresolved
                    )
                    raise ValueError(
                        "Final Control е блокиран от рамки с неопределена "
                        f"категория: {titles}. Реши ги и пусни контрола наново."
                    )
                _build_freeze_manifest(
                    root=root,
                    manifest_path=freeze_manifest_path,
                    identity=current_data_identity,
                    frames=frozen_frames,
                    files=[
                        ("SOURCE_DXF", source_path),
                        ("ENGINE_SNAPSHOT", engine_snapshot_path),
                        ("BATCH_REPORT", staged_report_path),
                        ("EXTRACTED_ENTITIES", extracted_path),
                        ("REVIEW_CENTER_STATE", state_path),
                        ("APPROVED_LINKS", approved_path),
                        ("FINAL_LEDGER", ledger_path),
                        ("FINAL_INTEGRITY_REPORT", integrity_report_path),
                    ],
                )
                _run_final_export_preflight(
                    root=root,
                    project_payload=project_payload,
                    expected_position_count=len(links),
                )
            except Exception as exc:  # fail closed; never export an unfrozen final state
                result.integrity_passed = False
                result.full_specification_complete = False
                result.freeze_required = False
                result.freeze_manifest_path = None
                preflight_failed = isinstance(exc, FinalExportPreflightError)
                if preflight_failed:
                    result.export_preflight_error = str(exc)
                result.issues.append(
                    IntegrityIssue(
                        code=(
                            "FINAL_EXPORT_PREFLIGHT_FAILED"
                            if preflight_failed
                            else "FINAL_CONTROL_FREEZE_FAILED"
                        ),
                        severity="ERROR",
                        message=(
                            f"Final Export preflight failed: {exc}"
                            if preflight_failed
                            else f"Final Control freeze manifest failed: {exc}"
                        ),
                    )
                )
                freeze_manifest_path.unlink(missing_ok=True)
                _write_json_atomic(integrity_report_path, result.to_dict())
        else:
            _write_json_atomic(integrity_report_path, result.to_dict())
    return result
