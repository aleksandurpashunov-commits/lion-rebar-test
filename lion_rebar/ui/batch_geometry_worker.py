from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QObject, Signal, Slot

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.export.auto_approval import (
    AutoApprovalSummary,
    auto_approve_gate_positions,
)
from lion_rebar.engine.models import EnginePackage, ShapeDefinition
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.models import ProjectModel
from lion_rebar.utils.app_paths import AppPaths


def _local_checkpoint_parent() -> Path | None:
    """Локална папка за междинния ``progress.json``.

    Прозорецът пише отчетите в ``LION_REBAR_CLOUD`` — на собственика това е
    Google Drive. Междинната точка се презаписва цяла на всеки 10 надписа и
    при 3 006 надписа сборът е около 16 GB през стриймващата файлова система
    (измерено на „т-1“, 21.09.2026). Крайните файлове остават в облака;
    местим само работната точка.

    При липсваща папка с данни се връща ``None`` и поведението е старото —
    анализът не бива да пада заради мястото на едно работно досие.
    """

    try:
        return AppPaths.default().root / "batch_progress"
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class BatchGeometryPipelineResult:
    report: object
    # Recovery.3: пакетният анализ вече не генерира AUTO_DRAFT Excel;
    # вместо това одобрява автоматично позициите през производствената порта.
    auto_approval: AutoApprovalSummary | None = None
    auto_approval_error: str = ""

    def __getattr__(self, name: str):
        return getattr(self.report, name)


class BatchGeometryWorker(QObject):
    """Runs the expensive report generation outside the GUI thread."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(
        self,
        *,
        entities: list[DxfEntityRecord],
        output_parent: Path,
        project_name: str,
        approved_links: dict[str, dict],
        shape_definitions: Iterable[ShapeDefinition],
        control_tests: Iterable[Mapping[str, Any]],
        policy: RecognitionPolicy | dict | None,
        engine_metadata: Mapping[str, Any] | None = None,
        project: ProjectModel | None = None,
        engine: EnginePackage | None = None,
        source_dxf_path: Path | None = None,
        data_identity: DataIdentity | None = None,
    ) -> None:
        super().__init__()
        self._entities = entities
        self._output_parent = Path(output_parent)
        self._project_name = project_name
        self._approved_links = dict(approved_links)
        self._shape_definitions = tuple(shape_definitions)
        self._control_tests = tuple(control_tests)
        self._policy = policy
        self._engine_metadata = dict(engine_metadata or {})
        self._project = project
        self._engine = engine
        self._source_dxf_path = Path(source_dxf_path) if source_dxf_path else None
        self._data_identity = data_identity
        # Рамките, начертани преди анализа. Четат се ТУК, а не вътре в
        # ``build_batch_geometry_report``: анализът остава чиста функция на
        # входа си, без да ходи до файловата система сам.
        self._operator_areas = self._load_operator_areas()
        self._cancel_event = threading.Event()

    def _load_operator_areas(self) -> tuple:
        from lion_rebar.export.operator_areas import load_operator_areas

        if self._project is None:
            return ()
        try:
            return load_operator_areas(Path(self._project.root), self._data_identity)
        except Exception:
            # Анализът не бива да пада заради повредено помощно досие --
            # без областите той просто работи както преди тях.
            return ()

    def request_cancel(self) -> None:
        """Thread-safe cancellation request callable directly from the GUI."""
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = build_batch_geometry_report(
                self._entities,
                self._output_parent,
                project_name=self._project_name,
                approved_links=self._approved_links,
                shape_definitions=self._shape_definitions,
                control_tests=self._control_tests,
                policy=self._policy,
                engine_metadata=self._engine_metadata,
                data_identity=self._data_identity,
                progress_callback=self._emit_progress,
                cancel_check=self._cancel_event.is_set,
                checkpoint_every=10,
                skip_approved=True,
                operator_areas=self._operator_areas,
                checkpoint_parent=_local_checkpoint_parent(),
            )
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())
            return
        auto_approval = None
        auto_approval_error = ""
        if not result.cancelled and self._project is not None and self._engine is not None:
            try:
                self._emit_progress(
                    result.processed_annotations,
                    result.total_annotations,
                    "Автоматично одобрение по производствената порта...",
                )
                auto_approval = auto_approve_gate_positions(
                    Path(self._project.root),
                    report_path=Path(result.json_path),
                    entities=self._entities,
                    engine=self._engine,
                    source_dxf_path=self._source_dxf_path,
                    data_identity=self._data_identity,
                )
                if auto_approval.failed:
                    auto_approval_error = auto_approval.error
            except Exception as exc:  # the batch report remains a valid deliverable
                auto_approval_error = f"{type(exc).__name__}: {exc}"
            try:
                # Automatic "silent wrong" provenance metric (т.15) — replaces
                # the manual, one-off audit that first found the AUTO_BATCH_GATE
                # count gap. Best-effort: never blocks the batch pipeline.
                from lion_rebar.export.silent_wrong_metrics import (
                    write_silent_wrong_metrics,
                )

                write_silent_wrong_metrics(Path(self._project.root), self._engine)
            except Exception:  # noqa: BLE001 - diagnostic only
                pass
        self.finished.emit(
            BatchGeometryPipelineResult(
                report=result,
                auto_approval=auto_approval,
                auto_approval_error=auto_approval_error,
            )
        )

    def _emit_progress(self, current: int, total: int, label: str) -> None:
        self.progress.emit(int(current), int(total), str(label))
