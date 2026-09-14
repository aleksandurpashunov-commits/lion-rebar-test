from __future__ import annotations

import csv
import json
import hashlib
import itertools
import math
import tempfile
import re
from collections import Counter, defaultdict
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, PatternFill, Side
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils.units import pixels_to_EMU

from lion_rebar.engine.bend_allowance import segments_match_total
from lion_rebar.config.constants import APP_VERSION
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_normalization import decode_dxf_text
from lion_rebar.dxf.text_parser import parse_rebar_text
from lion_rebar.engine.models import EnginePackage, ShapeDefinition
from lion_rebar.export.frame_detection import DrawingFrame, assign_point_to_frame, detect_drawing_frames, sanitize_sheet_name
from lion_rebar.export.shape_image import render_shape_png
from lion_rebar.export.errors import ExcelExportError, F006AsymmetricHookError
from lion_rebar.export.engine_shape_parameters import (
    EngineShapeParameterError,
    parameters_for_shape,
)
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    master_engine_preflight,
    parameters_from_segments,
)
from lion_rebar.manual_assignment import ManualGeometryError, summarize_selected_geometry
from lion_rebar.project.data_identity import IDENTITY_KEY, current_identity
from lion_rebar.project.models import ProjectModel


VISIBLE_COLUMNS = 11
DATA_START_ROW = 7
VID_COLUMN_WIDTH = 46.0
VID_ROW_HEIGHT_PT = 112.0
VID_IMAGE_WIDTH_PX = 310
VID_IMAGE_HEIGHT_PX = 124
VID_IMAGE_OFFSET_X_PX = 9
VID_IMAGE_OFFSET_Y_PX = 8
SUMMARY_ROW_HEIGHT_PT = 24.0
BEAM_HEADER_ROW_HEIGHT_PT = 24.0
VERTICAL_SECTION_X_MARGIN = 65.0
VERTICAL_SECTION_MIN_MARGIN = 1.0
EXPORT_TEMPLATE_VERSION = "1.0"
EXPORT_TEMPLATE_SHA256 = "ed7e4e0bd6a225a0fbbd6a7c7c906cfede40a206f5d98908b0d98109ccb93438"



class PngExcelImage(ExcelImage):
    """PNG image wrapper for openpyxl that does not require Pillow.

    openpyxl's default Image class imports Pillow even for PNG files. The
    managed LION REBAR runtime already has PySide6 for rendering, so we read
    the PNG header and bytes directly instead of introducing another compiled
    dependency into the shared runtime.
    """

    def __init__(self, img: str | Path):
        self.ref = str(img)
        data = Path(self.ref).read_bytes()
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ExcelExportError(f"Невалидно PNG изображение за Excel: {self.ref}")
        self.width = int.from_bytes(data[16:20], "big")
        self.height = int.from_bytes(data[20:24], "big")
        self.format = "png"

    def _data(self) -> bytes:
        return Path(self.ref).read_bytes()


@dataclass(frozen=True)
class ExportFrameConfig:
    frame_index: int
    category_code: str
    sheet_name: str
    title_text: str
    confirmed: bool = True


@dataclass
class ExportPosition:
    annotation_handle: str
    annotation_text: str
    count: int
    diameter_mm: float
    production_length_cm: float
    shape_code: str
    shape_name: str
    shape_revision: int
    geometry_handles: tuple[str, ...]
    frame_index: int
    segment_lengths_cm: tuple[float, ...] = ()
    turn_angles_deg: tuple[float, ...] = ()
    approval_status: str = "APPROVED"
    production_length_source: str = ""
    beam_name: str = ""
    beam_label_handle: str = ""
    beam_assignment_method: str = ""
    beam_assignment_score: float | None = None
    beam_assignment_margin: float | None = None
    vertical_section_title: str = ""
    vertical_section_label_handle: str = ""
    vertical_section_divider_handle: str = ""
    vertical_section_kind: str = ""
    vertical_section_order: int | None = None
    vertical_assignment_method: str = ""
    vertical_assignment_score: float | None = None
    vertical_assignment_margin: float | None = None
    master_topology_signature: str = ""
    #: Owner-requested 2026-09-12: the koloni group header this position was
    #: drawn under ("К23, К24 (35/35) - 2бр."). Purely a layout key for the
    #: working sheet -- deliberately NOT part of ``group_key``, so "Общо"
    #: keeps aggregating one row per production identity across all columns.
    column_group_label: str = ""

    @property
    def production_length_mm(self) -> int:
        return int(round(self.production_length_cm * 10.0))

    @property
    def segment_signature(self) -> tuple:
        if not self.segment_lengths_cm:
            return ()
        identity_segments = tuple(self.segment_lengths_cm)
        if str(self.shape_code or "").strip().upper() == "F002" and len(
            identity_segments
        ) == 3:
            outer_a, diagonal, outer_b = identity_segments
            identity_segments = (
                max(outer_a, outer_b),
                diagonal,
                min(outer_a, outer_b),
            )
            # Traversing the same reduction in reverse also reverses/sign-
            # flips its two DXF turns.  The locked MASTER uses only the
            # absolute reduction angle, so traversal signs are not part of
            # the production identity shown in ``Общо``.
            turn_magnitudes = tuple(
                abs(float(value)) for value in self.turn_angles_deg
            )
            identity_turns = (
                (sum(turn_magnitudes) / len(turn_magnitudes),)
                if turn_magnitudes
                else ()
            )
        elif str(self.shape_code or "").strip().upper() == "F005" and len(identity_segments) == 3:
            # F005 has two fixed right-angle turns in MASTER.  Old approved
            # links sometimes omit those fixed angles while keeping the same
            # three production arms; that metadata omission must not split
            # one global position into two rows in „Общо“.
            identity_turns = (
                tuple(abs(float(value)) for value in self.turn_angles_deg)
                if self.turn_angles_deg
                else (90.0, 90.0)
            )
        else:
            identity_turns = tuple(self.turn_angles_deg)
        return (
            tuple(int(round(value * 10.0)) for value in identity_segments),
            tuple(int(round(value * 10.0)) for value in identity_turns),
        )

    @property
    def group_key(self) -> tuple:
        return (
            str(self.shape_code or "").strip().upper(),
            round(float(self.diameter_mm), 4),
            self.production_length_mm,
            self.segment_signature,
            int(self.shape_revision),
            str(self.master_topology_signature or ""),
        )


@dataclass
class ExportGroup:
    key: tuple
    positions: list[ExportPosition] = field(default_factory=list)
    row_number: int | None = None
    position_number: int | None = None

    @property
    def representative(self) -> ExportPosition:
        return self.positions[0]

    @property
    def counts(self) -> list[int]:
        return [position.count for position in self.positions]




@dataclass(frozen=True)
class BeamLabel:
    frame_index: int
    handle: str
    title: str
    x: float
    y: float
    height: float = 0.0


@dataclass(frozen=True)
class BeamAssignment:
    label: BeamLabel
    score: float
    margin: float
    row_pitch: float
    method: str = "AUTO_BEAM_REGION_V1"


@dataclass
class BeamSection:
    title: str
    label_handle: str
    groups: list[ExportGroup] = field(default_factory=list)


@dataclass
class ColumnGroupSection:
    """One koloni column-group block on the working sheet: the designer's own
    header ("К23, К24 (35/35) - 2бр.") followed by the positions drawn under
    it. Mirrors ``BeamSection`` exactly, including its header row."""

    title: str
    groups: list[ExportGroup] = field(default_factory=list)


@dataclass(frozen=True)
class VerticalSectionLabel:
    frame_index: int
    title_handle: str
    divider_handle: str
    title: str
    kind: str
    order: int
    xmin: float
    xmax: float
    divider_y: float


@dataclass(frozen=True)
class VerticalAssignment:
    section: VerticalSectionLabel
    score: float
    margin: float
    method: str = "AUTO_VERTICAL_SECTION_V1"


@dataclass
class VerticalSection:
    title: str
    label_handle: str
    divider_handle: str
    kind: str
    order: int
    groups: list[ExportGroup] = field(default_factory=list)


@dataclass(frozen=True)
class _TemplateCellStyle:
    style: Any
    number_format: str
    alignment: Any
    fill: Any
    font: Any
    border: Any
    protection: Any


@dataclass(frozen=True)
class _TemplateRowStyle:
    cells: tuple[_TemplateCellStyle, ...]
    height: float | None


@dataclass(frozen=True)
class ExcelExportResult:
    workbook_path: Path
    traceability_path: Path
    frame_count: int
    safe_position_count: int
    grouped_row_count: int
    total_group_count: int
    position_numbers_by_annotation: dict[str, int]


def _canonical_master_number(value: float | None) -> str | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ExcelExportError("MASTER topology contains a non-finite number.")
    return format(number, ".12g")


def _master_topology_signature(shape: ShapeDefinition) -> str:
    """Return a stable digest of the manufacturing topology of one MASTER.

    Rendering, labels and explanatory metadata are intentionally excluded.
    Segment order, bend semantics, projections, radii and required parameter
    relations are included so that a changed topology cannot share an ``Общо``
    row merely because its visible code and dimensions happen to match.
    """

    segments = [
        {
            "index": int(segment.index),
            "length_code": str(segment.length_code or "").strip().upper(),
            "segment_type": str(segment.segment_type or "").strip().upper(),
            "fixed_length_cm": _canonical_master_number(segment.fixed_length_cm),
            "bend_after_deg": _canonical_master_number(segment.bend_after_deg),
            "bend_direction": str(segment.bend_direction or "").strip().upper(),
            "geometry_method": str(segment.geometry_method or "").strip().upper(),
            "projection_x_code": str(segment.projection_x_code or "").strip().upper(),
            "projection_y_code": str(segment.projection_y_code or "").strip().upper(),
            "radius_cm": _canonical_master_number(segment.radius_cm),
            "required": bool(segment.required),
            "drawing_direction": str(segment.drawing_direction or "").strip().upper(),
            "angle_mode": str(segment.angle_mode or "").strip().upper(),
            "angle_parameter_code": str(segment.angle_parameter_code or "").strip().upper(),
            "min_angle_deg": _canonical_master_number(segment.min_angle_deg),
            "max_angle_deg": _canonical_master_number(segment.max_angle_deg),
        }
        for segment in sorted(shape.segments, key=lambda item: int(item.index))
    ]
    relations = sorted(
        (
            str(relation.kind or "").strip().upper(),
            int(relation.left_segment),
            int(relation.right_segment),
            _canonical_master_number(relation.ratio),
            _canonical_master_number(relation.tolerance),
            bool(relation.required),
        )
        for relation in shape.parameter_relations
    )
    payload = {
        "initial_direction": str(shape.initial_direction or "").strip().upper(),
        "closed": bool(shape.closed),
        "symmetric": bool(shape.symmetric),
        "mirror_allowed": bool(shape.mirror_allowed),
        "segments": segments,
        "parameter_relations": relations,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _production_identity_token(key: tuple) -> str:
    """Stable hidden key used by scalable cross-sheet Excel formulas."""

    canonical = json.dumps(
        key,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "pid-sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_f002_turn_consistency(position: ExportPosition) -> None:
    """Reject incompatible F002 bends before traversal normalization.

    Reverse DXF traversal swaps/sign-flips the two reduction turns, therefore
    their magnitudes must describe the same physical angle.  A small 0.2°
    tolerance absorbs measurement rounding; larger disagreement is not one
    production identity and must never be silently collapsed.
    """

    if str(position.shape_code or "").strip().upper() != "F002":
        return
    turns = tuple(float(value) for value in position.turn_angles_deg)
    if any(not math.isfinite(value) for value in turns):
        raise ExcelExportError(
            f"F002 {position.annotation_handle} has a non-finite turn angle."
        )
    if len(turns) > 2:
        raise ExcelExportError(
            f"F002 {position.annotation_handle} has more than two turn angles."
        )
    if len(turns) == 2:
        if turns[0] * turns[1] >= 0:
            raise ExcelExportError(
                f"F002 {position.annotation_handle} reduction turns must have "
                f"opposite signs: {turns[0]:g}° / {turns[1]:g}°."
            )
        if abs(abs(turns[0]) - abs(turns[1])) > 0.2:
            raise ExcelExportError(
                f"F002 {position.annotation_handle} has inconsistent reduction "
                f"turns: {turns[0]:g}° / {turns[1]:g}°."
            )



def load_pinned_engine(project: ProjectModel, fallback: EnginePackage) -> EnginePackage:
    relative = str(project.extensions.get("engine_snapshot") or "").strip()
    if not relative:
        return fallback
    snapshot = Path(project.root) / relative
    if not snapshot.is_file():
        raise ExcelExportError(f"Липсва заключеният engine snapshot на проекта: {snapshot}")
    payload = _read_json(snapshot)
    engine = EnginePackage.from_dict(payload)
    if project.engine_id and engine.engine_id != project.engine_id:
        raise ExcelExportError("Engine snapshot-ът не съвпада с engine_id на проекта.")
    if project.engine_version and engine.version != project.engine_version:
        raise ExcelExportError("Engine snapshot-ът не съвпада с engine_version на проекта.")
    if project.engine_sha256 and engine.content_sha256 and engine.content_sha256 != project.engine_sha256:
        raise ExcelExportError("Engine snapshot checksum-ът не съвпада с проекта.")
    return engine



_FINAL_CONTROL_FREEZE_SCHEMA = "lion-rebar-final-control-freeze/v1"
_FINAL_CONTROL_FREEZE_ROLES = {
    "SOURCE_DXF",
    "ENGINE_SNAPSHOT",
    "BATCH_REPORT",
    "EXTRACTED_ENTITIES",
    "REVIEW_CENTER_STATE",
    "APPROVED_LINKS",
    "FINAL_LEDGER",
    "FINAL_INTEGRITY_REPORT",
    "EXPORT_CONFIG",
}


def _resolve_project_freeze_path(
    root: Path,
    raw: Any,
    *,
    label: str,
    allow_external: bool = False,
) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise ExcelExportError(f"Финалният контрол не съдържа път за {label}.")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ExcelExportError(
            f"Финалният контрол сочи {label} извън проекта: {resolved}"
        ) from exc
    return resolved


def _validate_final_control_freeze(
    *,
    project: ProjectModel,
    integrity_path: Path,
    integrity: dict[str, Any],
    ledger_path: Path,
    approved_path: Path,
) -> bool:
    """Validate the immutable Final Control snapshot before export.

    Export-ът използва само точните артефакти, приети от Final
    Control.  Any mutation after control -- including a stale batch report, a
    changed approval or a regenerated ledger -- blocks Excel/DXF generation.
    Legacy projects without ``freeze_required`` retain the historical path.
    """

    if not bool(integrity.get("freeze_required")):
        identity = current_identity(project)
        if identity.source_generation_id:
            raise ExcelExportError(
                "Този проект използва data identity, но няма заключен Final Ledger. "
                "Изпълни „Финален контрол“ отново."
            )
        return False

    root = Path(project.root).resolve()
    manifest_path = _resolve_project_freeze_path(
        root, integrity.get("freeze_manifest_path"), label="freeze manifest"
    )
    if not manifest_path.is_file():
        raise ExcelExportError(
            "Липсва final_control_freeze.json. Изпълни Финален контрол отново."
        )
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != _FINAL_CONTROL_FREEZE_SCHEMA:
        raise ExcelExportError("Freeze manifest-ът е с неподдържана схема.")
    # Стъпка 4 от Фаза 0 (независим одит на rc43): freeze-ът се проверяваше
    # тук САМО по schema + идентичност + SHA-256 на всеки artifact -- никога
    # по полето ``status``, което ``invalidate_final_control`` записва в
    # СЪЩИЯ файл след всяка production-impacting промяна (Apply to Group,
    # масово одобрение, инженерно решение). Ако промяната по-късно бъде
    # върната (undo) и файловете се озоват байт-точно в предишното си
    # състояние, SHA-256 проверките по-долу вече не хващат нищо -- само
    # ``status`` пази истината, че freeze-ът е бил обезсилен и изисква нов
    # Финален контрол. Проверката на UI ниво (``assert_export_allowed``) не
    # е достатъчна: тя пази бутона в main_window, но всеки друг caller на
    # ``load_export_context`` (CLI, скрипт, бъдещ автоматизиран export) я
    # заобикаляше напълно. Тук е библиотечният слой -- невъзможно е за
    # заобикаляне, без значение кой вика функцията.
    from lion_rebar.project.final_control_lifecycle import STATUS_VALID

    # Allowlist, не blocklist -- същият принцип като assert_export_allowed:
    # само точно ``VALID`` минава; липсващ, празен или друг статус блокира.
    manifest_status = str(manifest.get("status") or "").strip()
    if manifest_status != STATUS_VALID:
        raise ExcelExportError(
            "Финалният контрол е обезсилен след production промяна "
            f"(статус: {manifest_status or 'липсва'}). Изпълни Финален контрол "
            "наново преди export."
        )

    expected_identity = current_identity(project).to_dict()
    integrity_identity = integrity.get("data_identity")
    manifest_identity = manifest.get(IDENTITY_KEY)
    for label, candidate in (
        ("integrity report", integrity_identity),
        ("freeze manifest", manifest_identity),
    ):
        if not isinstance(candidate, dict):
            raise ExcelExportError(f"{label} е без data_identity.")
        for key in (
            "project_id",
            "source_sha256",
            "source_generation_id",
            "analysis_run_id",
        ):
            if str(candidate.get(key) or "") != str(expected_identity.get(key) or ""):
                raise ExcelExportError(
                    f"{label} не принадлежи на текущия проект/source/run ({key})."
                )

    source_path = _resolve_project_freeze_path(
        root, project.source_dxf or "source/original.dxf", label="source DXF"
    )
    batch_report_path = _resolve_project_freeze_path(
        root,
        integrity.get("report_path"),
        label="batch report",
    )
    expected_paths = {
        "SOURCE_DXF": source_path,
        "ENGINE_SNAPSHOT": _resolve_project_freeze_path(
            root,
            project.extensions.get("engine_snapshot"),
            label="Engine snapshot",
        ),
        "BATCH_REPORT": batch_report_path,
        "EXTRACTED_ENTITIES": (root / "working" / "extracted_entities.json").resolve(),
        "REVIEW_CENTER_STATE": (root / "working" / "review_center_state.json").resolve(),
        "APPROVED_LINKS": approved_path.resolve(),
        "FINAL_LEDGER": ledger_path.resolve(),
        "FINAL_INTEGRITY_REPORT": integrity_path.resolve(),
        "EXPORT_CONFIG": (
            root / "working" / "final_control_export_config.json"
        ).resolve(),
    }

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_FINAL_CONTROL_FREEZE_ROLES):
        raise ExcelExportError(
            "Freeze manifest-ът не съдържа точно деветте задължителни артефакта."
        )
    by_role: dict[str, dict[str, Any]] = {}
    for raw in artifacts:
        if not isinstance(raw, dict):
            raise ExcelExportError("Freeze manifest-ът съдържа невалиден artifact запис.")
        role = str(raw.get("role") or "").strip().upper()
        if role not in _FINAL_CONTROL_FREEZE_ROLES or role in by_role:
            raise ExcelExportError(f"Невалидна или повторена freeze роля: {role or '—'}.")
        by_role[role] = raw
    if set(by_role) != _FINAL_CONTROL_FREEZE_ROLES:
        raise ExcelExportError("Freeze manifest-ът е с непълен списък от роли.")

    sha_pattern = re.compile(r"^[0-9a-f]{64}$")
    for role, expected_path in expected_paths.items():
        artifact = by_role[role]
        if artifact.get("exists") is not True:
            raise ExcelExportError(f"Freeze artifact {role} не е доказан като съществуващ.")
        external = bool(artifact.get("external"))
        if external:
            raise ExcelExportError(
                f"Freeze artifact {role} е външен. Изпълни Финален контрол отново, "
                "за да се създаде самостоятелен project snapshot."
            )
        artifact_path = _resolve_project_freeze_path(
            root,
            artifact.get("path"),
            label=f"freeze artifact {role}",
        )
        if artifact_path != expected_path:
            raise ExcelExportError(
                f"Freeze artifact {role} сочи друг файл: {artifact_path}."
            )
        expected_sha = str(artifact.get("sha256") or "")
        if not sha_pattern.fullmatch(expected_sha):
            raise ExcelExportError(f"Freeze artifact {role} е с невалиден SHA-256.")
        if not artifact_path.is_file():
            raise ExcelExportError(f"Freeze artifact {role} липсва след Финален контрол.")
        actual_sha = _sha256_file(artifact_path)
        if actual_sha != expected_sha:
            raise ExcelExportError(
                f"Freeze artifact {role} е променен след Финален контрол. "
                "Изпълни контрола отново; export-ът е блокиран."
            )
    return True


def _ledger_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "да"}


def _ledger_int(value: Any, *, field: str, annotation_text: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ExcelExportError(
            f"Замразеният Ledger е с невалидно {field} за {annotation_text}."
        ) from exc
    if number < 0:
        raise ExcelExportError(
            f"Замразеният Ledger е с отрицателно {field} за {annotation_text}."
        )
    return number


def _ledger_optional_float(value: Any, *, field: str, annotation_text: str) -> float | None:
    if str(value or "").strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ExcelExportError(
            f"Замразеният Ledger е с невалидно {field} за {annotation_text}."
        ) from exc
    if not math.isfinite(number):
        raise ExcelExportError(
            f"Замразеният Ledger е с невалидно {field} за {annotation_text}."
        )
    return number


def _records_from_extracted_payload(payload: dict[str, Any]) -> list[DxfEntityRecord]:
    records: list[DxfEntityRecord] = []
    for raw in payload.get("entities", []):
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


def load_export_context(
    project: ProjectModel,
    entities: Sequence[DxfEntityRecord],
    engine: EnginePackage,
) -> tuple[list[DrawingFrame], list[ExportPosition]]:
    root = Path(project.root)
    working = root / "working"
    integrity_path = working / "final_integrity_report.json"
    ledger_path = working / "final_integrity_ledger.csv"
    approved_path = working / "approved_geometry_links.json"
    if not integrity_path.is_file() or not ledger_path.is_file() or not approved_path.is_file():
        raise ExcelExportError(
            "Липсва финален контрол. Изпълни „Финален контрол“ преди Excel export."
        )
    integrity = _read_json(integrity_path)
    if not bool(integrity.get("integrity_passed")):
        raise ExcelExportError("Финалният контрол не е преминал успешно.")
    if "full_specification_complete" in integrity and not bool(integrity.get("full_specification_complete")):
        raise ExcelExportError(
            "Пълната производствена спецификация не е завършена. "
            "Реши всички позиции с AUTO/USER инженерно решение; изключени и missing-MASTER позиции не се пропускат мълчаливо."
        )
    if int(integrity.get("duplicate_groups_remaining") or 0) != 0:
        raise ExcelExportError("Има оставащи двойни геометрии. Excel export е блокиран.")
    freeze_active = _validate_final_control_freeze(
        project=project,
        integrity_path=integrity_path,
        integrity=integrity,
        ledger_path=ledger_path,
        approved_path=approved_path,
    )
    if freeze_active:
        extracted_path = working / "extracted_entities.json"
        extracted_payload = _read_json(extracted_path)
        frozen_entities = _records_from_extracted_payload(extracted_payload)
        if not frozen_entities:
            raise ExcelExportError(
                "Заключеният extracted_entities.json не съдържа DXF обекти."
            )
        entities = frozen_entities

    # Един и същ resolved frame модел се използва от Final Control, Excel и
    # manifest-а. Записано операторско решение не бива да остава само като
    # provenance в manifest-а, докато Excel работи с суровото разпознаване.
    from lion_rebar.export.frame_decisions import load_frame_decisions
    from lion_rebar.export.frame_detection import apply_operator_frame_decisions

    frames = apply_operator_frame_decisions(
        detect_drawing_frames(entities),
        load_frame_decisions(root, current_identity(project)),
    )
    if not frames:
        raise ExcelExportError("Не са намерени чертожни рамки с поле „ЧЕРТЕЖ:“.")
    frame_map = {frame.index: frame for frame in frames}
    beam_labels = _detect_beam_labels(entities, frames)
    vertical_section_labels = _detect_vertical_section_labels(entities, frames)

    ledger_rows = list(csv.DictReader(ledger_path.open("r", encoding="utf-8-sig", newline="")))
    approved_payload = _read_json(approved_path)
    links = {
        str(link.get("annotation_handle") or ""): dict(link)
        for link in approved_payload.get("links", [])
        if isinstance(link, dict) and str(link.get("annotation_handle") or "")
    }
    entity_map = {record.handle: record for record in entities}
    all_shape_map = {shape.code.strip().upper(): shape for shape in engine.shapes}
    shape_map = {
        shape.code.strip().upper(): shape
        for shape in engine.shapes
        if shape.active
    }
    frozen_identity = current_identity(project).to_dict() if freeze_active else {}

    positions: list[ExportPosition] = []
    for row in ledger_rows:
        if str(row.get("final_status") or "").upper() != "APPROVED":
            continue
        annotation_handle = str(row.get("annotation_handle") or "")
        link = links.get(annotation_handle, {})
        annotation_text = str(
            link.get("resolved_annotation_text")
            or row.get("resolved_annotation_text")
            or row.get("annotation_text")
            or link.get("annotation_text")
            or row.get("original_annotation_text")
            or ""
        )
        parsed = parse_rebar_text(annotation_text)
        count = (
            _positive_int(link.get("production_count"))
            or _positive_int(link.get("annotation_count"))
            or parsed.count
        )
        diameter = _positive_float(link.get("production_diameter_mm"))
        if diameter is None:
            diameter = _positive_float(link.get("diameter_mm"))
        if diameter is None:
            diameter = parsed.diameter_mm
        if count is None or diameter is None:
            raise ExcelExportError(f"Одобрена позиция е без пълен брой/Ø: {annotation_text}")
        production_length = _positive_float(link.get("production_length_cm"))
        if production_length is None:
            production_length = _positive_float(row.get("production_length_cm"))
        if production_length is None:
            production_length = _positive_float(row.get("declared_length_cm"))
        if production_length is None:
            raise ExcelExportError(f"Одобрена позиция е без производствена дължина: {annotation_text}")
        shape_code = _resolve_active_shape_code(
            _resolve_shape_code(row, link),
            all_shape_map=all_shape_map,
        )
        if not shape_code or shape_code not in shape_map:
            raise ExcelExportError(f"Одобрена позиция е без активна MASTER форма: {annotation_text}")
        geometry_handles = tuple(
            str(value) for value in (link.get("geometry_handles") or []) if str(value)
        )
        annotation_entity = entity_map.get(annotation_handle)
        point = _entity_insert(annotation_entity)
        if point is None:
            raise ExcelExportError(f"Не е намерена позицията на надписа: {annotation_text}")
        current_frame_index = assign_point_to_frame(point[0], point[1], frames)
        if current_frame_index is None:
            raise ExcelExportError(f"Надписът не може да бъде свързан с чертежна рамка: {annotation_text}")
        if freeze_active:
            for key in (
                "project_id",
                "source_sha256",
                "source_generation_id",
                "analysis_run_id",
            ):
                if str(row.get(key) or "") != str(frozen_identity.get(key) or ""):
                    raise ExcelExportError(
                        f"Ledger редът за {annotation_text} не принадлежи на текущия "
                        f"project/source/run ({key})."
                    )
            if not _ledger_bool(row.get("beam_assignment_frozen")):
                raise ExcelExportError(
                    f"Ledger редът за {annotation_text} няма замразено frame/beam решение."
                )
            frame_index = _ledger_int(
                row.get("frame_index"),
                field="frame_index",
                annotation_text=annotation_text,
            )
            if frame_index != int(current_frame_index):
                raise ExcelExportError(
                    f"Рамката на {annotation_text} се различава от Финален контрол "
                    f"({frame_index}/{current_frame_index})."
                )
            frozen_frame = frame_map.get(frame_index)
            if frozen_frame is None:
                raise ExcelExportError(
                    f"Замразената рамка {frame_index} липсва за {annotation_text}."
                )
            if str(row.get("frame_category_code") or "") != str(
                frozen_frame.category_code or ""
            ):
                raise ExcelExportError(
                    f"Категорията на рамката е променена след Финален контрол: {annotation_text}."
                )
            if str(row.get("frame_title_text") or "").strip() != str(
                frozen_frame.combined_title or ""
            ).strip():
                raise ExcelExportError(
                    f"Заглавието на рамката е променено след Финален контрол: {annotation_text}."
                )
        else:
            frame_index = int(current_frame_index)

        segments, turns = _segments_for_link(
            link,
            shape=shape_map[shape_code],
            entity_map=entity_map,
            production_length_cm=production_length,
        )
        try:
            shape_parameters = parameters_for_shape(
                shape_map[shape_code],
                segments,
                production_length_cm=production_length,
                turn_angles_deg=turns,
            )
        except F006AsymmetricHookError:
            # This typed signal is intentionally preserved.  The batch/F006
            # gate uses it to keep form recognition STRONG while production
            # dimensions remain REVIEW.  If it reaches final export, the
            # position is still not production-ready and must remain blocked.
            raise
        except EngineShapeParameterError as exc:
            raise ExcelExportError(
                f"Позиция {annotation_text} ({annotation_handle}) не минава "
                f"проверката на заключения Engine: {exc}"
            ) from exc
        segments = tuple(shape_parameters.expanded_segments_cm)
        beam_name = ""
        beam_label_handle = ""
        beam_assignment_method = ""
        beam_assignment_score: float | None = None
        beam_assignment_margin: float | None = None
        vertical_section_title = ""
        vertical_section_label_handle = ""
        vertical_section_divider_handle = ""
        vertical_section_kind = ""
        vertical_section_order: int | None = None
        vertical_assignment_method = ""
        vertical_assignment_score: float | None = None
        vertical_assignment_margin: float | None = None
        frame = frame_map.get(int(frame_index))
        if frame is None:
            raise ExcelExportError(
                f"Замразената рамка {frame_index} за {annotation_text} липсва в текущия DXF."
            )
        if freeze_active:
            beam_name = str(row.get("beam_name") or "")
            beam_label_handle = str(row.get("beam_label_handle") or "")
            beam_assignment_method = str(row.get("beam_assignment_method") or "")
            beam_assignment_score = _ledger_optional_float(
                row.get("beam_assignment_score"),
                field="beam_assignment_score",
                annotation_text=annotation_text,
            )
            beam_assignment_margin = _ledger_optional_float(
                row.get("beam_assignment_margin"),
                field="beam_assignment_margin",
                annotation_text=annotation_text,
            )
            if frame.category_code == "BEAMS":
                if not beam_name or not beam_label_handle or not beam_assignment_method:
                    raise ExcelExportError(
                        f"Замразеният Ledger няма пълно beam решение за {annotation_text}."
                    )
                # Заглавието се сверява по handle. ``frame_index`` не се
                # изисква да съвпада, когато връзката е операторска: в ``s-9``
                # шест греди са начертани в рамка ДЕТАЙЛИ, а позициите им — в
                # рамка ГРЕДИ. За автоматичните връзки условието остава, там
                # ``_assign_beam_label`` и без това търси само в своята рамка.
                операторска = str(beam_assignment_method or "").startswith(
                    "OPERATOR_"
                )
                matching_label = next(
                    (
                        label
                        for label in beam_labels
                        if label.handle == beam_label_handle
                        and (
                            операторска
                            or label.frame_index == int(frame_index)
                        )
                    ),
                    None,
                )
                if matching_label is None or matching_label.title != beam_name:
                    raise ExcelExportError(
                        f"Замразената греда за {annotation_text} вече не съвпада с DXF. "
                        "Изпълни Финален контрол отново."
                    )
            elif beam_name or beam_label_handle:
                raise ExcelExportError(
                    f"Негредова рамка съдържа замразена греда за {annotation_text}."
                )
        elif frame.category_code == "BEAMS":
            assignment = _assign_beam_label(
                point[0],
                point[1],
                int(frame_index),
                beam_labels,
            )
            if assignment is None:
                raise ExcelExportError(
                    f"Позиция {annotation_text} ({annotation_handle}) не може да бъде свързана "
                    "еднозначно с конкретна греда. Export-ът е блокиран."
                )
            beam_name = assignment.label.title
            beam_label_handle = assignment.label.handle
            beam_assignment_method = assignment.method
            beam_assignment_score = assignment.score
            beam_assignment_margin = assignment.margin
        if freeze_active and frame.category_code == "VERTICALS":
            if not _ledger_bool(row.get("vertical_assignment_frozen")):
                raise ExcelExportError(
                    f"Frozen ledger has no vertical section ownership for "
                    f"{annotation_text}. Run Final Control again."
                )
            vertical_section_title = str(row.get("vertical_section_title") or "")
            vertical_section_label_handle = str(
                row.get("vertical_section_label_handle") or ""
            )
            vertical_section_divider_handle = str(
                row.get("vertical_section_divider_handle") or ""
            )
            vertical_section_kind = str(row.get("vertical_section_kind") or "")
            vertical_section_order = _ledger_int(
                row.get("vertical_section_order"),
                field="vertical_section_order",
                annotation_text=annotation_text,
            )
            vertical_assignment_method = str(
                row.get("vertical_assignment_method") or ""
            )
            vertical_assignment_score = _ledger_optional_float(
                row.get("vertical_assignment_score"),
                field="vertical_assignment_score",
                annotation_text=annotation_text,
            )
            vertical_assignment_margin = _ledger_optional_float(
                row.get("vertical_assignment_margin"),
                field="vertical_assignment_margin",
                annotation_text=annotation_text,
            )
            matching_section = next(
                (
                    section
                    for section in vertical_section_labels
                    if section.frame_index == int(frame_index)
                    and section.title_handle == vertical_section_label_handle
                    and section.divider_handle == vertical_section_divider_handle
                ),
                None,
            )
            current_assignment = _assign_vertical_section(
                point[0],
                point[1],
                int(frame_index),
                vertical_section_labels,
            )
            if (
                matching_section is None
                or current_assignment is None
                or current_assignment.section.title_handle
                != vertical_section_label_handle
                or current_assignment.section.divider_handle
                != vertical_section_divider_handle
                or matching_section.title != vertical_section_title
                or matching_section.kind != vertical_section_kind
                or matching_section.order != vertical_section_order
                or not vertical_assignment_method
            ):
                raise ExcelExportError(
                    f"Frozen vertical section for {annotation_text} no longer "
                    "matches the DXF. Run Final Control again."
                )
        elif not freeze_active and frame.category_code == "VERTICALS":
            vertical_assignment = _assign_vertical_section(
                point[0],
                point[1],
                int(frame_index),
                vertical_section_labels,
            )
            if vertical_assignment is None:
                raise ExcelExportError(
                    f"Vertical position {annotation_text} ({annotation_handle}) "
                    "cannot be assigned uniquely to a title/divider section."
                )
            section = vertical_assignment.section
            vertical_section_title = section.title
            vertical_section_label_handle = section.title_handle
            vertical_section_divider_handle = section.divider_handle
            vertical_section_kind = section.kind
            vertical_section_order = section.order
            vertical_assignment_method = vertical_assignment.method
            vertical_assignment_score = vertical_assignment.score
            vertical_assignment_margin = vertical_assignment.margin
        elif freeze_active and any(
            str(row.get(field) or "")
            for field in (
                "vertical_section_title",
                "vertical_section_label_handle",
                "vertical_section_divider_handle",
            )
        ):
            raise ExcelExportError(
                f"Non-vertical frame contains frozen vertical ownership for "
                f"{annotation_text}."
            )
        positions.append(
            ExportPosition(
                annotation_handle=annotation_handle,
                annotation_text=annotation_text,
                count=int(count),
                diameter_mm=float(diameter),
                production_length_cm=float(production_length),
                shape_code=shape_code,
                shape_name=str(link.get("master_shape_name") or row.get("shape_name") or shape_map[shape_code].name),
                shape_revision=int(shape_map[shape_code].revision),
                geometry_handles=geometry_handles,
                frame_index=int(frame_index),
                segment_lengths_cm=segments,
                turn_angles_deg=turns,
                approval_status=str(link.get("approval_status") or "APPROVED"),
                production_length_source=str(row.get("production_length_source") or link.get("production_length_source") or ""),
                beam_name=beam_name,
                beam_label_handle=beam_label_handle,
                beam_assignment_method=beam_assignment_method,
                beam_assignment_score=beam_assignment_score,
                beam_assignment_margin=beam_assignment_margin,
                vertical_section_title=vertical_section_title,
                vertical_section_label_handle=vertical_section_label_handle,
                vertical_section_divider_handle=vertical_section_divider_handle,
                vertical_section_kind=vertical_section_kind,
                vertical_section_order=vertical_section_order,
                vertical_assignment_method=vertical_assignment_method,
                vertical_assignment_score=vertical_assignment_score,
                vertical_assignment_margin=vertical_assignment_margin,
                column_group_label=str(row.get("column_group_label") or ""),
            )
        )
    _validate_beam_assignments(frames, beam_labels, positions)
    _validate_vertical_assignments(frames, vertical_section_labels, positions)
    return frames, positions


def generate_excel_export(
    project: ProjectModel,
    engine: EnginePackage,
    frames: Sequence[DrawingFrame],
    positions: Sequence[ExportPosition],
    frame_configs: Sequence[ExportFrameConfig],
    output_path: Path,
    *,
    require_frozen_config: bool = False,
) -> ExcelExportResult:
    # Конфигурацията трябва да съвпада със замразената при Финалния
    # контрол. Разминаване значи, че някой е сменил категория или лист
    # СЛЕД замразяването -- Excel-ът би излязъл по новото, а манифестът
    # би твърдял VERIFIED срещу стария freeze.
    try:
        from lion_rebar.export.frozen_export_config import (
            ExportConfigError,
            assert_config_matches_freeze,
        )

        config_path = (
            Path(project.root) / "working" / "final_control_export_config.json"
        )
        if require_frozen_config or config_path.is_file():
            assert_config_matches_freeze(Path(project.root), frame_configs, frames)
    except ExportConfigError as exc:
        raise ExcelExportError(str(exc)) from exc

    try:
        master_preflight = master_engine_preflight()
    except Exception as exc:
        raise ExcelExportError(f"MASTER preflight е неуспешен: {exc}") from exc
    active_shapes = {
        str(shape.code or "").strip().upper(): shape
        for shape in engine.shapes
        if bool(shape.active) and str(shape.state or "").strip().upper() != "RETIRED"
    }
    for position in positions:
        shape = active_shapes.get(str(position.shape_code or "").strip().upper())
        if shape is None:
            raise ExcelExportError(
                f"Позиция {position.annotation_text} ({position.annotation_handle}) "
                f"използва липсваща или неактивна Engine форма {position.shape_code}."
            )
        position.master_topology_signature = _master_topology_signature(shape)
        _validate_f002_turn_consistency(position)
        try:
            parameters_for_shape(
                shape,
                position.segment_lengths_cm or (position.production_length_cm,),
                production_length_cm=position.production_length_cm,
                turn_angles_deg=position.turn_angles_deg,
            )
        except (EngineShapeParameterError, F006AsymmetricHookError) as exc:
            raise ExcelExportError(
                f"Позиция {position.annotation_text} ({position.annotation_handle}) "
                f"е блокирана от MASTER/Engine: {exc}"
            ) from exc
    config_map = {config.frame_index: config for config in frame_configs}
    if {frame.index for frame in frames} != set(config_map):
        raise ExcelExportError("Всяка чертожна рамка трябва да има потвърдена категория и лист.")
    if any(not config.confirmed for config in frame_configs):
        raise ExcelExportError("Има непотвърдени категории на чертежи.")

    template_path = Path(__file__).resolve().parent.parent / "resources" / "export_template_v1.xlsx"
    if not template_path.is_file():
        raise ExcelExportError(f"Липсва заключеният Excel template: {template_path}")
    if _sha256_file(template_path) != EXPORT_TEMPLATE_SHA256:
        raise ExcelExportError("Excel template v1.0 е променен или повреден. Export-ът е блокиран.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = load_workbook(template_path)
    base_sheet = workbook[workbook.sheetnames[0]]

    ordered_frames = sorted(
        frames,
        key=lambda frame: (
            _category_order(config_map[frame.index].category_code),
            frame.index,
        ),
    )
    sheet_names = _unique_sheet_names(
        [config_map[frame.index].sheet_name for frame in ordered_frames] + ["Общо"]
    )
    sheet_by_frame: dict[int, Any] = {}
    base_sheet.title = sheet_names[0]
    sheet_by_frame[ordered_frames[0].index] = base_sheet
    for frame, sheet_name in zip(ordered_frames[1:], sheet_names[1:-1]):
        new_sheet = workbook.copy_worksheet(base_sheet)
        new_sheet.title = sheet_name
        sheet_by_frame[frame.index] = new_sheet
    total_sheet = workbook.copy_worksheet(base_sheet)
    total_sheet.title = sheet_names[-1]

    shape_map = active_shapes
    positions_by_frame: dict[int, list[ExportPosition]] = defaultdict(list)
    for position in positions:
        positions_by_frame[position.frame_index].append(position)

    # Build every sheet before assigning numbers. A position number belongs to
    # the production identity, not to a particular sheet. Repeated identities
    # therefore keep exactly the same number in lower/top/beams and in "Общо".
    frame_layouts: dict[
        int,
        list[ExportGroup] | list[BeamSection] | list[VerticalSection],
    ] = {}
    section_layout_frames: set[int] = set()
    global_numbers: dict[tuple, int] = {}
    next_position_number = 1
    for frame in ordered_frames:
        config = config_map[frame.index]
        frame_positions = positions_by_frame.get(frame.index, [])
        if config.category_code == "BEAMS":
            sections = _beam_sections(frame_positions)
            frame_layouts[frame.index] = sections
            section_layout_frames.add(frame.index)
            groups_in_order = [group for section in sections for group in section.groups]
        elif config.category_code == "VERTICALS" and any(
            position.vertical_section_label_handle for position in frame_positions
        ):
            vertical_sections = _vertical_sections(frame_positions)
            frame_layouts[frame.index] = vertical_sections
            section_layout_frames.add(frame.index)
            groups_in_order = [
                group
                for section in vertical_sections
                for group in section.groups
            ]
        elif any(position.column_group_label for position in frame_positions):
            # Owner-requested 2026-09-12: koloni sheets are read column group
            # by column group, exactly as the drawing titles them.
            column_sections = _column_group_sections(frame_positions)
            frame_layouts[frame.index] = column_sections
            section_layout_frames.add(frame.index)
            groups_in_order = [
                group for section in column_sections for group in section.groups
            ]
        else:
            groups = _group_positions(frame_positions)
            frame_layouts[frame.index] = groups
            groups_in_order = groups
        for group in groups_in_order:
            if group.key not in global_numbers:
                global_numbers[group.key] = next_position_number
                next_position_number += 1
            group.position_number = global_numbers[group.key]
        if frame.index in section_layout_frames:
            for section in frame_layouts[frame.index]:  # type: ignore[union-attr]
                section.groups.sort(key=lambda group: (group.position_number or 10**9, _group_sort_key(group)))
        else:
            frame_layouts[frame.index] = sorted(
                frame_layouts[frame.index],  # type: ignore[arg-type]
                key=lambda group: (group.position_number or 10**9, _group_sort_key(group)),
            )

    trace_rows: list[dict[str, Any]] = []
    source_location_by_annotation: dict[str, dict[str, Any]] = {}
    total_sources: dict[tuple, list[tuple[str, int, ExportGroup]]] = defaultdict(list)
    grouped_rows = 0
    with tempfile.TemporaryDirectory(prefix="lion_rebar_export_") as temp_dir:
        temp = Path(temp_dir)
        for frame in ordered_frames:
            sheet = sheet_by_frame[frame.index]
            config = config_map[frame.index]
            is_beam_sheet = config.category_code == "BEAMS"
            is_vertical_section_sheet = (
                config.category_code == "VERTICALS"
                and frame.index in section_layout_frames
            )
            is_column_group_sheet = frame.index in section_layout_frames and all(
                isinstance(item, ColumnGroupSection)
                for item in frame_layouts[frame.index]
            )
            row_specs: list[tuple[str, Any]] = []
            if is_column_group_sheet:
                sections = frame_layouts[frame.index]
                for section in sections:  # type: ignore[union-attr]
                    if section.title:
                        row_specs.append(("COLUMN_GROUP", section))
                    row_specs.extend(("GROUP", group) for group in section.groups)
            elif is_beam_sheet:
                sections = frame_layouts[frame.index]
                for section in sections:  # type: ignore[union-attr]
                    row_specs.append(("BEAM", section))
                    row_specs.extend(("GROUP", group) for group in section.groups)
            elif is_vertical_section_sheet:
                sections = frame_layouts[frame.index]
                for section in sections:  # type: ignore[union-attr]
                    row_specs.append(("VERTICAL_SECTION", section))
                    row_specs.extend(("GROUP", group) for group in section.groups)
            else:
                row_specs.extend(("GROUP", group) for group in frame_layouts[frame.index])  # type: ignore[union-attr]

            # One summary row is always written at the bottom of a source sheet.
            _prepare_sheet(sheet, len(row_specs) + 1)
            current_row = DATA_START_ROW
            data_rows: list[int] = []
            for kind, item in row_specs:
                if kind in {"BEAM", "VERTICAL_SECTION", "COLUMN_GROUP"}:
                    _write_beam_header(sheet, current_row, item.title)
                    if kind == "COLUMN_GROUP":
                        trace_rows.append(
                            {
                                "sheet": sheet.title,
                                "row": current_row,
                                "kind": "COLUMN_GROUP",
                                "frame_index": frame.index,
                                "category_code": config.category_code,
                                "column_group_label": item.title,
                            }
                        )
                    if kind == "VERTICAL_SECTION":
                        trace_rows.append(
                            {
                                "sheet": sheet.title,
                                "row": current_row,
                                "kind": "VERTICAL_SECTION",
                                "frame_index": frame.index,
                                "category_code": config.category_code,
                                "vertical_section_title": item.title,
                                "vertical_section_label_handle": item.label_handle,
                                "vertical_section_divider_handle": item.divider_handle,
                                "vertical_section_kind": item.kind,
                                "vertical_section_order": item.order,
                            }
                        )
                else:
                    group: ExportGroup = item
                    _validate_source_group(group)
                    _apply_data_row_style(sheet, current_row)
                    group.row_number = current_row
                    source_title = config.title_text
                    if group.representative.beam_name:
                        source_title = f"{source_title} | {group.representative.beam_name}"
                    elif group.representative.vertical_section_title:
                        source_title = (
                            f"{source_title} | "
                            f"{group.representative.vertical_section_title}"
                        )
                    _write_group_row(
                        sheet,
                        current_row,
                        group,
                        shape=shape_map[
                            str(group.representative.shape_code or "").strip().upper()
                        ],
                        image_dir=temp,
                        engine=engine,
                        source_title=source_title,
                    )
                    total_sources[group.key].append((sheet.title, current_row, group))
                    trace_rows.append(_trace_record(sheet.title, current_row, group, frame, config, engine))
                    source_group_id = _source_group_id(sheet.title, current_row)
                    source_cell = _sheet_ref(sheet.title, f"B{current_row}")
                    for position in group.positions:
                        annotation_handle = str(position.annotation_handle)
                        if annotation_handle in source_location_by_annotation:
                            raise ExcelExportError(
                                "Source Excel mapping contains annotation "
                                f"{annotation_handle} more than once."
                            )
                        source_location_by_annotation[annotation_handle] = {
                            "source_sheet": sheet.title,
                            "source_row": current_row,
                            "source_cell": source_cell,
                            "source_group_id": source_group_id,
                            "source_group_annotation_count": len(group.positions),
                        }
                    data_rows.append(current_row)
                    grouped_rows += 1
                current_row += 1
            summary_row = current_row
            _write_sheet_total(sheet, summary_row, data_rows, include_tonnes=False)
            _finalize_sheet(sheet, summary_row)

        _validate_source_coverage(
            positions,
            source_location_by_annotation,
            total_sources,
            grouped_rows,
        )

        total_groups: list[ExportGroup] = []
        for key, sources in total_sources.items():
            positions_for_total = [position for _sheet, _row, group in sources for position in group.positions]
            total_group = ExportGroup(
                key=key,
                positions=positions_for_total,
                position_number=global_numbers[key],
            )
            total_groups.append(total_group)
        total_groups.sort(key=lambda group: (group.position_number or 10**9, _group_sort_key(group)))
        global_aggregation_audit = _validate_global_aggregation(
            positions,
            total_sources,
            total_groups,
            global_numbers,
        )
        global_aggregation_audit["sheet"] = total_sheet.title

        # Total sheet has one kg row and one tonnes row.
        _prepare_sheet(total_sheet, len(total_groups) + 2)
        total_data_rows: list[int] = []
        for offset, group in enumerate(total_groups):
            row = DATA_START_ROW + offset
            _apply_data_row_style(total_sheet, row)
            group.row_number = row
            source_refs = total_sources[group.key]
            _write_total_group_row(
                total_sheet,
                row,
                group,
                source_refs=source_refs,
                shape=shape_map[
                    str(group.representative.shape_code or "").strip().upper()
                ],
                image_dir=temp,
                engine=engine,
            )
            total_data_rows.append(row)
            trace_rows.append(
                {
                    "sheet": total_sheet.title,
                    "row": row,
                    "kind": "TOTAL",
                    "position_number": group.position_number,
                    "shape_code": group.representative.shape_code,
                    "shape_revision": group.representative.shape_revision,
                    "master_topology_signature": (
                        group.representative.master_topology_signature
                    ),
                    "diameter_mm": group.representative.diameter_mm,
                    "production_length_cm": group.representative.production_length_cm,
                    "segment_lengths_cm": list(
                        group.representative.segment_lengths_cm
                    ),
                    "turn_angles_deg": list(group.representative.turn_angles_deg),
                    "source_cells": [f"'{name}'!B{source_row}" for name, source_row, _group in source_refs],
                    "source_group_ids": [
                        _source_group_id(name, source_row)
                        for name, source_row, _group in source_refs
                    ],
                    "source_group_count": len(source_refs),
                    "source_sheets": sorted(
                        {name for name, _source_row, _group in source_refs}
                    ),
                    "source_frame_indexes": sorted(
                        {int(position.frame_index) for position in group.positions}
                    ),
                    "row_granularity": "GLOBAL_PRODUCTION_IDENTITY",
                    "production_identity_key": group.key,
                    "annotation_count": len(group.positions),
                    "count_formula_terms": group.counts,
                    "count_total": sum(group.counts),
                    "annotation_handles": [
                        position.annotation_handle for position in group.positions
                    ],
                    "geometry_handles": [
                        handle
                        for position in group.positions
                        for handle in position.geometry_handles
                    ],
                }
            )
        total_kg_row = DATA_START_ROW + len(total_groups)
        _write_sheet_total(total_sheet, total_kg_row, total_data_rows, include_tonnes=True)
        _finalize_sheet(total_sheet, total_kg_row + 1)
        formula_audit = _validate_written_global_formulas(
            workbook,
            total_sheet,
            total_sources,
            total_groups,
        )
        global_aggregation_audit.update(formula_audit)
        workbook.active = 0
        workbook.save(output_path)

    position_numbers_by_annotation = {
        position.annotation_handle: int(global_numbers[position.group_key])
        for position in positions
    }
    vertical_source_rows = [
        row
        for row in trace_rows
        if row.get("kind") == "DRAWING"
        and row.get("vertical_section_label_handle")
    ]
    vertical_approved_annotation_count = sum(
        int(row.get("source_annotation_count") or 0)
        for row in vertical_source_rows
    )
    trace_path = output_path.with_name(output_path.stem + "_TRACEABILITY.json")
    trace_payload = {
        "schema": "lion-rebar-excel-traceability/v3",
        "project": project.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "engine_id": engine.engine_id,
        "engine_version": engine.version,
        "engine_sha256": engine.content_sha256,
        "master_renderer_file": master_preflight.filename,
        "master_renderer_version": master_preflight.version,
        "master_renderer_sha256": master_preflight.sha256,
        "master_renderer_tests": f"{master_preflight.tests_passed}/{master_preflight.test_count}",
        "template": "export_template_v1.xlsx",
        "template_version": EXPORT_TEMPLATE_VERSION,
        "template_sha256": EXPORT_TEMPLATE_SHA256,
        "safe_positions": len(positions),
        "global_position_count": len(total_groups),
        "global_aggregation": global_aggregation_audit,
        "annotation_position_map": [
            {
                "annotation_handle": position.annotation_handle,
                "annotation_text": position.annotation_text,
                "frame_index": int(position.frame_index),
                "position_number": position_numbers_by_annotation[position.annotation_handle],
                "production_identity_key": position.group_key,
                "master_topology_signature": position.master_topology_signature,
                "beam_name": position.beam_name,
                "beam_label_handle": position.beam_label_handle,
                "beam_assignment_method": position.beam_assignment_method,
                "beam_assignment_score": position.beam_assignment_score,
                "beam_assignment_margin": position.beam_assignment_margin,
                "vertical_section_title": position.vertical_section_title,
                "vertical_section_label_handle": position.vertical_section_label_handle,
                "vertical_section_divider_handle": position.vertical_section_divider_handle,
                "vertical_section_kind": position.vertical_section_kind,
                "vertical_section_order": position.vertical_section_order,
                "vertical_assignment_method": position.vertical_assignment_method,
                "vertical_assignment_score": position.vertical_assignment_score,
                "vertical_assignment_margin": position.vertical_assignment_margin,
                **source_location_by_annotation[position.annotation_handle],
            }
            for position in sorted(
                positions,
                key=lambda item: (
                    position_numbers_by_annotation[item.annotation_handle],
                    item.frame_index,
                    item.annotation_handle,
                ),
            )
        ],
        "beam_export": {
            "assignment_method": "AUTO_BEAM_REGION_V1",
            "beam_count": len({
                position.beam_label_handle
                for position in positions
                if position.beam_label_handle
            }),
            "beam_position_rows": sum(1 for position in positions if position.beam_label_handle),
            "one_row_per_annotation": True,
            "global_aggregation_sheet": "Общо",
        },
        "vertical_export": {
            "assignment_method": "AUTO_VERTICAL_SECTION_V1",
            "section_count": len({
                (
                    position.frame_index,
                    position.vertical_section_label_handle,
                    position.vertical_section_divider_handle,
                )
                for position in positions
                if position.vertical_section_label_handle
            }),
            "approved_annotation_count": vertical_approved_annotation_count,
            "source_group_row_count": len(vertical_source_rows),
            "collapsed_annotation_count": (
                vertical_approved_annotation_count - len(vertical_source_rows)
            ),
            "source_row_granularity": (
                "PRODUCTION_IDENTITY_WITHIN_FROZEN_VERTICAL_SECTION"
            ),
            "source_group_key_fields": [
                "frame_index",
                "vertical_section_label_handle",
                "vertical_section_divider_handle",
                "shape_code",
                "diameter_mm",
                "production_length_mm",
                "segment_signature",
                "shape_revision",
                "master_topology_signature",
            ],
            "cross_section_merge_on_source": False,
            "global_group_key_fields": [
                "shape_code",
                "diameter_mm",
                "production_length_mm",
                "segment_signature",
                "shape_revision",
                "master_topology_signature",
            ],
            # Compatibility fields: rows now mean section-source groups, not
            # individual annotations.  v3 consumers should use the explicit
            # fields above.
            "vertical_position_rows": len(vertical_source_rows),
            "one_row_per_annotation": False,
            "aggregation_sheet": total_sheet.title,
            "global_aggregation_sheet": total_sheet.title,
        },
        "frames": [
            {
                "frame_index": frame.index,
                "title": frame.combined_title,
                "category_code": config_map[frame.index].category_code,
                "sheet_name": sheet_by_frame[frame.index].title,
            }
            for frame in ordered_frames
        ],
        "rows": trace_rows,
    }
    trace_path.write_text(json.dumps(trace_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ExcelExportResult(
        workbook_path=output_path,
        traceability_path=trace_path,
        frame_count=len(frames),
        safe_position_count=len(positions),
        grouped_row_count=grouped_rows,
        total_group_count=len(total_groups),
        position_numbers_by_annotation=position_numbers_by_annotation,
    )


def _prepare_sheet(sheet, output_row_count: int) -> None:
    output_row_count = max(int(output_row_count), 1)
    template = _capture_template_row(sheet, DATA_START_ROW)
    if output_row_count > 1:
        sheet.insert_rows(DATA_START_ROW, amount=output_row_count - 1)
    for row in range(DATA_START_ROW, DATA_START_ROW + output_row_count):
        _restore_template_row(sheet, row, template)
        for column in range(1, 21):
            cell = sheet.cell(row, column)
            cell.value = None
            cell.comment = None
    sheet.column_dimensions["G"].width = VID_COLUMN_WIDTH
    for column in "LMNOPQRST":
        sheet.column_dimensions[column].hidden = True
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = None


def _capture_template_row(sheet, row: int) -> _TemplateRowStyle:
    cells: list[_TemplateCellStyle] = []
    for column in range(1, 21):
        cell = sheet.cell(row, column)
        cells.append(
            _TemplateCellStyle(
                style=copy(cell._style),
                number_format=str(cell.number_format or "General"),
                alignment=copy(cell.alignment),
                fill=copy(cell.fill),
                font=copy(cell.font),
                border=copy(cell.border),
                protection=copy(cell.protection),
            )
        )
    return _TemplateRowStyle(tuple(cells), sheet.row_dimensions[row].height)


def _restore_template_row(sheet, row: int, template: _TemplateRowStyle) -> None:
    for column, source in enumerate(template.cells, start=1):
        target = sheet.cell(row, column)
        target._style = copy(source.style)
        target.number_format = source.number_format
        target.alignment = copy(source.alignment)
        target.fill = copy(source.fill)
        target.font = copy(source.font)
        target.border = copy(source.border)
        target.protection = copy(source.protection)
    sheet.row_dimensions[row].height = template.height


def _apply_data_row_style(sheet, row: int) -> None:
    sheet.row_dimensions[row].height = VID_ROW_HEIGHT_PT
    # The VID image is an overlay. Apply the A:K grid explicitly so the
    # borders around column G remain a continuous table even when a PNG is
    # anchored inside the cell.
    thin = Side(style="thin", color="000000")
    medium = Side(style="medium", color="000000")
    for column in range(1, 12):
        sheet.cell(row, column).border = Border(
            left=medium if column == 1 else thin,
            right=medium if column == 11 else thin,
            top=thin,
            bottom=thin,
        )


def _write_beam_header(sheet, row: int, title: str) -> None:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=11)
    cell = sheet.cell(row, 1)
    cell.value = str(title or "НЕРАЗПОЗНАТА ГРЕДА").strip()
    header_font = copy(cell.font)
    header_font.bold = True
    header_font.color = "FFFFFF"
    header_font.sz = 11
    cell.font = header_font
    cell.fill = PatternFill("solid", fgColor="1F4E78")
    cell.alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[row].height = BEAM_HEADER_ROW_HEIGHT_PT


def _write_group_row(sheet, row: int, group: ExportGroup, *, shape: ShapeDefinition, image_dir: Path, engine: EnginePackage, source_title: str) -> None:
    representative = group.representative
    sheet.cell(row, 1).value = int(group.position_number or 0)
    sheet.cell(row, 2).value = "=" + "+".join(str(value) for value in group.counts)
    sheet.cell(row, 3).value = "N"
    sheet.cell(row, 4).value = _display_number(representative.diameter_mm)
    sheet.cell(row, 5).value = "x"
    sheet.cell(row, 6).value = _display_number(representative.production_length_cm)
    sheet.cell(row, 7).value = None
    sheet.cell(row, 8).value = "кг"
    sheet.cell(row, 9).value = f"=(D{row}/2)*(D{row}/2)*3.14*0.007859872"
    sheet.cell(row, 10).value = f"=B{row}*F{row}/100"
    sheet.cell(row, 11).value = f"=J{row}*I{row}"
    _write_hidden_trace(sheet, row, group, engine, source_title)
    _add_shape_image(sheet, row, group, shape, image_dir)


def _write_total_group_row(sheet, row: int, group: ExportGroup, *, source_refs, shape: ShapeDefinition, image_dir: Path, engine: EnginePackage) -> None:
    representative = group.representative
    sheet.cell(row, 1).value = int(group.position_number or 0)
    sheet.cell(row, 2).value = _sumif_identity_formula(
        source_refs, total_row=row, value_column="B"
    )
    sheet.cell(row, 3).value = "N"
    sheet.cell(row, 4).value = _display_number(representative.diameter_mm)
    sheet.cell(row, 5).value = "x"
    sheet.cell(row, 6).value = _display_number(representative.production_length_cm)
    sheet.cell(row, 7).value = None
    sheet.cell(row, 8).value = "кг"
    sheet.cell(row, 9).value = f"=(D{row}/2)*(D{row}/2)*3.14*0.007859872"
    sheet.cell(row, 10).value = _sumif_identity_formula(
        source_refs, total_row=row, value_column="J"
    )
    sheet.cell(row, 11).value = _sumif_identity_formula(
        source_refs, total_row=row, value_column="K"
    )
    _write_hidden_trace(sheet, row, group, engine, "Общо")
    _add_shape_image(sheet, row, group, shape, image_dir)


def _write_hidden_trace(sheet, row: int, group: ExportGroup, engine: EnginePackage, source_title: str) -> None:
    representative = group.representative
    annotations = [position.annotation_handle for position in group.positions]
    geometries = [handle for position in group.positions for handle in position.geometry_handles]
    sheet.cell(row, 12).value = representative.shape_code
    sheet.cell(row, 13).value = representative.shape_name
    sheet.cell(row, 14).value = representative.shape_revision
    sheet.cell(row, 15).value = ";".join(annotations)
    sheet.cell(row, 16).value = ";".join(geometries)
    sheet.cell(row, 17).value = engine.version
    sheet.cell(row, 18).value = engine.content_sha256 or ""
    sheet.cell(row, 19).value = source_title
    sheet.cell(row, 20).value = _production_identity_token(group.key)
    sheet.cell(row, 7).comment = Comment(
        "\n".join(
            [
                f"Позиция: {group.position_number}",
                f"MASTER: {representative.shape_code} — {representative.shape_name}",
                f"Ревизия: {representative.shape_revision}",
                f"Engine: {engine.engine_id} v{engine.version}",
                f"Annotation handles: {'; '.join(annotations)}",
                f"Geometry handles: {'; '.join(geometries)}",
                f"Греда: {representative.beam_name or '—'}",
                f"Beam assignment: {representative.beam_assignment_method or '—'}",
                f"Beam score/margin: {representative.beam_assignment_score!s} / {representative.beam_assignment_margin!s}",
                f"Вертикален раздел: {representative.vertical_section_title or '—'}",
                (
                    "Vertical section handles: "
                    f"{representative.vertical_section_label_handle or '—'} / "
                    f"{representative.vertical_section_divider_handle or '—'}"
                ),
                f"Vertical assignment: {representative.vertical_assignment_method or '—'}",
                (
                    "Vertical score/margin: "
                    f"{representative.vertical_assignment_score!s} / "
                    f"{representative.vertical_assignment_margin!s}"
                ),
                f"Окрупнени DXF надписи: {len(group.positions)}",
                f"Събираеми количества: {' + '.join(str(value) for value in group.counts)}",
            ]
        ),
        "LION REBAR",
    )


def _sumif_identity_formula(
    source_refs: Sequence[tuple[str, int, ExportGroup]],
    *,
    total_row: int,
    value_column: str,
) -> str:
    """Sum one identity with at most one SUMIF term per source sheet.

    The previous one-reference-per-row SUM formula could exceed Excel's
    255-argument limit.  Hidden column T carries a SHA-256 identity token, so
    hundreds of beam ledger rows on one sheet still need a single bounded
    SUMIF term.
    """

    rows_by_sheet: dict[str, list[int]] = defaultdict(list)
    for sheet_name, source_row, _group in source_refs:
        rows_by_sheet[str(sheet_name)].append(int(source_row))
    terms: list[str] = []
    for sheet_name, source_rows in rows_by_sheet.items():
        first_row = min(source_rows)
        last_row = max(source_rows)
        identity_range = _sheet_ref(
            sheet_name, f"$T${first_row}:$T${last_row}"
        )
        value_range = _sheet_ref(
            sheet_name,
            f"${value_column}${first_row}:${value_column}${last_row}",
        )
        terms.append(
            f"SUMIF({identity_range},$T{int(total_row)},{value_range})"
        )
    return "=" + "+".join(terms) if terms else "=0"


def _validate_written_global_formulas(
    workbook,
    total_sheet,
    total_sources: dict[tuple, list[tuple[str, int, ExportGroup]]],
    total_groups: Sequence[ExportGroup],
) -> dict[str, Any]:
    """Fail closed when written formulas, source cells or hidden keys drift."""

    max_formula_length = 0
    max_source_groups = 0
    max_source_sheets = 0
    source_identity_cells_validated = 0
    source_value_cells_validated = 0
    seen_source_coordinates: set[tuple[str, int]] = set()
    for group in total_groups:
        if group.row_number is None:
            raise ExcelExportError("Global total row has no assigned row number.")
        row = int(group.row_number)
        source_refs = total_sources[group.key]
        source_sheets = {str(name) for name, _source_row, _group in source_refs}
        max_source_groups = max(max_source_groups, len(source_refs))
        max_source_sheets = max(max_source_sheets, len(source_sheets))
        expected_token = _production_identity_token(group.key)
        for sheet_name, source_row, source_group in source_refs:
            normalized_sheet = str(sheet_name)
            normalized_row = int(source_row)
            coordinate = (normalized_sheet, normalized_row)
            if coordinate in seen_source_coordinates:
                raise ExcelExportError(
                    "Global total contains a duplicate source coordinate: "
                    f"{normalized_sheet}!{normalized_row}."
                )
            seen_source_coordinates.add(coordinate)
            if normalized_sheet not in workbook.sheetnames:
                raise ExcelExportError(
                    "Global total references a missing source sheet: "
                    f"{normalized_sheet}."
                )
            if normalized_row < DATA_START_ROW:
                raise ExcelExportError(
                    "Global total references an invalid source row: "
                    f"{normalized_sheet}!{normalized_row}."
                )
            if source_group.key != group.key:
                raise ExcelExportError(
                    "Global total source group has a different production "
                    "identity."
                )
            source_sheet = workbook[normalized_sheet]
            if source_sheet.cell(normalized_row, 20).value != expected_token:
                raise ExcelExportError(
                    "Source hidden production-identity token mismatch: "
                    f"{normalized_sheet}!T{normalized_row}."
                )
            source_identity_cells_validated += 1
            representative = source_group.representative
            expected_source_values = {
                2: (
                    "="
                    + "+".join(str(value) for value in source_group.counts)
                ),
                4: _display_number(representative.diameter_mm),
                6: _display_number(representative.production_length_cm),
                9: (
                    f"=(D{normalized_row}/2)*(D{normalized_row}/2)"
                    "*3.14*0.007859872"
                ),
                10: f"=B{normalized_row}*F{normalized_row}/100",
                11: f"=J{normalized_row}*I{normalized_row}",
            }
            for column_index, expected_value in expected_source_values.items():
                actual_value = source_sheet.cell(
                    normalized_row, column_index
                ).value
                if actual_value != expected_value:
                    raise ExcelExportError(
                        "Global total source value/formula mismatch: "
                        f"{normalized_sheet}!"
                        f"{source_sheet.cell(normalized_row, column_index).coordinate}."
                    )
                source_value_cells_validated += 1
        if total_sheet.cell(row, 20).value != expected_token:
            raise ExcelExportError(
                "Global total hidden production-identity token mismatch."
            )
        representative = group.representative
        expected_total_values = {
            4: _display_number(representative.diameter_mm),
            6: _display_number(representative.production_length_cm),
            9: f"=(D{row}/2)*(D{row}/2)*3.14*0.007859872",
        }
        for column_index, expected_value in expected_total_values.items():
            if total_sheet.cell(row, column_index).value != expected_value:
                raise ExcelExportError(
                    "Global total value/formula mismatch: "
                    f"{total_sheet.cell(row, column_index).coordinate}."
                )
        for column_index, column_name in ((2, "B"), (10, "J"), (11, "K")):
            expected = _sumif_identity_formula(
                source_refs,
                total_row=row,
                value_column=column_name,
            )
            actual = total_sheet.cell(row, column_index).value
            if actual != expected:
                raise ExcelExportError(
                    f"Global total {column_name}{row} formula mismatch."
                )
            max_formula_length = max(max_formula_length, len(expected))
            if len(expected) > 8192:
                raise ExcelExportError(
                    f"Global total {column_name}{row} exceeds Excel's "
                    "8192-character formula limit."
                )
    return {
        "formula_strategy": "SUMIF_BY_HIDDEN_PRODUCTION_IDENTITY_V1",
        "formula_cells_validated": len(total_groups) * 3,
        "max_formula_length": max_formula_length,
        "max_source_groups_per_identity": max_source_groups,
        "max_source_sheets_per_identity": max_source_sheets,
        "source_identity_cells_validated": source_identity_cells_validated,
        "source_value_cells_validated": source_value_cells_validated,
        "source_coordinates_unique": True,
        "excel_255_argument_limit_avoided": True,
    }


def _add_shape_image(sheet, row: int, group: ExportGroup, shape: ShapeDefinition, image_dir: Path) -> None:
    representative = group.representative
    image_path = image_dir / f"{sheet.title}_{row}_{representative.shape_code}.png"
    render_shape_png(
        image_path,
        shape=shape,
        segment_lengths_cm=representative.segment_lengths_cm or (representative.production_length_cm,),
        turn_angles_deg=representative.turn_angles_deg,
        production_length_cm=representative.production_length_cm,
    )
    image = PngExcelImage(image_path)
    image.width = VID_IMAGE_WIDTH_PX
    image.height = VID_IMAGE_HEIGHT_PX
    marker = AnchorMarker(
        col=6,
        row=row - 1,
        colOff=pixels_to_EMU(VID_IMAGE_OFFSET_X_PX),
        rowOff=pixels_to_EMU(VID_IMAGE_OFFSET_Y_PX),
    )
    image.anchor = OneCellAnchor(
        _from=marker,
        ext=XDRPositiveSize2D(
            cx=pixels_to_EMU(VID_IMAGE_WIDTH_PX),
            cy=pixels_to_EMU(VID_IMAGE_HEIGHT_PX),
        ),
    )
    sheet.add_image(image)


def _write_sheet_total(sheet, row: int, data_rows: Sequence[int], *, include_tonnes: bool) -> None:
    first_data = min(data_rows) if data_rows else DATA_START_ROW
    last_data = max(data_rows) if data_rows else DATA_START_ROW
    sheet.merge_cells(start_row=row, start_column=9, end_row=row, end_column=10)
    label = sheet.cell(row, 9)
    label.value = "ОБЩО КГ"
    label_font = copy(label.font)
    label_font.bold = True
    label.font = label_font
    label.alignment = Alignment(horizontal="right", vertical="center")
    sheet.cell(row, 11).value = f"=SUM(K{first_data}:K{last_data})" if data_rows else "=0"
    total_font = copy(sheet.cell(row, 11).font)
    total_font.bold = True
    sheet.cell(row, 11).font = total_font
    sheet.row_dimensions[row].height = SUMMARY_ROW_HEIGHT_PT
    if include_tonnes:
        tonne_row = row + 1
        sheet.merge_cells(start_row=tonne_row, start_column=9, end_row=tonne_row, end_column=10)
        tonne_label = sheet.cell(tonne_row, 9)
        tonne_label.value = "ОБЩО Т"
        tonne_label_font = copy(tonne_label.font)
        tonne_label_font.bold = True
        tonne_label.font = tonne_label_font
        tonne_label.alignment = Alignment(horizontal="right", vertical="center")
        sheet.cell(tonne_row, 11).value = f"=K{row}/1000"
        tonne_total_font = copy(sheet.cell(tonne_row, 11).font)
        tonne_total_font.bold = True
        sheet.cell(tonne_row, 11).font = tonne_total_font
        sheet.cell(tonne_row, 11).number_format = "0.000"
        sheet.row_dimensions[tonne_row].height = SUMMARY_ROW_HEIGHT_PT


def _finalize_sheet(sheet, last_row: int) -> None:
    sheet.auto_filter.ref = None
    sheet.print_area = f"A3:K{last_row}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "3:5"


def _group_positions(positions: Sequence[ExportPosition]) -> list[ExportGroup]:
    grouped: dict[tuple, ExportGroup] = {}
    for position in positions:
        grouped.setdefault(position.group_key, ExportGroup(key=position.group_key)).positions.append(position)
    return sorted(grouped.values(), key=_group_sort_key)


def _source_group_id(sheet_name: str, row: int) -> str:
    return f"{sheet_name}!B{int(row)}"


def _validate_identity_group(group: ExportGroup, *, context: str) -> None:
    if not group.positions:
        raise ExcelExportError(f"{context} Excel group has no positions.")
    mismatched = [
        str(position.annotation_handle)
        for position in group.positions
        if position.group_key != group.key
    ]
    if mismatched:
        raise ExcelExportError(
            f"{context} Excel group mixes different production identities: "
            + ", ".join(mismatched)
        )


def _validate_source_group(group: ExportGroup) -> None:
    _validate_identity_group(group, context="Source")

    vertical_members = [
        position
        for position in group.positions
        if position.vertical_section_label_handle
        or position.vertical_section_divider_handle
        or position.vertical_section_title
    ]
    if not vertical_members:
        return
    if len(vertical_members) != len(group.positions):
        raise ExcelExportError(
            "Source Excel group mixes vertical and non-vertical positions."
        )
    section_keys = {
        (
            int(position.frame_index),
            str(position.vertical_section_label_handle),
            str(position.vertical_section_divider_handle),
        )
        for position in vertical_members
    }
    if len(section_keys) != 1:
        raise ExcelExportError(
            "Source Excel group crosses frozen vertical-section ownership."
        )


def _validate_source_coverage(
    positions: Sequence[ExportPosition],
    source_locations: dict[str, dict[str, Any]],
    total_sources: dict[tuple, list[tuple[str, int, ExportGroup]]],
    grouped_row_count: int,
) -> None:
    expected = Counter(str(position.annotation_handle) for position in positions)
    duplicate_inputs = sorted(
        handle for handle, count in expected.items() if count != 1
    )
    if duplicate_inputs:
        raise ExcelExportError(
            "Production input contains duplicate annotation handles: "
            + ", ".join(duplicate_inputs)
        )

    actual_locations = Counter(str(handle) for handle in source_locations)
    if actual_locations != expected:
        missing = sorted((expected - actual_locations).elements())
        unexpected = sorted((actual_locations - expected).elements())
        raise ExcelExportError(
            "Source Excel annotation coverage mismatch. "
            f"Missing={missing}; unexpected={unexpected}."
        )

    source_refs = [
        source
        for sources in total_sources.values()
        for source in sources
    ]
    if len(source_refs) != int(grouped_row_count):
        raise ExcelExportError(
            "Total-source reference count differs from the generated source rows: "
            f"{len(source_refs)} != {grouped_row_count}."
        )
    total_coverage = Counter(
        str(position.annotation_handle)
        for _sheet, _row, group in source_refs
        for position in group.positions
    )
    if total_coverage != expected:
        missing = sorted((expected - total_coverage).elements())
        duplicated = sorted(
            handle for handle, count in total_coverage.items() if count > 1
        )
        raise ExcelExportError(
            "Global total does not reference every source annotation exactly once. "
            f"Missing={missing}; duplicated={duplicated}."
        )


def _formula_mass_kg(position: ExportPosition, count: int) -> float:
    """Evaluate the same manufacturing mass expression written to Excel."""

    diameter_mm = float(_display_number(position.diameter_mm))
    length_cm = float(_display_number(position.production_length_cm))
    unit_weight_kg_m = (diameter_mm / 2.0) ** 2 * 3.14 * 0.007859872
    return int(count) * length_cm / 100.0 * unit_weight_kg_m


def _group_formula_mass_kg(group: ExportGroup) -> float:
    return _formula_mass_kg(group.representative, sum(group.counts))


def _validate_global_aggregation(
    positions: Sequence[ExportPosition],
    total_sources: dict[tuple, list[tuple[str, int, ExportGroup]]],
    total_groups: Sequence[ExportGroup],
    global_numbers: dict[tuple, int],
) -> dict[str, Any]:
    """Prove that ``Общо`` is a lossless one-row-per-identity projection."""

    expected_by_key: dict[tuple, list[ExportPosition]] = defaultdict(list)
    for position in positions:
        expected_by_key[position.group_key].append(position)
    expected_keys = set(expected_by_key)

    total_key_counts = Counter(group.key for group in total_groups)
    duplicate_total_keys = [
        key for key, count in total_key_counts.items() if count != 1
    ]
    if duplicate_total_keys:
        raise ExcelExportError(
            "Global total contains duplicate production-identity rows: "
            + ", ".join(sorted(repr(key) for key in duplicate_total_keys))
        )

    source_keys = set(total_sources)
    total_keys = set(total_key_counts)
    number_keys = set(global_numbers)
    if not (expected_keys == source_keys == total_keys == number_keys):
        raise ExcelExportError(
            "Global production-identity key coverage mismatch. "
            f"input={len(expected_keys)}, source={len(source_keys)}, "
            f"total={len(total_keys)}, numbers={len(number_keys)}."
        )

    number_values = [int(value) for value in global_numbers.values()]
    if len(set(number_values)) != len(number_values) or set(number_values) != set(
        range(1, len(number_values) + 1)
    ):
        raise ExcelExportError(
            "Global position numbers are not a unique contiguous registry."
        )

    total_group_by_key = {group.key: group for group in total_groups}
    source_sheet_names: set[str] = set()
    input_count_total = 0
    source_count_total = 0
    total_count_total = 0
    input_mass_kg = 0.0
    source_mass_kg = 0.0
    total_mass_kg = 0.0

    for key, expected_positions in expected_by_key.items():
        source_groups: list[ExportGroup] = []
        for sheet_name, _row, source_group in total_sources[key]:
            source_sheet_names.add(str(sheet_name))
            if source_group.key != key:
                raise ExcelExportError(
                    "A source row is registered under the wrong production identity."
                )
            _validate_identity_group(source_group, context="Source")
            source_groups.append(source_group)

        total_group = total_group_by_key[key]
        _validate_identity_group(total_group, context="Total")

        expected_handles = Counter(
            str(position.annotation_handle) for position in expected_positions
        )
        source_handles = Counter(
            str(position.annotation_handle)
            for group in source_groups
            for position in group.positions
        )
        total_handles = Counter(
            str(position.annotation_handle) for position in total_group.positions
        )
        if expected_handles != source_handles or expected_handles != total_handles:
            raise ExcelExportError(
                "Per-identity annotation traceability mismatch in global total."
            )

        expected_count = sum(int(position.count) for position in expected_positions)
        source_count = sum(sum(group.counts) for group in source_groups)
        total_count = sum(total_group.counts)
        if expected_count != source_count or expected_count != total_count:
            raise ExcelExportError(
                "Per-identity count conservation failed in global total: "
                f"input={expected_count}, source={source_count}, total={total_count}."
            )

        expected_mass = sum(
            _formula_mass_kg(position, position.count)
            for position in expected_positions
        )
        source_mass = sum(_group_formula_mass_kg(group) for group in source_groups)
        total_mass = _group_formula_mass_kg(total_group)
        if not math.isclose(expected_mass, source_mass, rel_tol=1e-12, abs_tol=1e-9):
            raise ExcelExportError(
                "Per-identity mass conservation failed between input and source rows."
            )
        if not math.isclose(expected_mass, total_mass, rel_tol=1e-12, abs_tol=1e-9):
            raise ExcelExportError(
                "Per-identity mass conservation failed in global total."
            )

        input_count_total += expected_count
        source_count_total += source_count
        total_count_total += total_count
        input_mass_kg += expected_mass
        source_mass_kg += source_mass
        total_mass_kg += total_mass

    if not (
        input_count_total == source_count_total == total_count_total
        and math.isclose(input_mass_kg, source_mass_kg, rel_tol=1e-12, abs_tol=1e-9)
        and math.isclose(input_mass_kg, total_mass_kg, rel_tol=1e-12, abs_tol=1e-9)
    ):
        raise ExcelExportError("Global count/mass conservation failed.")

    return {
        "sheet": "Общо",
        "identity_schema": "MASTER_DIAMETER_LENGTH_SEGMENTS_TOPOLOGY_REVISION_V1",
        "group_key_fields": [
            "master_shape_code",
            "diameter_mm",
            "production_length_mm",
            "segment_lengths_mm_and_turn_angles_tenths_deg",
            "master_revision",
            "master_topology_signature",
        ],
        "source_sheets": sorted(source_sheet_names),
        "source_sheet_count": len(source_sheet_names),
        "source_group_count": sum(len(sources) for sources in total_sources.values()),
        "input_annotation_count": len(positions),
        "total_group_count": len(total_groups),
        "global_position_number_count": len(global_numbers),
        "duplicate_identity_key_count": 0,
        "input_count_total": input_count_total,
        "source_count_total": source_count_total,
        "total_count_total": total_count_total,
        "input_mass_kg": round(input_mass_kg, 9),
        "source_mass_kg": round(source_mass_kg, 9),
        "total_mass_kg": round(total_mass_kg, 9),
        "count_conserved": True,
        "mass_conserved": True,
        "one_total_row_per_identity": True,
        "identity_precision": {
            "diameter_decimals": 4,
            "length_mm": 1,
            "segment_length_mm": 1,
            "turn_angle_tenths_deg": 1,
        },
    }


def _column_group_sections(
    positions: Sequence[ExportPosition],
) -> list[ColumnGroupSection]:
    """Lay the working sheet out the way the drawing itself is organised:
    one block per column-group header, its own positions underneath.

    Unlike ``_beam_sections`` (an execution ledger that keeps every DXF
    annotation on its own row), identical production identities WITHIN one
    column group are merged into a single row -- that is how the owner's
    own specification reads it ("25*2=50N8x140", one line per shape per
    group). Identities are never merged ACROSS groups here; "Общо" still
    does that, unchanged.
    """

    by_group: dict[str, dict[tuple, ExportGroup]] = {}
    order: list[str] = []
    for position in positions:
        # The designer's header carries AutoCAD's underline control code
        # ("%%U..."); the sheet shows the plain title.
        title = decode_dxf_text(str(position.column_group_label or "")).strip()
        if title not in by_group:
            by_group[title] = {}
            order.append(title)
        groups = by_group[title]
        groups.setdefault(
            position.group_key, ExportGroup(key=position.group_key)
        ).positions.append(position)
    sections = [
        ColumnGroupSection(
            title=title,
            groups=sorted(by_group[title].values(), key=_group_sort_key),
        )
        for title in order
    ]
    sections.sort(key=lambda section: _column_group_sort_key(section.title))
    return sections


def _column_group_sort_key(title: str) -> tuple:
    """Order blocks the way an engineer reads a column schedule: by element
    prefix first, then by number ("K2" before "K10", every "K" before every
    "ВП"), with any unlabelled block last.

    Sorting on the bare number alone would interleave differently-named
    elements -- "ВП1" would land between "K9" and "K23" purely because its
    number is 1.
    """

    text = str(title or "").strip()
    if not text:
        return (1, "", 10**9, "")
    match = re.match(r"\s*([^\d\s,]*)\s*(\d+)", text)
    if match is None:
        return (0, text.upper(), 10**9, text)
    prefix = match.group(1).upper()
    return (0, prefix, int(match.group(2)), text)


def _beam_sections(positions: Sequence[ExportPosition]) -> list[BeamSection]:
    """Return beam-by-beam rows without merging separate DXF annotations.

    The working sheet "греди" is an execution ledger: every approved DXF
    annotation remains a separate row under its beam, even when another row in
    the same beam has the same production identity.  Global aggregation is
    performed only in "Общо" through ``total_sources``.
    """

    by_beam: dict[tuple[str, str], list[ExportPosition]] = defaultdict(list)
    for position in positions:
        title = str(position.beam_name or "НЕРАЗПОЗНАТА ГРЕДА").strip()
        by_beam[(title, position.beam_label_handle)].append(position)
    sections = [
        BeamSection(
            title=title,
            label_handle=handle,
            groups=[
                ExportGroup(key=position.group_key, positions=[position])
                for position in sorted(items, key=_beam_position_sort_key)
            ],
        )
        for (title, handle), items in by_beam.items()
    ]
    sections.sort(key=lambda section: _beam_sort_key(section.title))
    return sections


def _vertical_sections(positions: Sequence[ExportPosition]) -> list[VerticalSection]:
    """Aggregate equal production identities inside each frozen section.

    Cores, wall types and named verticals stay separate on the source sheet.
    Inside one section, equal production identities share one auditable row
    whose quantity formula preserves every DXF annotation as a separate term.
    The global total sheet performs the second aggregation across sections.
    """

    by_section: dict[
        tuple[int, str, str, str, str],
        list[ExportPosition],
    ] = defaultdict(list)
    seen_annotations: set[str] = set()
    metadata_by_label: dict[str, tuple[int, str, str, str]] = {}
    for position in positions:
        if not (
            position.vertical_section_title
            and position.vertical_section_label_handle
            and position.vertical_section_divider_handle
            and position.vertical_section_kind
            and position.vertical_section_order is not None
            and position.vertical_assignment_method
        ):
            raise ExcelExportError(
                f"Vertical position {position.annotation_text} "
                f"({position.annotation_handle}) has no complete frozen section ownership."
            )
        if position.annotation_handle in seen_annotations:
            raise ExcelExportError(
                f"Vertical annotation {position.annotation_handle} occurs more than once in the source ledger."
            )
        seen_annotations.add(position.annotation_handle)
        metadata = (
            int(position.vertical_section_order),
            position.vertical_section_title,
            position.vertical_section_divider_handle,
            position.vertical_section_kind,
        )
        previous = metadata_by_label.setdefault(
            position.vertical_section_label_handle,
            metadata,
        )
        if previous != metadata:
            raise ExcelExportError(
                f"Vertical section {position.vertical_section_label_handle} has conflicting frozen metadata."
            )
        key = (
            int(position.vertical_section_order),
            position.vertical_section_title,
            position.vertical_section_label_handle,
            position.vertical_section_divider_handle,
            position.vertical_section_kind,
        )
        by_section[key].append(position)

    sections = [
        VerticalSection(
            title=title,
            label_handle=label_handle,
            divider_handle=divider_handle,
            kind=kind,
            order=order,
            groups=_group_positions(
                sorted(items, key=_beam_position_sort_key)
            ),
        )
        for (order, title, label_handle, divider_handle, kind), items in by_section.items()
    ]
    sections.sort(
        key=lambda section: (
            int(section.order),
            section.title.casefold(),
            section.label_handle,
        )
    )
    return sections


def _beam_position_sort_key(position: ExportPosition) -> tuple:
    """Stable working order inside one beam section.

    Stirrups are shown first because they define the beam spacing/containment
    set, followed by the remaining bars in increasing diameter and length.
    Separate DXF annotations are never merged by this sort.
    """

    stirrup_priority = 0 if str(position.shape_code).strip().upper() == "F006" else 1
    return (
        stirrup_priority,
        float(position.diameter_mm),
        int(position.production_length_mm),
        str(position.shape_code),
        str(position.annotation_text),
        str(position.annotation_handle),
    )


def _group_sort_key(group: ExportGroup) -> tuple:
    representative = group.representative
    return (
        representative.diameter_mm,
        representative.shape_code,
        representative.production_length_mm,
        representative.segment_signature,
        representative.annotation_text,
    )


def _vertical_section_kind(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    upper = normalized.upper()
    has_schema = re.search(
        r"\u0421\u0425\s*\.\s*\u0410\u0420\u041C\s*\.",
        upper,
    )
    if not has_schema:
        return None
    # Axis letters may be Latin or visually equivalent Cyrillic glyphs.
    # Recognition remains fail-closed: a full schema title and one unique,
    # overlapping divider are still mandatory evidence.
    canonical = (
        upper
        .replace("\u041A", "K")
        .replace("\u0410", "A")
        .replace("\u0412", "B")
    )
    if re.match(
        r"^\s*\u0421\u0422\u0411\s*\.\s*\u042F\u0414\u0420\u041E\s+[A-Z]\s*\.\s*\d+",
        canonical,
    ):
        return "CORE"
    if re.match(r"^\s*\u0422\u0418\u041F\s+\S+", canonical) and re.search(
        r"\b\u0411\u0420\s*\.",
        canonical,
    ):
        return "TYPE"
    if re.match(
        r"^\s*(?:\u0428\s*\.\s*[A-Z]|K\s*\.\s*(?:\u0428\s*\.\s*)?[A-Z])",
        canonical,
    ) and re.search(r"\b\u0411\u0420\s*\.", canonical):
        return "NAMED_VERTICAL"
    return None


def _vertical_section_natural_key(kind: str, title: str) -> tuple:
    """Semantic display key; spatial coordinates remain ownership evidence."""

    normalized = re.sub(r"\s+", " ", str(title or "")).strip().upper()
    canonical = (
        normalized
        .replace("\u041A", "K")
        .replace("\u0410", "A")
        .replace("\u0412", "B")
    )
    if kind == "CORE":
        match = re.search(
            r"\u042F\u0414\u0420\u041E\s+([A-Z])\s*\.\s*(\d+)\s*([A-Z\u0410-\u042F]*)",
            canonical,
        )
        if match:
            return (
                match.group(1).casefold(),
                int(match.group(2)),
                match.group(3).casefold(),
                normalized,
            )
    if kind == "TYPE":
        match = re.match(
            r"^\s*\u0422\u0418\u041F\s+(\d+)\s*([A-Z\u0410-\u042F]*)",
            normalized,
        )
        if match:
            return (int(match.group(1)), match.group(2).casefold(), normalized)
    if kind == "NAMED_VERTICAL":
        canonical = canonical.replace("\u0428", "SH")
        compact = re.sub(r"\s+", "", canonical)
        prefix_rank = 3
        axis = ""
        remainder = canonical
        for rank, pattern in (
            (1, r"^\s*K\s*\.\s*SH\s*\.\s*([A-Z])"),
            (0, r"^\s*K\s*\.\s*([A-Z])"),
            (2, r"^\s*SH\s*\.\s*([A-Z])"),
        ):
            match = re.match(pattern, canonical)
            if match:
                prefix_rank = rank
                axis = match.group(1).casefold()
                remainder = canonical[match.end():]
                break
        number_match = re.search(r"(\d+)([A-Z]*)", remainder)
        if number_match:
            return (
                axis,
                prefix_rank,
                int(number_match.group(1)),
                number_match.group(2).casefold(),
                compact,
            )
        return (axis, prefix_rank, 10**9, "", compact)
    return (10**9, normalized)


def _vertical_section_detection_sort_key(
    item: tuple[int, str, str, str, str, float, float, float],
) -> tuple:
    frame_index, title_handle, _divider_handle, title, kind, xmin, _xmax, divider_y = item
    kind_rank = {
        "CORE": 0,
        "TYPE": 1,
        "NAMED_VERTICAL": 2,
    }.get(kind, 99)
    return (
        int(frame_index),
        kind_rank,
        _vertical_section_natural_key(kind, title),
        -float(divider_y),
        float(xmin),
        str(title_handle),
    )


def _entity_visual_bounds(
    record: DxfEntityRecord,
) -> tuple[float, float, float, float] | None:
    geometry = record.geometry or {}
    bbox = geometry.get("visual_bbox")
    if isinstance(bbox, (list, tuple)):
        points = [
            (float(value[0]), float(value[1]))
            for value in bbox
            if isinstance(value, (list, tuple)) and len(value) >= 2
        ]
        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return min(xs), min(ys), max(xs), max(ys)
    anchor = _entity_insert(record)
    if anchor is None:
        return None
    width = _positive_float(
        geometry.get("estimated_text_width") or geometry.get("width")
    ) or 0.0
    height = _positive_float(
        geometry.get("estimated_text_height")
        or geometry.get("height")
        or geometry.get("char_height")
    ) or 0.0
    return (
        anchor[0] - width / 2.0,
        anchor[1] - height / 2.0,
        anchor[0] + width / 2.0,
        anchor[1] + height / 2.0,
    )


def _horizontal_line_span(
    record: DxfEntityRecord,
) -> tuple[float, float, float] | None:
    if str(record.entity_type or "").upper() != "LINE":
        return None
    geometry = record.geometry or {}
    start = geometry.get("start")
    end = geometry.get("end")
    if not (
        isinstance(start, (list, tuple))
        and len(start) >= 2
        and isinstance(end, (list, tuple))
        and len(end) >= 2
    ):
        return None
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    length = abs(x2 - x1)
    if length <= 1.0 or abs(y2 - y1) > max(0.5, length * 0.002):
        return None
    return min(x1, x2), max(x1, x2), (y1 + y2) / 2.0


def _detect_vertical_section_labels(
    entities: Sequence[DxfEntityRecord],
    frames: Sequence[DrawingFrame],
) -> list[VerticalSectionLabel]:
    """Detect explicit vertical detail titles and their underline dividers.

    Ownership is based on the divider bounds below a strong section title.
    Generic plan labels are ignored.
    """

    frame_map = {int(frame.index): frame for frame in frames}
    line_spans: list[tuple[DxfEntityRecord, int, float, float, float]] = []
    for record in entities:
        span = _horizontal_line_span(record)
        if span is None:
            continue
        xmin, xmax, y = span
        frame_index = assign_point_to_frame(
            (xmin + xmax) / 2.0,
            y,
            list(frames),
        )
        if (
            frame_index is None
            or frame_map.get(int(frame_index)) is None
            or frame_map[int(frame_index)].category_code != "VERTICALS"
        ):
            continue
        line_spans.append((record, int(frame_index), xmin, xmax, y))

    detected: list[
        tuple[int, str, str, str, str, float, float, float]
    ] = []
    used_dividers: set[str] = set()
    for record in entities:
        title = re.sub(r"\s+", " ", str(record.text or "")).strip()
        kind = _vertical_section_kind(title)
        if kind is None:
            continue
        anchor = _entity_insert(record)
        bounds = _entity_visual_bounds(record)
        if anchor is None or bounds is None:
            raise ExcelExportError(
                f"Vertical section title {record.handle} has no visual bounds."
            )
        frame_index = assign_point_to_frame(anchor[0], anchor[1], list(frames))
        if (
            frame_index is None
            or frame_map.get(int(frame_index)) is None
            or frame_map[int(frame_index)].category_code != "VERTICALS"
        ):
            continue
        text_xmin, text_ymin, text_xmax, _text_ymax = bounds
        text_width = max(text_xmax - text_xmin, 1.0)
        candidates: list[
            tuple[float, float, str, DxfEntityRecord, float, float, float]
        ] = []
        for line, line_frame_index, xmin, xmax, divider_y in line_spans:
            if line_frame_index != int(frame_index):
                continue
            vertical_gap = text_ymin - divider_y
            if vertical_gap < 1.0 or vertical_gap > 80.0:
                continue
            overlap = max(0.0, min(text_xmax, xmax) - max(text_xmin, xmin))
            line_width = max(xmax - xmin, 1.0)
            if overlap / min(text_width, line_width) < 0.65:
                continue
            center_delta = abs(
                (text_xmin + text_xmax) / 2.0 - (xmin + xmax) / 2.0
            )
            score = vertical_gap + center_delta * 0.05 - overlap * 0.0001
            candidates.append(
                (score, center_delta, line.handle, line, xmin, xmax, divider_y)
            )
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        if not candidates:
            raise ExcelExportError(
                f"Vertical section title {record.handle} ({title}) has no explicit divider."
            )
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] <= 0.5:
            raise ExcelExportError(
                f"Vertical section title {record.handle} ({title}) has duplicate divider candidates."
            )
        _score, _center_delta, divider_handle, _line, xmin, xmax, divider_y = candidates[0]
        if divider_handle in used_dividers:
            raise ExcelExportError(
                f"Divider {divider_handle} is linked to more than one vertical section title."
            )
        used_dividers.add(divider_handle)
        detected.append(
            (
                int(frame_index),
                record.handle,
                divider_handle,
                title,
                kind,
                xmin,
                xmax,
                divider_y,
            )
        )

    detected.sort(key=_vertical_section_detection_sort_key)
    return [
        VerticalSectionLabel(
            frame_index=frame_index,
            title_handle=title_handle,
            divider_handle=divider_handle,
            title=title,
            kind=kind,
            order=order,
            xmin=xmin,
            xmax=xmax,
            divider_y=divider_y,
        )
        for order, (
            frame_index,
            title_handle,
            divider_handle,
            title,
            kind,
            xmin,
            xmax,
            divider_y,
        ) in enumerate(detected, start=1)
    ]


def _assign_vertical_section(
    x: float,
    y: float,
    frame_index: int,
    labels: Sequence[VerticalSectionLabel],
    *,
    x_margin: float = VERTICAL_SECTION_X_MARGIN,
    minimum_margin: float = VERTICAL_SECTION_MIN_MARGIN,
) -> VerticalAssignment | None:
    candidates: list[tuple[float, int, str, VerticalSectionLabel]] = []
    for label in labels:
        if label.frame_index != int(frame_index):
            continue
        vertical_distance = float(label.divider_y) - float(y)
        if vertical_distance < 0.0:
            continue
        horizontal_outside = max(
            float(label.xmin) - float(x),
            0.0,
            float(x) - float(label.xmax),
        )
        if horizontal_outside > float(x_margin):
            continue
        score = vertical_distance + 2.0 * horizontal_outside
        candidates.append((score, int(label.order), label.title_handle, label))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    if not candidates:
        return None
    best_score, _order, _handle, best = candidates[0]
    if len(candidates) > 1:
        margin = candidates[1][0] - best_score
        if margin <= float(minimum_margin):
            return None
    else:
        margin = 1_000_000_000.0
    return VerticalAssignment(
        section=best,
        score=float(best_score),
        margin=float(margin),
    )


def _validate_vertical_assignments(
    frames: Sequence[DrawingFrame],
    labels: Sequence[VerticalSectionLabel],
    positions: Sequence[ExportPosition],
) -> None:
    vertical_frames = {
        int(frame.index)
        for frame in frames
        if str(frame.category_code or "") == "VERTICALS"
    }
    vertical_positions = [
        position
        for position in positions
        if int(position.frame_index) in vertical_frames
    ]
    if not vertical_positions:
        return
    if not labels:
        raise ExcelExportError(
            "Vertical positions exist, but no explicit title/divider sections were detected."
        )
    if len({position.annotation_handle for position in vertical_positions}) != len(
        vertical_positions
    ):
        raise ExcelExportError(
            "A vertical annotation occurs more than once in the final export ledger."
        )
    label_map = {
        (label.frame_index, label.title_handle, label.divider_handle): label
        for label in labels
    }
    used: set[tuple[int, str, str]] = set()
    for position in vertical_positions:
        key = (
            int(position.frame_index),
            position.vertical_section_label_handle,
            position.vertical_section_divider_handle,
        )
        label = label_map.get(key)
        if (
            label is None
            or position.vertical_section_title != label.title
            or position.vertical_section_kind != label.kind
            or position.vertical_section_order != label.order
            or not position.vertical_assignment_method
        ):
            raise ExcelExportError(
                f"Vertical section ownership is missing or stale for "
                f"{position.annotation_text} ({position.annotation_handle})."
            )
        used.add(key)
    unused = [
        label.title
        for label in labels
        if (label.frame_index, label.title_handle, label.divider_handle) not in used
    ]
    if unused:
        raise ExcelExportError(
            "Detected vertical sections without approved positions: "
            + ", ".join(unused)
        )


def _detect_beam_labels(entities: Sequence[DxfEntityRecord], frames: Sequence[DrawingFrame]) -> list[BeamLabel]:
    result: list[BeamLabel] = []
    frame_map = {frame.index: frame for frame in frames}
    pattern = re.compile(r"^\s*ГР\.?\s*[A-ZА-Я]+\.\s*\d+", flags=re.IGNORECASE)
    for record in entities:
        text = re.sub(r"\s+", " ", str(record.text or "")).strip()
        if not pattern.search(text):
            continue
        point = _entity_insert(record)
        if point is None:
            continue
        frame_index = assign_point_to_frame(point[0], point[1], list(frames))
        if frame_index is None or frame_map.get(frame_index) is None:
            continue
        # Собственик, 2026-09-13, пред списъка с греди: „гр-13 я няма в
        # списъка да я избера и да нанеса позициите."
        #
        # Дотук се четяха само заглавията в рамка BEAMS. В ``s-9`` шест от
        # тридесет и двете греди -- B.1, B.7, B.13, B.18, B.24, B.29 -- са
        # начертани в рамката ДЕТАЙЛИ, на същия слой ``Section``. По нищо не
        # личи, че е грешка; просто така е чертано. Изхвърлянето им означава,
        # че операторът НЕ МОЖЕ да ги посочи -- нито от списък, нито с рамка,
        # която иска разпознато заглавие вътре.
        #
        # Автоматиката не се променя от това: ``_assign_beam_label`` търси
        # само сред заглавията със СЪЩИЯ ``frame_index``, така че греда от
        # рамка ДЕТАЙЛИ не може да открадне позиция от рамка ГРЕДИ.
        # Разширява се само КАКВО ВИЖДА операторът.
        height = _positive_float(
            record.geometry.get("height")
            or record.geometry.get("char_height")
            or record.geometry.get("estimated_text_height")
        ) or 0.0
        result.append(BeamLabel(frame_index, record.handle, text, point[0], point[1], height))
    return result


def _assign_beam_label(
    x: float,
    y: float,
    frame_index: int,
    labels: Sequence[BeamLabel],
    *,
    minimum_margin: float = 0.25,
) -> BeamAssignment | None:
    """Assign one reinforcement annotation to one beam title.

    Beam details in the supported designer layout are drawn to the right and
    mostly below their title.  A plain Euclidean nearest-neighbour rule crosses
    into the next beam row and was the root cause of incomplete beam sheets.
    This detector estimates the title-row pitch from the DXF itself and scores
    horizontal ownership and the expected vertical production-detail offset.

    The optional user layer ``Gredi`` is deliberately not consulted here.  It
    is used only as an external ground-truth fixture in regression tests.
    """

    candidates = [label for label in labels if label.frame_index == frame_index]
    if not candidates:
        return None
    row_pitch = _beam_label_row_pitch(candidates)
    target_vertical = 0.745 * row_pitch
    horizontal_scale = max(1e-6, 1.17 * row_pitch)
    vertical_scale = max(1e-6, 0.43 * row_pitch)
    right_tolerance = 0.15 * row_pitch
    min_vertical = -0.25 * row_pitch
    max_vertical = 1.10 * row_pitch

    scored: list[tuple[float, BeamLabel]] = []
    for label in candidates:
        dx = float(x) - label.x
        dy = label.y - float(y)
        if dx < -right_tolerance:
            continue
        if dy < min_vertical or dy > max_vertical:
            continue
        score = abs(dx) / horizontal_scale
        score += abs(dy - target_vertical) / vertical_scale
        # A title below the detail is possible but exceptional.  Penalising it
        # prevents a detail near the bottom of one row from jumping to the
        # next row while still allowing a clear horizontal winner.
        if dy < 0.0:
            score += 2.0
        scored.append((score, label))

    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], _beam_sort_key(item[1].title), item[1].handle))
    best_score, best_label = scored[0]
    margin = 999.0 if len(scored) == 1 else float(scored[1][0] - best_score)
    if margin < float(minimum_margin):
        return None
    return BeamAssignment(
        label=best_label,
        score=round(float(best_score), 6),
        margin=round(float(margin), 6),
        row_pitch=round(float(row_pitch), 6),
    )


def _nearest_beam_label(x: float, y: float, frame_index: int, labels: Sequence[BeamLabel]) -> BeamLabel | None:
    """Backward-compatible wrapper for the deterministic beam-region detector."""

    assignment = _assign_beam_label(x, y, frame_index, labels)
    return assignment.label if assignment is not None else None


def _beam_label_row_pitch(labels: Sequence[BeamLabel]) -> float:
    if not labels:
        return 450.0
    heights = [float(label.height) for label in labels if float(label.height) > 0.0]
    median_height = float(sorted(heights)[len(heights) // 2]) if heights else 0.0
    row_gap_threshold = max(4.0 * median_height, 20.0) if median_height else 100.0

    ordered = sorted(labels, key=lambda label: (-label.y, label.x, label.handle))
    rows: list[list[BeamLabel]] = []
    for label in ordered:
        if not rows:
            rows.append([label])
            continue
        previous_mean = sum(item.y for item in rows[-1]) / len(rows[-1])
        if previous_mean - label.y > row_gap_threshold:
            rows.append([label])
        else:
            rows[-1].append(label)

    row_means = [sum(item.y for item in row) / len(row) for row in rows]
    row_pitches = [
        row_means[index] - row_means[index + 1]
        for index in range(len(row_means) - 1)
        if row_means[index] > row_means[index + 1]
    ]
    if row_pitches:
        ordered_pitches = sorted(row_pitches)
        middle = len(ordered_pitches) // 2
        if len(ordered_pitches) % 2:
            return float(ordered_pitches[middle])
        return float((ordered_pitches[middle - 1] + ordered_pitches[middle]) / 2.0)
    if median_height > 0.0:
        return max(18.0 * median_height, 1.0)
    return 450.0


def _validate_beam_assignments(
    frames: Sequence[DrawingFrame],
    labels: Sequence[BeamLabel],
    positions: Sequence[ExportPosition],
) -> None:
    beam_frame_indexes = {frame.index for frame in frames if frame.category_code == "BEAMS"}
    labels_by_frame: dict[int, list[BeamLabel]] = defaultdict(list)
    for label in labels:
        if label.frame_index in beam_frame_indexes:
            labels_by_frame[label.frame_index].append(label)
    assigned_by_frame: dict[int, set[str]] = defaultdict(set)
    for position in positions:
        if position.frame_index not in beam_frame_indexes:
            continue
        if not position.beam_label_handle or not position.beam_name:
            raise ExcelExportError(
                f"Позиция {position.annotation_text} е без еднозначна греда. Export-ът е блокиран."
            )
        assigned_by_frame[position.frame_index].add(position.beam_label_handle)

    missing: list[str] = []
    for frame_index, frame_labels in labels_by_frame.items():
        assigned = assigned_by_frame.get(frame_index, set())
        missing.extend(label.title for label in frame_labels if label.handle not in assigned)
    if missing:
        preview = ", ".join(sorted(missing, key=_beam_sort_key)[:8])
        suffix = " …" if len(missing) > 8 else ""
        raise ExcelExportError(
            f"Открити са {len(missing)} греди без нито една свързана одобрена позиция: "
            f"{preview}{suffix}. Export-ът е блокиран."
        )


def _beam_sort_key(value: str) -> tuple:
    normalized = str(value or "").upper()
    match = re.search(r"ГР\.?\s*([A-ZА-Я]+)\.\s*(\d+)", normalized)
    if match:
        return (0, match.group(1), int(match.group(2)), normalized)
    return (1, normalized, 10**9, normalized)


def _segments_for_link(
    link: dict[str, Any],
    *,
    shape: ShapeDefinition,
    entity_map: dict[str, DxfEntityRecord],
    production_length_cm: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    # A straight bar has no independent segment breakdown. The reviewed
    # production total is therefore the only dimension that may be shown in
    # VID; schematic DXF measurements such as 269.9 must not leak into Excel.
    if str(shape.code).upper() == "F001":
        return (float(production_length_cm),), ()
    manual = link.get("manual_assignment") if isinstance(link.get("manual_assignment"), dict) else {}
    if manual.get("production_segment_lengths_cm"):
        values = tuple(float(value) for value in manual["production_segment_lengths_cm"])
        return _canonicalize_for_master(shape.code, values), tuple(float(value) for value in manual.get("turn_angles_deg") or [])

    # rc32 persisted five AUTO F007 links with nearby-dimension vectors even
    # though the same links retained their stronger contextual MASTER
    # canonical vector. Keep the frozen project immutable and repair the
    # read-path only under the full original proof chain. USER/manual vectors
    # never enter this branch and arbitrary F007 links are not normalized.
    auto_f007 = _proven_auto_f007_canonical_segments(
        link,
        shape_code=shape.code,
        production_length_cm=production_length_cm,
    )
    if auto_f007 is not None:
        return auto_f007

    manual_segments = link.get("production_segment_lengths_cm")
    if isinstance(manual_segments, list) and manual_segments:
        values = tuple(float(value) for value in manual_segments)
        return _canonicalize_for_master(shape.code, values), _manual_turns(link)
    contextual = _approved_contextual_segments(link, shape.code, production_length_cm)
    if contextual is not None:
        return contextual

    records = [entity_map[handle] for handle in link.get("geometry_handles") or [] if handle in entity_map]
    summary = None
    if records:
        try:
            summary = summarize_selected_geometry(records)
        except ManualGeometryError:
            summary = None

    # Dimension texts are authoritative when they can be assigned one-to-one
    # to the selected geometry and their MASTER formula exactly reproduces the
    # reviewed production total. This resolves schematic DXF examples such as
    # 9N10x215 = 20 + 175 + 20, even when one drawn hook measures 25.
    if summary is not None:
        dimensioned = _segments_from_nearby_dimension_texts(
            link,
            shape_code=shape.code,
            summary=summary,
            entity_map=entity_map,
            production_length_cm=production_length_cm,
        )
        if dimensioned is not None:
            return dimensioned
        parameter_texts = _segments_from_shape_parameter_texts(
            shape_code=shape.code,
            summary=summary,
            entity_map=entity_map,
            production_length_cm=production_length_cm,
        )
        if parameter_texts is not None:
            return parameter_texts

        # Candidate payloads created by earlier versions may contain a
        # canonical DXF traversal.  It is only usable after all explicit
        # project dimensions have been tried, and only when the exact MASTER
        # formula reproduces the reviewed production length.
        canonical = link.get("canonical_segment_lengths_cm")
        if isinstance(canonical, list) and canonical:
            canonical_values = _canonicalize_for_master(
                shape.code,
                tuple(float(value) for value in canonical),
                points=summary.points,
            )
            canonical_turns = tuple(
                float(value) for value in (link.get("canonical_turn_angles_deg") or [])
            )
            try:
                parameters_from_segments(
                    shape.code,
                    canonical_values,
                    production_length_cm=production_length_cm,
                    turn_angles_deg=canonical_turns,
                )
            except MasterDimensionError:
                pass
            else:
                return canonical_values, canonical_turns

        raw_values = tuple(float(value) for value in summary.segment_lengths_cm)
        if raw_values:
            canonical_raw = _canonicalize_for_master(shape.code, raw_values, points=summary.points)
            if segments_match_total(shape.code, sum(canonical_raw),
                                    float(production_length_cm), tolerance=0.11):
                return canonical_raw, tuple(float(value) for value in summary.turn_angles_deg)

    if shape.code == "F001":
        return (float(production_length_cm),), ()
    if manual.get("segment_lengths_cm"):
        values = tuple(float(value) for value in manual["segment_lengths_cm"])
        canonical_values = _canonicalize_for_master(shape.code, values)
        if segments_match_total(shape.code, sum(canonical_values),
                                float(production_length_cm), tolerance=0.11):
            return canonical_values, tuple(float(value) for value in manual.get("turn_angles_deg") or [])
    fixed = [segment.fixed_length_cm for segment in sorted(shape.segments, key=lambda item: item.index)]
    if fixed and all(value is not None for value in fixed):
        return tuple(float(value) for value in fixed if value is not None), ()

    raise ExcelExportError(
        f"{link.get('annotation_text') or link.get('annotation_handle')}: "
        f"няма доказани сегментни размери за {shape.code}. "
        "Export-ът не наглася винкели или прави части по усмотрение."
    )


def _proven_auto_f007_canonical_segments(
    link: dict[str, Any],
    *,
    shape_code: str,
    production_length_cm: float,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    """Return F007 canonical sizes only for the exact AUTO contextual proof."""

    if str(shape_code or "").strip().upper() != "F007":
        return None
    if str(link.get("approved_by") or "").strip().upper() != "AUTO_BATCH_GATE":
        return None
    auto_gate = link.get("auto_gate")
    if not isinstance(auto_gate, dict) or auto_gate.get("passed") is not True:
        return None
    if (
        str(link.get("contextual_rule_code") or "").strip().upper()
        != "F007_ESOVE_CURVED_CENTERLINE"
    ):
        return None
    raw = link.get("canonical_segment_lengths_cm")
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    try:
        canonical = tuple(float(value) for value in raw)
        turns = tuple(
            float(value)
            for value in (link.get("canonical_turn_angles_deg") or [90.0, 90.0])
        )
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 for value in canonical):
        return None
    if abs(canonical[0] - canonical[2]) > 0.11:
        return None
    try:
        parameters_from_segments(
            "F007",
            canonical,
            production_length_cm=float(production_length_cm),
            turn_angles_deg=turns,
        )
    except (MasterDimensionError, TypeError, ValueError):
        return None
    return canonical, turns


def _approved_contextual_segments(
    link: dict[str, Any],
    shape_code: str,
    production_length_cm: float,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    if str(shape_code).upper() != "F004":
        return None
    evidence = "\n".join(str(value) for value in link.get("candidate_notes") or [])
    match = re.search(
        r"одобрено\s+проектно\s+правило.*?:\s*"
        r"(?P<main>\d+(?:[.,]\d+)?)\s*\+\s*"
        r"(?P<hook>\d+(?:[.,]\d+)?)\s*cm\s*,\s*винкел\s*90",
        evidence,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    main = float(match.group("main").replace(",", "."))
    hook = float(match.group("hook").replace(",", "."))
    if abs(main + hook - float(production_length_cm)) > 0.11:
        return None
    return (main, hook), (90.0,)


def _segments_from_nearby_dimension_texts(
    link: dict[str, Any],
    *,
    shape_code: str,
    summary,
    entity_map: dict[str, DxfEntityRecord],
    production_length_cm: float,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    code = str(shape_code or "").upper()
    raw_segments = list(zip(summary.points, summary.points[1:]))
    if code not in {"F003", "F004"} or len(raw_segments) not in {2, 3}:
        return None
    candidates: list[tuple[DxfEntityRecord, float, tuple[float, float], float]] = []
    for record in entity_map.values():
        if record.entity_type.upper() not in {"TEXT", "MTEXT"}:
            continue
        match = re.fullmatch(
            r"\s*(\d+(?:[.,]\d+)?)\s*(?:CM)?\s*",
            str(record.text or ""),
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        point = _entity_text_anchor(record)
        if point is None:
            continue
        value = float(match.group(1).replace(",", "."))
        nearest = min(_point_segment_distance(point, left, right)[0] for left, right in raw_segments)
        if nearest > 55.0:
            continue
        rotation = float(record.geometry.get("rotation") or 0.0) % 180.0
        candidates.append((record, value, point, rotation))
    if len(candidates) < len(raw_segments):
        return None

    solutions: list[tuple[float, tuple[float, ...]]] = []
    for assignment in itertools.permutations(candidates, len(raw_segments)):
        values = tuple(item[1] for item in assignment)
        if not segments_match_total(code, sum(values), float(production_length_cm),
                                    tolerance=0.11):
            continue
        score = 0.0
        valid = True
        for (left, right), (_record, _value, point, rotation) in zip(raw_segments, assignment):
            distance, projection = _point_segment_distance(point, left, right)
            segment_angle = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0])) % 180.0
            angle_difference = _orientation_difference(rotation, segment_angle)
            if distance > 55.0 or angle_difference > 25.0:
                valid = False
                break
            score += distance + angle_difference * 1.5
            if projection < 0.0:
                score += abs(projection) * 50.0
            elif projection > 1.0:
                score += abs(projection - 1.0) * 50.0
        if not valid:
            continue
        canonical = _canonicalize_for_master(code, values, points=summary.points)
        try:
            parameters_from_segments(
                code,
                canonical,
                production_length_cm=production_length_cm,
                turn_angles_deg=summary.turn_angles_deg,
            )
        except MasterDimensionError:
            continue
        solutions.append((score, canonical))
    if not solutions:
        return None
    solutions.sort(key=lambda item: item[0])
    best_score, best_values = solutions[0]
    # If another similarly scored solution produces different dimensions, the
    # association is ambiguous and must not be silently selected.
    for other_score, other_values in solutions[1:]:
        if other_score - best_score > 8.0:
            break
        if tuple(round(value, 3) for value in other_values) != tuple(round(value, 3) for value in best_values):
            return None
    return best_values, tuple(float(value) for value in summary.turn_angles_deg)


def _segments_from_shape_parameter_texts(
    *,
    shape_code: str,
    summary,
    entity_map: dict[str, DxfEntityRecord],
    production_length_cm: float,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    code = str(shape_code or "").upper()
    if code not in {"F005", "F006", "F008"}:
        return None
    raw_segments = list(zip(summary.points, summary.points[1:]))
    candidates: list[tuple[float, float, float]] = []  # value, rotation, distance
    for record in entity_map.values():
        if record.entity_type.upper() not in {"TEXT", "MTEXT"}:
            continue
        match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:CM)?\s*", str(record.text or ""), flags=re.IGNORECASE)
        if not match:
            continue
        point = _entity_text_anchor(record)
        if point is None:
            continue
        nearest = min(_point_segment_distance(point, left, right)[0] for left, right in raw_segments)
        if nearest > 60.0:
            continue
        candidates.append((float(match.group(1).replace(",", ".")), float(record.geometry.get("rotation") or 0.0) % 180.0, nearest))
    total = float(production_length_cm)
    solutions: list[tuple[float, tuple[float, ...]]] = []
    if code == "F005":
        for arm, bridge in itertools.permutations(candidates, 2):
            calculated = 2.0 * arm[0] + bridge[0]
            if abs(calculated - total) > 0.11:
                continue
            score = arm[2] + bridge[2]
            solutions.append((score, (arm[0], bridge[0], arm[0])))
    elif code == "F006":
        horizontals = [item for item in candidates if _orientation_difference(item[1], 0.0) <= 20.0]
        verticals = [item for item in candidates if _orientation_difference(item[1], 90.0) <= 20.0]
        diagonals = [item for item in candidates if min(_orientation_difference(item[1], 45.0), _orientation_difference(item[1], 135.0)) <= 20.0]
        for hook in diagonals:
            for width in horizontals:
                for height in verticals:
                    calculated = 2.0 * (hook[0] + width[0] + height[0])
                    difference = abs(calculated - total)
                    # Единен източник на истина за добавката за огъване
                    # (lion_rebar.engine.bend_allowance). Проектантът
                    # изписва страните, а общата дължина включва добавка за
                    # огъвките -- формулата дава по-малко. Приема се само
                    # недостиг; надвишаване остава грешка.
                    if not segments_match_total("F006", calculated, total,
                                                tolerance=2.1):
                        continue
                    score = hook[2] + width[2] + height[2] + difference * 20.0
                    solutions.append((score, (hook[0], width[0], height[0], width[0], height[0], hook[0])))
    elif code == "F008":
        for foot, height, top in itertools.permutations(candidates, 3):
            calculated = 2.0 * foot[0] + 2.0 * height[0] + top[0]
            if abs(calculated - total) > 0.11:
                continue
            score = foot[2] + height[2] + top[2]
            solutions.append((score, (foot[0], height[0], top[0], height[0], foot[0])))
    if not solutions:
        return None
    solutions.sort(key=lambda item: item[0])
    best_score, best = solutions[0]
    for score, values in solutions[1:]:
        if score - best_score > 8.0:
            break
        if tuple(round(v, 3) for v in values) != tuple(round(v, 3) for v in best):
            return None
    return best, tuple(float(value) for value in summary.turn_angles_deg)


def _canonicalize_for_master(
    shape_code: str,
    values: Sequence[float],
    *,
    points: Sequence[tuple[float, float]] | None = None,
) -> tuple[float, ...]:
    code = str(shape_code or "").upper()
    lengths = tuple(float(value) for value in values)
    if code == "F002" and len(lengths) == 3:
        outer_a, diagonal, outer_b = lengths
        return max(outer_a, outer_b), diagonal, min(outer_a, outer_b)
    if code == "F004" and len(lengths) == 2:
        main_index = 0 if lengths[0] >= lengths[1] else 1
        return lengths[main_index], lengths[1 - main_index]
    if code == "F003" and len(lengths) == 3:
        main_index = max(range(3), key=lambda index: lengths[index])
        if main_index == 1 and points is not None and len(points) >= 4:
            start_x = float(points[1][0])
            end_x = float(points[2][0])
            if start_x <= end_x:
                return lengths[0], lengths[1], lengths[2]
            return lengths[2], lengths[1], lengths[0]
        return lengths
    if code == "F005" and len(lengths) == 3:
        return lengths
    if code == "F006" and len(lengths) >= 6:
        # Locked F006 traversal: hook, side A, side B, side A, side B, hook.
        # The previous slice-based implementation accidentally treated the
        # first hook as a rectangle side and one rectangle side as the second
        # hook.  Rounded 10-object F006 candidates rely on this canonical path
        # because their DXF objects are not a single ordinary line chain.
        hook_start, side_a1, side_b1, side_a2, side_b2, hook_end = lengths[:6]
        if abs(hook_start - hook_end) > 0.11:
            raise F006AsymmetricHookError(hook_start, hook_end)
        if abs(side_a1 - side_a2) > 0.11 or abs(side_b1 - side_b2) > 0.11:
            raise ExcelExportError(
                "F006 правоъгълният контур няма две еднакви ширини и две еднакви височини."
            )
        width = max(side_a1, side_b1)
        height = min(side_a1, side_b1)
        return hook_start, width, height, width, height, hook_start
    return lengths


def _entity_text_anchor(record: DxfEntityRecord) -> tuple[float, float] | None:
    geometry = record.geometry or {}
    for key in ("visual_anchor", "insert", "text_midpoint", "defpoint"):
        value = geometry.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return float(value[0]), float(value[1])
    return None


def _point_segment_distance(
    point: tuple[float, float],
    left: tuple[float, float],
    right: tuple[float, float],
) -> tuple[float, float]:
    vx, vy = right[0] - left[0], right[1] - left[1]
    wx, wy = point[0] - left[0], point[1] - left[1]
    denominator = vx * vx + vy * vy
    if denominator <= 1e-12:
        return math.hypot(wx, wy), 0.0
    projection = (wx * vx + wy * vy) / denominator
    clamped = min(1.0, max(0.0, projection))
    nearest = (left[0] + clamped * vx, left[1] + clamped * vy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1]), projection


def _orientation_difference(left: float, right: float) -> float:
    difference = abs((float(left) - float(right)) % 180.0)
    return min(difference, 180.0 - difference)

def _manual_turns(link: dict[str, Any]) -> tuple[float, ...]:
    production = link.get("production_turn_angles_deg")
    if isinstance(production, (list, tuple)):
        return tuple(float(value) for value in production)
    manual = link.get("manual_assignment") if isinstance(link.get("manual_assignment"), dict) else {}
    return tuple(float(value) for value in manual.get("turn_angles_deg") or [])


def _trace_record(sheet_name: str, row: int, group: ExportGroup, frame: DrawingFrame, config: ExportFrameConfig, engine: EnginePackage) -> dict[str, Any]:
    representative = group.representative
    if representative.vertical_section_label_handle:
        row_granularity = "PRODUCTION_IDENTITY_WITHIN_VERTICAL_SECTION"
    elif representative.beam_label_handle:
        row_granularity = "ANNOTATION_WITHIN_BEAM"
    else:
        row_granularity = "PRODUCTION_IDENTITY_WITHIN_FRAME"
    return {
        "sheet": sheet_name,
        "row": row,
        "kind": "DRAWING",
        "source_group_id": _source_group_id(sheet_name, row),
        "row_granularity": row_granularity,
        "source_annotation_count": len(group.positions),
        "production_identity_key": group.key,
        "position_number": group.position_number,
        "frame_index": frame.index,
        "frame_title": frame.combined_title,
        "category_code": config.category_code,
        "beam_name": group.representative.beam_name,
        "beam_label_handle": group.representative.beam_label_handle,
        "beam_assignment_method": group.representative.beam_assignment_method,
        "beam_assignment_score": group.representative.beam_assignment_score,
        "beam_assignment_margin": group.representative.beam_assignment_margin,
        "vertical_section_title": group.representative.vertical_section_title,
        "vertical_section_label_handle": group.representative.vertical_section_label_handle,
        "vertical_section_divider_handle": group.representative.vertical_section_divider_handle,
        "vertical_section_kind": group.representative.vertical_section_kind,
        "vertical_section_order": group.representative.vertical_section_order,
        "vertical_assignment_method": group.representative.vertical_assignment_method,
        "vertical_assignment_score": group.representative.vertical_assignment_score,
        "vertical_assignment_margin": group.representative.vertical_assignment_margin,
        "shape_code": group.representative.shape_code,
        "shape_name": group.representative.shape_name,
        "shape_revision": group.representative.shape_revision,
        "master_topology_signature": (
            group.representative.master_topology_signature
        ),
        "diameter_mm": group.representative.diameter_mm,
        "production_length_cm": group.representative.production_length_cm,
        "segment_lengths_cm": list(group.representative.segment_lengths_cm),
        "turn_angles_deg": list(group.representative.turn_angles_deg),
        "production_length_source": group.representative.production_length_source,
        "count_formula_terms": group.counts,
        "count_total": sum(group.counts),
        "annotation_handles": [position.annotation_handle for position in group.positions],
        "geometry_handles": [handle for position in group.positions for handle in position.geometry_handles],
        "engine_version": engine.version,
        "engine_sha256": engine.content_sha256,
    }



def _resolve_shape_code(row: dict[str, Any], link: dict[str, Any]) -> str:
    direct = str(row.get("shape_code") or link.get("master_shape_code") or "").strip().upper()
    if direct:
        return direct
    evidence = list(link.get("candidate_notes") or []) + [
        link.get("candidate_confidence_before_approval"),
        link.get("candidate_basis"),
    ]
    for note in evidence:
        match = re.search(r"\b(F\d{3})\b", str(note or ""), flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    manual = link.get("manual_assignment") if isinstance(link.get("manual_assignment"), dict) else {}
    code = str(manual.get("shape_code") or manual.get("master_shape_code") or "").strip().upper()
    if code:
        return code
    segment_count = int(link.get("segment_count") or 0)
    if segment_count == 1:
        return "F001"
    return ""


def _resolve_active_shape_code(
    shape_code: str,
    *,
    all_shape_map: dict[str, ShapeDefinition],
) -> str:
    """Resolve a retired historical code to its active canonical replacement.

    New v0.3.8 projects already store canonical codes.  This migration path is
    for approved links created before canonicalization; it never guesses from
    geometry and follows only explicit ENGINE ``alias_of`` declarations.
    """

    current = str(shape_code or "").strip().upper()
    visited: set[str] = set()
    while current:
        if current in visited:
            return ""
        visited.add(current)
        shape = all_shape_map.get(current)
        if shape is None:
            return current
        if shape.active:
            return current
        target = str(
            shape.alias_of
            or shape.recognition.get("canonical_replacement")
            or ""
        ).strip().upper()
        if not target:
            return current
        current = target
    return ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExcelExportError(f"Не може да се прочете {path.name}: {exc}") from exc
    return value if isinstance(value, dict) else {}


def _entity_insert(record: DxfEntityRecord | None) -> tuple[float, float] | None:
    if record is None:
        return None
    value = record.geometry.get("insert") or record.geometry.get("visual_anchor")
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return None


def _positive_int(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _display_number(value: float):
    rounded = round(float(value), 1)
    return int(round(rounded)) if abs(rounded - round(rounded)) < 1e-9 else rounded


def _sheet_ref(sheet_name: str, cell: str) -> str:
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'!{cell}"


def _category_order(code: str) -> int:
    order = {"BOTTOM": 10, "BOTTOM_EXTRA": 20, "TOP": 30, "TOP_EXTRA": 40, "DETAILS": 50, "BEAMS": 60}
    return order.get(str(code), 999)


def _unique_sheet_names(values: Sequence[str]) -> list[str]:
    used: set[str] = set()
    result: list[str] = []
    for index, raw in enumerate(values, start=1):
        base = sanitize_sheet_name(raw, fallback=f"лист {index}")
        candidate = base
        suffix = 2
        while candidate.lower() in used:
            tail = f" {suffix}"
            candidate = sanitize_sheet_name(base[: 31 - len(tail)] + tail)
            suffix += 1
        used.add(candidate.lower())
        result.append(candidate)
    return result
