from __future__ import annotations

import json
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.bundle import replace_export_bundle
from lion_rebar.export.dxf_position_export import (
    DxfPositionExportResult,
    generate_dxf_position_export,
)
from lion_rebar.export.export_paths import build_paired_export_paths
from lion_rebar.export.excel_export import (
    ExcelExportResult,
    ExportFrameConfig,
    ExportPosition,
    generate_excel_export,
)
from lion_rebar.export.frame_detection import DrawingFrame
from lion_rebar.config.constants import APP_VERSION
from lion_rebar.export.final_export_manifest import sha256_file
from lion_rebar.project.models import ProjectModel
from lion_rebar.project.data_identity import current_identity
from lion_rebar.project.final_control_lifecycle import assert_export_allowed
from lion_rebar.export.final_export_manifest import (
    build_final_export_manifest,
    verify_final_export_manifest,
    write_final_export_manifest,
)
from lion_rebar.export.frame_decisions import load_frame_decisions
from lion_rebar.export.frame_detection import (
    apply_operator_frame_decisions,
    frame_provenance_rows,
    unresolved_frames,
)


@dataclass(frozen=True)
class PairedExportResult:
    excel_result: ExcelExportResult
    dxf_result: DxfPositionExportResult
    excel_path: Path
    excel_trace_path: Path
    dxf_path: Path
    dxf_trace_path: Path
    manifest_path: Path | None = None
    production_verified: bool = False
    verification_problems: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """``PRODUCTION VERIFIED`` или ``NOT PRODUCTION VERIFIED``.

        Файловете може да са физически на диска и в двата случая; статусът
        казва дали export-ът е производствено доказан.
        """

        return "PRODUCTION VERIFIED" if self.production_verified else "NOT PRODUCTION VERIFIED"


class ExportWorker(QObject):
    """Generate the paired Excel + DXF export outside the GUI thread.

    Temporary files are intentionally created directly beside the final files.
    ``replace_export_bundle`` uses atomic ``os.replace`` operations and requires
    every temporary and final file to have the same parent directory.
    """

    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str, str, str)

    def __init__(
        self,
        *,
        project: ProjectModel,
        engine,
        entities: Sequence[DxfEntityRecord],
        frames: Sequence[DrawingFrame],
        positions: Sequence[ExportPosition],
        configs: Sequence[ExportFrameConfig],
        excel_path: Path,
    ) -> None:
        super().__init__()
        self._project = project
        self._engine = engine
        self._entities = list(entities)
        self._frames = list(frames)
        self._positions = list(positions)
        self._configs = list(configs)
        self._excel_path = Path(excel_path)
        self._manifest_error = ""

    @Slot()
    def run(self) -> None:
        temporary_files: list[Path] = []
        try:
            # P0-5: export НЕ може да ползва обезсилен Final Control.
            blockers = assert_export_allowed(Path(self._project.root))
            if blockers:
                raise RuntimeError("; ".join(blockers))
            self.progress.emit(5, "Подготвям временните файлове...")
            excel_path = self._excel_path
            excel_path.parent.mkdir(parents=True, exist_ok=True)
            paths = build_paired_export_paths(excel_path, uuid.uuid4().hex)
            temporary_files.extend(paths.temporary_files)

            self.progress.emit(15, "Генерирам Excel спецификацията...")
            excel_result = generate_excel_export(
                self._project,
                self._engine,
                self._frames,
                self._positions,
                self._configs,
                paths.temporary_excel,
                require_frozen_config=True,
            )

            self.progress.emit(60, "Добавям кръглите позиционни блокове в DXF...")
            dxf_result = generate_dxf_position_export(
                self._project,
                self._entities,
                self._positions,
                excel_result.position_numbers_by_annotation,
                paths.temporary_dxf,
            )

            self.progress.emit(88, "Проверявам и записвам четирите крайни файла...")
            replace_export_bundle(
                (
                    (excel_result.workbook_path, paths.excel),
                    (excel_result.traceability_path, paths.excel_trace),
                    (dxf_result.dxf_path, paths.dxf),
                    (dxf_result.traceability_path, paths.dxf_trace),
                )
            )
            self.progress.emit(95, "Записвам и проверявам FINAL_EXPORT_MANIFEST.json...")
            manifest_path, problems = self._write_and_verify_manifest(paths)
            verified = manifest_path is not None and not problems
            if verified:
                _record_verified_export(
                    Path(self._project.root), manifest_path,
                    final_control_id=current_identity(self._project).analysis_run_id,
                )
                self.progress.emit(100, "Excel и DXF са готови и производствено проверени.")
            else:
                # Файловете остават на диска, но операцията НЕ е успешен
                # production export. Порта, не козметика.
                self.progress.emit(
                    100,
                    "NOT PRODUCTION VERIFIED: файловете са записани, но "
                    "производствената проверка не мина.",
                )
            self.finished.emit(
                PairedExportResult(
                    excel_result=excel_result,
                    dxf_result=dxf_result,
                    excel_path=paths.excel,
                    excel_trace_path=paths.excel_trace,
                    dxf_path=paths.dxf,
                    dxf_trace_path=paths.dxf_trace,
                    manifest_path=manifest_path,
                    production_verified=verified,
                    verification_problems=tuple(problems),
                )
            )
        except Exception as exc:
            self.failed.emit(type(exc).__name__, str(exc), traceback.format_exc())
        finally:
            for path in temporary_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _write_and_verify_manifest(self, paths) -> tuple[Path | None, list[str]]:
        """P0-7: манифестът е ЧАСТ ОТ production verification gate.

        Ако създаването или проверката се провали, Excel и DXF остават
        физически на диска (те вече са записани атомарно), но операцията
        завършва като ``NOT PRODUCTION VERIFIED`` — не като успешен
        production export.
        """

        self._manifest_error = ""
        manifest_path = self._write_export_manifest(paths)
        if manifest_path is None:
            detail = self._manifest_error or "неизвестна причина"
            return None, [f"MANIFEST_NOT_CREATED: производствената проверка "
                          f"не може да бъде извършена ({detail})"]
        try:
            problems = verify_final_export_manifest(manifest_path)
        except Exception as exc:  # noqa: BLE001
            return manifest_path, [f"MANIFEST_VERIFICATION_FAILED: {exc}"]
        return manifest_path, list(problems)

    def _write_export_manifest(self, paths) -> Path | None:
        try:
            root = Path(self._project.root)
            identity = current_identity(self._project)
            working = root / "working"
            ledger = working / "final_integrity_ledger.csv"
            freeze = working / "final_control_freeze.json"
            # Одит RC1, находка F1: операторските решения за UNKNOWN рамки
            # реално се прилагат по export пътя, а не стоят като backend модул.
            frames = apply_operator_frame_decisions(
                list(self._frames),
                load_frame_decisions(root, identity),
            )
            manifest = build_final_export_manifest(
                app_version=APP_VERSION,
                engine_id=str(getattr(self._engine, "engine_id", "")),
                engine_version=str(getattr(self._engine, "version", "")),
                engine_sha256=str(getattr(self._engine, "content_sha256", "")),
                matcher_sha256=_matcher_sha256(),
                engine_bundle_sha256=_engine_bundle_sha256(self._project),
                project_id=identity.project_id,
                source_sha256=identity.source_sha256,
                source_generation_id=identity.source_generation_id,
                analysis_run_id=identity.analysis_run_id,
                final_control_id=identity.analysis_run_id,
                final_control_sha256=(
                    sha256_file(freeze) if freeze.is_file() else ""
                ),
                final_ledger_path=ledger if ledger.is_file() else None,
                excel_path=paths.excel,
                final_dxf_path=paths.dxf,
                project_root=root,
                export_config_path=(
                    working / "final_control_export_config.json"
                ),
                excel_trace_path=paths.excel_trace,
                dxf_trace_path=paths.dxf_trace,
                final_control_path=freeze,
                ledger_row_count=_ledger_counts(ledger)["rows"],
                excel_row_count=len(self._positions),
                engineer_resolved_without_geometry_count=(
                    _ledger_counts(ledger)["without_geometry"]
                    or sum(1 for position in self._positions
                           if not getattr(position, "geometry_handles", None))
                ),
                # Одит RC1, находка M1: броячът идва от Final Ledger, а не е
                # фиксиран 0. Манифест, който винаги твърди "нула изключени
                # позиции", е по-лош от липсващ манифест.
                non_production_position_count=_ledger_counts(ledger)["non_production"],
                frame_provenance=frame_provenance_rows(frames),
                unresolved_frame_count=len(unresolved_frames(frames)),
            )
            return write_final_export_manifest(paths.excel.parent, manifest)
        except Exception as exc:  # noqa: BLE001
            # Windows приемане rc4: манифестът липсваше БЕЗ следа, защото
            # изключението се гълташе. Мълчалив провал на production
            # verification gate е по-лош от самия провал — причината се
            # запазва и се изнася като проблем.
            self._manifest_error = f"{type(exc).__name__}: {exc}"
            return None


def _record_verified_export(
    project_root: Path, manifest_path: Path, *, final_control_id: str,
) -> None:
    """Записва локално доказателство, използвано от безопасния Undo gate."""

    journal = Path(project_root) / "working" / "production_export_history.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema": "lion-rebar-production-export-history/v1",
        "event": "PRODUCTION_VERIFIED",
        "at_unix_ns": time.time_ns(),
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "final_control_id": str(final_control_id or ""),
    }
    with open(journal, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _engine_bundle_sha256(project) -> str:
    """SHA-256 на закачения .lionengine файл, ако е достъпен."""

    try:
        pinned = getattr(project, "engine_bundle_path", "") or ""
        if pinned and Path(pinned).is_file():
            return sha256_file(Path(pinned))
        root = Path(__file__).resolve().parents[2] / "engine_updates"
        version = str(getattr(project, "engine_version", "") or "")
        for candidate in sorted(root.glob(f"*{version}*.lionengine")):
            return sha256_file(candidate)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _matcher_sha256() -> str:
    try:
        import hashlib
        from lion_rebar.dxf import shape_recognition

        return hashlib.sha256(
            Path(shape_recognition.__file__).read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


#: Статуси в Final Ledger, които означават изключена от производство позиция.
_NON_PRODUCTION_LEDGER_STATUSES = {"NON_PRODUCTION_POSITION", "NON_PRODUCTION", "EXCLUDED"}


def _ledger_counts(path: Path) -> dict:
    """Реални броячи от Final Ledger — единственият production source of truth.

    Връща ``rows``, ``non_production`` и ``without_geometry``. При липсващ или
    нечетим Ledger връща нули; извикващият вече е блокиран от
    ``assert_export_allowed`` в такъв случай.
    """

    counts = {"rows": 0, "non_production": 0, "without_geometry": 0}
    if not path.is_file():
        return counts
    try:
        import csv as _csv

        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in _csv.DictReader(handle):
                counts["rows"] += 1
                status = str(
                    row.get("final_status") or row.get("production_status") or ""
                ).strip().upper()
                reason = str(row.get("decision_reason_code") or "").strip().upper()
                if status in _NON_PRODUCTION_LEDGER_STATUSES or \
                        reason in _NON_PRODUCTION_LEDGER_STATUSES:
                    counts["non_production"] += 1
                handles = str(row.get("geometry_handles_count") or "").strip()
                provenance = str(row.get("geometry_provenance") or "").strip().upper()
                if handles == "0" or provenance.startswith("NONE"):
                    counts["without_geometry"] += 1
    except (OSError, UnicodeDecodeError):
        return {"rows": 0, "non_production": 0, "without_geometry": 0}
    return counts


def _csv_row_count(path: Path) -> int:
    return _ledger_counts(path)["rows"]
