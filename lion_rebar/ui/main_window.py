from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import string
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QGuiApplication, QKeySequence, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
    QDialog,
    QDockWidget,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSizePolicy,
    QToolBar,
)

from lion_rebar.project.engine_pin import describe_engine_pin_mismatch
from lion_rebar.project.final_control_lifecycle import (
    assert_export_allowed,
    invalidate_final_control,
)
from lion_rebar.project.data_identity import current_identity
from lion_rebar.project.review_recovery import (
    ACTION_BLOCKED,
    ReviewRecoveryDecision,
    decide_review_entry,
)
from lion_rebar.config.constants import APP_EDITION, APP_NAME, APP_VERSION
from lion_rebar.diagnostics import (
    append_feedback_event,
    create_diagnostic_bundle,
)
from lion_rebar.dxf.batch_geometry_report import count_batch_annotations
from lion_rebar.dxf.geometry_candidates import (
    GeometryCandidate,
    GeometryCandidateAnalysis,
    build_geometry_candidate_context,
    find_geometry_candidates,
)
from lion_rebar.dxf.importer import DxfImportError, DxfImporter
from lion_rebar.dxf.inventory import build_rebar_inventory
from lion_rebar.dxf.models import DxfImportResult
from lion_rebar.dxf.position_key_resolver import build_position_key_legend_index
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.rebar_analyzer import analyze_rebar_annotation
from lion_rebar.dxf.text_parser import looks_like_rebar_text, parse_rebar_text
from lion_rebar.engine.project_shape_candidate import (
    build_project_shape_candidate,
    write_project_shape_candidate,
)
from lion_rebar.review_center_model import (
    ReviewReportError,
    REJECTED_REVIEW_DECISIONS,
    bulk_eligible,
    f006_bulk_eligible,
    final_confirmation_eligible,
    geometry_bulk_eligible,
    geometry_analysis_from_report_row,
    is_missing_master_row,
    is_position_key_legend_reference,
    load_review_report,
    missing_master_reason,
)
from lion_rebar.review.bulk_member_verdicts import bulk_member_verdicts
from lion_rebar.engine.repository import EngineRepository, EngineRepositoryError
from lion_rebar.engine.shape_index import EngineShapeIndex
from lion_rebar.engineer_decision import (
    GeometryTakeoverRequired,
    save_engineer_decision,
)
from lion_rebar.project.data_identity import (
    begin_analysis_run,
    current_identity,
    mismatch_reason,
    stamp_payload,
)
from lion_rebar.project.manager import ProjectError, ProjectManager
from lion_rebar.project.integrity import reconcile_project_integrity
from lion_rebar.export.excel_export import ExcelExportError, load_export_context, load_pinned_engine
from lion_rebar.manual_assignment import (
    SUPPORTED_MANUAL_GEOMETRY,
    ManualGeometryError,
    summarize_selected_geometry,
    validate_manual_geometry_records,
)
from lion_rebar.review.decision_suggestions import (
    DecisionSuggestion,
    DecisionSuggestionIndex,
)
from lion_rebar.project.models import ProjectModel
from lion_rebar.session import SessionService
from lion_rebar.session.decision_contract import (
    append_project_decision_event,
    flush_project_decision_outbox,
    is_human_decision_event,
)
from lion_rebar.session.layer_classifier import (
    classify_layers,
    classification_by_layer,
    filter_analysis_entities,
    high_confidence_noise_layers,
)
from lion_rebar.ui.drawing_view import (
    DrawingView,
    SceneBuildResult,
    build_scene,
    centerline_path_for_record,
)
from lion_rebar.ui.engine_studio import EngineStudio
from lion_rebar.ui.export_excel_dialog import ExportExcelDialog
from lion_rebar.ui.export_worker import ExportWorker, PairedExportResult
from lion_rebar.ui.entity_panel import EntityPanel
from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog
from lion_rebar.ui.geometry_candidates_dialog import GeometryCandidatesDialog
from lion_rebar.project.convention_profile_decisions import (
    policy_with_confirmed_profile,
)
from lion_rebar.ui.import_report import ImportReportDialog
from lion_rebar.ui.layer_panel import LayerPanel
from lion_rebar.ui.manual_form_assignment_dialog import ManualFormAssignmentDialog
from lion_rebar.ui.rebar_inventory_dialog import RebarInventoryDialog
from lion_rebar.ui.review_center import ReviewCenterDialog, ReviewStateError
from lion_rebar.ui.batch_geometry_worker import BatchGeometryWorker
from lion_rebar.session.operator_identity import (
    НЕЗАЯВЕН,
    OperatorIdentityError,
    claimed_operator,
    declare_operator,
)
from lion_rebar.utils.app_paths import AppPaths
from lion_rebar.utils.files import atomic_write_text, safe_filename
from lion_rebar.utils.process_lock import exclusive_file_lock


def _locked_project_file(relative: str):
    """Методът държи изключително заключване върху този проектен файл.

    Шестте четене-промяна-запис в този прозорец минават през
    ``atomic_write_text``: файлът никога не е наполовина записан. Това не пази
    РЕШЕНИЕТО — два процеса, прочели едно и също състояние, записват две пълни,
    но взаимно изключващи се версии и една от двете изчезва (§ 7.4 от заданието
    от 30.09.2026).

    Заключването се взема за целия метод, защото четенето и записът са в двата
    му края. Без отворен проект пътят не е известен и методът върви както преди
    — той и без това вдига грешка за липсващия проект.
    """

    def обвий(func):
        @functools.wraps(func)
        def вътре(self, *args, **kwargs):
            project = getattr(self, "project", None)
            if project is None:
                return func(self, *args, **kwargs)
            with exclusive_file_lock(Path(project.root) / relative):
                return func(self, *args, **kwargs)

        return вътре

    return обвий


def _blocking_integrity_issue_text(result, lifecycle_blockers=()) -> str:
    """Return the real non-position blockers for operator dialogs."""

    entries: list[str] = []
    for issue in getattr(result, "issues", ()) or ():
        if str(getattr(issue, "severity", "")).upper() != "ERROR":
            continue
        code = str(getattr(issue, "code", "") or "TECHNICAL_BLOCKER")
        message = str(getattr(issue, "message", "") or "Неописана техническа грешка.")
        entries.append(f"• {code}: {message}")
    for blocker in lifecycle_blockers or ():
        entries.append(f"• EXPORT_LIFECYCLE: {blocker}")
    if not entries:
        return ""
    shown = entries[:8]
    if len(entries) > len(shown):
        shown.append(f"• … и още {len(entries) - len(shown)}")
    return "Блокиращи технически причини:\n" + "\n".join(shown) + "\n"


def _unresolved_beam_handles(result) -> list[str]:
    """Позициите, чиято греда Финалният контрол не можа да докаже."""

    handles: list[str] = []
    for issue in getattr(result, "issues", ()) or ():
        if str(getattr(issue, "code", "") or "").upper() != "BEAM_ASSIGNMENT_NOT_UNIQUE":
            continue
        for handle in getattr(issue, "annotation_handles", ()) or ():
            text = str(handle or "")
            if text and text not in handles:
                handles.append(text)
    return handles


def _has_frame_resolution_issue(result) -> bool:
    for issue in getattr(result, "issues", ()) or ():
        code = str(getattr(issue, "code", "") or "").upper()
        message = str(getattr(issue, "message", "") or "").casefold()
        if code == "FRAME_CATEGORY_UNRESOLVED":
            return True
        if code == "FINAL_CONTROL_FREEZE_FAILED" and "неопределена категория" in message:
            return True
    return False


def _has_position_blockers(result) -> bool:
    return any(
        (
            getattr(result, "unresolved_positions", 0) or 0,
            getattr(result, "safe_confirmation_pending", 0) or 0,
            getattr(result, "engineering_decisions_pending", 0) or 0,
            getattr(result, "explicit_exclusions", 0) or 0,
            getattr(result, "intrinsic_missing_master", 0) or 0,
        )
    )


def _dimension_number(value: object) -> str:
    number = float(value)
    return f"{number:g}"


def _shape_dimension_overlay_text(
    shape_code: object,
    segment_lengths_cm: object,
    total_length_cm: object = None,
) -> str | None:
    """Build a compact, non-authoritative viewer label for F006/F007."""

    code = str(shape_code or "").strip().upper()
    if code not in {"F006", "F007"}:
        return None
    try:
        segments = tuple(
            float(value)
            for value in (segment_lengths_cm or ())
            if float(value) > 0
        )
    except (TypeError, ValueError):
        return None
    if not segments:
        return None
    try:
        total = float(total_length_cm)
    except (TypeError, ValueError):
        total = None
    suffix = f" | L={_dimension_number(total)} cm" if total and total > 0 else ""
    if code == "F006":
        hook = width = height = None
        if len(segments) == 3:
            # Locked MASTER parameter order: hook, side A, side B.
            hook, side_a, side_b = segments
            width, height = max(side_a, side_b), min(side_a, side_b)
        elif len(segments) >= 6:
            # Production traversal: hook, side A, side B, side A, side B, hook.
            hook_start, side_a1, side_b1, side_a2, side_b2, hook_end = segments[:6]
            pair_tolerance_cm = 0.11
            if (
                abs(hook_start - hook_end) <= pair_tolerance_cm
                and abs(side_a1 - side_a2) <= pair_tolerance_cm
                and abs(side_b1 - side_b2) <= pair_tolerance_cm
            ):
                hook = (hook_start + hook_end) / 2.0
                side_a = (side_a1 + side_a2) / 2.0
                side_b = (side_b1 + side_b2) / 2.0
                width, height = max(side_a, side_b), min(side_a, side_b)
        if hook is not None and width is not None and height is not None:
            return (
                f"F006 | {_dimension_number(width)} × {_dimension_number(height)} cm"
                f" | куки {_dimension_number(hook)} cm{suffix}"
            )
    if code == "F007" and len(segments) == 3:
        arm_start, middle, arm_end = segments
        return (
            f"F007 | рамена {_dimension_number(arm_start)} / "
            f"{_dimension_number(arm_end)} cm | среда {_dimension_number(middle)} cm"
            f"{suffix}"
        )
    values = " + ".join(_dimension_number(value) for value in segments)
    return f"{code} | сегменти {values} cm{suffix}"


def _shape_dimension_overlay_input(
    shape_code: object,
    production_segments: object = None,
    canonical_segments: object = None,
    suggested_segments: object = None,
) -> tuple[object, str | None]:
    """Choose the strongest viewer dimensions and normalize their known schema."""

    code = str(shape_code or "").strip().upper()
    for source, raw in (
        ("production", production_segments),
        ("canonical", canonical_segments),
        ("suggested", suggested_segments),
    ):
        if not raw:
            continue
        values: object = raw
        if source == "suggested" and code == "F006":
            try:
                suggestion = tuple(float(value) for value in raw)
            except (TypeError, ValueError):
                suggestion = ()
            if len(suggestion) == 3:
                # Resolver suggestion schema: width, height, hook.
                width, height, hook = suggestion
                values = (hook, width, height)
        return (
            values,
            "ПРЕДЛОЖЕНИЕ — потвърди" if source == "suggested" else None,
        )
    return (), None


def _manual_geometry_exceeds_declared_length(
    measured_length_cm: object,
    declared_length_cm: object,
) -> bool:
    """Reject obvious outline/perimeter picks while allowing normal DXF rounding."""

    try:
        measured = float(measured_length_cm)
        declared = float(declared_length_cm)
    except (TypeError, ValueError):
        return False
    if measured <= 0 or declared <= 0:
        return False
    return measured > declared + max(5.0, declared * 0.05)


@dataclass(frozen=True)
class _EngineCorrection:
    """Една изрично разрешена семантична поправка на MASTER engine.

    ``review_shape_codes`` са кодовете, чиято САМОЛИЧНОСТ се променя с тази
    версия. Старите одобрения точно с тях се връщат за нов преглед, защото
    решението на инженера е било взето върху друга форма; всичко останало се
    запазва. Проект с версия извън ``from_versions`` НЕ се мигрира.
    """

    to_version: str
    from_versions: frozenset[str]
    review_shape_codes: frozenset[str]
    reason: str
    message: str


#: Текстът за 0.3.15 стои на едно място: и двата пътя към нея го ползват.
_OPEN_STIRRUP_IDENTITY_MESSAGE = (
    "Отвореното петсегментно стреме вече излиза като F009 „ОТВОРЕНА ФОРМА С "
    "ДВЕ РАМЕНА“ — формата, чийто одобрен контролен тест е точно този прът "
    "(8+79+15+79+8 = 189 cm) и която има свой прегледан чертеж. F010 "
    "(„НЕСТАНДАРТНА – ДОЛНА СКАРА“, чертеж през общия резервен рисувач) сочи "
    "към нея."
)

#: Историята на семантичните поправки, всяка с точните версии и текст.
_KNOWN_ENGINE_CORRECTIONS: tuple[_EngineCorrection, ...] = (
    _EngineCorrection(
        to_version="0.3.14",
        from_versions=frozenset({"0.3.12", "0.3.13"}),
        review_shape_codes=frozenset({"F003", "F005"}),
        reason="F005 independent arms and two-sided U relation correction",
        message=(
            "F005 вече приема три независими рамена, а F003/F005 се разделят "
            "по двете външни рамена преди анализа на котите."
        ),
    ),
    _EngineCorrection(
        to_version="0.3.15",
        from_versions=frozenset({"0.3.14"}),
        review_shape_codes=frozenset({"F010"}),
        reason="F009 open five-segment stirrup identity restored; F010 aliased",
        message=_OPEN_STIRRUP_IDENTITY_MESSAGE,
    ),
    # Проект, прескочил 0.3.14: получава ДВЕТЕ поправки наведнъж, затова за
    # преглед се връщат и формите на по-старата. Иначе решение, взето върху
    # старата самоличност на F003/F005, би минало незабелязано.
    _EngineCorrection(
        to_version="0.3.15",
        from_versions=frozenset({"0.3.12", "0.3.13"}),
        review_shape_codes=frozenset({"F003", "F005", "F010"}),
        reason=(
            "F005 independent arms, two-sided U relation and F009 open "
            "five-segment stirrup identity"
        ),
        message=(
            "Проектът получава две поправки наведнъж. F005 вече приема три "
            "независими рамена, а F003/F005 се разделят по двете външни "
            "рамена преди анализа на котите. " + _OPEN_STIRRUP_IDENTITY_MESSAGE
        ),
    ),
)
def operator_area_preview(
    entities,
    *,
    box,
    bent_around=(),
    kind,
    title_handle,
    policy=None,
):
    """Какво показва рамката с два клика, преди да се запише.

    Връща ``(брой_позиции, [(надпис, сегашна_греда), …])``.

    БРОЯТ Е НА ПОЗИЦИИ, НЕ НА ОБЕКТИ. Дотук се обикаляха всички DXF обекти в
    правоъгълника и въпросът за гредата се задаваше на всеки от тях — включително
    на ЗАГЛАВИЕТО на самата греда, която операторът огражда. ``_assign_beam_label``
    е направена за позиции (детайли вдясно и под заглавието си); подадеш ли ѝ
    точката на заглавието, тя връща гредата на реда НАД него.

    Измерено на „тест-006" (2026-09-24): 16 от 31 рамки искаха обосновка, а
    всичките 42 „премествани" бяха заглавия на греди и бележки „стр. 59N8/s=20".
    Нито една позиция. Сверка на автоматиката срещу същите 31 рамки: 146 от 146
    съвпадения, тоест нямаше какво да се мести. Целият път, пуснат два пъти —
    с рамките и без тях — дава един и същ ledger до клетка и една и съща греда
    на всяка от 139-те позиции; различават се само проследимите полета
    ``beam_assignment_method`` и score/margin.

    Затова тук се броят и се питат САМО истинските армировъчни надписи — същото
    множество, което брои ``count_batch_annotations`` при анализа.

    Старият филтър по одобрени връзки отпада: той пазеше донякъде, но само АКО
    вече има одобрени. При чертане на рамки преди анализа одобрени няма и
    филтърът не правеше нищо — точно тогава дефектът хапеше.
    """

    from lion_rebar.export.operator_area_members import (
        ПРИЕТИ,
        ПРЕМЕСТЕН,
        area_member_verdicts,
    )

    try:
        членове = area_member_verdicts(
            entities=list(entities),
            policy=policy,
            box=tuple(box),
            bent_around=tuple(bent_around),
            kind=kind,
            title_handle=title_handle,
        )
    except Exception:  # noqa: BLE001 - прегледът не бива да чупи записа
        return 0, []
    обхванати = sum(1 for член in членове if член.verdict in ПРИЕТИ)
    премествани = [
        (член.text or член.handle, член.from_title or "")
        for член in членове
        if член.verdict == ПРЕМЕСТЕН
    ]
    return обхванати, премествани





class _UchebenCikal(QObject):
    """Учебният цикъл в отделна нишка — за да не блокира прозореца.

    Нищо от Qt не влиза в ``learning/`` — тук е единственото място, където
    двете се срещат.
    """

    done = Signal(object)

    def __init__(self, status, bridge, entities, drawing,
                 source_sha256: str = "") -> None:
        super().__init__()
        self._status = status
        self._bridge = bridge
        self._entities = entities
        self._drawing = drawing
        self._source_sha256 = source_sha256

    def run(self) -> None:
        from lion_rebar.learning.integration import run_cycle_after_analysis

        състояние = run_cycle_after_analysis(
            status=self._status, bridge_report=self._bridge,
            entities=self._entities, drawing=self._drawing,
            family=self._drawing, source_sha256=self._source_sha256,
        )
        self.done.emit(състояние)


class MainWindow(QMainWindow):
    def __init__(
        self,
        engine_repository: EngineRepository,
        logger: logging.Logger,
        *,
        session_service: SessionService | None = None,
    ):
        super().__init__()
        self.engine_repository = engine_repository
        self.logger = logger
        self.project_manager = ProjectManager()
        self.session_service = session_service or SessionService(AppPaths.default())
        # Заявеното име на оператора. Чете се веднъж при старта; нечетими
        # настройки НЕ спират програмата и НЕ дават име.
        try:
            self.operator_name = claimed_operator(self._operator_settings_file())
        except Exception as exc:  # noqa: BLE001 - помощен път
            self.logger.warning("Името на оператора не беше прочетено: %s", exc)
            self.operator_name = ""
        self.project: ProjectModel | None = None
        self.import_result: DxfImportResult | None = None
        self.scene_result: SceneBuildResult | None = None
        self._geometry_dialog: GeometryCandidatesDialog | None = None
        self._geometry_highlights: list[object] = []
        self._batch_thread: QThread | None = None
        self._batch_worker: BatchGeometryWorker | None = None
        self._batch_progress: QProgressDialog | None = None
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None
        self._export_progress: QProgressDialog | None = None
        self._batch_using_cloud = False
        self._batch_archived_source: Path | None = None
        self._last_batch_report_path: Path | None = None
        self._review_center: ReviewCenterDialog | None = None
        self._review_dock: QDockWidget | None = None
        self._review_center_cleanup_active = False
        self._pending_review_decision: ReviewRecoveryDecision | None = None
        self._review_quick_mode_confirmed = False
        self._diagnostic_thread: threading.Thread | None = None
        self._diagnostic_lock = threading.Lock()
        self._manual_selection_row: dict | None = None
        self._manual_selection_scene = None
        self._manual_selection_highlights: list[object] = []
        self._manual_geometry_guard_cache_key: tuple | None = None
        self._manual_geometry_guard_cache_ready = False
        self._manual_allowed_geometry_handles: frozenset[str] = frozenset()
        self._manual_protected_legend_handles: frozenset[str] = frozenset()
        self._manual_geometry_guard_error: str | None = None
        self._loaded_source_generation_id: str | None = None

        self.setWindowTitle(
            f"{APP_NAME} — {APP_EDITION} · v{APP_VERSION}"
            if APP_EDITION
            else f"{APP_NAME} v{APP_VERSION}"
        )
        self.resize(1500, 900)
        # Отделни настройки на прозореца за тестовата линия: иначе двете
        # версии делят един ключ в регистъра (размери, отворени панели).
        self._ui_settings = QSettings(
            "LION REBAR",
            f"LION REBAR DESKTOP — {APP_EDITION}" if APP_EDITION else "LION REBAR DESKTOP",
        )
        self.drawing_view = DrawingView()
        self.drawing_view.operatorBoxDrawn.connect(self._operator_box_drawn)
        self.setCentralWidget(self.drawing_view)
        self.drawing_view.mouseScenePositionChanged.connect(self._show_cursor_coordinates)

        self.layer_panel = LayerPanel()
        self.layer_panel.layerVisibilityChanged.connect(self._set_layer_visibility)
        layer_dock = QDockWidget("DXF слоеве", self)
        layer_dock.setWidget(self.layer_panel)
        self.addDockWidget(Qt.LeftDockWidgetArea, layer_dock)

        self.entity_panel = EntityPanel()
        self.entity_dock = QDockWidget("Избран DXF обект", self)
        self.entity_dock.setObjectName("EntityDock")
        self.entity_dock.setWidget(self.entity_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.entity_dock)

        self.project_label = QLabel("Няма отворен проект")
        self.engine_label = QLabel()
        self.session_label = QLabel()
        self.cursor_label = QLabel("X: — | Y: —")
        self.statusBar().addPermanentWidget(self.project_label, 2)
        self.statusBar().addPermanentWidget(self.engine_label, 2)
        self.statusBar().addPermanentWidget(self.session_label, 2)
        self.statusBar().addPermanentWidget(self.cursor_label, 1)

        self._create_actions()
        self._create_edition_banner()
        self._create_toolbar()
        self._create_menus()
        self._refresh_engine_label()
        self._refresh_session_label()
        self.session_service.record_system_event(
            "APP_STARTED",
            {"window": "MainWindow", "application_version": APP_VERSION},
        )
        self._refresh_session_label()

    def _create_actions(self) -> None:
        self.action_new_project = QAction("Нов проект", self)
        self.action_new_project.setShortcut(QKeySequence.New)
        self.action_new_project.triggered.connect(self.new_project)

        self.action_open_project = QAction("Отвори проект", self)
        self.action_open_project.setShortcut(QKeySequence.Open)
        self.action_open_project.triggered.connect(self.open_project)

        self.action_save_project = QAction("Запази проект", self)
        self.action_save_project.setShortcut(QKeySequence.Save)
        self.action_save_project.triggered.connect(self.save_project)

        self.action_import_dxf = QAction("Импорт DXF", self)
        self.action_import_dxf.triggered.connect(self.import_dxf)

        self.action_fit = QAction("Покажи целия чертеж", self)
        self.action_fit.setShortcut("F")
        self.action_fit.triggered.connect(self.drawing_view.fit_all)

        self.action_inventory = QAction("ARM_N списък", self)
        self.action_inventory.triggered.connect(self.open_rebar_inventory)

        self.action_geometry_candidates = QAction("Геометрични кандидати", self)
        self.action_geometry_candidates.triggered.connect(self.open_geometry_candidates)

        self.action_batch_geometry = QAction("Пакетен анализ + Excel", self)
        self.action_batch_geometry.setToolTip(
            "Анализира целия DXF и автоматично генерира Excel по MASTER шаблона. "
            "Нерешените позиции се блокират и не участват в количествата."
        )
        self.action_batch_geometry.triggered.connect(self.run_batch_geometry_analysis)

        self.action_review_center = QAction("Review Center", self)
        self.action_review_center.triggered.connect(lambda: self.open_review_center())

        self.action_ai_diagnostic = QAction("Пакет за AI проверка", self)
        self.action_ai_diagnostic.setToolTip(
            "Създава един възпроизводим ZIP с DXF, точния engine, отчета, решенията и логовете."
        )
        self.action_ai_diagnostic.triggered.connect(
            lambda _checked=False: self.create_ai_diagnostic_bundle()
        )

        self.action_operator_box = QAction("Рамка с два клика", self)
        # Клавишът стои изписан до действието в менюто: рамките се чертаят
        # една след друга и посягането към менюто всеки път е загубено време.
        self.action_operator_box.setShortcut(QKeySequence("R"))
        self.action_operator_box.setToolTip(
            "Начертай с два клика област около заглавието на гредата, шайбата "
            "или групата колони. Анализът я чете и слага всичко вътре в тази "
            "група. Чертае се ПРЕДИ анализа.  (R)"
        )
        self.action_operator_box.triggered.connect(self.start_operator_box)

        self.action_show_unresolved = QAction("Покажи гредите и несвързаното", self)
        self.action_show_unresolved.setToolTip(
            "Оцветява заглавията на гредите и позициите, които автоматиката не "
            "е свързала с нито една — за да се вижда къде да се чертаят рамките."
        )
        self.action_show_unresolved.triggered.connect(self.show_beam_overview)

        self.action_operator_areas = QAction("Начертани рамки…", self)
        self.action_operator_areas.setToolTip(
            "Показва начертаните рамки и позволява сгрешена да бъде оттеглена."
        )
        self.action_operator_areas.triggered.connect(self.manage_operator_areas)

        self.action_withdraw_decision = QAction(
            "Оттегляне на инженерно решение…", self
        )
        self.action_withdraw_decision.setToolTip(
            "Записано инженерно решение, което се е оказало грешно. "
            "Оттеглянето иска автор и мотив, НЕ трие историята и обезсилва "
            "всяко предложение, което стъпва на това решение."
        )
        self.action_withdraw_decision.triggered.connect(
            self.withdraw_engineer_decision
        )

        self.action_final_integrity = QAction("Финален контрол", self)
        self.action_final_integrity.triggered.connect(self.run_final_integrity_control)

        self.action_learning_report = QAction("Отчет за конвенциите", self)
        self.action_learning_report.triggered.connect(self.show_learning_report)

        self.action_ownership_conflicts = QAction("Спорно владение на геометрия…", self)
        self.action_ownership_conflicts.setToolTip(
            "Един начертан обект е първи кандидат на няколко надписа. Реши кой "
            "го владее или заяви, че е общ по замисъл. Решението се записва с "
            "автор, време и одитна следа."
        )
        self.action_ownership_conflicts.triggered.connect(
            self.open_ownership_conflicts
        )

        self.action_drawing_passport = QAction("Паспорт на чертежа…", self)
        self.action_drawing_passport.setToolTip(
            "Предлага ролята на всеки слой по СЪДЪРЖАНИЕТО му — позиционни "
            "ключове, заглавия на зони, надписи и армировъчна геометрия — с "
            "доказателство и с контрола, и иска потвърждение."
        )
        self.action_drawing_passport.triggered.connect(self.open_drawing_passport)

        self.action_operator = QAction("Оператор…", self)
        self.action_operator.setToolTip(
            "Кой работи сега. Името влиза като автор във всяко ново решение. "
            "То е ЗАЯВЕНО, не удостоверено — програмата не проверява самоличност. "
            "Смяната не променя вече записаните решения."
        )
        self.action_operator.triggered.connect(self.change_operator_name)

        self.action_session_summary = QAction("Състояние на сесията", self)
        self.action_session_summary.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.action_session_summary.triggered.connect(self.show_session_summary)

        self.action_end_session = QAction("Край на сесията", self)
        self.action_end_session.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.action_end_session.setToolTip(
            "Записва дневника, създава Learning Pack за всички проекти от сесията "
            "и приключва работния ден без автоматична промяна на Engine/Matcher."
        )
        self.action_end_session.triggered.connect(self.end_work_session)

        self.action_layer_report = QAction("Класификация на слоевете", self)
        self.action_layer_report.triggered.connect(self.show_layer_classification)

        self.action_export_excel = QAction("Експорт Excel + DXF", self)
        self.action_export_excel.triggered.connect(self.run_excel_export)

        self.action_engine_studio = QAction("MASTER Studio", self)
        self.action_engine_studio.triggered.connect(self.open_engine_studio)

        self.action_shape_builder = QAction("Конструктор форми", self)
        self.action_shape_builder.triggered.connect(self.open_shape_builder)

        self.action_form_editor = QAction("Редактор на форми…", self)
        self.action_form_editor.triggered.connect(self.open_form_editor)

        self.action_exit = QAction("Изход", self)
        self.action_exit.triggered.connect(self.close)

    def _create_edition_banner(self) -> None:
        """Оранжева лента най-горе: тестовата линия не се бърка с основната.

        Само при ``APP_EDITION``. Показва и папката с данни, защото тя е
        разликата, която има значение — отделен engine и отделни сесии.
        """

        if not APP_EDITION:
            return
        надпис = QLabel(
            f"  {APP_EDITION} — не е основната програма LION REBAR.   "
            f"Данни: {getattr(getattr(self.session_service, 'paths', None), 'root', '—')}"
        )
        надпис.setObjectName("EditionBannerLabel")
        надпис.setStyleSheet(
            "QLabel { background: #ea580c; color: white; font-weight: bold;"
            " padding: 5px 8px; }"
        )
        лента = QToolBar("Тестова версия", self)
        лента.setObjectName("EditionBanner")
        лента.setMovable(False)
        лента.setFloatable(False)
        # Лентата е през цялата ширина: видима е и при тесен прозорец.
        надпис.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        лента.addWidget(надпис)
        self.addToolBar(Qt.TopToolBarArea, лента)
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.edition_banner = надпис

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Основни действия")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for action in (
            self.action_new_project,
            self.action_open_project,
            self.action_save_project,
            self.action_import_dxf,
            self.action_fit,
            self.action_inventory,
            self.action_geometry_candidates,
            self.action_batch_geometry,
            self.action_review_center,
            self.action_ai_diagnostic,
            self.action_final_integrity,
            self.action_export_excel,
            self.action_end_session,
            self.action_shape_builder,
            self.action_engine_studio,
        ):
            toolbar.addAction(action)

        self._manual_selection_toolbar = QToolBar("Ръчен избор на геометрия", self)
        self._manual_selection_toolbar.setMovable(False)
        self._manual_selection_toolbar.setFloatable(False)
        self._manual_selection_toolbar.setStyleSheet(
            "QToolBar { background:#e0f2fe; border-top:2px solid #00a3ff; spacing:8px; padding:5px; }"
            "QToolButton { font-weight:700; padding:6px 10px; }"
        )
        self._manual_selection_label = QLabel("Режим избор на геометрия")
        self._manual_selection_label.setStyleSheet("font-weight:700; color:#075985; padding:0 8px;")
        self._manual_selection_toolbar.addWidget(self._manual_selection_label)
        self._manual_selection_toolbar.addSeparator()

        self._manual_selection_done_action = QAction("Готово — Инженерно решение", self)
        self._manual_selection_done_action.setShortcut(QKeySequence("Ctrl+Return"))
        self._manual_selection_done_action.triggered.connect(self._complete_manual_geometry_selection)
        self._manual_selection_toolbar.addAction(self._manual_selection_done_action)

        self._manual_selection_without_geometry_action = QAction(
            "Без DXF връзка — Инженерно решение",
            self,
        )
        self._manual_selection_without_geometry_action.setToolTip(
            "За липсваща/невалидна ос: формата и сегментите се въвеждат изрично; "
            "причина и инженерна бележка остават задължителни."
        )
        self._manual_selection_without_geometry_action.triggered.connect(
            self._complete_manual_without_geometry
        )
        self._manual_selection_toolbar.addAction(
            self._manual_selection_without_geometry_action
        )

        self._manual_selection_clear_action = QAction("Изчисти избора", self)
        self._manual_selection_clear_action.setShortcut(QKeySequence("Ctrl+Backspace"))
        self._manual_selection_clear_action.triggered.connect(self._clear_manual_geometry_selection)
        self._manual_selection_toolbar.addAction(self._manual_selection_clear_action)

        self._manual_selection_cancel_action = QAction("Отказ", self)
        self._manual_selection_cancel_action.setShortcut(QKeySequence("Esc"))
        self._manual_selection_cancel_action.triggered.connect(self._cancel_manual_geometry_selection)
        self._manual_selection_toolbar.addAction(self._manual_selection_cancel_action)

        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self._manual_selection_toolbar)
        self._manual_selection_toolbar.hide()

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        for action in (
            self.action_new_project,
            self.action_open_project,
            self.action_save_project,
            self.action_import_dxf,
            self.action_exit,
        ):
            file_menu.addAction(action)
        view_menu = self.menuBar().addMenu("Изглед")
        view_menu.addAction(self.action_fit)
        analysis_menu = self.menuBar().addMenu("Анализ")
        analysis_menu.addAction(self.action_inventory)
        analysis_menu.addAction(self.action_geometry_candidates)
        analysis_menu.addAction(self.action_batch_geometry)
        analysis_menu.addAction(self.action_review_center)
        analysis_menu.addAction(self.action_ai_diagnostic)
        analysis_menu.addAction(self.action_final_integrity)
        analysis_menu.addAction(self.action_show_unresolved)
        analysis_menu.addAction(self.action_operator_box)
        analysis_menu.addAction(self.action_operator_areas)
        analysis_menu.addAction(self.action_learning_report)
        analysis_menu.addAction(self.action_ownership_conflicts)
        analysis_menu.addAction(self.action_withdraw_decision)
        analysis_menu.addAction(self.action_drawing_passport)
        analysis_menu.addAction(self.action_export_excel)
        analysis_menu.addAction(self.action_layer_report)
        session_menu = self.menuBar().addMenu("Сесия")
        session_menu.addAction(self.action_operator)
        session_menu.addSeparator()
        session_menu.addAction(self.action_session_summary)
        session_menu.addAction(self.action_end_session)
        master_menu = self.menuBar().addMenu("MASTER")
        master_menu.addAction(self.action_shape_builder)
        master_menu.addAction(self.action_form_editor)
        master_menu.addAction(self.action_engine_studio)
        self._refresh_operator_action()

    # --- Кой работи сега -----------------------------------------------------
    #
    # Решение на собственика, 01.10.2026: при първото записано решение се
    # въвежда име и то се запомня; текущият оператор се вижда и може да се
    # сменя; всяко решение пази автора КЪМ МОМЕНТА НА ЗАПИСА, а смяната не
    # пренаписва историята. Името е заявено, не удостоверено.

    def _operator_settings_file(self) -> Path:
        return Path(self.session_service.paths.settings_file)

    def _operator_for_record(self) -> str:
        """Авторът, който влиза в новите записи.

        Незаявеното име е ``НЕЗАЯВЕН``, а НЕ историческото „оператор": старите
        записи остават с неизвестен автор и не се приписват със задна дата на
        никого, затова новите трябва да са различими от тях.
        """

        return self.operator_name or НЕЗАЯВЕН

    def _refresh_operator_action(self) -> None:
        име = self.operator_name or "незаявен"
        action = getattr(self, "action_operator", None)
        if action is not None:
            action.setText("Оператор: %s…" % име)

    def _ask_operator_name(self, *, prompt: str) -> str:
        """Пита за име и го запомня. Връща заявеното или празен низ."""

        текущо = self.operator_name
        име, ок = QInputDialog.getText(
            self, "Кой работи", prompt, text=текущо
        )
        if not ок:
            return ""
        try:
            declare_operator(self._operator_settings_file(), име)
        except OperatorIdentityError as error:
            QMessageBox.warning(self, "Името не беше прието", str(error))
            return ""
        except Exception as error:  # noqa: BLE001 - помощен път
            self._error(f"Името не беше записано.\n\n{error}")
            return ""
        self.operator_name = claimed_operator(self._operator_settings_file())
        self._refresh_operator_action()
        if self._review_center is not None:
            self._review_center.set_operator_name(self.operator_name)
        return self.operator_name

    def change_operator_name(self) -> None:
        заявено = self._ask_operator_name(
            prompt=(
                "Име на оператора, което да влиза като автор в новите решения.\n\n"
                "Името е ЗАЯВЕНО, не удостоверено — програмата не проверява\n"
                "самоличност. Смяната НЕ променя вече записаните решения, а\n"
                "записите от преди въвеждането остават с неизвестен автор."
            )
        )
        if заявено:
            self.statusBar().showMessage(
                "Оператор: %s. Авторът на вече записаните решения не е променян."
                % заявено,
                10000,
            )

    def _ensure_operator_declared(self) -> bool:
        """Пита веднъж, преди първото записано решение. Отказът не блокира.

        Ако операторът не иска да каже име, работата продължава и записът носи
        ``НЕЗАЯВЕН``. Да спрем инженерната работа заради име би било по-лошо от
        видимата празнота; измислено име би било най-лошото.
        """

        if self.operator_name:
            return True
        заявено = self._ask_operator_name(
            prompt=(
                "Това е първото решение, което се записва на този компютър.\n"
                "Кой работи? Името влиза като автор в записа на решението.\n\n"
                "Името е ЗАЯВЕНО, не удостоверено. Може да се откажеш — тогава\n"
                "авторът остава незаявен, а решението се записва."
            )
        )
        if not заявено:
            self.statusBar().showMessage(
                "Решението се записва с НЕЗАЯВЕН автор. "
                "Името се въвежда от Сесия → Оператор.",
                10000,
            )
        return bool(заявено)

    def new_project(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "Избери папка за новия проект")
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "Нов проект", "Име на проекта:")
        if not ok:
            return
        try:
            engine = self.engine_repository.load_current()
            project = self.project_manager.create_project(Path(parent), name, engine)
            self.project_manager.save_engine_snapshot(project, engine)
            self._set_project(project)
            self._session_project_event(
                "PROJECT_CREATED",
                {"project_name": project.name, "project_root_name": Path(project.root).name},
            )
            if not engine.production_ready:
                QMessageBox.warning(
                    self,
                    "Няма production MASTER",
                    "Проектът е създаден с bootstrap engine. Импортирай, провери и публикувай реалния MASTER през MASTER Studio преди производствен анализ.",
                )
        except (ProjectError, EngineRepositoryError) as exc:
            self._error(str(exc))

    def open_project(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Отвори LION REBAR проект",
            "",
            "Project JSON (project.json);;JSON (*.json)",
        )
        if not filename:
            return
        try:
            project = self.project_manager.open_project(Path(filename))
            self._set_project(project)
            self._session_project_event(
                "PROJECT_OPENED",
                {"project_name": project.name, "project_root_name": Path(project.root).name},
            )
            self._apply_known_engine_correction()
            self._check_project_engine_pin()
            self._warn_about_foreign_artifacts()
            source = self.project_manager.source_dxf_path(project)
            if source:
                self._load_dxf_from_project(source, show_report=False)
        except (ProjectError, EngineRepositoryError) as exc:
            self._error(str(exc))

    def _unresolved_beam_context(self):
        """Позициите без доказана греда и ВСИЧКИ заглавия на греди.

        Връща ``(позиции, заглавия)``, всяко с координати, готово за
        ``operator_group_box.resolve_operator_box``.
        """

        from lion_rebar.export.excel_export import (
            _assign_beam_label,
            _detect_beam_labels,
            _entity_insert,
        )
        from lion_rebar.export.frame_detection import (
            assign_point_to_frame,
            detect_drawing_frames,
        )

        entities = list(
            self.import_result.entities if self.import_result is not None else []
        )
        frames = detect_drawing_frames(entities)
        labels = _detect_beam_labels(entities, frames)
        beam_titles = [
            (label.handle, label.title, label.x, label.y) for label in labels
        ]
        frame_map = {frame.index: frame for frame in frames}
        unresolved = []
        for record in entities:
            point = _entity_insert(record)
            if point is None:
                continue
            index = assign_point_to_frame(point[0], point[1], list(frames))
            frame = frame_map.get(index) if index is not None else None
            if frame is None or frame.category_code != 'BEAMS':
                continue
            if _assign_beam_label(point[0], point[1], int(index), labels) is not None:
                continue
            unresolved.append((str(record.handle), point[0], point[1]))
        return unresolved, beam_titles

    def start_operator_box(self) -> None:
        """Пуска режима: следващите два клика чертаят рамка."""

        if self.project is None or self.import_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        self.drawing_view.begin_operator_box()
        self.statusBar().showMessage(
            "Рамка с два клика: щракни двата ъгъла така, че вътре да остане "
            "ЕДНО заглавие — на гредата, шайбата или групата колони. Десен бутон отказва.",
            15000,
        )

    def _operator_box_drawn(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Запазва начертаната рамка като ОБЛАСТ.

        Не се записват решения за отделни позиции: рамката се чертае преди
        анализа, когато позиции още няма. Пази се правоъгълникът и
        заглавието в него, а кое е попаднало вътре анализът решава сам,
        всеки път наново (``export/operator_areas.py``).
        """

        from lion_rebar.export.frame_detection import detect_drawing_frames
        from lion_rebar.export.operator_areas import (
            KIND_BEAM,
            OperatorAreaError,
            bend_around_existing,
            load_operator_areas,
            record_operator_area,
        )
        from lion_rebar.export.operator_group_box import (
            OperatorBoxError,
            collect_area_titles,
            resolve_operator_area,
        )
        from lion_rebar.project.data_identity import current_identity

        if self.project is None or self.import_result is None:
            return
        try:
            entities = list(self.import_result.entities)
            frames = detect_drawing_frames(entities)
            titles = collect_area_titles(entities, frames)
            съществуващи = load_operator_areas(
                Path(self.project.root), current_identity(self.project)
            )
            resolution = resolve_operator_area(
                (x0, y0),
                (x1, y1),
                titles=titles,
                claimed_title_handles=[
                    area.title_handle for area in съществуващи
                ],
            )
        except OperatorBoxError as error:
            QMessageBox.warning(self, "Рамката не беше приета", str(error))
            return
        except Exception as error:  # noqa: BLE001 - помощен път
            self._error(f"Рамката не може да бъде разчетена: {error}")
            return

        purpose = (
            "гредата"
            if resolution.kind == KIND_BEAM
            else "групата в описа"
        )
        # Огъването се смята тук, за да е ВЯРЕН броят в потвърждението:
        # операторът вижда какво остава негово, а не какво е оградил.
        огънато = bend_around_existing(
            resolution.box,
            съществуващи,
            kind=resolution.kind,
            title_handle=resolution.title_handle,
        )
        # § 6.1: всеки член минава СВОЯ проверка и присъдата му се вижда преди
        # записа, а после се записва с рамката. Дотук преценката се правеше и
        # се изхвърляше: след записа нито един член не можеше да се провери.
        from lion_rebar.export.operator_area_members import (
            БЕЗ_ТОЧКА,
            НЕ_Е_ПОЗИЦИЯ,
            ПОТВЪРДЕН_ДРУГАДЕ,
            ПРИЕТИ,
            ordered_for_review,
            summarise,
        )

        try:
            членове = self._operator_area_members(resolution, огънато)
        except Exception as error:  # noqa: BLE001 - помощен път
            self._error(
                "Членовете на рамката не можаха да бъдат проверени, затова тя "
                f"НЕ е записана.\n\n{error}"
            )
            return
        сводка = summarise(членове)
        обхванати = сводка["accepted"]
        # Подредени за преглед: пръв е случаят, в който автоматиката е била
        # най-уверена. Показателите само подреждат — праг няма.
        премествани = [
            (член.text or член.handle, член.from_title or "")
            for член in ordered_for_review(членове)
        ]
        за_преглед = [член for член in членове if член.verdict == БЕЗ_ТОЧКА]
        потвърдени_другаде = [
            член for член in членове if член.verdict == ПОТВЪРДЕН_ДРУГАДЕ
        ]
        не_позиции = [член for член in членове if член.verdict == НЕ_Е_ПОЗИЦИЯ]
        редове = [
            f"Всичко в рамката ще се отнесе към {purpose}",
            f"„{resolution.title_text}“.",
            "",
            f"Позиции в рамката: {обхванати}.",
        ]
        if не_позиции:
            # Дотук се пропускаха мълчаливо и броят не се връзваше с видяното.
            редове.append(
                f"В рамката има и {len(не_позиции)} обекта, които не са позиции "
                "(бележки и заглавия) — те не стават нейни."
            )
        if потвърдени_другаде:
            # Състояние 3 от таблицата на собственика: очертаването на рамка не
            # отменя предишно потвърдено решение.
            редове.append("")
            редове.append(
                f"НЕ СЕ МЕСТЯТ: {len(потвърдени_другаде)} позиции с вече "
                "ПОТВЪРДЕНО решение за друга греда."
            )
            редове.append("Остават за изрично решение:")
            for член in потвърдени_другаде[:5]:
                редове.append(
                    f"    {член.text or член.handle}: потвърдена за "
                    f"„{член.from_title or член.from_handle}“"
                )
            if len(потвърдени_другаде) > 5:
                редове.append(f"    … и още {len(потвърдени_другаде) - 5}")
        if за_преглед:
            редове.append("")
            редове.append(
                f"ЗА ПРЕГЛЕД: {len(за_преглед)} позиции без точка на вмъкване —"
            )
            редове.append("не може да се реши дали са в рамката:")
            for член in за_преглед[:5]:
                редове.append(f"    {член.text or член.handle}")
            if len(за_преглед) > 5:
                редове.append(f"    … и още {len(за_преглед) - 5}")
        if огънато:
            съседни = sorted(
                {
                    f"„{area.title_text}“"
                    for area in съществуващи
                    if (area.x0, area.y0, area.x1, area.y1) in огънато
                }
            )
            редове.append(
                f"Огъва се по вече начертаните: {', '.join(съседни)} — "
                "техните позиции остават техни."
            )
        if премествани:
            # Рамката бие автоматиката, затова какво точно се мести трябва
            # да се види ПРЕДИ записа. Собственик, 2026-09-14: рамката да
            # печели, но да показва какво премества.
            редове.append("")
            редове.append(
                f"ВНИМАНИЕ: {len(премествани)} от тях автоматиката е свързала"
            )
            редове.append("с друга греда и ще бъдат преместени:")
            for текст, стара in премествани[:8]:
                редове.append(f"    {текст}: „{стара}“ → „{resolution.title_text}“")
            if len(премествани) > 8:
                редове.append(f"    … и още {len(премествани) - 8}")
        # Обосновка се иска САМО когато рамката отменя нещо.
        #
        # Собственик, 2026-09-15, докато огражда елементите в s-11: „в това
        # съобщение, което трябва да опиша, не намирам логика."
        #
        # Прав е. Мотивът пази решение, което БИЕ автоматиката — затова е
        # задължителен, когато позиции се местят от една греда на друга. Но
        # когато рамката само огражда елемент преди анализа, няма какво да
        # се оправдава: нищо не се отменя. Тридесет елемента значеха
        # тридесет обосновки за нищо.
        #
        # Потвърждение се иска само когато има за какво.
        #
        # Собственик, 2026-09-24, след 31 греди в „тест-006": „минимализирай
        # исканията и питанията за рамки, те са дразнещо".
        #
        # Рамка, която обхваща позиции и не мести нищо, е рутинна: броят ѝ
        # отива в лентата на състоянието и рамката се записва. Диалог остава
        # за двата случая, в които операторът наистина трябва да погледне —
        # рамка, която МЕСТИ позиции (иска и мотив), и рамка, хванала НУЛА
        # позиции, където или правоъгълникът е сгрешен, или заглавието е чуждо.
        if премествани:
            редове.extend(["", "Обосновка:"])
            note, accepted = QInputDialog.getText(
                self, "Рамка с два клика", "\n".join(редове)
            )
            if not accepted:
                return
        elif not обхванати:
            note = "Оградено преди анализа — нищо не се отменя."
            редове.extend(["", "Рамката не хвана НИТО ЕДНА позиция.",
                           "Да я запиша ли въпреки това?"])
            if QMessageBox.question(
                self,
                "Рамка с два клика",
                "\n".join(редове),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        else:
            note = "Оградено преди анализа — нищо не се отменя."
        try:
            area = record_operator_area(
                Path(self.project.root),
                box=resolution.box,
                kind=resolution.kind,
                title_handle=resolution.title_handle,
                title_text=resolution.title_text,
                frame_index=resolution.frame_index,
                decided_by=self._operator_for_record(),
                note=note,
                identity=current_identity(self.project),
                members=[член.to_dict() for член in членове],
            )
        except OperatorAreaError as error:
            QMessageBox.warning(self, "Областта не беше записана", str(error))
            return
        except Exception as error:  # noqa: BLE001 - помощен път
            self._error(f"Областта не може да бъде записана: {error}")
            return
        self._session_project_event(
            "OPERATOR_AREA_RECORDED",
            {
                "kind": area.kind,
                "title": area.title_text,
                "area_id": area.area_id,
                # Сводката по присъди: едно групово решение не е N независими
                # потвърждения и броят му трябва да се разчита без да се
                # отваря файлът на областите.
                "members": сводка,
            },
        )
        # Преди съобщението, за да е вече на чертежа, щом го затвори.
        self._refresh_operator_areas()
        # Лентата на състоянието, не диалог. Рамката вече се вижда на
        # чертежа (``_refresh_operator_areas`` по-горе), а при 31 греди
        # кутията „Записано" значи 31 натискания за нищо.
        self.statusBar().showMessage(
            f"Областта за „{area.title_text}“ е записана "
            f"({обхванати} позиции). Пусни анализа или Финален контрол, "
            "за да влезе в сила.",
            8000,
        )

    #: Цвят на рамката според вида на заглавието, което е хванала.
    _ЦВЯТ_НА_РАМКА = {"BEAM": "#ef6c00", "GROUP": "#2e7d32"}

    def _refresh_operator_areas(self) -> None:
        """Пречертава запазените рамки върху чертежа.

        Собственик, 2026-09-15: „рамката с два клика трябва да е видима за
        да може оператора да вижда какво е маркирал."

        Чете се от диска, не от паметта: рамките са състояние на проекта и
        трябва да се появят и при отваряне на вече започнат проект, не само
        веднага след чертането.

        Никога не спира работата: ако областите не могат да се прочетат,
        чертежът остава без рамки, но си работи.
        """

        view = getattr(self, "drawing_view", None)
        if view is None:
            return
        try:
            from lion_rebar.export.operator_areas import load_operator_areas
            from lion_rebar.project.data_identity import current_identity

            if self.project is None or self.import_result is None:
                view.clear_operator_areas()
                return
            areas = load_operator_areas(
                Path(self.project.root), current_identity(self.project)
            )
        except Exception:  # noqa: BLE001 - показването не бива да вали импорта
            self.logger.exception("Cannot draw operator areas")
            return
        view.show_operator_areas(
            [
                (
                    area.x0,
                    area.y0,
                    area.x1,
                    area.y1,
                    area.title_text,
                    self._ЦВЯТ_НА_РАМКА.get(area.kind, "#2e7d32"),
                    # Изрезките се рисуват: рамка, която се е огънала по
                    # съседна, трябва да се вижда огъната, иначе операторът
                    # чете на екрана друго, а не каквото е записано.
                    area.bent_around,
                )
                for area in areas
            ]
        )

    def show_beam_overview(self) -> None:
        """Показва върху чертежа гредите и позициите без доказана греда.

        Собственик, 2026-09-13: „няма как да знам кое желязо на коя греда
        принадлежи при положение че не мога да ги видя и да ги маркирам."

        Рамката се чертае ПРЕДИ анализа, но дотогава операторът нямаше на
        какво да стъпи: заглавията на гредите се губят в чертежа, а кои
        позиции остават несвързани се разбираше чак от блокирания Финален
        контрол, и то като списък с handles.

        Тук и двете са върху самия чертеж: заглавията в синьо, несвързаните
        позиции в червено, с надписа си. Второто повикване ги маха.
        """

        if self.project is None or self.import_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        if self.drawing_view.has_markers():
            self.drawing_view.clear_markers()
            self.statusBar().showMessage("Маркерите са скрити.", 6000)
            return
        try:
            unresolved, beam_titles = self._unresolved_beam_context()
        except Exception as error:  # noqa: BLE001 - помощен път
            self._error(f"Прегледът не може да бъде направен: {error}")
            return

        # Само позициите, които НАИСТИНА блокират. ``_unresolved_beam_context``
        # обхожда всеки DXF обект в гредовата рамка, включително котировки,
        # легенди и помощни текстове -- в ``s-9`` те са 67, докато Финалният
        # контрол спира само заради 8. Да се покажат всичките би пратило
        # оператора да огражда неща, които изобщо не влизат в описа.
        approved = set()
        try:
            approved = {
                str(handle)
                for handle in self._read_all_approved_geometry_links()
            }
        except Exception:  # noqa: BLE001 - още няма одобрени връзки
            approved = set()
        if approved:
            unresolved = [
                item for item in unresolved if str(item[0]) in approved
            ]

        texts = {
            str(record.handle): re.sub(r"\s+", " ", str(record.text or "")).strip()
            for record in self.import_result.entities
        }
        title_marks = [
            (x, y, title) for _handle, title, x, y in beam_titles
        ]
        position_marks = [
            (x, y, texts.get(handle, "") or handle)
            for handle, x, y in unresolved
        ]
        if not title_marks and not position_marks:
            self._info("В този чертеж няма греди за показване.")
            return

        self.drawing_view.show_markers(title_marks, colour="#1565c0", radius=55.0)
        self.drawing_view.show_markers(
            position_marks, colour="#d93025", radius=40.0, append=True
        )
        self.drawing_view.zoom_to_markers()
        self._session_project_event(
            "BEAM_OVERVIEW_SHOWN",
            {"beams": len(title_marks), "unresolved": len(position_marks)},
        )
        self.statusBar().showMessage(
            f"Сини: {len(title_marks)} заглавия на греди. "
            f"Червени: {len(position_marks)} позиции без доказана греда. "
            "Огради всяка червена ЗАЕДНО със синьото ѝ заглавие "
            "(Анализ → Рамка с два клика). Действието ги маха.",
            0,
        )

    def _operator_area_members(self, resolution, bent_around=()):
        """Присъдата за ВСЕКИ член на рамката (§ 6.1).

        Връща списък от ``ЧленНаОбласт``. Не прихваща грешки: рамка, за която
        преценката не е минала, не бива да се записва като безпроблемна.
        """

        from lion_rebar.export.beam_decisions import load_beam_decisions
        from lion_rebar.export.operator_area_members import area_member_verdicts
        from lion_rebar.project.data_identity import current_identity

        потвърдени: dict[str, str] = {}
        if self.project is not None:
            # Потвърдените принадлежности са ВХОД за присъдата: рамката не бива
            # да мести позиция, за която операторът вече е решил друго.
            потвърдени = dict(
                load_beam_decisions(
                    Path(self.project.root), current_identity(self.project)
                )
            )
        return area_member_verdicts(
            entities=list(self.import_result.entities),
            policy=self._recognition_policy(),
            box=resolution.box,
            bent_around=bent_around,
            kind=resolution.kind,
            title_handle=resolution.title_handle,
            confirmed_beams=потвърдени,
        )

    def _operator_area_preview(self, resolution, bent_around=()):
        """Колко ПОЗИЦИИ обхваща рамката и кои от тях премества.

        Връща ``(брой_обхванати, [(надпис, сегашна_греда), …])``. Второто са
        позициите, които автоматиката вече е свързала с ДРУГА греда — те се
        показват поименно, за да не се мести нищо тихо.

        ``bent_around`` са съседните рамки, по които тази се огъва: техните
        позиции не се броят тук, защото няма да станат нейни.
        """

        try:
            entities = list(self.import_result.entities)
            policy = self._recognition_policy()
        except Exception:  # noqa: BLE001 - прегледът не бива да чупи записа
            return 0, []
        return operator_area_preview(
            entities,
            box=resolution.box,
            bent_around=bent_around,
            kind=resolution.kind,
            title_handle=resolution.title_handle,
            policy=policy,
        )

    def _recognition_policy(self):
        """Политиката на закования engine; при липса — подразбирането."""

        from lion_rebar.dxf.recognition_policy import RecognitionPolicy

        try:
            engine = load_pinned_engine(
                self.project, self.engine_repository.load_current()
            )
            return RecognitionPolicy.from_settings(
                (engine.settings or {}).get("geometry_recognition") or {}
            )
        except Exception:  # noqa: BLE001 - подразбирането е достатъчно за преглед
            return RecognitionPolicy.from_settings({})


    def manage_operator_areas(self) -> None:
        """Показва начертаните рамки и оттегля сгрешена.

        Без този изход сгрешена рамка остава в проекта, докато чертежът не се
        смени: тя се чете при всеки анализ и всеки Финален контрол. Оттеглянето
        иска мотив и се записва в същата одитна следа като чертането.
        """

        from lion_rebar.export.operator_areas import (
            KIND_BEAM,
            OperatorAreaError,
            load_operator_areas,
            withdraw_operator_area,
        )
        from lion_rebar.project.data_identity import current_identity

        if self.project is None:
            self._info("Първо отвори проект.")
            return
        areas = load_operator_areas(
            Path(self.project.root), current_identity(self.project)
        )
        if not areas:
            self._info(
                "Няма начертани рамки за този чертеж.\n\n"
                "„Анализ → Рамка с два клика“ чертае нова."
            )
            return

        подредени = sorted(areas, key=lambda a: (a.kind, a.title_text))
        редове = [
            "{}  ({})  —  {}".format(
                area.title_text,
                "греда" if area.kind == KIND_BEAM else "група",
                area.note,
            )
            for area in подредени
        ]
        избор, прието = QInputDialog.getItem(
            self,
            "Начертани рамки",
            f"{len(подредени)} рамки за този чертеж.\n"
            "Избери рамка, за да я оттеглиш, или откажи:",
            редове,
            0,
            False,
        )
        if not прието or избор not in редове:
            return
        area = подредени[редове.index(избор)]

        мотив, прието = QInputDialog.getText(
            self,
            "Оттегляне на рамка",
            f"Рамката за „{area.title_text}“ ще бъде оттеглена.\n\nПричина:",
        )
        if not прието:
            return
        try:
            withdraw_operator_area(
                Path(self.project.root),
                area.area_id,
                withdrawn_by=self._operator_for_record(),
                note=мотив,
            )
        except OperatorAreaError as error:
            QMessageBox.warning(self, "Рамката не беше оттеглена", str(error))
            return
        self._session_project_event(
            "OPERATOR_AREA_WITHDRAWN",
            {"kind": area.kind, "title": area.title_text, "area_id": area.area_id},
        )
        self._refresh_operator_areas()
        QMessageBox.information(
            self,
            "Оттеглено",
            "\n".join(
                [
                    f"Рамката за „{area.title_text}“ е оттеглена.",
                    "",
                    "Пусни анализа или Финален контрол наново, за да отпадне",
                    "и от резултата.",
                ]
            ),
        )

    def withdraw_engineer_decision(self) -> None:
        """Оттегля записано инженерно решение — с автор, мотив и следа.

        Защо е нужно. Щом едно решение може да стане ОСНОВАНИЕ за предложение
        в друг случай, трябва да има и начин основанието да бъде обезсилено.
        Дотук журналът можеше да отмени решение само с НОВО решение върху
        същата позиция; нямаше изход „това беше грешно и го махам".

        Какво прави оттеглянето:

        * дописва събитие — НИЩО не се трие и историята остава четима;
        * решението спира да бъде доказателство за предложение веднага;
        * вече приетите решения, които стъпват на него, се ИЗБРОЯВАТ за
          повторна проверка и се показват на оператора.
        """

        from lion_rebar.review.decision_reuse import (
            зависими_за_повторна_проверка,
        )
        from lion_rebar.session.decision_contract import (
            DecisionWithdrawError,
            JOURNAL_NAME,
            withdraw_project_decision,
            withdrawn_decision_ids,
        )

        if self.project is None:
            self._info("Първо отвори проект.")
            return
        журнал = Path(self.project.root) / "working" / JOURNAL_NAME
        if not журнал.is_file():
            self._info("Този проект още няма записани решения.")
            return
        вече_оттеглени = withdrawn_decision_ids(Path(self.project.root))
        отменени: set[str] = set()
        пликове: list[dict] = []
        for ред in журнал.read_text(encoding="utf-8-sig").splitlines():
            try:
                плик = json.loads(ред)
            except ValueError:
                continue
            if not isinstance(плик, dict):
                continue
            for ид in плик.get("supersedes_event_ids") or []:
                отменени.add(str(ид))
            пликове.append(плик)
        живи = [
            плик
            for плик in пликове
            if str(плик.get("event_type") or "").upper() == "ENGINEER_RESOLVED"
            and bool(плик.get("active", True))
            and str(плик.get("event_id") or "") not in вече_оттеглени
            and str(плик.get("event_id") or "") not in отменени
        ]
        if not живи:
            self._info(
                "Няма живо инженерно решение за оттегляне.\n\n"
                "Отменените и вече оттеглените остават в историята, но не могат"
                " да бъдат оттеглени повторно."
            )
            return
        живи.sort(key=lambda плик: str(плик.get("occurred_at") or ""), reverse=True)
        редове = []
        for плик in живи:
            товар = плик.get("payload") or {}
            # ПОЗИЦИЯТА е в текста нарочно: два записа за един и същ
            # надпис, взети в една и съща секунда, иначе са неразличими и
            # операторът не може да знае кой оттегля.
            редове.append(
                "{}  —  позиция {}  —  {} {}  ({})".format(
                    str(товар.get("annotation_text") or "—"),
                    str(товар.get("annotation_handle") or "—"),
                    str(товар.get("action_class") or "—"),
                    str(товар.get("reason_code") or "—"),
                    str(плик.get("occurred_at") or "—"),
                )
            )
        избор, прието = QInputDialog.getItem(
            self,
            "Оттегляне на инженерно решение",
            "{} живи решения в този проект.\n"
            "Избери решението, което ще оттеглиш, или откажи:".format(len(живи)),
            редове,
            0,
            False,
        )
        if not прието or избор not in редове:
            return
        плик = живи[редове.index(избор)]
        товар = плик.get("payload") or {}
        мотив, прието = QInputDialog.getText(
            self,
            "Оттегляне на инженерно решение",
            "Решението за „{}“ ще бъде оттеглено.\n\n"
            "Историята НЕ се трие. Мотив:".format(
                str(товар.get("annotation_text") or "—")
            ),
        )
        if not прието:
            return
        try:
            оттеглянето = withdraw_project_decision(
                Path(self.project.root),
                [str(плик.get("event_id") or "")],
                actor=self._operator_for_record(),
                reason=мотив,
            )
        except DecisionWithdrawError as грешка:
            QMessageBox.warning(
                self, "Решението не беше оттеглено", str(грешка)
            )
            return
        self._session_project_event(
            "ENGINEER_DECISION_WITHDRAWN",
            {
                "withdrawn_event_id": str(плик.get("event_id") or ""),
                "annotation_text": str(товар.get("annotation_text") or ""),
                "withdrawal_event_id": str(оттеглянето.get("event_id") or ""),
            },
        )
        зависими = зависими_за_повторна_проверка(Path(self.project.root))
        редове_за_показване = [
            "Решението за „{}“ е оттеглено.".format(
                str(товар.get("annotation_text") or "—")
            ),
            "",
            "Записано е като ново събитие: историята е цяла.",
            "Предложенията, които стъпваха на него, вече не са в сила.",
        ]
        if зависими:
            редове_за_показване += [
                "",
                "ЗА ПОВТОРНА ПРОВЕРКА — {} вече приети решения стъпват на "
                "оттегленото:".format(len(зависими)),
            ]
            for запис in зависими[:12]:
                редове_за_показване.append(
                    "  • {} (позиция {})".format(
                        запис.get("annotation_text") or "—",
                        запис.get("annotation_handle") or "—",
                    )
                )
            if len(зависими) > 12:
                редове_за_показване.append(
                    "  … и още {}".format(len(зависими) - 12)
                )
        редове_за_показване += [
            "",
            "Пусни анализа и Финалния контрол наново, за да се види в резултата.",
        ]
        QMessageBox.information(
            self, "Оттеглено", "\n".join(редове_за_показване)
        )

    def _offer_unresolved_beam_resolution(self, handles) -> None:
        """Предлага на оператора да посочи гредата на блокираните позиции.

        Автоматиката вече отказа да гадае -- и с право: разширяване на
        геометричния прозорец дотолкова, че да ги покрие, беше измерено върху
        реален чертеж и премества 27 вече верни позиции на съседна греда.
        """

        if self.project is None or not handles:
            return
        try:
            from lion_rebar.export.excel_export import _detect_beam_labels
            from lion_rebar.export.frame_detection import detect_drawing_frames
            from lion_rebar.project.data_identity import current_identity
            from lion_rebar.ui.unresolved_beam_dialog import UnresolvedBeamDialog

            entities = (
                self.import_result.entities
                if self.import_result is not None
                else []
            )
            labels = _detect_beam_labels(list(entities), detect_drawing_frames(entities))
            if not labels:
                return
            texts = {str(record.handle): str(record.text or "") for record in entities}
            positions = [(handle, texts.get(handle, handle)) for handle in handles]
            beam_titles = [(label.handle, label.title) for label in labels]
            identity = current_identity(self.project)
        except Exception as exc:  # noqa: BLE001 - диалогът е помощен, не критичен
            self._error(f"Позициите без доказана греда не могат да бъдат показани: {exc}")
            return

        answer = QMessageBox.question(
            self,
            "Позиции без доказана греда",
            f"{len(positions)} позиции нямат доказана греда и спират export-а.\n\n"
            "Да отворя ли списъка, за да ги посочиш?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = UnresolvedBeamDialog(
            positions,
            beam_titles,
            Path(self.project.root),
            identity,
            operator=self._operator_for_record(),
            parent=self,
        )
        accepted = dialog.exec()
        if getattr(dialog, "wants_drawing_overview", False):
            # Операторът предпочете да ги огради, вместо да ги изброява.
            self.drawing_view.clear_markers()
            self.show_beam_overview()
            QMessageBox.information(
                self,
                "Огради гредите",
                "\n".join(
                    [
                        "Червените кръгове са позициите без доказана греда,",
                        "сините — заглавията на гредите.",
                        "",
                        "За всяка греда: „Анализ → Рамка с два клика“ и",
                        "обхвани заглавието ѝ ЗАЕДНО с червените ѝ позиции.",
                        "",
                        "Накрая пусни Финалния контрол наново.",
                    ]
                ),
            )
            return
        if accepted and dialog.decisions_made:
            self._session_project_event(
                "BEAM_DECISIONS_RECORDED", {"count": dialog.decisions_made}
            )

    def _offer_unknown_frame_resolution(self) -> None:
        """Предлага на оператора да реши рамките с неопределена категория."""

        if self.project is None:
            return
        try:
            from lion_rebar.export.frame_detection import (
                apply_operator_frame_decisions,
                detect_drawing_frames,
                unresolved_frames,
            )
            from lion_rebar.export.frame_decisions import load_frame_decisions
            from lion_rebar.project.data_identity import current_identity
            from lion_rebar.ui.unknown_frame_dialog import UnknownFrameDialog

            identity = current_identity(self.project)
            entities = (
                self.import_result.entities
                if self.import_result is not None
                else []
            )
            frames = unresolved_frames(
                apply_operator_frame_decisions(
                    detect_drawing_frames(entities),
                    load_frame_decisions(Path(self.project.root), identity),
                )
            )
        except Exception:  # noqa: BLE001 - предложението не бива да чупи анализа
            self.logger.exception("Unknown-frame resolution preflight failed")
            return
        if not frames:
            return
        answer = QMessageBox.question(
            self,
            "Рамки с неопределена категория",
            f"{len(frames)} рамки нямат разпозната категория и блокират "
            "автоматичния Excel.\n\nДа ги решиш ли сега?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = UnknownFrameDialog(
            frames, Path(self.project.root), identity,
            operator=self._operator_for_record(), parent=self)
        if dialog.exec() and dialog.decisions_made:
            self._session_project_event(
                "FRAME_DECISIONS_RECORDED", {"count": dialog.decisions_made})

    def open_ownership_conflicts(self) -> None:
        """Операторският път за спорно владение на геометрия (§ 6.2).

        Споровете идват от ПОСЛЕДНИЯ пакетен отчет, защото владението се решава
        върху резултата от конкретен анализ: нов прогон може да даде други
        кандидати и други спорове. Затова и решението носи ``analysis_run_id``.
        """

        from lion_rebar.ui.ownership_conflict_dialog import (
            OwnershipConflictDialog,
            conflict_clusters,
        )

        if self.project is None:
            self._info("Първо отвори проект.")
            return
        report_path = self._find_latest_review_report()
        if report_path is None or not report_path.is_file():
            self._info(
                "Няма пакетен отчет. Пусни „Анализ → Пакетен анализ + Excel“, "
                "преди да решаваш спорове за владение."
            )
            return
        try:
            report = load_review_report(report_path)
        except ReviewReportError as exc:
            self._error(str(exc))
            return
        клъстери = conflict_clusters(report.rows)
        if not клъстери:
            self._info(
                "В последния анализ няма спорно владение на геометрия — нито "
                "един начертан обект не е първи кандидат на няколко надписа."
            )
            return
        self._ensure_operator_declared()
        dialog = OwnershipConflictDialog(
            клъстери,
            Path(self.project.root),
            current_identity(self.project),
            operator=self._operator_for_record(),
            parent=self,
        )
        if dialog.exec() and dialog.decisions_made:
            self._session_project_event(
                "OWNERSHIP_DECISIONS_RECORDED",
                {
                    "count": dialog.decisions_made,
                    "clusters": len(клъстери),
                },
            )
            self.statusBar().showMessage(
                "Записани решения за владение: %d. Пусни пакетния анализ наново."
                % dialog.decisions_made,
                12000,
            )

    def open_drawing_passport(self) -> None:
        """Паспорт на чертежа: предложените роли по съдържание за потвърждение.

        Профилът се записва с автор, време и самоличност на данните, но ЗАСЕГА
        не влиза в разпознаването — виж докстринга на
        ``project/convention_profile_decisions.py``.
        """

        if self.project is None or self.import_result is None:
            QMessageBox.information(
                self,
                "Няма чертеж",
                "Отвори проект и импортирай DXF, преди да искаш паспорт.",
            )
            return
        from lion_rebar.dxf.convention_profile import propose_convention_profile
        from lion_rebar.ui.drawing_passport_dialog import DrawingPassportDialog

        identity = current_identity(self.project)
        try:
            proposal = propose_convention_profile(
                self.import_result.entities,
                source_sha256=identity.source_sha256 or None,
            )
        except Exception as error:  # noqa: BLE001 - помощен път
            self._error(f"Паспортът не може да бъде съставен: {error}")
            return
        if not proposal.get("proposals") and not proposal.get("refusals"):
            QMessageBox.information(
                self,
                "Паспорт на чертежа",
                "Нито едно правило не намери доказателство в този чертеж.\n\n"
                "Това не е грешка — правилата мълчат, вместо да гадаят.",
            )
            return
        dialog = DrawingPassportDialog(
            proposal,
            Path(self.project.root),
            identity,
            self._operator_for_record(),
            self,
        )
        dialog.exec()

    def show_learning_report(self) -> None:
        """Отчет какви конвенции е открила програмата в този проект.

        Отчетът СЪБИРА доказателства и НЕ променя нито едно правило.
        Решението кое наблюдение да стане правило се взима от инженер.
        """

        if self.project is None:
            self._error("Отвори проект, преди да поискаш отчет.")
            return
        rows = list(getattr(self, "_batch_rows", None) or [])
        if not rows:
            self._error(
                "Няма данни за отчет. Пусни пакетен анализ и опитай отново.")
            return
        try:
            from lion_rebar.review.learning_report import (
                build_learning_report,
                stable_conventions,
                write_learning_report,
            )

            report = build_learning_report(self.project.name, rows)
            path = write_learning_report(Path(self.project.root), report)
        except Exception as exc:  # noqa: BLE001
            self._error(f"Отчетът не можа да бъде създаден: {exc}")
            return

        lines = ["<b>Открити конвенции в този проект</b><br>"]
        if report.conventions:
            for item in report.conventions:
                mark = "\u2713" if item.is_stable else "?"
                lines.append(
                    f"{mark} {item.name}: <b>{item.value}</b> "
                    f"({item.occurrences} от {item.total}, "
                    f"{item.consistency:.0%})<br>")
        else:
            lines.append("Няма изведени конвенции.<br>")
        stable = stable_conventions(report)
        lines.append(
            f"<br><b>Устойчиви:</b> {len(stable)} от {len(report.conventions)}<br>")
        lines.append(f"<br>Записан в: {path}")
        lines.append(
            "<br><br><i>Отчетът е доказателствен. Нито едно правило не се "
            "променя автоматично.</i>")
        box = QMessageBox(self)
        box.setWindowTitle("Отчет за конвенциите")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("".join(lines))
        box.exec()

    def _save_decision_with_takeover_prompt(self, payload: dict):
        """Записва инженерно решение; пита, ако отнема чужда геометрия.

        Позицията, която губи геометрията си, е била ОДОБРЕНА и ще се
        върне за преглед. Досега това ставаше мълчаливо и операторът
        виждаше нови нерешени позиции чак след Финалния контрол.
        """

        root = Path(self.project.root)
        try:
            return save_engineer_decision(root, payload)
        except GeometryTakeoverRequired as conflict:
            answer = QMessageBox.question(
                self,
                "Геометрията е заета",
                f"{conflict}\n\nДа продължа ли въпреки това?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            self._session_project_event(
                "GEOMETRY_TAKEOVER_CONFIRMED",
                {"losing_annotation": conflict.losing_annotation,
                 "annotation": str((payload.get("annotation") or {}).get("handle") or "")},
            )
            return save_engineer_decision(root, payload,
                                          allow_geometry_takeover=True)

    def _run_engineer_editor(
        self,
        editor: EngineerDecisionDialog,
        review_dialog: ReviewCenterDialog,
        *,
        log_message: str,
    ) -> tuple[dict, Path] | None:
        """Keep the same editor and its inputs alive until save succeeds/cancels."""

        while editor.exec() == QDialog.DialogCode.Accepted:
            payload = editor.result_payload
            if not isinstance(payload, dict):
                return None
            try:
                path = self._save_decision_with_takeover_prompt(payload)
            except Exception as exc:
                self.logger.exception(log_message)
                message = f"Инженерното решение не беше записано: {exc}"
                review_dialog.mark_error(message)
                editor.status_label.setText(message + "\nПолетата са запазени; коригирай и опитай отново.")
                editor.status_label.setStyleSheet("color:#b91c1c; font-weight:700;")
                QMessageBox.warning(self, "Инженерното решение не е записано", message)
                continue
            if path is None:
                editor.status_label.setText(
                    "Решението не е записано. Полетата са запазени; промени избора или натисни Отказ."
                )
                editor.status_label.setStyleSheet("color:#b91c1c; font-weight:700;")
                continue
            return payload, Path(path)
        return None

    def _warn_about_foreign_artifacts(self) -> None:
        """Предупреждава за артефакти, които не принадлежат на този проект.

        Изолационната проверка съществуваше, но резултатът ѝ се пазеше в
        ``last_isolation_report`` и никой не го четеше — защитата беше
        невидима за оператора.

        ПРЕДУПРЕЖДАВА, не отказва: още не знаем дали копирането на
        проектни папки е нормална практика, а забрана на нещо ежедневно
        чупи работата. Всяко предупреждение влиза в журнала на сесията.
        """

        report = getattr(self.project_manager, "last_isolation_report", None)
        if report is None:
            return
        artifacts = list(report.violations)
        if not artifacts:
            return
        lines = []
        for item in artifacts[:8]:
            name = item.artifact or "?"
            reason = item.reason or "чужда identity"
            lines.append(f"  \u2022 {name} — {reason}")
        if len(artifacts) > 8:
            lines.append(f"  \u2022 ... и още {len(artifacts) - 8}")
        self._session_project_event(
            "FOREIGN_ARTIFACTS_DETECTED",
            {"count": len(artifacts),
             "artifacts": [item.artifact for item in artifacts]},
        )
        QMessageBox.warning(
            self,
            "Артефакти от друг проект",
            "В работната папка има файлове, които НЕ принадлежат на този "
            "проект:\n\n" + "\n".join(lines) +
            "\n\nТова обикновено значи копирана папка. Прегледай ги, преди "
            "да пуснеш анализ — стари решения от друг проект могат да "
            "повлияят на резултата.",
        )

    def _apply_known_engine_correction(self) -> None:
        """Прехвърля проекта през ЕДНА изрично разрешена семантична поправка.

        Всяка корекция се вписва тук с точните версии, кодовете, чиято
        самоличност се променя, и текста към оператора. Нищо не се мигрира
        мълчаливо: комбинация, която не е в таблицата, оставя проекта на
        неговия snapshot (виж ``docs/ENGINE_UPGRADE_AND_APPROVAL_BG.md``).
        """

        if self.project is None:
            return
        current = self.engine_repository.load_current()
        correction = next(
            (
                item
                for item in _KNOWN_ENGINE_CORRECTIONS
                if item.to_version == current.version
                and self.project.engine_version in item.from_versions
            ),
            None,
        )
        if correction is None:
            return
        from_version = self.project.engine_version
        returned = self.project_manager.migrate_known_engine_snapshot(
            self.project,
            current,
            allowed_from_versions=set(correction.from_versions),
            reason=(
                f"Engine v{from_version} to v{correction.to_version}: "
                + correction.reason
            ),
            return_to_review_shape_codes=set(correction.review_shape_codes),
        )
        self._refresh_engine_label()
        codes = "/".join(sorted(correction.review_shape_codes))
        QMessageBox.information(
            self,
            f"Engine корекция v{correction.to_version}",
            f"Проектът е прехвърлен от Engine v{from_version} към "
            f"v{correction.to_version}. " + correction.message
            + (
                f"\n\n{returned} стари {codes} одобрения са върнати за нов анализ."
                if returned
                else ""
            ),
        )

    def save_project(self) -> None:
        if self.project is None:
            self._info("Няма отворен проект.")
            return
        if self.scene_result is not None:
            self.project.layer_visibility = self.layer_panel.visibility()
        try:
            path = self.project_manager.save(self.project)
            self.statusBar().showMessage(f"Записан проект: {path}", 5000)
        except ProjectError as exc:
            self._error(str(exc))

    def import_dxf(self) -> None:
        if self.project is None:
            self._info("Първо създай или отвори проект.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Импорт DXF", "", "DXF (*.dxf)")
        if not filename:
            return
        try:
            previous_generation = current_identity(self.project).source_generation_id
            copied = self.project_manager.import_dxf_source(self.project, Path(filename))
            current_generation = current_identity(self.project).source_generation_id
            if current_generation != previous_generation:
                self._last_batch_report_path = None
            self._load_dxf_from_project(copied, show_report=True)
        except (ProjectError, DxfImportError) as exc:
            self._error(str(exc))

    def _load_dxf_from_project(self, path: Path, *, show_report: bool) -> None:
        # A Review Center row and its selection scene belong to one immutable
        # DXF import.  Tear them down synchronously before either import_result
        # or the scene can be replaced, so a delayed signal cannot combine an
        # old annotation with a handle from the new drawing.
        self._close_review_center()
        self._close_geometry_dialog()
        self._clear_geometry_highlights()
        self._invalidate_manual_geometry_guard_cache()
        importer = DxfImporter()
        previous_import_result = self.import_result
        previous_scene_result = self.scene_result
        previous_drawing_scene = self.drawing_view.scene()
        previous_loaded_generation = self._loaded_source_generation_id
        current_source_generation = (
            current_identity(self.project).source_generation_id
            if self.project is not None
            else None
        )
        source_identity_changed = bool(
            previous_import_result is not None
            and previous_loaded_generation != current_source_generation
        )
        commit_succeeded = False
        project_write_started = False
        previous_project_state = None
        if self.project is not None:
            previous_project_state = (
                dict(self.project.import_summary or {}),
                self.project.units,
                dict(self.project.layer_visibility or {}),
            )
        QApplicationOverrideCursor.set_wait()
        try:
            result = importer.import_file(path)
            source_sha256 = None
            if self.project:
                source_sha256 = str(self.project.extensions.get("source_sha256") or "") or None
            layer_report = classify_layers(
                result.entities,
                declared_layers=result.summary.layers,
                source_sha256=source_sha256,
            )
            layer_roles = classification_by_layer(layer_report)
            previous_visibility = (
                dict(self.project.layer_visibility or {})
                if self.project is not None
                else {}
            )
            staged_visibility = {
                layer: (
                    previous_visibility[layer]
                    if layer in previous_visibility
                    else bool((layer_roles.get(layer) or {}).get("default_visible", True))
                )
                for layer in result.summary.layers
            }
            # All fallible parsing/classification/scene work is staged before
            # either half of the public import/scene pair is published.
            staged_scene_result = build_scene(
                result.entities,
                result.display_entities,
            )
            if self.project:
                self.project.import_summary = result.summary.to_dict()
                self.project.units = result.summary.units_name
                self.project.layer_visibility = dict(staged_visibility)
                # From this point a write can have partially or fully committed
                # the new working/source state.  Any subsequent failure must
                # clear the viewer, never resurrect the old scene in memory.
                project_write_started = True
                self.project_manager.save_extracted_entities(
                    self.project,
                    result.to_dict(),
                )
                self.project_manager.save_layer_classification(
                    self.project,
                    layer_report,
                )
                self.project_manager.save(self.project)
            try:
                self.import_result = result
                self.scene_result = staged_scene_result
                self.drawing_view.setScene(staged_scene_result.scene)
                staged_scene_result.scene.selectionChanged.connect(
                    self._on_scene_selection_changed
                )
                self.entity_panel.clear()
                self.layer_panel.set_layers(
                    result.summary.layers,
                    staged_visibility,
                    classifications=layer_roles,
                )
                for layer, visible in staged_visibility.items():
                    self._set_layer_visibility(layer, visible)
                # This is the existing blocking import path and the wait cursor
                # is still active.  Build authorization once here; the first
                # manual click then performs only a cache lookup.
                self._manual_geometry_guard_handles()
                self.drawing_view.fit_all()
            except Exception:
                fail_closed_required = source_identity_changed or project_write_started
                if fail_closed_required:
                    self.import_result = None
                    self.scene_result = None
                    self._loaded_source_generation_id = None
                    try:
                        self.drawing_view.setScene(None)
                    except RuntimeError:
                        pass
                else:
                    self.import_result = previous_import_result
                    self.scene_result = previous_scene_result
                    self._loaded_source_generation_id = previous_loaded_generation
                    try:
                        self.drawing_view.setScene(previous_drawing_scene)
                    except RuntimeError:
                        pass
                if (
                    not fail_closed_required
                    and self.project is not None
                    and previous_project_state is not None
                ):
                    (
                        self.project.import_summary,
                        self.project.units,
                        self.project.layer_visibility,
                    ) = previous_project_state
                    previous_layers = []
                    if previous_import_result is not None:
                        previous_layers = list(previous_import_result.summary.layers)
                    self.layer_panel.set_layers(
                        previous_layers,
                        self.project.layer_visibility,
                    )
                elif fail_closed_required:
                    self.layer_panel.set_layers([])
                self.entity_panel.clear()
                self._invalidate_manual_geometry_guard_cache()
                raise
            self._loaded_source_generation_id = current_source_generation
            commit_succeeded = True
            if show_report:
                ImportReportDialog(result.summary, self, layer_report=layer_report).exec()
            if self.project:
                self._session_project_event(
                    "DXF_IMPORTED" if show_report else "DXF_RELOADED",
                    {
                        "filename": result.summary.filename,
                        "units": result.summary.units_name,
                        "total_entities": result.summary.total_entities,
                        "potential_rebar_texts": result.summary.potential_rebar_texts,
                        "proxy_display_instances": (
                            result.summary.proxy_display_instance_count
                        ),
                        "proxy_display_primitives": (
                            result.summary.proxy_display_rendered_primitive_count
                        ),
                        "proxy_display_partial": (
                            result.summary.proxy_display_partial_count
                        ),
                        "proxy_display_unreadable": (
                            result.summary.proxy_display_unreadable_count
                        ),
                        "layer_classification": dict(layer_report.get("summary") or {}),
                        "layer_classification_path": "working/layer_classification.json",
                    },
                )
            self.logger.info(
                "Imported DXF %s: %s engineering entities, %s potential rebar texts, "
                "%s display-only proxy primitives",
                path,
                result.summary.total_entities,
                result.summary.potential_rebar_texts,
                result.summary.proxy_display_rendered_primitive_count,
            )
        except Exception:
            if (
                (source_identity_changed or project_write_started)
                and not commit_succeeded
            ):
                self.import_result = None
                self.scene_result = None
                self._loaded_source_generation_id = None
                try:
                    self.drawing_view.setScene(None)
                except RuntimeError:
                    pass
                self.layer_panel.set_layers([])
                self.entity_panel.clear()
                self._invalidate_manual_geometry_guard_cache()
            raise
        finally:
            QApplicationOverrideCursor.restore()
        # Сцената е нова: рамките се пречертават върху нея. Тук се хваща и
        # отварянето на започнат проект, не само току-що начертана рамка.
        self._refresh_operator_areas()

    def _on_scene_selection_changed(self) -> None:
        if self.scene_result is None:
            return
        selected = self.scene_result.scene.selectedItems()
        if not selected:
            self.entity_panel.clear()
            return
        handle = selected[0].data(0)
        record = self.scene_result.handle_to_record.get(str(handle))
        if record:
            entities = self.import_result.entities if self.import_result else []
            analysis = analyze_rebar_annotation(record, entities)
            self.entity_panel.show_record(record, analysis)
            if analysis.recognized:
                self.statusBar().showMessage(
                    "Армировъчният надпис е анализиран. Позиция и стъпка не се свързват по близост; геометрията още не е свързана.",
                    7000,
                )
            if self.project:
                self.project.selected_handle = record.handle

    def _set_layer_visibility(self, layer: str, visible: bool) -> None:
        if self.scene_result:
            self.scene_result.set_layer_visibility(layer, visible)
        if self.project:
            self.project.layer_visibility[layer] = visible

    def open_rebar_inventory(self) -> None:
        if self.project is None or self.import_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        rows, summary = build_rebar_inventory(self.import_result.entities)
        if not rows:
            self._info("Не са намерени ARM_N блокове с пълен директен източник BJ/FI/L.")
            return
        default_path = (
            Path(self.project.root)
            / "output"
            / "ARM_N_inventory_v0.1.7.csv"
        )
        dialog = RebarInventoryDialog(rows, summary, default_path, self)
        dialog.handleActivated.connect(self._select_inventory_handle)
        dialog.exec()

    def _select_inventory_handle(self, handle: str) -> None:
        if self.scene_result is None:
            return
        item = self.scene_result.handle_to_item.get(handle)
        if item is None:
            return
        self.scene_result.scene.clearSelection()
        item.setVisible(True)
        item.setSelected(True)
        self.drawing_view.centerOn(item)
        self.drawing_view.ensureVisible(item, 100, 100)

    def _analysis_entities(self):
        if self.import_result is None or self.project is None:
            return []
        path = Path(self.project.root) / "working" / "layer_classification.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        noise_layers = high_confidence_noise_layers(payload)
        if not noise_layers:
            return list(self.import_result.entities)
        filtered = filter_analysis_entities(self.import_result.entities, payload)
        self.logger.info(
            "Layer prefilter ignored %s high-confidence noise entities on %s layers",
            len(self.import_result.entities) - len(filtered),
            len(noise_layers),
        )
        return filtered

    def run_batch_geometry_analysis(self) -> None:
        if self.project is None or self.import_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        if self._batch_thread is not None and self._batch_thread.isRunning():
            self._info("Вече се изпълнява пакетен анализ.")
            return

        # Нова форма да влезе в този анализ, а не чак при следващото отваряне.
        # Собственик, 22.09.2026: „когато направя нова форма, да може да се
        # разпознава… в този чертеж“. Пренася се само добавка — проверката е
        # същата и отказва при всяка промяна в съществуваща форма.
        try:
            добавени = self.project_manager.migrate_additive_engine(
                self.project,
                self.engine_repository.load_current(),
                by="Пакетен анализ",
            )
        except Exception:  # noqa: BLE001 — анализът не бива да пада заради това
            добавени = ()
        if добавени:
            self._refresh_engine_label()
            self._info(
                "Проектът е пренесен към engine с %d нови форми: %s.\n"
                "Нищо съществуващо не е променяно."
                % (len(добавени), ", ".join(добавени))
            )

        output_parent = self._detect_cloud_review_folder()
        using_cloud = output_parent is not None
        if output_parent is None:
            output_parent = Path(self.project.root) / "output" / "04_REVIEW_REPORTS"
        output_parent.mkdir(parents=True, exist_ok=True)

        try:
            approved_links = self._read_all_approved_geometry_links()
            engine = load_pinned_engine(self.project, self.engine_repository.load_current())
            analysis_entities = self._analysis_entities()
            total_annotations = count_batch_annotations(
                analysis_entities,
                policy=engine.settings,
            )
        except Exception as exc:
            self.logger.exception("Cannot prepare batch geometry analysis")
            self._error(f"Пакетният анализ не може да стартира.\n\n{exc}")
            return
        if total_annotations <= 0:
            self._info("Не са намерени валидни армировъчни надписи за пакетен анализ.")
            return
        try:
            data_identity = begin_analysis_run(self.project)
            self.project_manager.save(self.project)
        except Exception as exc:
            self.logger.exception("Cannot start a new analysis run identity")
            self._error(f"Пакетният анализ не може да създаде нов analysis run.\n\n{exc}")
            return

        self._batch_using_cloud = using_cloud
        self._batch_archived_source = (
            self._archive_current_dxf_to_cloud() if using_cloud else None
        )

        progress = QProgressDialog(
            "Подготовка на геометричния индекс...",
            "Прекрати",
            0,
            total_annotations,
            self,
        )
        progress.setWindowTitle("LION REBAR — пакетен анализ")
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.resize(560, progress.sizeHint().height())

        thread = QThread(self)
        worker = BatchGeometryWorker(
            entities=analysis_entities,
            output_parent=output_parent,
            project_name=self.project.name,
            approved_links=approved_links,
            shape_definitions=engine.shapes,
            control_tests=engine.tests,
            # Политиката на engine-а, наслоена с потвърдения профил на
            # чертежа: обявените от оператора слоеве влизат в
            # разпознаването. Без профил се връща непроменена.
            # Една и съща логика с безинтерфейсния път: потвърденият профил
            # плюс НАУЧЕНОТО. Виж ``learning/integration.prepare_policy``.
            policy=self._политика_с_naucheno(
                engine.settings, Path(self.project.root),
                identity=data_identity, entities=analysis_entities,
            ),
            engine_metadata={
                "engine_id": engine.engine_id,
                "engine_version": engine.version,
                "content_sha256": engine.content_sha256,
            },
            project=self.project,
            engine=engine,
            source_dxf_path=(
                self.project_manager.source_dxf_path(self.project)
                if self.project is not None
                else None
            ),
            data_identity=data_identity,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._batch_progress_updated)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._batch_finished)
        worker.failed.connect(thread.quit)
        worker.failed.connect(worker.deleteLater)
        worker.failed.connect(self._batch_failed)
        thread.finished.connect(self._batch_thread_finished)
        thread.finished.connect(thread.deleteLater)
        progress.canceled.connect(self._request_batch_cancel)

        self._batch_thread = thread
        self._batch_worker = worker
        self._batch_progress = progress
        self._set_batch_running(True)
        progress.show()
        thread.start()
        self.statusBar().showMessage(
            f"Пакетен анализ: 0 от {total_annotations}. Програмата остава активна.",
            10000,
        )

    def _политика_с_naucheno(self, settings, project_root, *, identity, entities):
        """Политиката за анализа, наслоена с наученото; пази състоянието за лентата."""

        from lion_rebar.learning.integration import prepare_policy

        име = self.project.name if self.project is not None else ""
        policy, състояние, мост = prepare_policy(
            settings, project_root, identity=identity,
            entities=entities, drawing=име,
        )
        self._learning_status = състояние
        self._learning_bridge = мост
        self._learning_entities = list(entities)
        if състояние.enabled:
            self.statusBar().showMessage(състояние.short_line(), 12000)
        return policy

    def _pusni_uchebniya_cikal(self) -> None:
        """Ученето СЛЕД анализа, в отделна нишка — интерфейсът не се блокира."""

        състояние = getattr(self, "_learning_status", None)
        if състояние is None or not състояние.enabled:
            return
        записи = getattr(self, "_learning_entities", None) or []
        мост = getattr(self, "_learning_bridge", None) or {}
        име = self.project.name if self.project is not None else ""

        # Самоличността на изходния DXF: копие под друго име на проект НЕ е
        # второ доказателство (вж. `auto_cycle.record_observation`).
        сха = ""
        if self.project is not None:
            сха = str((self.project.extensions or {}).get("source_sha256") or "")

        нишка = QThread(self)
        обект = _UchebenCikal(състояние, мост, записи, име, сха)
        обект.moveToThread(нишка)
        нишка.started.connect(обект.run)
        обект.done.connect(self._uchebniyat_cikal_zavurshi)
        обект.done.connect(нишка.quit)
        обект.done.connect(обект.deleteLater)
        нишка.finished.connect(нишка.deleteLater)
        # И ДВЕТЕ препратки се пазят: без тази към работния обект Python го
        # събира, преди нишката да е тръгнала, и ``done`` никога не се обажда.
        self._learning_thread = нишка
        self._learning_worker = обект
        нишка.start()

    def _uchebniyat_cikal_zavurshi(self, състояние) -> None:
        self._learning_status = състояние
        self.statusBar().showMessage(състояние.short_line(), 15000)

    def _batch_progress_updated(self, current: int, total: int, label: str) -> None:
        if self._batch_progress is not None:
            if self._batch_progress.maximum() != total:
                self._batch_progress.setMaximum(total)
            self._batch_progress.setValue(current)
            self._batch_progress.setLabelText(
                f"Обработени {current} от {total}\n{label}"
            )
        self.statusBar().showMessage(
            f"Пакетен анализ: {current} от {total} — {label}",
            5000,
        )

    def _request_batch_cancel(self) -> None:
        worker = self._batch_worker
        if worker is None:
            return
        worker.request_cancel()
        if self._batch_progress is not None:
            self._batch_progress.setLabelText(
                "Прекратяването е заявено. Текущата позиция ще бъде довършена, "
                "а междинният отчет ще бъде запазен."
            )
        self.statusBar().showMessage(
            "Пакетният анализ се прекратява контролирано...",
            10000,
        )

    def _batch_finished(self, result) -> None:
        if self._batch_progress is not None:
            self._batch_progress.setValue(result.processed_annotations)
            self._batch_progress.close()
            self._batch_progress.deleteLater()
            self._batch_progress = None

        # Ученето тръгва ВЕДНАГА след анализа, в отделна нишка. Нарочно е тук, а
        # не в края: по-долу има диалози към оператора и цикълът не бива да чака
        # той да ги затвори.
        self._pusni_uchebniya_cikal()

        destination_label = "Google Drive" if self._batch_using_cloud else "папката output на проекта"
        title = (
            "Пакетният анализ е прекратен — частичният отчет е запазен"
            if result.cancelled
            else "Пакетният анализ е готов"
        )
        self._last_batch_report_path = Path(result.json_path)
        if self.project is not None:
            self.project.extensions["latest_batch_report_path"] = str(result.json_path)
            self.project.extensions["latest_batch_report_generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            try:
                self.project_manager.save(self.project)
            except ProjectError:
                pass
        auto_approval = getattr(result, "auto_approval", None)
        auto_approval_error = str(getattr(result, "auto_approval_error", "") or "")
        if auto_approval is not None and not auto_approval.failed:
            reason_lines = "".join(
                f"   за преглед по {code}: {count}\n"
                for code, count in sorted(
                    auto_approval.blocked_reason_counts.items(),
                    key=lambda item: -item[1],
                )
            )
            auto_output_message = (
                "\nАвтоматично одобрени по производствената порта: "
                f"{auto_approval.newly_approved_count} "
                f"от {auto_approval.actionable_count}\n"
                f"Запазени ръчни одобрения: {auto_approval.kept_manual_count}\n"
                f"Наистина ръчни за Review Center: {auto_approval.blocked_count}\n"
                + reason_lines
            )
        elif auto_approval_error:
            auto_output_message = (
                "\nАвтоматичното одобрение не е приложено. Пакетният отчет е запазен.\n"
                f"Причина: {auto_approval_error}\n"
            )
            # Рамка с неопределена категория блокира изхода, но операторът
            # има какво да реши. Досега backend-ът съществуваше без път до
            # него и позицията оставаше блокирана без изход.
            if "неопределена категория" in str(auto_approval_error):
                auto_output_message += (
                    "Отвори „Рамки с неопределена категория“, за да ги решиш.\n"
                )
                self._offer_unknown_frame_resolution()
        else:
            auto_output_message = (
                "\nАвтоматично одобрение не е прилагано за прекратен анализ.\n"
            )
        message = (
            f"Обработени надписи: {result.processed_annotations} от {result.total_annotations}\n"
            f"Реални позиции с пълен брой/Ø/L: {result.actionable_count}\n"
            f"Контекстни или непълни надписи: {result.context_count}\n"
            f"Позиции с кандидат: {result.candidate_count}\n"
            f"F003/F005, разрешени по именуван контекст: {result.named_u_subtype_context_resolved_count}\n"
            f"Силни с потвърден Ø от слоя: {result.strong_count}\n"
            f"Силни по геометрия (Ø от надписа): {result.geometry_strong_count}\n"
            f"За ръчен преглед: {result.review_count}\n"
            f"Рискови: {result.risk_count}\n"
            f"Без кандидат: {result.no_candidate_count}\n"
            f"Възстановени с резервен кандидат: {result.ownership_reassigned_count}\n"
            f"От тях силни с пълно доказателство: {result.ownership_recovery_verified_count}\n"
            f"Позиции с приложени размери A–E: {result.dimension_evidence_applied_count}\n"
            f"MASTER двусмислия, разрешени по размери A–E: {result.dimension_master_resolved_count}\n"
            f"Локални равенства, разрешени по размери A–E: {result.dimension_candidate_tiebreak_count}\n"
            f"Точни F007 в тесния разширен радиус: {result.f007_extended_association_count}\n"
            f"Разрешени чрез позиционен ключ и легенда: {result.position_key_legend_resolved_count}\n"
            f"От тях заместени слаби кандидати: {result.position_key_manual_override_count}\n"
            f"Разрешени чрез отделния F001 допуск: {result.f001_extended_exact_length_count}\n"
            f"Разрешени чрез proxy parent provenance: {result.proxy_parent_provenance_count}\n"
            f"MASTER двусмислия: {result.master_ambiguity_count}\n"
            f"Конфликти за DXF собственост: {result.ownership_conflict_count}\n"
            f"Разрешени конфликти с тройно доказателство: {result.ownership_tiebreak_resolved_count}\n"
            f"Общо оставащи конфликти: {result.conflict_count}\n"
            f"Ръчни одобрения, пропуснати при повторния анализ: {result.approved_count}\n"
            f"Грешки по отделни надписи: {result.error_count}\n\n"
            f"Записано в {destination_label}:\n{result.report_dir}\n"
            + (
                f"Архивиран DXF: {self._batch_archived_source}\n"
                if self._batch_archived_source
                else ""
            )
            + auto_output_message
        )
        self.statusBar().showMessage(
            f"Пакетен анализ: {result.html_path}",
            12000,
        )
        self._session_project_event(
            "BATCH_COMPLETED" if not result.cancelled else "BATCH_CANCELLED",
            {
                "report_path": str(result.json_path),
                "report_dir": str(result.report_dir),
                "processed_annotations": result.processed_annotations,
                "total_annotations": result.total_annotations,
                "actionable_count": result.actionable_count,
                "candidate_count": result.candidate_count,
                "strong_count": result.strong_count,
                "geometry_strong_count": result.geometry_strong_count,
                "review_count": result.review_count,
                "risk_count": result.risk_count,
                "no_candidate_count": result.no_candidate_count,
                "master_ambiguity_count": result.master_ambiguity_count,
                "ownership_conflict_count": result.ownership_conflict_count,
                "conflict_count": result.conflict_count,
                "error_count": result.error_count,
                "auto_approved_count": (
                    auto_approval.newly_approved_count
                    if auto_approval is not None and not auto_approval.failed
                    else 0
                ),
                "auto_blocked_count": (
                    auto_approval.blocked_count
                    if auto_approval is not None and not auto_approval.failed
                    else None
                ),
                "auto_blocked_reason_counts": (
                    dict(auto_approval.blocked_reason_counts)
                    if auto_approval is not None and not auto_approval.failed
                    else {}
                ),
            },
            report_path=Path(result.json_path),
        )
        needs_review = (
            result.cancelled
            or auto_approval is None
            or auto_approval.failed
            or auto_approval.blocked_count > 0
        )
        if needs_review:
            answer = QMessageBox.question(
                self,
                title,
                message
                + "\nНерешените позиции не са включени в Excel. "
                "Да отворя ли Review Center сега?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.open_review_center(Path(result.json_path))
        else:
            QMessageBox.information(
                self,
                "Пакетният анализ и Excel са готови",
                message + "\nНяма блокирани производствени позиции.",
            )

    def _batch_failed(self, message: str, traceback_text: str) -> None:
        self.logger.error("Batch geometry analysis failed: %s\n%s", message, traceback_text)
        self._session_project_event(
            "BATCH_FAILED",
            {"message": message, "traceback_tail": str(traceback_text or "")[-4000:]},
            severity="ERROR",
        )
        if self._batch_progress is not None:
            self._batch_progress.close()
            self._batch_progress.deleteLater()
            self._batch_progress = None
        self._error(
            "Пакетният анализ спря поради системна грешка. "
            "Ако е създаден progress.json, междинните резултати са запазени "
            "в папката с данни на приложението, подпапка batch_progress.\n\n"
            f"{message}"
        )

    def _batch_thread_finished(self) -> None:
        self._batch_thread = None
        self._batch_worker = None
        self._set_batch_running(False)

    def _set_batch_running(self, running: bool) -> None:
        for action in (
            self.action_new_project,
            self.action_open_project,
            self.action_import_dxf,
            self.action_batch_geometry,
            self.action_review_center,
            self.action_ai_diagnostic,
            self.action_engine_studio,
            self.action_shape_builder,
            self.action_end_session,
            self.action_layer_report,
        ):
            action.setEnabled(not running)

    def _detect_cloud_review_folder(self) -> Path | None:
        """Find the locally synchronized Drive review folder without hard-coding G:."""
        relative_candidates = (
            Path("My Drive") / "LION_REBAR_CLOUD" / "04_REVIEW_REPORTS",
            Path("Моят диск") / "LION_REBAR_CLOUD" / "04_REVIEW_REPORTS",
            Path("LION_REBAR_CLOUD") / "04_REVIEW_REPORTS",
        )
        roots: list[Path] = []
        for variable in ("GOOGLE_DRIVE", "GOOGLEDRIVE", "GDRIVE"):
            value = os.environ.get(variable)
            if value:
                roots.append(Path(value))
        roots.extend([Path.home(), Path.home() / "Google Drive"])
        if os.name == "nt":
            roots.extend(Path(f"{letter}:\\") for letter in string.ascii_uppercase)

        seen: set[str] = set()
        for root in roots:
            for relative in relative_candidates:
                candidate = root / relative
                normalized = str(candidate).casefold()
                if normalized in seen:
                    continue
                seen.add(normalized)
                try:
                    if candidate.is_dir():
                        return candidate
                except OSError:
                    continue
        return None

    def _archive_current_dxf_to_cloud(self) -> Path | None:
        if self.project is None:
            return None
        review_folder = self._detect_cloud_review_folder()
        if review_folder is None:
            return None
        source = self.project_manager.source_dxf_path(self.project)
        if source is None or not source.is_file():
            return None
        target_folder = review_folder.parent / "03_TEST_DXF"
        target_folder.mkdir(parents=True, exist_ok=True)
        target = target_folder / f"{self.project.name}_AUTO.dxf"
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            self.logger.warning("Cannot archive DXF to Google Drive: %s", exc)
            return None
        return target

    def _read_all_approved_geometry_links(self) -> dict[str, dict]:
        output_path = self._approved_geometry_links_path()
        if output_path is None or not output_path.exists():
            return {}
        try:
            data = json.loads(output_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        links = data.get("links", []) if isinstance(data, dict) else []
        result: dict[str, dict] = {}
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, dict):
                    continue
                handle = str(link.get("annotation_handle", ""))
                if handle:
                    result[handle] = dict(link)
        return result

    def _review_entry_decision(self) -> ReviewRecoveryDecision:
        """P0-3: решението се взима от ЕДИНСТВЕНАТА обща функция.

        Тестовете упражняват точно този код — няма дублирана логика между
        UI и тестовете.
        """

        candidates: list[tuple[Path, dict | None]] = []
        latest = self._find_latest_review_report()
        if latest is not None and latest.is_file():
            payload: dict | None
            try:
                payload = json.loads(latest.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                payload = None
            candidates.append((latest, payload))
        identity = None
        if self.project is not None:
            try:
                identity = current_identity(self.project)
            except Exception:  # noqa: BLE001 - липсваща identity е валидно състояние
                identity = None
        return decide_review_entry(
            has_project=self.project is not None,
            has_import=self.import_result is not None and self.scene_result is not None,
            current=identity,
            candidates=candidates,
            project_name=self.project.name if self.project is not None else "",
        )

    def open_review_center(self, report_path: Path | None = None) -> None:
        if isinstance(report_path, bool):
            report_path = None
        if report_path is None:
            decision = self._review_entry_decision()
            if decision.action == ACTION_BLOCKED:
                self._info(decision.message)
                return
            if decision.opens_directly:
                report_path = decision.report_path
            else:
                self._pending_review_decision = decision
        if self.project is None or self.import_result is None or self.scene_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        resolved = Path(report_path) if report_path else None
        if resolved is None or not resolved.is_file():
            # P0-3: файловият диалог НЕ е нормалният workflow. Ако няма
            # валиден Review за текущия analysis run, операторът получава
            # изричен избор, а не диалог за вътрешен JSON.
            choice = QMessageBox(self)
            choice.setWindowTitle("Няма Review за текущия анализ")
            choice.setIcon(QMessageBox.Icon.Question)
            pending = getattr(self, "_pending_review_decision", None)
            choice.setText(
                pending.message if pending is not None
                else "За текущия проект и анализ няма валиден пакетен Review отчет."
            )
            choice.setInformativeText("Какво да направя?")
            run_button = choice.addButton(
                "Стартирай нов анализ", QMessageBox.ButtonRole.AcceptRole)
            historical_button = choice.addButton(
                "Избери исторически анализ", QMessageBox.ButtonRole.ActionRole)
            choice.addButton("Откажи", QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            clicked = choice.clickedButton()
            if clicked is run_button:
                self.run_batch_geometry_analysis()
                return
            if clicked is not historical_button:
                return
            filename, _ = QFileDialog.getOpenFileName(
                self,
                "Избери исторически пакетен JSON отчет",
                "",
                "LION REBAR batch JSON (*_geometry_batch.json);;JSON (*.json)",
            )
            if not filename:
                return
            resolved = Path(filename)
        try:
            report = load_review_report(resolved)
        except ReviewReportError as exc:
            self._error(str(exc))
            return
        if report.project_name and report.project_name != self.project.name:
            self._error(
                f"Отчетът е за проект „{report.project_name}“, а отвореният проект е "
                f"„{self.project.name}“. Review Center отказва смесване на проекти."
            )
            return
        if not self._review_report_matches_current_project(resolved):
            self._error(
                "Отчетът не принадлежи изцяло на текущия DXF/analysis run. "
                "Пусни нов пакетен анализ; Review Center отказва смесени или стари редове."
            )
            return

        self._close_review_center()
        self._last_batch_report_path = resolved
        state_path = Path(self.project.root) / "working" / "review_center_state.json"
        review_dock = QDockWidget(f"Review Center v{APP_VERSION}", self)
        review_dock.setObjectName("ReviewCenterDock")
        review_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        review_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        review_dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        try:
            dialog = ReviewCenterDialog(
                resolved,
                state_path=state_path,
                approved_links=self._read_all_approved_geometry_links(),
                embedded=True,
                project_root=Path(self.project.root),
                operator_name=self.operator_name,
                parent=review_dock,
            )
        except ReviewStateError as exc:
            # Нечетим файл със състоянието: прозорецът НЕ се отваря, за да не
            # презапише единственото копие на операторската работа с празно.
            review_dock.deleteLater()
            self._error(str(exc))
            return
        review_dock.setWidget(dialog)
        dialog.previewRequested.connect(self._review_preview_row)
        dialog.referenceGeometryRequested.connect(self._review_preview_reference_geometry)
        dialog.locateAnnotationRequested.connect(self._review_locate_annotation)
        dialog.fullCandidatesRequested.connect(self._review_open_candidates)
        dialog.approvalRequested.connect(self._review_approve_row)
        dialog.bulkApprovalRequested.connect(self._review_bulk_approve_rows)
        dialog.geometryBulkApprovalRequested.connect(self._review_bulk_approve_geometry_rows)
        dialog.f006BulkApprovalRequested.connect(self._review_bulk_approve_f006_rows)
        dialog.finalBulkConfirmationRequested.connect(self._review_final_confirm_rows)
        dialog.engineerDecisionRequested.connect(self._review_engineer_decision)
        dialog.formAssignmentRequested.connect(self._review_begin_form_assignment)
        dialog.newShapeFromCaseRequested.connect(self._review_new_shape_from_case)
        dialog.allNewShapesRequested.connect(self._review_all_new_shapes)
        dialog.decisionRecorded.connect(self._review_decision_recorded)
        dialog.decisionsChanged.connect(self._review_group_decisions_changed)
        dialog.diagnosticExportRequested.connect(self.create_ai_diagnostic_bundle)
        dialog.cancelSelectionRequested.connect(self._cancel_manual_geometry_selection)
        review_dock.visibilityChanged.connect(
            lambda visible, current=review_dock: self._review_dock_visibility_changed(current, visible)
        )
        review_dock.destroyed.connect(
            lambda _obj=None, current_dock=review_dock, current=dialog: self._review_dock_destroyed(
                current_dock, current
            )
        )
        self._review_center = dialog
        self._review_dock = review_dock
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, review_dock)
        self.splitDockWidget(self.entity_dock, review_dock, Qt.Orientation.Vertical)
        self._restore_review_dock_state(review_dock)
        review_dock.show()
        review_dock.raise_()
        dialog.show()
        self.drawing_view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _review_decision_recorded(self, annotation_handle: str, decision: dict) -> None:
        code = str((decision or {}).get("decision") or "")
        self._record_review_event(
            code or "REVIEW_DECISION",
            {
                "annotation_handle": str(annotation_handle),
                "decision": dict(decision or {}),
            },
        )
        if code not in {
            "PROBLEM_RECORDED", "EXCLUDED_BY_ENGINEER", "NON_PRODUCTION_POSITION", "FLAGGED", "NO_VALID_CANDIDATE", "MISSING_MASTER"
        }:
            return
        try:
            self._revoke_approved_geometry_link(
                str(annotation_handle),
                reason=str((decision or {}).get("reason") or (decision or {}).get("reason_code") or code),
                decision_code=code,
            )
        except Exception as exc:
            self.logger.exception("Failed to revoke stale approval after Review Center decision")
            self._error(
                "Решението е записано, но старото одобрение не можа да бъде премахнато. "
                f"Пусни „Финален контрол“.\n\n{exc}"
            )

    @_locked_project_file("working/approved_geometry_links.json")
    def _revoke_approved_geometry_link(
        self,
        annotation_handle: str,
        *,
        reason: str,
        decision_code: str,
    ) -> bool:
        output_path = self._approved_geometry_links_path()
        if output_path is None or not output_path.is_file():
            return False
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("links", []), list):
            raise RuntimeError("approved_geometry_links.json е с неочаквана структура.")
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        retained: list[dict] = []
        history = [dict(value) for value in payload.get("history", []) if isinstance(value, dict)]
        removed = False
        for raw in payload.get("links", []):
            if not isinstance(raw, dict):
                continue
            link = dict(raw)
            if str(link.get("annotation_handle") or "") != str(annotation_handle):
                retained.append(link)
                continue
            removed = True
            link.update(
                {
                    "revoked_at": timestamp,
                    "revoked_by": "REVIEW_CENTER_DECISION",
                    "revocation_decision": decision_code,
                    "revocation_reason": reason,
                }
            )
            history.append(link)
        if not removed:
            return False
        payload["links"] = retained
        payload["history"] = history
        payload["updated_at"] = timestamp
        atomic_write_text(
            output_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        return True

    def run_final_integrity_control(self) -> None:
        if self.project is None:
            self._info("Първо отвори проект.")
            return
        report_path = self._find_latest_review_report()
        if report_path is None or not report_path.is_file():
            self._info("Не е намерен пакетен JSON отчет за финалната равносметка.")
            return
        answer = QMessageBox.question(
            self,
            "Финален контрол на проектните решения",
            "Ще бъде направен автоматичен backup на working файловете, ще се премахнат "
            "стари одобрения, отменени от Review Center, и ще се проверят двойно използвани "
            "DXF геометрии. Безопасна алтернатива се прилага само при еднозначно съвпадение.\n\n"
            "Да продължа ли?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._close_review_center()
        try:
            result = reconcile_project_integrity(
                Path(self.project.root),
                report_path=report_path,
                apply=True,
            )
        except Exception as exc:
            self.logger.exception("Final integrity control failed")
            self._error(f"Финалният контрол не завърши. Данните не са подменени.\n\n{exc}")
            return
        self._record_review_event(
            "FINAL_INTEGRITY",
            {
                "integrity_passed": result.integrity_passed,
                "full_specification_complete": result.full_specification_complete,
                "actionable_positions": result.actionable_positions,
                "approved_after": result.approved_after,
                "explicit_exclusions": result.explicit_exclusions,
                "intrinsic_missing_master": result.intrinsic_missing_master,
                "duplicate_groups_found": result.duplicate_groups_found,
                "duplicate_links_repaired": result.duplicate_links_repaired,
                "duplicate_groups_remaining": result.duplicate_groups_remaining,
                "unresolved_positions": result.unresolved_positions,
                "safe_confirmation_pending": result.safe_confirmation_pending,
                "engineering_decisions_pending": result.engineering_decisions_pending,
                "non_production_positions": result.non_production_positions,
                "engineer_resolved_without_geometry_count": result.engineer_resolved_without_geometry_count,
                "identity_failures": result.identity_failures,
                "beam_assignments_frozen": result.beam_assignments_frozen,
                "freeze_required": result.freeze_required,
                "issue_codes": [
                    issue.code
                    for issue in result.issues
                    if str(issue.severity).upper() == "ERROR"
                ],
            },
        )
        # Final control writes the authoritative ledger and may repair links.
        # Refresh LATEST after those mutations so the one-file hand-off never
        # contains the state from just before integrity reconciliation.
        self.create_ai_diagnostic_bundle(silent=True)
        lifecycle_blockers = (
            assert_export_allowed(Path(self.project.root))
            if result.full_specification_complete
            else []
        )
        final_ready = result.full_specification_complete and not lifecycle_blockers
        if final_ready:
            title = "Финалният контрол е успешен — пълна спецификация"
        elif _has_position_blockers(result):
            title = "Финалният контрол откри непълна спецификация"
        else:
            title = "Финалният контрол е блокиран от техническа причина"
        export_preflight_line = (
            f"Export preflight: БЛОКИРАН — {result.export_preflight_error}\n"
            if result.export_preflight_error
            else ""
        )
        issue_text = _blocking_integrity_issue_text(result, lifecycle_blockers)
        text = (
            f"Реални позиции: {result.actionable_positions if result.actionable_positions is not None else '—'}\n"
            f"Одобрени производствени позиции: {result.approved_after}\n"
            f"Непроизводствени позиции с инженерно доказателство: {result.non_production_positions}\n"
            f"Инженерно решени без DXF геометрия: {result.engineer_resolved_without_geometry_count}\n"
            f"Стари изрични изключвания, които още блокират: {result.explicit_exclusions}\n"
            f"Позиции без валидна MASTER форма: {result.intrinsic_missing_master}\n"
            f"Data-identity грешки: {result.identity_failures}\n"
            f"Замразени frame/beam решения: {result.beam_assignments_frozen}\n"
            f"Двойни геометрии намерени: {result.duplicate_groups_found}\n"
            f"Двойни геометрии поправени: {result.duplicate_links_repaired}\n"
            f"Оставащи двойни геометрии: {result.duplicate_groups_remaining}\n"
            f"Нерешени позиции: {result.unresolved_positions if result.unresolved_positions is not None else '—'}\n"
            f"  • За общо USER потвърждение: {result.safe_confirmation_pending if result.safe_confirmation_pending is not None else '—'}\n"
            f"  • За инженерно решение: {result.engineering_decisions_pending if result.engineering_decisions_pending is not None else '—'}\n"
            f"{export_preflight_line}\n"
            f"{issue_text}\n"
            f"Отчет: {Path(self.project.root) / 'working' / 'final_integrity_report.json'}\n"
            f"Контролен списък: {result.ledger_path or '—'}\n"
            f"Freeze manifest: {result.freeze_manifest_path or '—'}"
        )
        if final_ready:
            QMessageBox.information(self, title, text)
        else:
            QMessageBox.warning(self, title, text)
            if _has_frame_resolution_issue(result):
                self._offer_unknown_frame_resolution()
            self._offer_unresolved_beam_resolution(_unresolved_beam_handles(result))
        self.statusBar().showMessage(title, 12000)

    def _run_final_integrity_for_export(self) -> bool:
        """Recovery.3: финалният контрол е автоматична част от export-а.

        Изпълнява същата равносметка като менюто „Финален контрол", но без
        въпроси. Диалог се показва само когато има реален проблем — тогава
        export-ът спира и нерешените позиции отиват в Review Center.
        """

        report_path = self._find_latest_review_report()
        if report_path is None or not report_path.is_file():
            self._info("Не е намерен пакетен JSON отчет. Пусни първо пакетен анализ.")
            return False
        self._close_review_center()
        try:
            result = reconcile_project_integrity(
                Path(self.project.root),
                report_path=report_path,
                apply=True,
            )
        except Exception as exc:
            self.logger.exception("Automatic final integrity control failed")
            self._error(
                f"Финалният контрол не завърши. Данните не са подменени.\n\n{exc}"
            )
            return False
        self._record_review_event(
            "FINAL_INTEGRITY_AUTO",
            {
                "integrity_passed": result.integrity_passed,
                "full_specification_complete": result.full_specification_complete,
                "actionable_positions": result.actionable_positions,
                "approved_after": result.approved_after,
                "explicit_exclusions": result.explicit_exclusions,
                "unresolved_positions": result.unresolved_positions,
                "safe_confirmation_pending": result.safe_confirmation_pending,
                "engineering_decisions_pending": result.engineering_decisions_pending,
                "non_production_positions": result.non_production_positions,
                "engineer_resolved_without_geometry_count": result.engineer_resolved_without_geometry_count,
                "identity_failures": result.identity_failures,
                "beam_assignments_frozen": result.beam_assignments_frozen,
                "freeze_required": result.freeze_required,
                "issue_codes": [
                    issue.code
                    for issue in result.issues
                    if str(issue.severity).upper() == "ERROR"
                ],
            },
        )
        self.create_ai_diagnostic_bundle(silent=True)
        lifecycle_blockers = (
            assert_export_allowed(Path(self.project.root))
            if result.full_specification_complete
            else []
        )
        if result.full_specification_complete and not lifecycle_blockers:
            self.statusBar().showMessage(
                "Финален контрол: OK — "
                f"{result.approved_after} производствени + "
                f"{result.non_production_positions} непроизводствени от "
                f"{result.actionable_positions} позиции; "
                f"{result.beam_assignments_frozen} frame/beam решения са замразени.",
                8000,
            )
            return True
        issue_text = _blocking_integrity_issue_text(result, lifecycle_blockers)
        position_blocked = _has_position_blockers(result)
        next_step = (
            "В Review Center реши оставащите позиции и след това използвай "
            "„Потвърди всички останали безопасни“."
            if position_blocked
            else (
                "Позиционните решения са пълни. Отстрани посочената "
                "техническа причина и пусни Финален контрол отново."
            )
        )
        lead = (
            "Export-ът е спрян: пълната производствена спецификация не е завършена."
            if position_blocked
            else "Export-ът е спрян от техническата проверка след пълните позиционни решения."
        )
        self._error(
            lead + "\n\n"
            f"За общо USER потвърждение: {result.safe_confirmation_pending or 0}\n"
            f"За инженерно решение: {result.engineering_decisions_pending or 0}\n"
            f"Стари изрично изключени (блокиращи): {result.explicit_exclusions}\n"
            f"Data-identity грешки: {result.identity_failures}\n"
            f"Непроизводствени позиции (не блокират): {result.non_production_positions}\n\n"
            + (
                f"Export preflight: {result.export_preflight_error}\n\n"
                if result.export_preflight_error
                else ""
            )
            + issue_text
            + "\n"
            + next_step
            + " Export-ът включва всички AUTO + USER позиции и не пропуска нищо мълчаливо.\n\n"
            f"Контролен списък: {Path(self.project.root) / 'working' / 'final_integrity_ledger.csv'}"
        )
        if _has_frame_resolution_issue(result):
            self._offer_unknown_frame_resolution()
        self._offer_unresolved_beam_resolution(_unresolved_beam_handles(result))
        return False

    def run_excel_export(self) -> None:
        if self.project is None or self.import_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        if self._export_thread is not None and self._export_thread.isRunning():
            self._info("Вече се изпълнява Excel + DXF export.")
            return
        if not self._run_final_integrity_for_export():
            return
        try:
            engine = load_pinned_engine(self.project, self.engine_repository.load_current())
            frames, positions = load_export_context(
                self.project,
                self.import_result.entities,
                engine,
            )
        except (ExcelExportError, EngineRepositoryError) as exc:
            self._error(str(exc))
            return

        dialog = ExportExcelDialog(frames, positions, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        configs = dialog.configs()
        default_name = f"{self.project.name}_СПЕЦИФИКАЦИЯ.xlsx"
        default_path = Path(self.project.root) / "output" / default_name
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Запази Excel спецификация и DXF с позиции",
            str(default_path),
            "Excel Workbook (*.xlsx)",
        )
        if not filename:
            return
        output_path = Path(filename)
        if output_path.suffix.lower() != ".xlsx":
            output_path = output_path.with_suffix(".xlsx")
        dxf_output_path = output_path.with_name(output_path.stem + "_ПОЗИЦИИ.dxf")
        excel_trace_path = output_path.with_name(output_path.stem + "_TRACEABILITY.json")
        dxf_trace_path = dxf_output_path.with_name(dxf_output_path.stem + "_TRACEABILITY.json")

        existing = [
            path
            for path in (output_path, excel_trace_path, dxf_output_path, dxf_trace_path)
            if path.exists()
        ]
        if existing:
            answer = QMessageBox.question(
                self,
                "Файловете съществуват",
                "Следните export файлове вече съществуват и ще бъдат заменени:\n\n"
                + "\n".join(str(path) for path in existing)
                + "\n\nДа продължа ли?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        progress = QProgressDialog(
            "Подготвям Excel + DXF export...",
            "",
            0,
            100,
            self,
        )
        progress.setWindowTitle("LION REBAR — Excel + DXF export")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        progress.setValue(0)
        progress.resize(590, progress.sizeHint().height())

        thread = QThread(self)
        worker = ExportWorker(
            project=self.project,
            engine=engine,
            entities=self.import_result.entities,
            frames=frames,
            positions=positions,
            configs=configs,
            excel_path=output_path,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._export_progress_updated)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._export_finished)
        worker.failed.connect(thread.quit)
        worker.failed.connect(worker.deleteLater)
        worker.failed.connect(self._export_failed)
        thread.finished.connect(self._export_thread_finished)
        thread.finished.connect(thread.deleteLater)

        self._export_thread = thread
        self._export_worker = worker
        self._export_progress = progress
        self._set_export_running(True)
        progress.show()
        thread.start()
        self.statusBar().showMessage(
            "Excel + DXF export: подготвяне... Програмата остава активна.",
            10000,
        )

    def _export_progress_updated(self, percent: int, label: str) -> None:
        value = max(0, min(100, int(percent)))
        # ``QProgressDialog.setValue()`` върти цикъла на събитията, когато
        # диалогът е модален. Докато го върти, може да пристигне сигналът за
        # край, ``_export_finished`` да занули ``self._export_progress`` и
        # следващият ред да пипне ``None``. Затова: местна препратка, и
        # ``setValue`` последно — то е единственото, което върти цикъла.
        диалог = self._export_progress
        if диалог is not None:
            диалог.setLabelText(f"{label}\nПрогрес: {value}%")
            диалог.setValue(value)
        self.statusBar().showMessage(
            f"Excel + DXF export: {value}% — {label}",
            5000,
        )

    def _export_finished(self, result: PairedExportResult) -> None:
        # Полето се занулява ПРЕДИ да се пипне диалогът: ``setValue`` върти
        # цикъла на събитията и без това второ влизане в същия обработчик би
        # затворило диалога два пъти.
        диалог = self._export_progress
        self._export_progress = None
        if диалог is not None:
            диалог.setValue(100)
            диалог.close()
            диалог.deleteLater()

        excel_result = result.excel_result
        dxf_result = result.dxf_result
        self._session_project_event(
            "EXPORT_COMPLETED" if result.production_verified
            else "EXPORT_NOT_PRODUCTION_VERIFIED",
            {
                "production_verified": result.production_verified,
                "verification_problems": list(result.verification_problems),
                "manifest_path": str(result.manifest_path or ""),
                "excel_path": str(result.excel_path),
                "dxf_path": str(result.dxf_path),
                "excel_trace_path": str(result.excel_trace_path),
                "dxf_trace_path": str(result.dxf_trace_path),
                "frame_count": excel_result.frame_count,
                "safe_position_count": excel_result.safe_position_count,
                "unique_position_count": dxf_result.unique_position_count,
                "marker_count": dxf_result.marker_count,
                "layer_name": dxf_result.layer_name,
            },
        )
        if not result.production_verified:
            # Файловете са на диска, но операцията НЕ е успешен production
            # export. Операторът трябва да го научи веднага и еднозначно.
            QMessageBox.warning(
                self,
                "NOT PRODUCTION VERIFIED",
                "Excel и DXF са записани на диска, но производствената "
                "проверка НЕ мина.\n\nТези файлове не са производствено "
                "доказан export.\n\nПроблеми:\n  - "
                + "\n  - ".join(result.verification_problems or ["неизвестна причина"]),
            )
        manifest_line = (
            f"\nExport манифест: {result.manifest_path}"
            if result.manifest_path is not None
            else "\nExport манифест: НЕ Е СЪЗДАДЕН (виж предупреждението)"
        )
        QMessageBox.information(
            self,
            "Excel + DXF export е готов" if result.production_verified
            else "Excel + DXF са записани (без production проверка)",
            f"Чертожни листове: {excel_result.frame_count} + Общо\n"
            f"Безопасни позиции: {excel_result.safe_position_count}\n"
            f"Уникални Excel/DXF номера: {dxf_result.unique_position_count}\n"
            f"DXF блокове в чертежа: {dxf_result.marker_count}\n"
            f"Слой: {dxf_result.layer_name}\n\n"
            f"Excel: {result.excel_path}\n"
            f"DXF с позиции: {result.dxf_path}\n"
            f"Excel проследимост: {result.excel_trace_path}\n"
            f"DXF проследимост: {result.dxf_trace_path}\n"
            f"{manifest_line}\n\n"
            "Всеки номер в DXF е кръгъл BLOCK и е идентичен с номера в Excel. "
            "Оригиналният DXF не е променен.",
        )
        self.statusBar().showMessage(
            f"Excel + DXF export: {result.excel_path.name} / {result.dxf_path.name}",
            15000,
        )

    def _export_failed(self, error_type: str, message: str, traceback_text: str) -> None:
        self.logger.error(
            "Excel + DXF export failed: %s: %s\n%s",
            error_type,
            message,
            traceback_text,
        )
        self._session_project_event(
            "EXPORT_FAILED",
            {
                "error_type": error_type,
                "message": message,
                "traceback_tail": str(traceback_text or "")[-4000:],
            },
            severity="ERROR",
        )
        if self._export_progress is not None:
            self._export_progress.close()
            self._export_progress.deleteLater()
            self._export_progress = None
        known_errors = {
            "ExcelExportError",
            "DxfPositionExportError",
            "ExportBundleError",
        }
        details = message if error_type in known_errors else f"{error_type}: {message}"
        self._error(
            "Excel + DXF export не завърши. Оригиналният DXF и проектните решения "
            "не са променени.\n\n" + details
        )

    def _export_thread_finished(self) -> None:
        self._export_thread = None
        self._export_worker = None
        self._set_export_running(False)

    def _set_export_running(self, running: bool) -> None:
        for action in (
            self.action_new_project,
            self.action_open_project,
            self.action_import_dxf,
            self.action_batch_geometry,
            self.action_review_center,
            self.action_ai_diagnostic,
            self.action_final_integrity,
            self.action_export_excel,
            self.action_engine_studio,
            self.action_shape_builder,
            self.action_end_session,
            self.action_layer_report,
        ):
            action.setEnabled(not running)

    def _review_report_matches_current_project(self, path: Path) -> bool:
        """Accept only a report from the current project/source/run.

        Project-name filtering is retained for legacy projects.  Once a project
        has data identity, timestamps and filenames are never sufficient: the
        root report and every row must carry the active analysis identity.
        """

        if self.project is None or not path.is_file():
            return False
        try:
            report = load_review_report(path)
        except ReviewReportError:
            return False
        if report.project_name and report.project_name != self.project.name:
            return False

        identity = current_identity(self.project)
        if not identity.source_generation_id:
            return True
        if not identity.analysis_run_id:
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        if mismatch_reason(payload, identity, require_run=True):
            return False
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return False
        return all(
            isinstance(row, dict)
            and not mismatch_reason(row, identity, require_run=True)
            for row in rows
        )

    def _validated_review_report_path(self, path: Path | None) -> Path | None:
        if path is None or not path.is_file():
            return None
        return path if self._review_report_matches_current_project(path) else None

    def _find_latest_review_report(self) -> Path | None:
        validated = self._validated_review_report_path(self._last_batch_report_path)
        if validated is not None:
            return validated
        if self.project is None:
            return None
        stored_report = str(self.project.extensions.get("latest_batch_report_path") or "")
        validated = self._validated_review_report_path(
            Path(stored_report) if stored_report else None
        )
        if validated is not None:
            return validated
        parents: list[Path] = []
        cloud = self._detect_cloud_review_folder()
        if cloud is not None:
            parents.append(cloud)
        parents.append(Path(self.project.root) / "output" / "04_REVIEW_REPORTS")
        project_token = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in self.project.name
        ).strip("_")
        candidates: list[Path] = []
        for parent in parents:
            try:
                if not parent.is_dir():
                    continue
                parent_candidates: list[Path] = []
                patterns = (
                    f"{project_token}_BATCH_REVIEW_*/*_geometry_batch.json",
                    "*_BATCH_REVIEW_*/*_geometry_batch.json",
                )
                for pattern in patterns:
                    parent_candidates = [path for path in parent.glob(pattern) if path.is_file()]
                    if parent_candidates:
                        break
                candidates.extend(parent_candidates)
            except OSError:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
        for candidate in candidates:
            validated = self._validated_review_report_path(candidate)
            if validated is not None:
                return validated
        # Shared cloud review folders may contain newer reports for another
        # project or an older source/run. Never mix them into this project.
        return None

    def create_ai_diagnostic_bundle(self, *, silent: bool = False) -> Path | None:
        """Write the one file needed to reproduce the current project state.

        The package is local only.  Nothing is uploaded automatically.  A stable
        ``LATEST`` filename means the owner never has to hunt through report
        folders or send screenshots.
        """

        if self.project is None:
            if not silent:
                self._info("Първо отвори проект.")
            return None
        if not self._diagnostic_lock.acquire(blocking=False):
            if not silent:
                self._info(
                    "Диагностичният пакет вече се обновява във фонов режим. "
                    "Изчакай да приключи и опитай отново."
                )
            return None
        project_root = Path(self.project.root)
        token = safe_filename(self.project.name, "LION_REBAR_PROJECT")
        output_path = (
            project_root
            / "output"
            / "05_DIAGNOSTICS"
            / f"{token}_AI_FEEDBACK_LATEST.zip"
        )
        try:
            try:
                result = create_diagnostic_bundle(
                    project_root,
                    output_path=output_path,
                    include_source_dxf=True,
                    latest_report_path=self._find_latest_review_report(),
                )
                try:
                    self.project.extensions["latest_ai_feedback_bundle"] = str(
                        result.relative_to(project_root)
                    )
                    self.project_manager.save(self.project)
                except (ValueError, ProjectError):
                    # The bundle itself is already valid; metadata persistence is
                    # helpful, but must never invalidate the diagnostic export.
                    pass
            except Exception as exc:
                # The explicit button must never take down the project window
                # because of a filesystem race or an unexpected serializer value.
                self.logger.exception("AI diagnostic bundle creation failed")
                if not silent:
                    self._error(f"Пакетът за AI проверка не беше създаден.\n\n{exc}")
                return None
        finally:
            self._diagnostic_lock.release()

        message = f"Готов пакет за AI проверка: {result}"
        self._session_project_event(
            "AI_DIAGNOSTIC_CREATED",
            {"path": str(result), "filename": result.name},
        )
        self.statusBar().showMessage(message, 15000)
        if not silent:
            QMessageBox.information(
                self,
                "Пакетът за AI проверка е готов",
                "Качи само този ZIP. Той съдържа нужните данни за възпроизвеждане — "
                "не са нужни снимки и преписване.\n\n"
                f"{result}",
            )
        return result

    def _schedule_ai_diagnostic_bundle(self) -> None:
        """Refresh LATEST without blocking Review Center or the main UI."""

        if self.project is None:
            return
        existing = self._diagnostic_thread
        if existing is not None and existing.is_alive():
            return
        project_root = Path(self.project.root)
        token = safe_filename(self.project.name, "LION_REBAR_PROJECT")
        output_path = (
            project_root
            / "output"
            / "05_DIAGNOSTICS"
            / f"{token}_AI_FEEDBACK_LATEST.zip"
        )
        latest_report_path = self._find_latest_review_report()

        def build_in_background() -> None:
            started = perf_counter()
            if not self._diagnostic_lock.acquire(blocking=False):
                return
            try:
                result = create_diagnostic_bundle(
                    project_root,
                    output_path=output_path,
                    include_source_dxf=True,
                    latest_report_path=latest_report_path,
                )
            except Exception:
                self.logger.exception("Background AI diagnostic bundle creation failed")
            else:
                self.logger.info(
                    "Background diagnostic ready in %.1f ms: %s",
                    (perf_counter() - started) * 1000.0,
                    result,
                )
            finally:
                self._diagnostic_lock.release()

        thread = threading.Thread(
            target=build_in_background,
            name="lion-rebar-diagnostic",
            daemon=True,
        )
        self._diagnostic_thread = thread
        thread.start()

    @staticmethod
    def _bulk_decision_payload(
        rows: list[dict],
        approved_handles: list[str],
        *,
        mode: str,
    ) -> dict:
        approved = {str(value) for value in approved_handles}
        decision_code = {
            "STRICT_SAMPLE": "APPROVED_BULK_AFTER_SAMPLE",
            "GEOMETRY_SAMPLE": "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE",
            "F006_SAMPLE": "APPROVED_BULK_AFTER_F006_SAMPLE",
            "FINAL_USER_CONFIRMATION": "APPROVED_BULK_FINAL_USER_CONFIRMATION",
        }.get(str(mode), "APPROVED_BULK_AFTER_SAMPLE")
        decisions: list[dict] = []
        for row in rows:
            handle = str(row.get("annotation_handle") or "")
            if not handle or handle not in approved:
                continue
            decisions.append(
                {
                    "annotation_handle": handle,
                    "annotation_text": str(row.get("annotation_text") or ""),
                    "annotation_count": row.get("annotation_count"),
                    "diameter_mm": row.get("diameter_mm"),
                    "declared_length_cm": row.get("declared_length_cm"),
                    "production_length_cm": row.get("effective_geometry_length_cm") or row.get("declared_length_cm"),
                    "master_shape_code": row.get("master_shape_code"),
                    "master_shape_name": row.get("master_shape_name"),
                    "candidate_handles": list(row.get("candidate_handles") or []),
                    "candidate_layer": row.get("candidate_layer"),
                    "canonical_segment_lengths_cm": list(
                        row.get("canonical_segment_lengths_cm") or []
                    ),
                    "canonical_signature": row.get("canonical_signature"),
                    "numeric_difference": row.get("numeric_difference"),
                    "geometry_owner_conflict": bool(row.get("geometry_owner_conflict")),
                    "global_conflict": bool(row.get("global_conflict")),
                    "batch_status": row.get("batch_status"),
                    "matcher_disposition": row.get("matcher_disposition"),
                    "matcher_evidence_band": row.get("matcher_evidence_band"),
                    "contextual_rule_code": row.get("contextual_rule_code"),
                    "review_reason_codes": list(row.get("review_reason_codes") or []),
                    "decision": decision_code,
                    "action_class": "CONFIRMATION",
                    "reason_code": (
                        "FINAL_USER_CONFIRMATION"
                        if str(mode) == "FINAL_USER_CONFIRMATION"
                        else "CONTROL_SAMPLE_CONFIRMATION"
                    ),
                }
            )
        return {
            "mode": str(mode),
            "annotation_handles": [str(value) for value in approved_handles],
            "decision_count": len(decisions),
            "decisions": decisions,
        }

    def _record_review_event(self, event_type: str, payload: dict) -> None:
        if self.project is None:
            return
        enriched = dict(payload or {})
        enriched.setdefault("session_id", self.session_service.session_id)
        annotation_handle = str(enriched.get("annotation_handle") or "")
        if not annotation_handle and isinstance(enriched.get("decision"), dict):
            annotation_handle = str(enriched["decision"].get("annotation_handle") or "")
        if annotation_handle and "row_context" not in enriched:
            context = self._review_row_context(annotation_handle)
            if context:
                enriched["row_context"] = context

        envelope = None
        human_decision = is_human_decision_event(event_type)
        if human_decision:
            # Авторът влиза в записа КЪМ МОМЕНТА НА ЗАПИСА. Пита се веднъж, при
            # първото решение на този компютър; отказът не блокира работата, а
            # оставя автора незаявен. Подаден автор от самия път не се замества.
            self._ensure_operator_declared()
            enriched.setdefault("actor", self._operator_for_record())
        if human_decision:
            try:
                envelope = append_project_decision_event(
                    Path(self.project.root), event_type, enriched
                )
                enriched["stable_event_id"] = envelope["event_id"]
                enriched["human_decision"] = envelope
            except Exception as exc:
                self.logger.error("Durable decision outbox could not be written: %s", exc)
                self.statusBar().showMessage(
                    "Решението е приложено, но обучителният project журнал не е записан. "
                    "Не затваряй проекта; виж логовете.",
                    15000,
                )

        try:
            append_feedback_event(Path(self.project.root), event_type, enriched)
        except Exception as exc:
            self.logger.warning("Review feedback event was not recorded: %s", exc)

        if envelope is not None:
            try:
                self._flush_project_decision_outbox()
            except Exception as exc:
                # The PENDING fsync is authoritative for later recovery.
                self.logger.warning("Decision remains pending for Session DB: %s", exc)
        elif not human_decision:
            try:
                self.session_service.record_project_event(
                    self.project,
                    event_type,
                    enriched,
                    report_path=self._find_latest_review_report(),
                )
            except Exception as exc:
                self.logger.warning("Global session event was not recorded: %s", exc)
        self._refresh_session_label()

    def _flush_project_decision_outbox(self) -> tuple[int, int]:
        if self.project is None:
            return 0, 0

        def deliver(envelope):
            payload = dict(envelope.get("payload") or {})
            payload["stable_event_id"] = envelope.get("event_id")
            payload["human_decision"] = dict(envelope)
            self.session_service.record_project_event(
                self.project,
                str(envelope.get("event_type") or "HUMAN_DECISION"),
                payload,
                report_path=self._find_latest_review_report(),
                occurred_at=str(envelope.get("occurred_at") or "") or None,
            )
            return self.session_service.session_id

        return flush_project_decision_outbox(Path(self.project.root), deliver)

    def _review_group_decisions_changed(self) -> None:
        try:
            delivered, remaining = self._flush_project_decision_outbox()
        except Exception as exc:
            self.logger.warning("Group decision outbox remains pending: %s", exc)
            return
        if delivered:
            self.statusBar().showMessage(
                f"Груповото решение е записано: {delivered} операция, pending {remaining}",
                7000,
            )


    def _session_project_event(
        self,
        event_type: str,
        payload: dict | None = None,
        *,
        severity: str = "INFO",
        report_path: Path | None = None,
    ) -> None:
        if self.project is None:
            return
        try:
            self.session_service.record_project_event(
                self.project,
                event_type,
                dict(payload or {}),
                severity=severity,
                report_path=report_path,
            )
        except Exception as exc:
            self.logger.warning("Session event was not recorded: %s", exc)
        self._refresh_session_label()

    def _review_row_context(self, annotation_handle: str) -> dict:
        handle = str(annotation_handle or "")
        if not handle:
            return {}
        rows = ()
        dialog = self._review_center
        if dialog is not None:
            rows = dialog.report.rows
        else:
            report_path = self._find_latest_review_report()
            if report_path is not None:
                try:
                    rows = load_review_report(report_path).rows
                except ReviewReportError:
                    rows = ()
        row = next(
            (value for value in rows if str(value.get("annotation_handle") or "") == handle),
            None,
        )
        if not isinstance(row, dict):
            return {}
        return {
            "annotation_handle": handle,
            "annotation_text": row.get("annotation_text"),
            "annotation_count": row.get("annotation_count"),
            "diameter_mm": row.get("diameter_mm"),
            "declared_length_cm": row.get("declared_length_cm"),
            "batch_status": row.get("batch_status"),
            "matcher_disposition": row.get("matcher_disposition"),
            "matcher_evidence_band": row.get("matcher_evidence_band"),
            "candidate_confidence": row.get("candidate_confidence"),
            "candidate_handles": list(row.get("candidate_handles") or []),
            "candidate_layer": row.get("candidate_layer"),
            "candidate_basis": row.get("candidate_basis"),
            "geometry_provenance": row.get("geometry_provenance"),
            "master_shape_code": row.get("master_shape_code"),
            "master_shape_name": row.get("master_shape_name"),
            "canonical_segment_lengths_cm": list(row.get("canonical_segment_lengths_cm") or []),
            "canonical_turn_angles_deg": list(row.get("canonical_turn_angles_deg") or []),
            "numeric_difference": row.get("numeric_difference"),
            "raw_numeric_difference": row.get("raw_numeric_difference"),
            "distance_raw": row.get("distance_raw"),
            "global_conflict": bool(row.get("global_conflict")),
            "geometry_owner_conflict": bool(row.get("geometry_owner_conflict")),
            "master_conflict": bool(row.get("master_conflict")),
            "data_quality_flags": list(row.get("data_quality_flags") or []),
            "review_reason_codes": list(row.get("review_reason_codes") or []),
            "contextual_rule_code": row.get("contextual_rule_code"),
            "contextual_rule_note": row.get("contextual_rule_note"),
            "candidate_notes": list(row.get("candidate_notes") or []),
            "alternative_candidate_count": len(row.get("alternative_candidates") or []),
            "frame_title": row.get("frame_title"),
            "frame_category_code": row.get("frame_category_code"),
        }

    def _decision_suggestion_for_row(
        self,
        row: dict,
        *,
        engine_version: str,
    ) -> DecisionSuggestion | None:
        """Read a fail-closed hint from delivered human decisions.

        The lookup is intentionally rebuilt for every editor opening.  A
        decision just saved in the current project journal can therefore help
        the next identical case, while a read/normalization problem can never
        block manual engineering work.
        """

        if self.project is None:
            return None
        try:
            database = getattr(self.session_service, "database", None)
            index = DecisionSuggestionIndex.from_sources(
                session_db_path=getattr(database, "path", None),
                project_root=Path(self.project.root),
            )
            for warning in index.source_stats.warnings:
                self.logger.warning("Decision suggestion source: %s", warning)
            return index.lookup(
                dict(row),
                identity=current_identity(self.project),
                engine_version=str(engine_version or ""),
            )
        except Exception as exc:
            self.logger.warning(
                "Previous engineer decision suggestion is unavailable: %s",
                exc,
            )
            return None

    def _reuse_index(self):
        """Указателят за преизползване — пресглобява се при всяко отваряне.

        Нарочно се чете наново: решение, записано или ОТТЕГЛЕНО току-що, трябва
        да важи за следващия случай веднага. Проблем с четенето никога не спира
        ръчната инженерна работа.
        """

        import os

        from lion_rebar.review.decision_reuse import Указател, донорски_пътища

        if self.project is None:
            return None
        if str(
            os.environ.get("LION_REBAR_DECISION_MEMORY", "")
        ).strip().lower() in {"off", "0", "false"}:
            return None
        донори = донорски_пътища()
        try:
            указател = Указател.от_проект(
                Path(self.project.root), допълнителни=донори
            )
        except Exception as грешка:
            self.logger.warning("Преизползването е недостъпно: %s", грешка)
            return None
        for предупреждение in указател.сводка.предупреждения:
            self.logger.warning("Преизползване: %s", предупреждение)
        return указател

    def _reuse_proposal_for_row(self, row: dict, *, engine, index=None):
        """Предложение с разделени етапи за ЕДИН ред. Нищо не се прилага."""

        if index is None:
            index = self._reuse_index()
        if index is None or self.project is None:
            return None, None
        активни = {
            str(форма.code).upper(): int(форма.revision or 1)
            for форма in (getattr(engine, "shapes", ()) or ())
            if bool(getattr(форма, "active", True))
        }
        try:
            return index, index.предложи(
                dict(row),
                самоличност=current_identity(self.project),
                активни_форми=активни or None,
            )
        except Exception as грешка:
            self.logger.warning("Предложението за преизползване падна: %s", грешка)
            return index, None

    def _review_preview_row(self, row: dict) -> None:
        analysis = geometry_analysis_from_report_row(row)
        if not analysis.candidates:
            self._review_locate_annotation(row)
            return
        self._select_annotation_handle(analysis.annotation_handle)
        overlay_segments, overlay_source_label = _shape_dimension_overlay_input(
            row.get("master_shape_code") or row.get("suggested_shape_code"),
            row.get("production_segments_cm"),
            row.get("canonical_segment_lengths_cm"),
            row.get("suggested_segments_cm"),
        )
        self._preview_geometry_candidate(
            analysis.candidates[0],
            overlay_shape_code=(
                row.get("master_shape_code")
                or row.get("suggested_shape_code")
            ),
            overlay_segments=overlay_segments,
            overlay_total_length_cm=(
                row.get("production_length_cm")
                if row.get("production_length_cm") is not None
                else row.get("declared_length_cm")
            ),
            overlay_source_label=overlay_source_label,
        )

    def _review_preview_reference_geometry(self, row: dict) -> None:
        """Show the annotation's own attached, reference-only geometry in blue.

        Used only when no PROVEN geometry candidate exists
        (``candidate_handles`` empty) but the annotation already carries
        known DXF fragment handles via ``reference_geometry_handles``
        (see dxf/batch_geometry_report.py::_apply_proxy_reference_geometry_fallback).
        Deliberately a separate path from ``_preview_geometry_candidate``:
        there is no ``GeometryCandidate`` here, no measured length, no
        distance -- only the declared text is trusted -- so the highlight
        is drawn in blue (never red) and nothing here writes to
        ``candidate_handles`` or any approval-eligible field.
        """
        if self.scene_result is None:
            return
        handles = [
            str(value) for value in (row.get("reference_geometry_handles") or []) if str(value)
        ]
        if not handles:
            self._review_locate_annotation(row)
            return

        self._clear_geometry_highlights()
        self.scene_result.scene.clearSelection()

        centreline_items = []
        for handle in handles:
            record = self.scene_result.handle_to_record.get(handle)
            item = self.scene_result.handle_to_item.get(handle)
            if record is None or item is None:
                continue
            scene_path = centerline_path_for_record(record)
            if scene_path is None or scene_path.isEmpty():
                continue
            centreline_items.append((item, scene_path))
        if not centreline_items:
            self._review_locate_annotation(row)
            return

        bounding_rect = None
        highlight_pen = QPen(QColor("#0369a1"), 5.0)
        highlight_pen.setCosmetic(True)

        for item, scene_path in centreline_items:
            item.setVisible(True)
            item.setSelected(True)
            rect = scene_path.boundingRect()
            bounding_rect = rect if bounding_rect is None else bounding_rect.united(rect)
            try:
                overlay = QGraphicsPathItem(scene_path)
                overlay.setPen(highlight_pen)
                overlay.setZValue(1_000_000.0)
                overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.scene_result.scene.addItem(overlay)
                self._geometry_highlights.append(overlay)
            except Exception as exc:
                self.logger.warning("Cannot build reference geometry highlight: %s", exc)

        annotation_handle = str(row.get("annotation_handle") or "")
        annotation_item = self.scene_result.handle_to_item.get(annotation_handle)
        if annotation_item is not None:
            annotation_rect = annotation_item.sceneBoundingRect()
            if not annotation_rect.isEmpty():
                bounding_rect = (
                    annotation_rect
                    if bounding_rect is None
                    else bounding_rect.united(annotation_rect)
                )

        if bounding_rect is not None:
            margin = max(5.0, max(bounding_rect.width(), bounding_rect.height()) * 0.02)
            frame = QGraphicsRectItem(bounding_rect.adjusted(-margin, -margin, margin, margin))
            frame_pen = QPen(QColor("#0369a1"), 2.0)
            frame_pen.setStyle(Qt.PenStyle.DashLine)
            frame_pen.setCosmetic(True)
            frame.setPen(frame_pen)
            frame.setZValue(999_999.0)
            frame.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene_result.scene.addItem(frame)
            self._geometry_highlights.append(frame)

            overlay_segments, overlay_source_label = _shape_dimension_overlay_input(
                row.get("suggested_shape_code"),
                row.get("production_segments_cm"),
                row.get("canonical_segment_lengths_cm"),
                row.get("suggested_segments_cm"),
            )
            self._add_shape_dimension_overlay(
                shape_code=row.get("suggested_shape_code"),
                segment_lengths_cm=overlay_segments,
                total_length_cm=(
                    row.get("production_length_cm")
                    if row.get("production_length_cm") is not None
                    else row.get("declared_length_cm")
                ),
                bounding_rect=bounding_rect,
                color=QColor("#0369a1"),
                storage=self._geometry_highlights,
                z_value=1_000_001.0,
                source_label=overlay_source_label or "СПРАВОЧНО (не е доказан кандидат)",
            )

            view_margin = margin * 4.0
            self.drawing_view.fitInView(
                bounding_rect.adjusted(-view_margin, -view_margin, view_margin, view_margin),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            view_point = self.drawing_view.mapFromScene(bounding_rect.center())
            global_point = self.drawing_view.viewport().mapToGlobal(view_point)
            if self._geometry_dialog is not None:
                self._geometry_dialog.place_away_from(global_point)
            if self._review_center is not None:
                self._review_center.place_away_from(global_point)

        self.statusBar().showMessage(
            f"Показана СПРАВОЧНА геометрия (недоказана): {', '.join(handles)}. "
            "Синьото маркиране НЕ е потвърден кандидат.",
            9000,
        )

    def _review_locate_annotation(self, row: dict) -> None:
        handle = str(row.get("annotation_handle") or "")
        if not self._select_annotation_handle(handle):
            self._info("Надписът от отчета не е намерен в текущия DXF.")
            return
        self._begin_manual_geometry_selection(row)
        # The manual centreline filter deliberately deselects BLOCK_ATTR_TEXT,
        # TEXT and MTEXT items.  Draw a separate, non-selectable overlay after
        # the filter is active so a no-candidate row never loses its visual
        # anchor while the operator searches for the correct geometry.
        self._show_annotation_review_zone(row)

    def _show_annotation_review_zone(self, row: dict) -> bool:
        """Persistently mark a no-candidate annotation and its search zone."""

        if self.scene_result is None:
            return False
        handle = str(row.get("annotation_handle") or "")
        item = self.scene_result.handle_to_item.get(handle)
        if item is None:
            return False

        self._clear_geometry_highlights()
        item.setVisible(True)
        annotation_rect = item.sceneBoundingRect()
        if annotation_rect.isEmpty():
            return False

        raw_policy = row.get("recognition_policy")
        policy = RecognitionPolicy.from_settings(
            raw_policy if isinstance(raw_policy, dict) else None
        )
        try:
            declared_length_cm = max(
                0.0,
                float(row.get("declared_length_cm") or 0.0),
            )
        except (TypeError, ValueError):
            declared_length_cm = 0.0
        search_radius = max(
            1.0,
            float(policy.search_distance_limit(declared_length_cm)),
        )

        scene = self.scene_result.scene
        marker_margin = max(
            3.0,
            min(
                16.0,
                max(annotation_rect.width(), annotation_rect.height(), 1.0) * 0.08,
            ),
        )
        marker_rect = annotation_rect.adjusted(
            -marker_margin,
            -marker_margin,
            marker_margin,
            marker_margin,
        )
        marker = QGraphicsRectItem(marker_rect)
        marker_pen = QPen(QColor("#e00000"), 4.0)
        marker_pen.setCosmetic(True)
        marker.setPen(marker_pen)
        marker.setZValue(1_000_002.0)
        marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        scene.addItem(marker)
        self._geometry_highlights.append(marker)

        search_rect = annotation_rect.adjusted(
            -search_radius,
            -search_radius,
            search_radius,
            search_radius,
        )
        search_zone = QGraphicsRectItem(search_rect)
        search_pen = QPen(QColor("#0284c7"), 2.5)
        search_pen.setStyle(Qt.PenStyle.DashLine)
        search_pen.setCosmetic(True)
        search_zone.setPen(search_pen)
        search_zone.setZValue(1_000_000.0)
        search_zone.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        scene.addItem(search_zone)
        self._geometry_highlights.append(search_zone)

        rejected_handles: list[str] = []
        rejected = row.get("ownership_rejected_candidate")
        if isinstance(rejected, dict):
            rejected_handles = [
                str(value)
                for value in (rejected.get("handles") or [])
                if str(value)
            ]
        rejected_pen = QPen(QColor("#d97706"), 4.0)
        rejected_pen.setStyle(Qt.PenStyle.DashLine)
        rejected_pen.setCosmetic(True)
        shown_rejected: list[str] = []
        for rejected_handle in rejected_handles:
            record = self.scene_result.handle_to_record.get(rejected_handle)
            if record is None:
                continue
            path = centerline_path_for_record(record)
            if path is None or path.isEmpty():
                continue
            overlay = QGraphicsPathItem(path)
            overlay.setPen(rejected_pen)
            overlay.setZValue(1_000_001.0)
            overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            scene.addItem(overlay)
            self._geometry_highlights.append(overlay)
            shown_rejected.append(rejected_handle)

        view_margin = max(8.0, search_radius * 0.08)
        fit_rect = search_rect.adjusted(
            -view_margin,
            -view_margin,
            view_margin,
            view_margin,
        )
        # build_scene() pins an explicit sceneRect around the imported DXF.
        # An annotation close to that boundary can therefore have part of its
        # local search zone outside the scrollable area.  Extend the scene only
        # by the bounded local fit rectangle; never let a long rejected path
        # decide the zoom or the additional scene extent here.
        if not scene.sceneRect().contains(fit_rect):
            scene.setSceneRect(scene.sceneRect().united(fit_rect))
        self.drawing_view.fitInView(
            fit_rect,
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        if self._review_center is not None:
            view_point = self.drawing_view.mapFromScene(annotation_rect.center())
            global_point = self.drawing_view.viewport().mapToGlobal(view_point)
            self._review_center.place_away_from(global_point)

        rejected_note = ""
        if shown_rejected:
            rejected_note = (
                " Кехлибареният пунктир показва отхвърлена чужда геометрия; "
                "тя не е одобрена за тази позиция."
            )
        self.statusBar().showMessage(
            "Надписът за преглед е ограден в червено; синият пунктир "
            f"показва локалната зона за търсене ({search_radius:g}).{rejected_note}",
            0,
        )
        return True

    def _review_engineer_decision(self, row: dict) -> None:
        dialog = self._review_center
        if dialog is None or self.project is None:
            return
        try:
            engine = load_pinned_engine(
                self.project, self.engine_repository.load_current()
            )
        except (EngineRepositoryError, ExcelExportError) as exc:
            dialog.mark_error(str(exc))
            return
        existing = self._read_approved_geometry_link(str(row.get("annotation_handle") or "")) or {}
        problem_decision = dialog.decision_payload(
            str(row.get("annotation_handle") or "")
        )
        (
            _указател_за_преизползване,
            _предложение_за_преизползване,
        ) = self._reuse_proposal_for_row(row, engine=engine)
        editor = EngineerDecisionDialog(
            row=dict(row),
            shapes=engine.shapes,
            existing_link=existing,
            problem_decision=problem_decision,
            engine_version=engine.version,
            project_name=self.project.name,
            project_version=self.project.version,
            decision_suggestion=self._decision_suggestion_for_row(
                row,
                engine_version=engine.version,
            ),
            reuse_index=_указател_за_преизползване,
            reuse_proposal=_предложение_за_преизползване,
            parent=self,
        )
        saved = self._run_engineer_editor(
            editor,
            dialog,
            log_message="Engineer decision save failed",
        )
        if saved is None:
            return
        payload, path = saved
        annotation_handle = str((payload.get("annotation") or {}).get("handle") or "")
        canonical_decision = dialog.mark_engineer_resolved(
            annotation_handle,
            payload,
        )
        self._record_review_event(
            (
                "NON_PRODUCTION_POSITION"
                if str(payload.get("reason_code") or "").upper() == "NON_PRODUCTION_POSITION"
                else "ENGINEER_RESOLVED"
            ),
            {
                "annotation_handle": annotation_handle,
                "annotation_text": (payload.get("annotation") or {}).get("text"),
                "resolved_annotation_text": (payload.get("annotation") or {}).get("resolved_text"),
                "action_class": payload.get("action_class"),
                "reason_code": payload.get("reason_code"),
                "reason": payload.get("reason"),
                "note": payload.get("note"),
                "row_context": payload.get("row_context"),
                "original": payload.get("original"),
                "resolved": payload.get("resolved"),
                "geometry": payload.get("geometry"),
                "diagnostic_problem": payload.get("diagnostic_problem"),
                "decision_suggestion": payload.get("decision_suggestion"),
                # ОСНОВАНИЕТО на приетото предложение влиза в журнала:
                # по него се намира какво да се провери наново, ако
                # решението-донор бъде оттеглено.
                "preizpolzvane": payload.get("preizpolzvane"),
                "evidence": canonical_decision.get("evidence"),
                "canonical_decision": canonical_decision,
            },
        )
        self.statusBar().showMessage(f"Инженерното решение е записано: {path}", 12000)

    def _review_all_new_shapes(self, payload: dict) -> None:
        """Всички семейства без MASTER форма — в ЕДНА чернова.

        Собственик, 22.09.2026: „адски тромаво и бавно“. Дотук всяка форма
        искаше своя чернова, свое одобрение и свое публикуване; в един чертеж
        семействата са няколко.

        Пътят остава: черновата не влиза в производството, а одобрението и
        публикуването са си отделни стъпки в Engine Studio.
        """

        from lion_rebar.engine.shape_from_case import (
            ShapeFromCaseError,
            build_draft_shape,
            collect_cases,
            propose_shape,
        )
        from lion_rebar.ui.new_shapes_dialog import NewShapesDialog

        rows = list(payload.get("rows") or [])
        project_name = str(getattr(self.project, "name", "") or "")
        точки = self._block_points_by_handle()

        семейства = collect_cases(rows, project_name=project_name,
                                  points_by_handle=точки)
        предложения: list[tuple[str, Any]] = []
        откази: list[str] = []
        engine = self.engine_repository.load_current()
        следващ = self._next_free_shape_code(engine)
        номер = int(следващ[1:])
        for _ключ, случаи in sorted(семейства.items(), key=lambda kv: -len(kv[1])):
            try:
                предложения.append((f"F{номер:03d}", propose_shape(случаи)))
                номер += 1
            except ShapeFromCaseError as exc:
                откази.append(str(exc))

        if not предложения:
            self._info(
                "Няма семейство, от което да се роди форма.\n\n"
                + ("\n".join(откази[:5]) if откази else
                   "Всички позиции без форма нямат годна геометрия.")
            )
            return

        dialog = NewShapesDialog(предложения, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        избрани = dialog.selected()
        if not избрани:
            return

        try:
            чернова = self.engine_repository.clone_current_to_draft(
                self._next_draft_version(engine), created_by="Review Center"
            )
            имена = []
            for код, име, предложение in избрани:
                форма = build_draft_shape(предложение, code=код, name=име)
                # Същата проверка, както при раждането от един случай: и тук
                # черновата не бива да влиза със скрит двойник.
                if not self._shape_duplicate_check_passes(
                    форма, engine, заглавие="Формата %s прилича на съществуваща" % код
                ):
                    continue
                чернова.shapes.append(форма)
                имена.append(f"{код} „{име}“")
            if not имена:
                self._info("Нито една форма не беше записана.")
                return
            път = self.engine_repository.save_draft(чернова)
        except ShapeFromCaseError as exc:
            self._error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._error(f"Черновата не можа да се запише: {exc}")
            return

        QMessageBox.information(
            self,
            "Черновата е записана",
            # Текстът казваше „Всички са НЕАКТИВНИ“. Вече не е вярно:
            # ``build_draft_shape`` ги ражда с ``active=True``, защото лостът
            # за одобрение е ``state``, не ``active`` — точно поправката, след
            # която F020 спря да остава мълчалива в публикуван engine.
            #
            # Черновата не влиза в производството по друга причина: ЧЕРНОВИЯТ
            # engine не е активният, а матчърът индексира само форми в
            # ``APPROVED``/``LOCKED`` (``engine.shape_index``).
            "В чернова %s влязоха %d форми:\n  %s\n\n"
            "Всички са ЧЕРНОВИ (state=DRAFT) и затова не се разпознават:\n"
            "матчърът индексира само одобрени форми. Отвори MASTER →\n"
            "Engine Studio, пусни контролните тестове, одобри всяка форма и\n"
            "публикувай веднъж.\n\n%s"
            % (чернова.version, len(избрани), "\n  ".join(имена), път),
        )

    def _block_points_by_handle(self) -> dict:
        """Върховете на начертаните полилинии по handle.

        С тях посоката на огъвките идва от чертежа, а не от нормализираната
        сигнатура, която не пази накъде прескача прътът.
        """

        точки: dict[str, list] = {}
        if self.import_result is None:
            return точки
        for запис in self.import_result.entities:
            геометрия = запис.geometry or {}
            върхове = геометрия.get("points") or геометрия.get("vertices")
            if върхове:
                точки[str(запис.handle)] = list(върхове)
        return точки

    def _review_new_shape_from_case(self, payload: dict) -> None:
        """Ражда ЧЕРНОВА форма от отказан случай и от събратята му в чертежа.

        Собственик, 22.09.2026, за кобилицата в `тест-001`: „много сложно се
        чертае в нашия конструктор форми; искам да мога да го маркирам и да го
        пусна за одобрение“.

        Прозорецът не смята нищо — цялата работа е в
        ``engine.shape_from_case``, което е покрито с тестове. Тук стоят само
        въпросите към оператора и записът на черновата.

        Формата излиза в състояние ``DRAFT`` и затова не се разпознава:
        ``engine.shape_index`` индексира само ``APPROVED``/``LOCKED``. Пътят до
        производството остава непроменен: Engine Studio -> контролни тестове ->
        одобрение -> публикуване.
        """

        from lion_rebar.engine.shape_from_case import (
            ShapeFromCaseError,
            build_draft_shape,
            case_from_report_row,
            collect_cases,
            family_key,
            propose_shape,
        )

        row = dict(payload.get("row") or {})
        rows = list(payload.get("rows") or [])
        project_name = str(getattr(self.project, "name", "") or "")

        точки = self._block_points_by_handle()

        случай = case_from_report_row(
            row, project_name=project_name,
            geometry_points=next(
                (точки[str(h)] for h in (row.get("candidate_handles") or ())
                 if str(h) in точки),
                None,
            ),
        )
        if случай is None:
            self._error("Този случай няма годна геометрия за нова форма.")
            return

        ключ = family_key(случай)
        семейства = collect_cases(rows, project_name=project_name,
                                  points_by_handle=точки)
        случаи = семейства.get(ключ) or [случай]

        try:
            предложение = propose_shape(случаи)
        except ShapeFromCaseError as exc:
            self._error(
                "Форма не се ражда от този случай.\n\n"
                f"{exc}\n\n"
                "Размер не се извежда от измереното — проектантът трябва да го е "
                "написал."
            )
            return

        редове = "\n".join(
            "    %s:  %s  ->  сбор %g cm при надпис %g cm"
            % (
                стойности.case.annotation_text,
                ", ".join(
                    f"{код}={стойност:g}"
                    for код, стойност in sorted(стойности.parameter_values_cm.items())
                ),
                стойности.total_cm,
                стойности.case.declared_length_cm,
            )
            for стойности in предложение.values
        )
        ъгли = ", ".join(f"{a:+g}°" for a in предложение.turn_angles_deg)
        отговор = QMessageBox.question(
            self,
            "Нова форма от случай",
            "Намерена е форма с %d участъка и образец %s.\n\n"
            "Ъгли: %s\nУравнение: %s\n\n"
            "Случаи в този чертеж (%d):\n%s\n\n"
            "Да се роди ли ЧЕРНОВА форма от тях?"
            % (
                предложение.segment_count,
                предложение.pattern,
                ъгли,
                предложение.equation,
                предложение.case_count,
                редове,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if отговор != QMessageBox.StandardButton.Yes:
            return

        име, ok = QInputDialog.getText(
            self, "Име на формата", "Как се казва тази форма:"
        )
        if not ok or not str(име).strip():
            return

        try:
            engine = self.engine_repository.load_current()
            код = self._next_free_shape_code(engine)
            чернова = self.engine_repository.clone_current_to_draft(
                self._next_draft_version(engine), created_by="Review Center"
            )
            форма = build_draft_shape(предложение, code=код, name=str(име))
            # Проверката за дублиране е ПРЕДИ записа. Нова форма няма право да
            # ражда двусмислие точно там, където днес има еднозначност, а
            # точната проверка по сигнатура не вижда разлика от един градус
            # (`engine/shape_duplicates.py`).
            if not self._shape_duplicate_check_passes(форма, engine):
                return
            чернова.shapes.append(форма)
            път = self.engine_repository.save_draft(чернова)
        except ShapeFromCaseError as exc:
            self._error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._error(f"Черновата не можа да се запише: {exc}")
            return

        QMessageBox.information(
            self,
            "Черновата е записана",
            "Формата %s „%s“ е записана в чернова %s.\n\n"
            "Тя е ЧЕРНОВА (state=DRAFT) и затова не се разпознава: матчърът "
            "индексира само одобрени форми. Отвори MASTER -> Engine Studio, "
            "за да прегледаш силуета, да пуснеш контролните тестове (%d от "
            "случаите), да я одобриш и да публикуваш.\n\n%s"
            % (код, форма.name, чернова.version, len(форма.tests), път),
        )

    def _shape_duplicate_check_passes(
        self, форма, engine, *, заглавие: str = "Формата прилича на съществуваща",
    ) -> bool:
        """Показва присъдата за дублиране и пита, когато има сблъсък.

        Досега сблъсъкът се откриваше чак при публикуване, а при записа на
        чернова се изхвърляше мълчаливо: операторът виждаше „Черновата е
        записана“ без дума за това, че ражда двойник. Тук присъдата идва ПРЕДИ
        записа и точен дубликат не може да се запише изобщо.
        """

        from lion_rebar.engine import shape_duplicates as дубл

        присъда = дубл.провери(форма, engine.shapes, policy=self._recognition_policy())
        if присъда.вид == дубл.НОВА:
            return True

        редове = "\n".join(
            "  • %s „%s“ — %s%s"
            % (н.код, н.име, н.обяснение,
               "" if н.разлика_deg is None else " (разлика %.2f°)" % н.разлика_deg)
            for н in присъда.находки
        )
        if присъда.е_дубликат:
            self._error(
                "Формата НЕ е записана: тя дублира вече съществуваща.\n\n%s\n\n"
                "Допускът е този на разпознаването (%.2f°): две форми, които "
                "матчърът не може да различи, не са две форми. Ако все пак са "
                "различни, разликата трябва да е в отношенията между рамената "
                "и да е записана изрично." % (редове, присъда.допуск_deg)
            )
            return False
        отговор = QMessageBox.question(
            self,
            заглавие,
            "Новата форма не е дубликат, но се доближава до вече описана:\n\n%s\n\n"
            "Да я запиша ли като чернова?" % редове,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return отговор == QMessageBox.StandardButton.Yes

    def _next_free_shape_code(self, engine) -> str:
        """Първият свободен код. Оттеглените кодове НЕ се преизползват."""

        заети = {
            int(shape.code[1:])
            for shape in engine.shapes
            if str(shape.code or "").startswith("F") and shape.code[1:].isdigit()
        }
        номер = max(заети, default=9) + 1
        return f"F{номер:03d}"

    def _next_draft_version(self, engine) -> str:
        """Първата версия, за която НЯМА вече чернова.

        ``save_draft`` пише по име на файл, а името идва от версията. Ако се
        върне версия, която вече има чернова, тя се презаписва мълчаливо —
        чужда работа изчезва без въпрос. Затова заетите се прескачат.
        """

        # Заети са и черновите, И вече публикуваните: ``publish`` отказва
        # номер, който вече е публикуван („Използвай нов номер“), а
        # ``save_draft`` презаписва чернова със същото име. Гледането само на
        # черновите оставяше и двата капана.
        заети = {str(info.version) for info in self.engine_repository.list_drafts()}
        заети |= {str(info.version) for info in self.engine_repository.list_published()}
        части = str(engine.version or "0.0.0").split(".")
        try:
            последна = int(части[-1])
        except (ValueError, IndexError):
            return "0.0.1"
        for стъпка in range(1, 1000):
            кандидат = ".".join(части[:-1] + [str(последна + стъпка)])
            if кандидат not in заети:
                return кандидат
        return ".".join(части[:-1] + [str(последна + 1)])

    def _review_begin_form_assignment(self, row: dict) -> None:
        """Open the explicit cyan selection mode before any manual assignment."""
        if self.scene_result is None:
            return
        self._clear_geometry_highlights()
        self.scene_result.scene.clearSelection()
        selected = 0
        for handle in row.get("candidate_handles", []):
            item = self.scene_result.handle_to_item.get(str(handle))
            if item is None:
                continue
            item.setVisible(True)
            item.setSelected(True)
            selected += 1
        if selected == 0:
            annotation_handle = str(row.get("annotation_handle") or "")
            annotation_item = self.scene_result.handle_to_item.get(annotation_handle)
            if annotation_item is not None:
                annotation_item.setVisible(True)
                annotation_item.setSelected(True)
        self._begin_manual_geometry_selection(row)

    def _invalidate_manual_geometry_guard_cache(self) -> None:
        """Drop import/Engine-specific manual-selection authorization."""

        self._manual_geometry_guard_cache_key = None
        self._manual_geometry_guard_cache_ready = False
        self._manual_allowed_geometry_handles = frozenset()
        self._manual_protected_legend_handles = frozenset()
        self._manual_geometry_guard_error = None
        try:
            self.drawing_view.set_selection_entity_filter(None)
        except RuntimeError:
            pass

    def _manual_geometry_guard_handles(
        self,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Return authoritative selectable and protected handles for this DXF.

        A real project/import fails closed if its pinned Engine or geometry
        indexes cannot be loaded.  Small isolated Qt fixtures without a real
        project/import retain the historical type-only fallback, so UI widgets
        remain independently testable.
        """

        if self.scene_result is None:
            return frozenset(), frozenset()
        if self._manual_geometry_guard_cache_ready:
            return (
                self._manual_allowed_geometry_handles,
                self._manual_protected_legend_handles,
            )

        is_real_import = self.project is not None and self.import_result is not None
        if not is_real_import:
            cache_key = ("ISOLATED_UI_FIXTURE", id(self.scene_result))
            if self._manual_geometry_guard_cache_key != cache_key:
                self._manual_allowed_geometry_handles = frozenset(
                    str(record.handle).strip().upper()
                    for record in self.scene_result.handle_to_record.values()
                    if str(record.handle).strip()
                    and str(record.entity_type or "").upper()
                    in SUPPORTED_MANUAL_GEOMETRY
                )
                self._manual_protected_legend_handles = frozenset()
                self._manual_geometry_guard_error = None
                self._manual_geometry_guard_cache_key = cache_key
                self._manual_geometry_guard_cache_ready = True
            return (
                self._manual_allowed_geometry_handles,
                self._manual_protected_legend_handles,
            )

        try:
            engine = load_pinned_engine(
                self.project,
                self.engine_repository.load_current(),
            )
            cache_key = (
                "PROJECT_IMPORT_ENGINE",
                id(self.import_result),
                str(engine.engine_id or ""),
                str(engine.version or ""),
                str(engine.content_sha256 or ""),
            )
            if self._manual_geometry_guard_cache_key == cache_key:
                return (
                    self._manual_allowed_geometry_handles,
                    self._manual_protected_legend_handles,
                )

            entities = tuple(self.import_result.entities)
            policy = RecognitionPolicy.from_settings(engine.settings)
            context = build_geometry_candidate_context(entities, policy=policy)
            legend_index = build_position_key_legend_index(
                entities,
                shape_definitions=engine.shapes,
                control_tests=engine.tests,
                policy=policy,
                context=context,
                shape_index=EngineShapeIndex.from_shapes(engine.shapes),
            )
            self._manual_allowed_geometry_handles = frozenset(
                str(primitive.handle).strip().upper()
                for primitive in context.primitives
                if str(primitive.handle).strip()
            )
            self._manual_protected_legend_handles = frozenset(
                str(handle).strip().upper()
                for handle in legend_index.protected_geometry_handles
                if str(handle).strip()
            )
            self._manual_geometry_guard_error = None
            self._manual_geometry_guard_cache_key = cache_key
            self._manual_geometry_guard_cache_ready = True
        except Exception as exc:  # noqa: BLE001 - selection must fail closed
            self.logger.exception("Manual geometry authorization build failed")
            self._manual_allowed_geometry_handles = frozenset()
            self._manual_protected_legend_handles = frozenset()
            self._manual_geometry_guard_cache_key = (
                "PROJECT_IMPORT_ENGINE_FAILURE",
                id(self.import_result),
            )
            # Keep the current attempt fail-closed, but do not make a transient
            # Engine/index failure permanent for this import.  A later manual
            # attempt revalidates and can recover without reloading the DXF.
            self._manual_geometry_guard_cache_ready = False
            self._manual_geometry_guard_error = (
                "Защитата за ръчен избор не можа да провери текущия DXF и "
                f"Engine. Изборът е блокиран: {exc}"
            )
        return (
            self._manual_allowed_geometry_handles,
            self._manual_protected_legend_handles,
        )

    def _validate_manual_geometry_records(self, records):
        allowed, protected = self._manual_geometry_guard_handles()
        if self._manual_geometry_guard_error:
            raise ManualGeometryError(self._manual_geometry_guard_error)
        return validate_manual_geometry_records(
            records,
            allowed_handles=allowed,
            protected_handles=protected,
        )

    def _begin_manual_geometry_selection(self, row: dict) -> None:
        if self.scene_result is None:
            return
        self._leave_manual_geometry_selection(restore_review=False, clear_selection=False)
        self._clear_geometry_highlights()
        self._clear_manual_selection_highlights()
        self._manual_selection_row = dict(row)
        self._manual_selection_row["_manual_import_token"] = id(self.import_result)
        scene = self.scene_result.scene
        self._manual_selection_scene = scene
        allowed, protected = self._manual_geometry_guard_handles()
        self.drawing_view.set_selection_entity_filter(
            SUPPORTED_MANUAL_GEOMETRY,
            allowed_handles=allowed.difference(protected),
        )
        try:
            scene.selectionChanged.connect(self._update_manual_selection_toolbar)
        except (TypeError, RuntimeError):
            pass
        if self._review_center is not None:
            self._review_center.set_geometry_selection_active(
                True,
                str(row.get("annotation_text") or ""),
            )
        if self._review_dock is not None:
            self._review_dock.show()
            self._review_dock.raise_()
        self._manual_selection_toolbar.show()
        self._manual_selection_toolbar.raise_()
        self._update_manual_selection_toolbar()
        self.statusBar().showMessage(
            (
                self._manual_geometry_guard_error
                or "Режим избор: маркирай само разрешената армировъчна "
                "LINE/POLYLINE/ARC геометрия; схеми и заключена обща легенда "
                "не се избират. При липсваща производствена ос използвай "
                "„Без DXF връзка“."
            ),
            0,
        )
        self.raise_()
        self.activateWindow()
        self.drawing_view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _selected_manual_geometry_records(self, row: dict | None = None) -> list:
        if self.scene_result is None:
            return []
        annotation_handle = str((row or self._manual_selection_row or {}).get("annotation_handle") or "")
        selected_records = []
        seen: set[str] = set()
        for item in self.scene_result.scene.selectedItems():
            handle = str(item.data(0) or "")
            if not handle or handle == annotation_handle or handle in seen:
                continue
            record = self.scene_result.handle_to_record.get(handle)
            if record is None:
                continue
            if record.entity_type.upper() in SUPPORTED_MANUAL_GEOMETRY:
                selected_records.append(record)
                seen.add(handle)
        return selected_records

    def _update_manual_selection_toolbar(self) -> None:
        if self._manual_selection_row is None:
            return
        records = self._selected_manual_geometry_records()
        self._refresh_manual_selection_highlights(records)
        annotation = str(self._manual_selection_row.get("annotation_text") or "—")
        self._manual_selection_label.setText(
            f"СИНЯ ГЕОМЕТРИЯ за {annotation} | Избрани DXF обекти: {len(records)} | "
            "Ctrl+клик за добавяне/махане"
        )
        self._manual_selection_done_action.setEnabled(bool(records))
        self._manual_selection_clear_action.setEnabled(bool(records))

    def _refresh_manual_selection_highlights(self, records: list) -> None:
        self._clear_manual_selection_highlights()
        if self.scene_result is None:
            return
        highlight_pen = QPen(QColor("#00a3ff"), 7.0)
        highlight_pen.setCosmetic(True)
        bounding_rect = None
        for record in records:
            item = self.scene_result.handle_to_item.get(str(record.handle))
            if item is None:
                continue
            item.setVisible(True)
            try:
                scene_path = centerline_path_for_record(record)
                if scene_path is None:
                    continue
                if scene_path.isEmpty():
                    continue
                rect = scene_path.boundingRect()
                bounding_rect = (
                    rect if bounding_rect is None else bounding_rect.united(rect)
                )
                overlay = QGraphicsPathItem(scene_path)
                overlay.setPen(highlight_pen)
                overlay.setZValue(1_000_010.0)
                overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.scene_result.scene.addItem(overlay)
                self._manual_selection_highlights.append(overlay)
            except Exception as exc:
                self.logger.warning("Cannot build manual-selection highlight: %s", exc)
        if bounding_rect is not None:
            extent = max(bounding_rect.width(), bounding_rect.height(), 1.0)
            margin = max(5.0, extent * 0.05)
            self.drawing_view.fitInView(
                bounding_rect.adjusted(-margin, -margin, margin, margin),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            row = self._manual_selection_row or {}
            overlay_segments, overlay_source_label = _shape_dimension_overlay_input(
                row.get("master_shape_code") or row.get("suggested_shape_code"),
                row.get("production_segments_cm"),
                row.get("canonical_segment_lengths_cm"),
                row.get("suggested_segments_cm"),
            )
            self._add_shape_dimension_overlay(
                shape_code=(
                    row.get("master_shape_code")
                    or row.get("suggested_shape_code")
                ),
                segment_lengths_cm=overlay_segments,
                total_length_cm=row.get("declared_length_cm"),
                bounding_rect=bounding_rect,
                color=QColor("#0369a1"),
                storage=self._manual_selection_highlights,
                z_value=1_000_011.0,
                source_label=overlay_source_label,
            )

    def _add_shape_dimension_overlay(
        self,
        *,
        shape_code: object,
        segment_lengths_cm: object,
        total_length_cm: object,
        bounding_rect,
        color: QColor,
        storage: list,
        z_value: float,
        source_label: str | None = None,
    ) -> None:
        """Draw a screen-sized read-only label beside the reviewed centreline."""

        if self.scene_result is None:
            return
        text = _shape_dimension_overlay_text(
            shape_code,
            segment_lengths_cm,
            total_length_cm,
        )
        if not text:
            return
        if source_label:
            text = f"{source_label} | {text}"
        label = QGraphicsSimpleTextItem(text)
        font = QFont("Arial", 13)
        font.setBold(True)
        label.setFont(font)
        label.setBrush(color)
        halo = QPen(QColor("#ffffff"), 3.0)
        halo.setCosmetic(True)
        label.setPen(halo)
        label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        label.setZValue(z_value)
        offset = max(2.0, max(bounding_rect.width(), bounding_rect.height(), 1.0) * 0.02)
        label.setPos(bounding_rect.left(), bounding_rect.top() - offset)
        label.setToolTip(
            "Read-only визуален overlay; не променя DXF и не е ново доказателство."
        )
        self.scene_result.scene.addItem(label)
        storage.append(label)

    def _clear_manual_selection_highlights(self) -> None:
        scene = self.scene_result.scene if self.scene_result is not None else None
        if scene is not None:
            for item in self._manual_selection_highlights:
                try:
                    scene.removeItem(item)
                except RuntimeError:
                    pass
        self._manual_selection_highlights.clear()

    def _clear_manual_geometry_selection(self) -> None:
        if self.scene_result is None or self._manual_selection_row is None:
            return
        annotation_handle = str(self._manual_selection_row.get("annotation_handle") or "")
        self.scene_result.scene.clearSelection()
        annotation_item = self.scene_result.handle_to_item.get(annotation_handle)
        if annotation_item is not None:
            annotation_item.setVisible(True)
            annotation_item.setSelected(True)
        self._update_manual_selection_toolbar()

    def _complete_manual_geometry_selection(self) -> None:
        row = dict(self._manual_selection_row or {})
        if not row:
            return
        if not self._selected_manual_geometry_records(row):
            self._info("Не е избрана геометрия. Задръж Ctrl и маркирай линиите на позицията.")
            return
        self._leave_manual_geometry_selection(restore_review=False, clear_selection=False)
        try:
            self._review_assign_form(row)
        finally:
            self._restore_review_center_after_manual_selection()

    def _complete_manual_without_geometry(self) -> None:
        """Open the existing strict engineer gate with no inferred DXF link."""

        row = dict(self._manual_selection_row or {})
        if not row:
            return
        row["candidate_handles"] = []
        row["canonical_segment_lengths_cm"] = []
        row["canonical_turn_angles_deg"] = []
        row["candidate_basis"] = "OWNER_EXPLICIT_NO_DXF_GEOMETRY"
        row["force_geometryless_engineer_decision"] = True
        self._leave_manual_geometry_selection(
            restore_review=False,
            clear_selection=True,
        )
        try:
            self._review_engineer_decision(row)
        finally:
            self._restore_review_center_after_manual_selection()

    def _cancel_manual_geometry_selection(self) -> None:
        self._leave_manual_geometry_selection(restore_review=True, clear_selection=True)

    def _leave_manual_geometry_selection(
        self,
        *,
        restore_review: bool,
        clear_selection: bool,
    ) -> None:
        scene = self._manual_selection_scene
        if scene is not None:
            try:
                scene.selectionChanged.disconnect(self._update_manual_selection_toolbar)
            except (TypeError, RuntimeError):
                pass
        row = dict(self._manual_selection_row or {})
        self._manual_selection_scene = None
        self._manual_selection_row = None
        try:
            self.drawing_view.set_selection_entity_filter(None)
        except RuntimeError:
            # A deferred Review Center destroyed signal can arrive while the
            # parent window is already tearing down.  The Python-side state
            # above must still be cleared even if the C++ DrawingView is gone.
            pass
        if hasattr(self, "_manual_selection_toolbar"):
            try:
                self._manual_selection_toolbar.hide()
            except RuntimeError:
                pass
        if self._review_center is not None:
            try:
                self._review_center.set_geometry_selection_active(False)
            except RuntimeError:
                pass
        self._clear_manual_selection_highlights()
        self._clear_geometry_highlights()
        if clear_selection and self.scene_result is not None:
            self.scene_result.scene.clearSelection()
            annotation_handle = str(row.get("annotation_handle") or "")
            item = self.scene_result.handle_to_item.get(annotation_handle)
            if item is not None:
                item.setVisible(True)
                item.setSelected(True)
        try:
            self.statusBar().clearMessage()
        except RuntimeError:
            pass
        if restore_review:
            self._restore_review_center_after_manual_selection()

    def _restore_review_center_after_manual_selection(self) -> None:
        dialog = self._review_center
        if dialog is None:
            return
        try:
            dialog._restore_after_preview()
            dialog.set_geometry_selection_active(False)
            if self._review_dock is not None:
                self._review_dock.show()
                self._review_dock.raise_()
            dialog.show()
        except RuntimeError:
            pass

    def _review_assign_form(self, row: dict) -> None:
        dialog = self._review_center
        if dialog is None or self.project is None or self.scene_result is None:
            return
        annotation_handle = str(row.get("annotation_handle") or "")
        import_token = row.get("_manual_import_token")
        current_annotation = self.scene_result.handle_to_record.get(annotation_handle)
        if (
            current_annotation is None
            or (
                import_token is not None
                and int(import_token) != id(self.import_result)
            )
        ):
            dialog.mark_error(
                "Решението е от предишен DXF import и е обезсилено. "
                "Отвори отново Review Center за текущия чертеж."
            )
            return
        selected_records = self._selected_manual_geometry_records(row)
        if not selected_records:
            self._info(
                "Първо избери правилната геометрия с Ctrl+клик и натисни "
                "„Готово — Инженерно решение“."
            )
            return
        try:
            engine = load_pinned_engine(
                self.project, self.engine_repository.load_current()
            )
            policy = RecognitionPolicy.from_settings(engine.settings)
            selected_records = self._validate_manual_geometry_records(
                selected_records
            )
            summary = summarize_selected_geometry(selected_records, policy=policy)
        except (EngineRepositoryError, ExcelExportError, ManualGeometryError) as exc:
            dialog.mark_error(str(exc))
            return
        except Exception as exc:
            self.logger.exception("Engineer selected geometry summary failed")
            dialog.mark_error(f"Избраната геометрия не може да бъде обработена: {exc}")
            return

        selected_row = dict(row)
        declared_length = row.get("declared_length_cm")
        reference_only = _manual_geometry_exceeds_declared_length(
            summary.total_length_cm,
            declared_length,
        )
        if reference_only:
            # A drawn bar outline (DB80 in the R-008 case) can sit directly
            # under an annotation but cannot become production centreline
            # evidence: its perimeter is many times the declared bar length.
            # Keep it only as visual context and enter the existing strict
            # geometry-less engineer flow.
            notice = (
                "Избраният DXF обект е само графичен контекст: измерената му "
                f"дължина {summary.total_length_cm:g} cm надвишава "
                f"L={float(declared_length):g} cm. "
                "Той няма да бъде записан като производствена ос. Избери F006/F007 "
                "и въведи окончателните сегменти ръчно."
            )
            selected_row.update(
                {
                    "candidate_handles": [],
                    "candidate_basis": "OWNER_SELECTED_DXF_REFERENCE_ONLY",
                    "candidate_notes": [*summary.warnings, notice],
                    "canonical_segment_lengths_cm": [],
                    "canonical_turn_angles_deg": [],
                    "geometry_length_raw": summary.total_length_cm,
                    "effective_geometry_length_cm": None,
                    "segment_count": 0,
                    "manual_geometry_notice": notice,
                    "force_geometryless_engineer_decision": True,
                    "global_conflict": False,
                    "geometry_owner_conflict": False,
                }
            )
        else:
            selected_row.update(
                {
                    "candidate_handles": list(summary.handles),
                    "candidate_layer": (
                        summary.layers[0]
                        if summary.layers
                        else row.get("candidate_layer")
                    ),
                    "candidate_basis": "OWNER_SELECTED_DXF_GEOMETRY",
                    "candidate_notes": list(summary.warnings),
                    "canonical_segment_lengths_cm": list(summary.segment_lengths_cm),
                    "canonical_turn_angles_deg": list(summary.turn_angles_deg),
                    "geometry_length_raw": summary.total_length_cm,
                    "effective_geometry_length_cm": summary.total_length_cm,
                    "segment_count": summary.segment_count,
                    "global_conflict": False,
                    "geometry_owner_conflict": False,
                }
            )
        existing = self._read_approved_geometry_link(annotation_handle) or {}
        problem_decision = dialog.decision_payload(annotation_handle)
        (
            _указател_за_преизползване,
            _предложение_за_преизползване,
        ) = self._reuse_proposal_for_row(selected_row, engine=engine)
        editor = EngineerDecisionDialog(
            row=selected_row,
            shapes=engine.shapes,
            existing_link=existing,
            problem_decision=problem_decision,
            engine_version=engine.version,
            project_name=self.project.name,
            project_version=self.project.version,
            decision_suggestion=self._decision_suggestion_for_row(
                selected_row,
                engine_version=engine.version,
            ),
            reuse_index=_указател_за_преизползване,
            reuse_proposal=_предложение_за_преизползване,
            parent=self,
        )
        saved = self._run_engineer_editor(
            editor,
            dialog,
            log_message="Engineer selected-geometry decision save failed",
        )
        if saved is None:
            return
        payload, path = saved
        canonical_decision = dialog.mark_engineer_resolved(
            annotation_handle,
            payload,
        )
        self._record_review_event(
            (
                "NON_PRODUCTION_POSITION"
                if str(payload.get("reason_code") or "").upper() == "NON_PRODUCTION_POSITION"
                else "ENGINEER_RESOLVED"
            ),
            {
                "annotation_handle": annotation_handle,
                "annotation_text": (payload.get("annotation") or {}).get("text"),
                "resolved_annotation_text": (payload.get("annotation") or {}).get("resolved_text"),
                "action_class": payload.get("action_class"),
                "reason_code": payload.get("reason_code"),
                "reason": payload.get("reason"),
                "note": payload.get("note"),
                "row_context": payload.get("row_context"),
                "original": payload.get("original"),
                "resolved": payload.get("resolved"),
                "geometry": payload.get("geometry"),
                "diagnostic_problem": payload.get("diagnostic_problem"),
                "decision_suggestion": payload.get("decision_suggestion"),
                # ОСНОВАНИЕТО на приетото предложение влиза в журнала:
                # по него се намира какво да се провери наново, ако
                # решението-донор бъде оттеглено.
                "preizpolzvane": payload.get("preizpolzvane"),
                "evidence": canonical_decision.get("evidence"),
                "canonical_decision": canonical_decision,
            },
        )
        self.statusBar().showMessage(f"Инженерното решение е записано: {path}", 12000)

    def _select_annotation_handle(self, handle: str) -> bool:
        if self.scene_result is None:
            return False
        item = self.scene_result.handle_to_item.get(str(handle))
        if item is None:
            return False
        self._clear_geometry_highlights()
        self.scene_result.scene.clearSelection()
        item.setVisible(True)
        item.setSelected(True)
        rect = item.sceneBoundingRect()
        margin = max(20.0, max(rect.width(), rect.height(), 1.0) * 6.0)
        self.drawing_view.fitInView(
            rect.adjusted(-margin, -margin, margin, margin),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        if self._review_center is not None:
            view_point = self.drawing_view.mapFromScene(rect.center())
            global_point = self.drawing_view.viewport().mapToGlobal(view_point)
            self._review_center.place_away_from(global_point)
        return True

    def _review_open_candidates(self, row: dict) -> None:
        analysis = geometry_analysis_from_report_row(row)
        if not analysis.candidates:
            self._review_locate_annotation(row)
            self._info(
                "Пакетният отчет няма кандидат за тази позиция. "
                "Надписът е показан в чертежа за ръчна диагностика."
            )
            return
        approved_link = self._read_approved_geometry_link(analysis.annotation_handle)
        self._close_geometry_dialog()
        self._clear_geometry_highlights()
        dialog = GeometryCandidatesDialog(analysis, self, approved_link=approved_link)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.candidatePreviewRequested.connect(self._preview_geometry_candidate)
        dialog.candidateApprovalRequested.connect(
            lambda candidate, current_analysis=analysis, current_dialog=dialog: self._approve_geometry_candidate(
                current_analysis, candidate, current_dialog
            )
        )
        dialog.finished.connect(
            lambda _result, current_dialog=dialog: self._geometry_dialog_closed(current_dialog)
        )
        self._geometry_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _review_approve_row(self, row: dict) -> None:
        dialog = self._review_center
        if dialog is None:
            return
        handle = str(row.get("annotation_handle") or "")
        current_decision = dialog.decision_payload(handle)
        if (
            str(current_decision.get("decision") or "")
            in {"PROBLEM_RECORDED", "FLAGGED", "NO_VALID_CANDIDATE", "MISSING_MASTER"}
            or not final_confirmation_eligible(row)
        ):
            dialog.mark_error(
                "Тази позиция не може да бъде потвърдена с обикновено „Одобри“. "
                "Използвай „Инженерно решение“ и задай точната форма, брой, Ø, L и сегменти."
            )
            return
        if is_missing_master_row(row):
            dialog.mark_missing_master(str(row.get("annotation_handle") or ""), row)
            self.statusBar().showMessage(missing_master_reason(row), 12000)
            return
        analysis = geometry_analysis_from_report_row(row)
        if not analysis.candidates:
            dialog.mark_error("Тази позиция няма кандидат за одобряване.")
            return
        candidate = analysis.candidates[0]
        if bool(row.get("global_conflict")) or str(row.get("batch_status")) == "РИСК":
            answer = QMessageBox.question(
                self,
                "Високорисково ръчно одобрение",
                f"Надпис: {analysis.label}\nКандидат: {', '.join(candidate.handles)}\n\n"
                "Позицията е конфликтна или рискова. Одобряваш ли точно тази връзка?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        elif not self._review_quick_mode_confirmed:
            answer = QMessageBox.question(
                self,
                "Включване на бърз режим",
                "Review Center използва две отделни действия: първо „Покажи“, после "
                "„Одобри и следващ“. След потвърждението връзката ще се записва без "
                "втори изскачащ прозорец за всяка позиция.\n\nДа включа ли бързия режим за тази сесия?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._review_quick_mode_confirmed = True
        try:
            saved_path = self._write_approved_geometry_link(analysis, candidate)
        except Exception as exc:
            self.logger.exception("Review Center approval failed")
            dialog.mark_error(f"Връзката не беше записана: {exc}")
            return
        dialog.mark_approved(analysis.annotation_handle)
        self._record_review_event(
            "APPROVED",
            {
                "annotation_handle": analysis.annotation_handle,
                "annotation_text": analysis.label,
                "geometry_handles": list(candidate.handles),
                "master_shape_code": candidate.master_shape_code,
                "master_shape_mirrored": candidate.master_shape_mirrored,
                "review_reason_codes": list(candidate.review_reason_codes),
                "geometry_length_raw": candidate.geometry_length_raw,
                "declared_length_cm": candidate.declared_length_cm,
            },
        )
        self.statusBar().showMessage(f"Одобрено чрез Review Center: {saved_path}", 7000)

    def _review_bulk_rows_have_blocking_decision(self, rows: list[dict]) -> bool:
        dialog = self._review_center
        if dialog is None:
            return True
        blocked: list[str] = []
        for row in rows:
            handle = str(row.get("annotation_handle") or "")
            decision = str(dialog.decision_payload(handle).get("decision") or "")
            if decision in REJECTED_REVIEW_DECISIONS:
                blocked.append(handle or str(row.get("annotation_text") or "?"))
        if blocked:
            dialog.mark_error(
                "Масовото приемане е отказано: в подадения пакет има диагностични "
                "проблеми или изрично изключени позиции. Реши ги поотделно чрез "
                "„Инженерно решение“. Блокирани: " + ", ".join(blocked[:8])
                + (" ..." if len(blocked) > 8 else "")
            )
            return True
        return False

    def _bulk_member_preview(self, rows: list[dict], *, approval_mode: str) -> str:
        """Какво ТОЧНО ще се промени — преди прилагането (§ 6.3).

        Дотук диалогът обещаваше „ще бъдат записани N връзки", където N беше
        броят на подадените редове, а проверката по членове се случваше чак в
        писача: операторът научаваше за пропуснатите СЛЕД записа. Присъдите тук
        идват от същата функция, която писачът ползва, затова двете числа не
        могат да се разминат.
        """

        from lion_rebar.review.bulk_member_verdicts import (
            bulk_member_verdicts,
            refusal_lines,
            summarise,
        )

        links: list[dict] = []
        try:
            път = self._ledger_path()
            # Под същата ключалка като писача: иначе четенето може да улови мига
            # на ``os.replace`` и прегледът да покаже по-голямо число от това,
            # което писачът после ще запише.
            with exclusive_file_lock(път):
                if път.is_file():
                    данни = json.loads(път.read_text(encoding="utf-8-sig"))
                    links = [
                        стойност
                        for стойност in (данни.get("links") or [])
                        if isinstance(стойност, dict)
                    ]
        except Exception as exc:  # noqa: BLE001 - помощен път
            self.logger.warning("Прегледът на масовото приемане чете без ledger: %s", exc)
        присъди = bulk_member_verdicts(
            rows, approval_mode=approval_mode, existing_links=links
        )
        сводка = summarise(присъди)
        редове = [
            "Ще бъдат записани %d от %d подадени."
            % (сводка["accepted"], сводка["total"])
        ]
        if сводка["refused"]:
            редове.append("")
            редове.append(
                "%d НЯМА да бъдат записани и остават за индивидуално решение:"
                % сводка["refused"]
            )
            редове.extend(refusal_lines(присъди))
        return "\n".join(редове)

    def _review_bulk_approve_rows(self, rows: list[dict]) -> None:
        dialog = self._review_center
        if dialog is None or not rows:
            return
        if self._review_bulk_rows_have_blocking_decision(rows):
            return
        answer = QMessageBox.question(
            self,
            "Контролирано масово приемане",
            f"Контролната извадка е премината без маркиран проблем.\n\n"
            + self._bulk_member_preview(rows, approval_mode="STRONG")
            + "\n\nВръзките са силни, с потвърден Ø от DXF слоя.\n"
            "Няма да се приемат геометрично силни, рискови, конфликтни или непълни позиции.\n\n"
            "Да запиша ли тези връзки?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ledger_snapshot = self._read_snapshot(self._ledger_path())
        state_snapshot = self._read_snapshot(dialog.state_path)
        try:
            path, handles = self._write_approved_geometry_links_from_report_rows(rows)
        except Exception as exc:
            self.logger.exception("Review Center bulk approval failed")
            dialog.mark_error(f"Масовото записване беше отказано: {exc}")
            return
        dialog.mark_bulk_approved(handles)
        self._record_bulk_operation(
            dialog, mode="STRICT_SAMPLE", handles=handles,
            ledger_snapshot=ledger_snapshot, state_snapshot=state_snapshot,
        )
        self._record_review_event(
            "BULK_APPROVED",
            self._bulk_decision_payload(rows, handles, mode="STRICT_SAMPLE"),
        )
        self.statusBar().showMessage(
            f"Контролирано масово приети връзки: {len(handles)} | {path}",
            12000,
        )

    def _review_bulk_approve_geometry_rows(self, rows: list[dict]) -> None:
        dialog = self._review_center
        if dialog is None or not rows:
            return
        if self._review_bulk_rows_have_blocking_decision(rows):
            return
        answer = QMessageBox.question(
            self,
            "Масово приемане — силни по геометрия",
            f"Поне 10 позиции от тази опашка са проверени и няма маркиран проблем.\n\n"
            + self._bulk_member_preview(rows, approval_mode="GEOMETRY_STRONG")
            + "\n\nВръзките са геометрично силни.\n"
            "Диаметърът се взема от ARM_N надписа, защото DXF слоят не го потвърждава.\n"
            "Рискови, конфликтни, непълни и невалидни MASTER форми няма да се приемат.\n\n"
            "Да запиша ли тези връзки?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ledger_snapshot = self._read_snapshot(self._ledger_path())
        state_snapshot = self._read_snapshot(dialog.state_path)
        try:
            path, handles = self._write_approved_geometry_links_from_report_rows(
                rows, approval_mode="GEOMETRY_STRONG"
            )
        except Exception as exc:
            self.logger.exception("Review Center geometry-strong bulk approval failed")
            dialog.mark_error(f"Масовото записване беше отказано: {exc}")
            return
        dialog.mark_geometry_bulk_approved(handles)
        self._record_bulk_operation(
            dialog, mode="GEOMETRY_SAMPLE", handles=handles,
            ledger_snapshot=ledger_snapshot, state_snapshot=state_snapshot,
        )
        self._record_review_event(
            "BULK_APPROVED",
            self._bulk_decision_payload(rows, handles, mode="GEOMETRY_SAMPLE"),
        )
        self.statusBar().showMessage(
            f"Масово приети геометрично силни връзки: {len(handles)} | {path}",
            12000,
        )

    def _review_bulk_approve_f006_rows(self, rows: list[dict]) -> None:
        dialog = self._review_center
        if dialog is None or not rows:
            return
        if self._review_bulk_rows_have_blocking_decision(rows):
            return
        answer = QMessageBox.question(
            self,
            "Масово приемане — автоматично разпознати стремена F006",
            f"Поне 5 стремена F006 са проверени и няма маркиран проблем.\n\n"
            + self._bulk_member_preview(rows, approval_mode="F006_STRONG")
            + "\n\nСтремената са строго разпознати съставни.\n"
            "Формата е четиристранен контур с два отделени ключа. Стойността след x "
            "в надписа не се използва като разгъната дължина; реалната L се пази от DXF.\n"
            "Конфликтни, рискови и различни геометрии няма да се приемат.\n\n"
            "Да запиша ли тези връзки?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ledger_snapshot = self._read_snapshot(self._ledger_path())
        state_snapshot = self._read_snapshot(dialog.state_path)
        try:
            path, handles = self._write_approved_geometry_links_from_report_rows(
                rows, approval_mode="F006_STRONG"
            )
        except Exception as exc:
            self.logger.exception("Review Center F006 bulk approval failed")
            dialog.mark_error(f"Масовото записване на стремената беше отказано: {exc}")
            return
        dialog.mark_f006_bulk_approved(handles)
        self._record_bulk_operation(
            dialog, mode="F006_SAMPLE", handles=handles,
            ledger_snapshot=ledger_snapshot, state_snapshot=state_snapshot,
        )
        self._record_review_event(
            "BULK_APPROVED",
            self._bulk_decision_payload(rows, handles, mode="F006_SAMPLE"),
        )
        self.statusBar().showMessage(
            f"Масово приети стремена F006: {len(handles)} | {path}",
            12000,
        )

    def _review_final_confirm_rows(self, rows: list[dict]) -> None:
        dialog = self._review_center
        if dialog is None or not rows:
            return
        invalid = [row for row in rows if not final_confirmation_eligible(row)]
        if invalid:
            dialog.mark_error(
                "Общото потвърждение е отказано: поне една позиция вече не минава безопасната порта."
            )
            return
        answer = QMessageBox.question(
            self,
            "Потвърди останалите безопасни позиции",
            "Всички реални инженерни казуси са решени индивидуално.\n\n"
            + self._bulk_member_preview(rows, approval_mode="FINAL_USER_CONFIRMATION")
            + "\n\nТова са останалите CONTROL позиции без ownership/Master конфликт, "
            "без блокирани размери и без data-quality флагове.\n\n"
            "Това е изрично USER потвърждение. Не променя Engine/Matcher.\n\n"
            "Да продължа ли?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ledger_snapshot = self._read_snapshot(self._ledger_path())
        state_snapshot = self._read_snapshot(dialog.state_path)
        try:
            path, handles = self._write_approved_geometry_links_from_report_rows(
                rows, approval_mode="FINAL_USER_CONFIRMATION"
            )
        except Exception as exc:
            self.logger.exception("Final user confirmation failed")
            dialog.mark_error(f"Общото потвърждение беше отказано: {exc}")
            return
        dialog.mark_final_bulk_approved(handles)
        self._record_bulk_operation(
            dialog, mode="FINAL_USER_CONFIRMATION", handles=handles,
            ledger_snapshot=ledger_snapshot, state_snapshot=state_snapshot,
        )
        self._record_review_event(
            "BULK_APPROVED",
            self._bulk_decision_payload(rows, handles, mode="FINAL_USER_CONFIRMATION"),
        )
        self.statusBar().showMessage(
            f"Потвърдени останали безопасни позиции: {len(handles)} | {path}", 12000
        )

    def _ledger_path(self) -> Path:
        return Path(self.project.root) / "working" / "approved_geometry_links.json"

    @staticmethod
    def _read_snapshot(path: Path) -> bytes | None:
        return path.read_bytes() if path.is_file() else None

    def _record_bulk_operation(
        self,
        dialog,
        *,
        mode: str,
        handles: list[str],
        ledger_snapshot: bytes | None,
        state_snapshot: bytes | None,
    ) -> None:
        """Записва масовата операция в journal-а, за да може да се върне.

        Best-effort: ако самият audit запис се провали, вече записаните
        production одобрения НЕ се отменят заради това — операторското
        решение е реално и стои, дори undo-то на тази конкретна операция
        да остане недостъпно. Грешката отива в лога, не се крие тихо.
        """

        if self.project is None or not handles:
            return
        try:
            from lion_rebar.review.bulk_approval_operations import record_bulk_operation

            record_bulk_operation(
                self.project.root,
                mode=mode,
                applied_by=self._operator_for_record(),
                affected_handles=handles,
                ledger_snapshot=ledger_snapshot,
                state_snapshot=state_snapshot,
                identity=current_identity(self.project),
            )
        except Exception:  # noqa: BLE001
            self.logger.exception(
                "Bulk approval operation could not be journaled for undo"
            )

    @_locked_project_file("working/approved_geometry_links.json")
    def _write_approved_geometry_links_from_report_rows(
        self,
        rows: list[dict],
        *,
        approval_mode: str = "STRONG",
    ) -> tuple[Path, list[str]]:
        if self.project is None:
            raise RuntimeError("Няма отворен проект.")
        working_dir = Path(self.project.root) / "working"
        working_dir.mkdir(parents=True, exist_ok=True)
        output_path = working_dir / "approved_geometry_links.json"
        identity = current_identity(self.project)
        if identity.source_generation_id and not identity.analysis_run_id:
            raise RuntimeError(
                "Проектът няма активен analysis run. Пусни пакетния анализ отново."
            )
        if output_path.exists():
            data = json.loads(output_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("links", []), list):
                raise RuntimeError("Файлът approved_geometry_links.json е с неочаквана структура.")
        else:
            data = {
                "schema": "lion-rebar-approved-geometry-links/v1",
                "project_name": self.project.name,
                "links": [],
                "history": [],
            }

        links = [dict(value) for value in data.get("links", []) if isinstance(value, dict)]
        history = [dict(value) for value in data.get("history", []) if isinstance(value, dict)]
        existing_by_annotation = {
            str(link.get("annotation_handle") or ""): link for link in links
        }
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        if identity.source_generation_id:
            stale_rows = [
                str(row.get("annotation_handle") or "")
                for row in rows
                if mismatch_reason(row, identity, require_run=True)
            ]
            if stale_rows:
                raise RuntimeError(
                    "Масовото решение съдържа редове извън текущия project/source/run: "
                    + ", ".join(stale_rows[:5])
                )
        new_links: list[dict] = []
        approved_handles: list[str] = []
        # ПРОБЛЕМНАТА ПОЗИЦИЯ СЕ ПРОПУСКА, ГРУПАТА ПРОДЪЛЖАВА.
        # По-ранната версия вдигаше изключение вътре в цикъла и отменяше
        # ЦЕЛИЯ пакет заради един конфликт — 21 годни позиции оставаха
        # неодобрени заради една чужда геометрия. Конфликтът е свойство на
        # позицията, не на групата.
        #
        # Присъдите се смятат от ``review/bulk_member_verdicts.py`` — СЪЩАТА
        # функция, която потвърждението показва преди записа (§ 6.3). Две
        # реализации биха се разминали при първата промяна в едната.
        verdicts = bulk_member_verdicts(
            rows, approval_mode=approval_mode, existing_links=links
        )
        skipped = [
            (член.label, член.reason) for член in verdicts if not член.accepted
        ]
        for член in verdicts:
            if not член.accepted:
                continue
            row = член.row
            analysis = член.analysis
            candidate = член.candidate
            annotation = член.annotation_handle
            geometry_key = член.geometry_key
            shared_legend_reference = член.shared_legend_reference
            existing = existing_by_annotation.get(annotation)
            if existing is not None:
                archived = dict(existing)
                archived["replaced_at"] = timestamp
                history.append(archived)
            link = {
                "annotation_handle": annotation,
                "annotation_text": analysis.label,
                "resolved_annotation_text": analysis.label,
                "annotation_count": row.get("annotation_count"),
                "production_count": row.get("annotation_count"),
                "diameter_mm": analysis.diameter_mm,
                "production_diameter_mm": analysis.diameter_mm,
                "declared_length_cm": analysis.declared_length_cm,
                "production_length_cm": candidate.effective_geometry_length_cm,
                "production_length_source": "REVIEW_REPORT_CONFIRMED",
                "production_segment_lengths_cm": list(candidate.canonical_segment_lengths_cm),
                "geometry_handles": list(candidate.handles),
                "geometry_layer": candidate.layer,
                "segment_count": candidate.segment_count,
                "geometry_length_raw": candidate.geometry_length_raw,
                "numeric_difference": candidate.numeric_difference,
                "raw_numeric_difference": candidate.raw_numeric_difference,
                "effective_geometry_length_cm": candidate.effective_geometry_length_cm,
                "distance_raw": candidate.distance_raw,
                "diameter_layer_match": candidate.diameter_layer_match,
                "candidate_confidence_before_approval": candidate.confidence,
                "candidate_basis": candidate.basis,
                "candidate_notes": list(candidate.notes),
                "master_shape_code": candidate.master_shape_code,
                "master_shape_name": candidate.master_shape_name,
                "contextual_rule_code": candidate.contextual_rule_code,
                "contextual_rule_note": candidate.contextual_rule_note,
                "canonical_segment_lengths_cm": list(candidate.canonical_segment_lengths_cm),
                "canonical_turn_angles_deg": list(candidate.canonical_turn_angles_deg),
                "annotation_parameter_role": (
                    "STIRRUP_SERIES_PARAMETER" if approval_mode == "F006_STRONG" else "DECLARED_LENGTH"
                ),
                "approval_status": (
                    "ОДОБРЕНО СЛЕД ФИНАЛНО ИНЖЕНЕРНО ПОТВЪРЖДЕНИЕ"
                    if approval_mode == "FINAL_USER_CONFIRMATION"
                    else (
                        "ОДОБРЕНО СЛЕД 5-ПОЗИЦИОННА F006 ИЗВАДКА"
                        if approval_mode == "F006_STRONG"
                        else (
                            "ОДОБРЕНО СЛЕД 10-ПОЗИЦИОННА ГЕОМЕТРИЧНА ИЗВАДКА"
                            if approval_mode == "GEOMETRY_STRONG"
                            else "ОДОБРЕНО СЛЕД КОНТРОЛНА ИЗВАДКА"
                        )
                    )
                ),
                "approved_at": timestamp,
                "approved_by": (
                    "USER_FINAL_CONFIRMATION"
                    if approval_mode == "FINAL_USER_CONFIRMATION"
                    else (
                        "USER_BULK_AFTER_F006_SAMPLE"
                        if approval_mode == "F006_STRONG"
                        else (
                            "USER_BULK_AFTER_GEOMETRY_SAMPLE"
                            if approval_mode == "GEOMETRY_STRONG"
                            else "USER_BULK_AFTER_STRATIFIED_SAMPLE"
                        )
                    )
                ),
                "engineer_decision": (
                    {
                        "schema": "lion-rebar-engineer-decision/v1",
                        "action_class": "CONFIRMATION",
                        "reason_code": "FINAL_USER_CONFIRMATION",
                        "reason": "Инженерът потвърди всички останали безопасни предложения.",
                    }
                    if approval_mode == "FINAL_USER_CONFIRMATION"
                    else None
                ),
                "automatic_outputs": {
                    "position": False,
                    "shape": False,
                    "zone": False,
                    "spacing": False,
                    "specification_row": approval_mode == "FINAL_USER_CONFIRMATION",
                },
            }
            if identity.source_generation_id:
                stamp_payload(link, identity)
            existing_by_annotation[annotation] = link
            new_links.append(link)
            approved_handles.append(annotation)

        retained = [
            link
            for link in links
            if str(link.get("annotation_handle") or "") not in set(approved_handles)
        ]
        retained.extend(new_links)
        data["schema"] = "lion-rebar-approved-geometry-links/v1"
        data["project_name"] = self.project.name
        data["updated_at"] = timestamp
        data["links"] = retained
        data["history"] = history
        if identity.source_generation_id:
            stamp_payload(data, identity)
        invalidate_final_control(
            Path(working_dir).parent,
            event="ENGINEER_DECISION",
            actor="MAIN_WINDOW",
            note="Променени одобрени геометрични връзки.",
        )
        atomic_write_text(
            output_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        if skipped:
            # Пропуснатите се изброяват изрично: мълчаливо пропускане е
            # по-лошо от отказ, защото операторът не научава кои позиции
            # са останали неодобрени.
            lines = [f"  \u2022 {label}: {reason}" for label, reason in skipped[:10]]
            if len(skipped) > 10:
                lines.append(f"  \u2022 ... и още {len(skipped) - 10}")
            QMessageBox.warning(
                self,
                "Част от позициите не бяха одобрени",
                f"Одобрени: {len(approved_handles)}\n"
                f"Пропуснати: {len(skipped)}\n\n" + "\n".join(lines) +
                "\n\nПропуснатите остават за индивидуално решение.",
            )
        return output_path, approved_handles

    @_locked_project_file("working/approved_geometry_links.json")
    def _write_manual_form_assignment(self, payload: dict) -> Path:
        if self.project is None:
            raise RuntimeError("Няма отворен проект.")
        annotation = dict(payload.get("annotation") or {})
        geometry = dict(payload.get("geometry") or {})
        dimensions = dict(payload.get("dimensions") or {})
        annotation_handle = str(annotation.get("handle") or "")
        geometry_handles = tuple(sorted(str(value) for value in geometry.get("handles", []) if str(value)))
        shape_code = str(payload.get("shape_code") or "")
        shape_name = str(payload.get("shape_name") or shape_code)
        if not annotation_handle or not geometry_handles or not shape_code:
            raise RuntimeError("Липсват задължителни данни за ръчната форма.")

        working_dir = Path(self.project.root) / "working"
        working_dir.mkdir(parents=True, exist_ok=True)
        output_path = working_dir / "approved_geometry_links.json"
        identity = current_identity(self.project)
        if identity.source_generation_id and not identity.analysis_run_id:
            raise RuntimeError(
                "Проектът няма активен analysis run. Пусни пакетния анализ отново."
            )
        if output_path.exists():
            data = json.loads(output_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("links", []), list):
                raise RuntimeError("approved_geometry_links.json е с неочаквана структура.")
        else:
            data = {
                "schema": "lion-rebar-approved-geometry-links/v1",
                "project_name": self.project.name,
                "links": [],
                "history": [],
            }

        links = [dict(value) for value in data.get("links", []) if isinstance(value, dict)]
        history = [dict(value) for value in data.get("history", []) if isinstance(value, dict)]
        for existing in links:
            existing_annotation = str(existing.get("annotation_handle") or "")
            existing_key = tuple(sorted(str(value) for value in existing.get("geometry_handles", []) if str(value)))
            if existing_key == geometry_handles and existing_annotation and existing_annotation != annotation_handle:
                raise RuntimeError(
                    f"Избраната геометрия вече е одобрена за друг надпис: {existing_annotation}."
                )

        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        retained = []
        for existing in links:
            if str(existing.get("annotation_handle") or "") == annotation_handle:
                archived = dict(existing)
                archived["replaced_at"] = timestamp
                history.append(archived)
            else:
                retained.append(existing)

        declared = annotation.get("declared_length_cm")
        total = geometry.get("total_length_cm")
        production_length = dimensions.get("production_length_cm", declared)
        dimension_source = str(dimensions.get("source") or "ANNOTATION")
        production_segments = list(dimensions.get("production_segment_lengths_cm") or [])
        try:
            difference = float(total) - float(declared)
        except (TypeError, ValueError):
            difference = None
        try:
            production_difference = float(total) - float(production_length)
        except (TypeError, ValueError):
            production_difference = None
        link = {
            "annotation_handle": annotation_handle,
            "annotation_text": str(annotation.get("text") or ""),
            "annotation_count": annotation.get("count"),
            "diameter_mm": annotation.get("diameter_mm"),
            "declared_length_cm": declared,
            "production_length_cm": production_length,
            "production_length_source": dimension_source,
            "production_segment_lengths_cm": production_segments,
            "geometry_handles": list(geometry_handles),
            "geometry_layer": ", ".join(str(value) for value in geometry.get("layers", [])),
            "segment_count": geometry.get("segment_count"),
            "geometry_length_raw": total,
            "numeric_difference": difference,
            "geometry_vs_production_difference_cm": production_difference,
            "distance_raw": None,
            "diameter_layer_match": False,
            "candidate_confidence_before_approval": "РЪЧНО ЗАДАДЕНА ФОРМА",
            "candidate_basis": "OWNER_SELECTED_DXF_GEOMETRY",
            "candidate_notes": list(geometry.get("warnings", [])),
            "master_shape_code": shape_code,
            "master_shape_name": shape_name,
            "approval_status": "ОДОБРЕНО РЪЧНО СЪС ЗАДАДЕНА ФОРМА",
            "approved_at": timestamp,
            "approved_by": "OWNER_MANUAL_FORM_ASSIGNMENT",
            "manual_assignment": {
                "reason": payload.get("reason"),
                "note": payload.get("note"),
                "canonical_signature": geometry.get("canonical_signature"),
                "closed": geometry.get("closed"),
                "connected": geometry.get("connected"),
                "segment_lengths_cm": geometry.get("segment_lengths_cm", []),
                "turn_angles_deg": geometry.get("turn_angles_deg", []),
                "entity_types": geometry.get("entity_types", []),
                "training_example_saved": bool(payload.get("save_training_example")),
                "dimension_source": dimension_source,
                "production_length_cm": production_length,
                "production_segment_lengths_cm": production_segments,
                "measured_geometry_length_cm": total,
                "declared_annotation_length_cm": declared,
            },
            "automatic_outputs": {
                "position": False,
                "shape": True,
                "zone": False,
                "spacing": False,
                "specification_row": False,
            },
        }
        if identity.source_generation_id:
            stamp_payload(link, identity)
        retained.append(link)
        data["schema"] = "lion-rebar-approved-geometry-links/v1"
        data["project_name"] = self.project.name
        data["updated_at"] = timestamp
        data["links"] = retained
        data["history"] = history
        if identity.source_generation_id:
            stamp_payload(data, identity)
        invalidate_final_control(
            Path(working_dir).parent,
            event="ENGINEER_DECISION",
            actor="MAIN_WINDOW",
            note="Променени одобрени геометрични връзки.",
        )
        atomic_write_text(
            output_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )

        if payload.get("save_training_example"):
            try:
                self._append_manual_training_example(payload, status="ASSIGNED_TO_MASTER")
            except Exception as exc:
                logger = getattr(self, "logger", None)
                if logger is not None:
                    logger.warning("Manual training example was not saved: %s", exc)
        return output_path

    @_locked_project_file("working/manual_shape_drafts.json")
    def _write_manual_shape_draft(
        self,
        payload: dict,
        *,
        existing_shapes=(),
    ) -> Path:
        if self.project is None:
            raise RuntimeError("Няма отворен проект.")
        working_dir = Path(self.project.root) / "working"
        working_dir.mkdir(parents=True, exist_ok=True)
        output_path = working_dir / "manual_shape_drafts.json"
        if output_path.exists():
            data = json.loads(output_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("drafts", []), list):
                raise RuntimeError("manual_shape_drafts.json е с неочаквана структура.")
        else:
            data = {
                "schema": "lion-rebar-manual-shape-drafts/v1",
                "project_name": self.project.name,
                "drafts": [],
                "history": [],
            }
        annotation = dict(payload.get("annotation") or {})
        annotation_handle = str(annotation.get("handle") or "")
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        drafts = [dict(value) for value in data.get("drafts", []) if isinstance(value, dict)]
        history = [dict(value) for value in data.get("history", []) if isinstance(value, dict)]
        retained = []
        preferred_code = None
        for existing in drafts:
            if str((existing.get("annotation") or {}).get("handle") or "") == annotation_handle:
                archived = dict(existing)
                archived["replaced_at"] = timestamp
                history.append(archived)
                preferred_code = str(
                    (existing.get("candidate_pack") or {}).get("suggested_shape_code")
                    or ""
                ) or None
            else:
                retained.append(existing)

        reserved_codes = [
            str((existing.get("candidate_pack") or {}).get("suggested_shape_code") or "")
            for existing in retained
        ]
        candidate_pack = build_project_shape_candidate(
            payload,
            existing_shapes=existing_shapes,
            reserved_codes=reserved_codes,
            preferred_code=preferred_code,
            project_name=self.project.name,
            created_at=timestamp,
        )
        candidate_path = write_project_shape_candidate(working_dir, candidate_pack)
        candidate = dict(candidate_pack.get("candidate") or {})
        candidate_import = dict(candidate.get("engine_studio_import") or {})
        candidate_metadata = {
            "schema": candidate_pack.get("schema"),
            "candidate_id": candidate.get("candidate_id"),
            "relative_path": candidate_path.relative_to(
                Path(self.project.root)
            ).as_posix(),
            "suggested_shape_code": candidate.get("suggested_shape_code"),
            "engine_studio_import_ready": bool(candidate_import.get("ready")),
            "import_blocker_codes": list(candidate_import.get("blocker_codes") or []),
        }
        payload["candidate_pack"] = candidate_metadata
        draft = dict(payload)
        draft["status"] = "DRAFT_REQUIRES_MASTER_APPROVAL"
        draft["created_at"] = timestamp
        # Черновата е направена върху ЕДНА редакция на чертежа. Без печат на
        # самоличността операторът не различава чия е тя, ако чертежът е внесен
        # наново. Нищо в анализа не чете черновата, затова печатът е за
        # проследимост, а не порта.
        stamp_payload(draft, current_identity(self.project))
        retained.append(draft)
        data["updated_at"] = timestamp
        data["drafts"] = retained
        data["history"] = history
        atomic_write_text(
            output_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        if payload.get("save_training_example"):
            try:
                self._append_manual_training_example(payload, status="MISSING_MASTER_DRAFT")
            except Exception as exc:
                logger = getattr(self, "logger", None)
                if logger is not None:
                    logger.warning("Manual draft training example was not saved: %s", exc)
        return output_path

    @_locked_project_file("working/manual_shape_training_examples.json")
    def _append_manual_training_example(self, payload: dict, *, status: str) -> Path:
        if self.project is None:
            raise RuntimeError("Няма отворен проект.")
        output_path = Path(self.project.root) / "working" / "manual_shape_training_examples.json"
        if output_path.exists():
            data = json.loads(output_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("examples", []), list):
                raise RuntimeError("manual_shape_training_examples.json е с неочаквана структура.")
        else:
            data = {
                "schema": "lion-rebar-manual-shape-training/v1",
                "project_name": self.project.name,
                "examples": [],
            }
        example = dict(payload)
        example["status"] = status
        example["recorded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        annotation_handle = str((payload.get("annotation") or {}).get("handle") or "")
        examples = [
            dict(value)
            for value in data.get("examples", [])
            if isinstance(value, dict)
            and str((value.get("annotation") or {}).get("handle") or "") != annotation_handle
        ]
        stamp_payload(example, current_identity(self.project))
        examples.append(example)
        data["updated_at"] = example["recorded_at"]
        data["examples"] = examples
        atomic_write_text(
            output_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        return output_path

    def _close_review_center(self) -> None:
        if self._manual_selection_row is not None:
            self._leave_manual_geometry_selection(restore_review=False, clear_selection=False)
        dock = self._review_dock
        dialog = self._review_center
        self._review_dock = None
        self._review_center = None
        if dock is not None:
            try:
                self._save_review_dock_state(dock)
                dock.close()
                dock.deleteLater()
            except RuntimeError:
                pass
        elif dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass

    def _review_dock_visibility_changed(self, dock: QDockWidget, visible: bool) -> None:
        if not visible:
            self._save_review_dock_state(dock)

    def _review_dock_destroyed(
        self,
        dock: QDockWidget,
        dialog: ReviewCenterDialog,
    ) -> None:
        if self._review_dock is dock:
            self._review_dock = None
        self._review_center_closed(dialog)

    def _save_review_dock_state(self, dock: QDockWidget) -> None:
        try:
            self._ui_settings.setValue("review_center/width", max(360, dock.width()))
            self._ui_settings.setValue("review_center/floating", dock.isFloating())
            self._ui_settings.setValue("review_center/geometry", dock.saveGeometry())
            self._ui_settings.sync()
        except RuntimeError:
            pass

    def _restore_review_dock_state(self, dock: QDockWidget) -> None:
        width = int(self._ui_settings.value("review_center/width", 460) or 460)
        floating = str(self._ui_settings.value("review_center/floating", "false")).lower() in {
            "1", "true", "yes"
        }
        width = max(360, min(width, 480, max(400, int(self.width() * 0.48))))
        if floating:
            dock.setFloating(True)
            geometry = self._ui_settings.value("review_center/geometry")
            if geometry is not None:
                dock.restoreGeometry(geometry)
            screen = dock.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                frame = dock.frameGeometry()
                x = max(available.left(), min(frame.left(), available.right() - min(frame.width(), available.width()) + 1))
                y = max(available.top(), min(frame.top(), available.bottom() - min(frame.height(), available.height()) + 1))
                dock.move(x, y)
        else:
            self.resizeDocks([dock], [width], Qt.Orientation.Horizontal)

    def _review_center_closed(self, dialog: ReviewCenterDialog) -> None:
        # Ignore a delayed destroyed signal from an older dock after a new
        # Review Center has already been opened.  A programmatic close sets the
        # current reference to None before the dock's deferred deletion.
        if self._review_center is not None and self._review_center is not dialog:
            return
        if self._review_center_cleanup_active:
            return
        self._review_center_cleanup_active = True
        try:
            if self._review_center is dialog:
                # The embedded dialog may already be a deleted QObject.  Drop
                # the reference before leaving manual mode so cleanup never
                # calls back into a destroyed Review Center.
                self._review_center = None
            self._leave_manual_geometry_selection(
                restore_review=False,
                clear_selection=False,
            )
            self._clear_geometry_highlights()
        finally:
            self._review_center_cleanup_active = False
        # Keep one current reproducible feedback package, but never compress
        # the DXF and reports in the GUI thread.
        self._schedule_ai_diagnostic_bundle()

    def open_geometry_candidates(self) -> None:
        if self.project is None or self.import_result is None or self.scene_result is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        selected_items = self.scene_result.scene.selectedItems()
        if not selected_items:
            self._info("Първо избери ARM_N надпис от чертежа или от ARM_N списъка.")
            return
        handle = str(selected_items[0].data(0) or "")
        record = self.scene_result.handle_to_record.get(handle)
        if record is None:
            self._info("Избраният графичен обект няма DXF запис.")
            return
        if record.entity_type.upper() not in {"BLOCK_ATTR_TEXT", "TEXT", "MTEXT"} or not looks_like_rebar_text(record.text or ""):
            self._info(
                "Избери валиден армировъчен надпис: BLOCK ATTRIB, TEXT или MTEXT с брой, диаметър и дължина."
            )
            return

        try:
            engine = self.engine_repository.load_current()
            analysis = find_geometry_candidates(
                record,
                self.import_result.entities,
                shape_definitions=engine.shapes,
                control_tests=engine.tests,
                policy=engine.settings,
            )
        except EngineRepositoryError as exc:
            self._error(f"Не може да се зареди MASTER engine.\n\n{exc}")
            return
        if not analysis.has_candidates:
            details = " ".join(analysis.warnings) or analysis.status
            self._info(f"Не са намерени геометрични кандидати.\n\n{details}")
            return

        approved_link = self._read_approved_geometry_link(analysis.annotation_handle)

        self._close_geometry_dialog()
        self._clear_geometry_highlights()
        dialog = GeometryCandidatesDialog(
            analysis,
            self,
            approved_link=approved_link,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.candidatePreviewRequested.connect(self._preview_geometry_candidate)
        dialog.candidateApprovalRequested.connect(
            lambda candidate, current_analysis=analysis, current_dialog=dialog: self._approve_geometry_candidate(
                current_analysis, candidate, current_dialog
            )
        )
        dialog.finished.connect(
            lambda _result, current_dialog=dialog: self._geometry_dialog_closed(current_dialog)
        )
        self._geometry_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _preview_geometry_candidate(
        self,
        candidate: GeometryCandidate,
        *,
        overlay_shape_code: object = None,
        overlay_segments: object = None,
        overlay_total_length_cm: object = None,
        overlay_source_label: str | None = None,
    ) -> None:
        if self.scene_result is None:
            return
        self._clear_geometry_highlights()

        centreline_items = []
        for handle in candidate.handles:
            record = self.scene_result.handle_to_record.get(str(handle))
            item = self.scene_result.handle_to_item.get(str(handle))
            if record is None or item is None:
                continue
            scene_path = centerline_path_for_record(record)
            if scene_path is None or scene_path.isEmpty():
                continue
            centreline_items.append((item, scene_path))
        if not centreline_items:
            self._info(
                "Кандидатът няма LINE/POLYLINE/ARC производствена ос в текущата сцена."
            )
            return

        self.scene_result.scene.clearSelection()
        bounding_rect = None
        highlight_pen = QPen(QColor("#e00000"), 5.0)
        highlight_pen.setCosmetic(True)

        for item, scene_path in centreline_items:
            item.setVisible(True)
            item.setSelected(True)
            rect = scene_path.boundingRect()
            bounding_rect = rect if bounding_rect is None else bounding_rect.united(rect)

            try:
                overlay = QGraphicsPathItem(scene_path)
                overlay.setPen(highlight_pen)
                overlay.setZValue(1_000_000.0)
                overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.scene_result.scene.addItem(overlay)
                self._geometry_highlights.append(overlay)
            except Exception as exc:
                self.logger.warning("Cannot build exact candidate highlight: %s", exc)

        if bounding_rect is not None:
            margin = max(5.0, max(bounding_rect.width(), bounding_rect.height()) * 0.02)
            frame = QGraphicsRectItem(bounding_rect.adjusted(-margin, -margin, margin, margin))
            frame_pen = QPen(QColor("#e00000"), 2.0)
            frame_pen.setStyle(Qt.PenStyle.DashLine)
            frame_pen.setCosmetic(True)
            frame.setPen(frame_pen)
            frame.setZValue(999_999.0)
            frame.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene_result.scene.addItem(frame)
            self._geometry_highlights.append(frame)
            self._add_shape_dimension_overlay(
                shape_code=overlay_shape_code or candidate.master_shape_code,
                segment_lengths_cm=(
                    overlay_segments
                    if overlay_segments
                    else candidate.canonical_segment_lengths_cm
                ),
                total_length_cm=(
                    overlay_total_length_cm
                    if overlay_total_length_cm is not None
                    else candidate.declared_length_cm
                ),
                bounding_rect=bounding_rect,
                color=QColor("#b91c1c"),
                storage=self._geometry_highlights,
                z_value=1_000_001.0,
                source_label=overlay_source_label,
            )
            view_margin = margin * 4.0
            self.drawing_view.fitInView(
                bounding_rect.adjusted(-view_margin, -view_margin, view_margin, view_margin),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            view_point = self.drawing_view.mapFromScene(bounding_rect.center())
            global_point = self.drawing_view.viewport().mapToGlobal(view_point)
            if self._geometry_dialog is not None:
                self._geometry_dialog.place_away_from(global_point)
            if self._review_center is not None:
                self._review_center.place_away_from(global_point)

        self.statusBar().showMessage(
            f"Показана геометрия: {', '.join(candidate.handles)}. "
            "Червеното маркиране служи за визуална проверка.",
            9000,
        )

    def _approve_geometry_candidate(
        self,
        analysis: GeometryCandidateAnalysis,
        candidate: GeometryCandidate,
        dialog: GeometryCandidatesDialog,
    ) -> None:
        if self.project is None:
            dialog.mark_error("Няма отворен проект.")
            return

        notes = "\n".join(f"• {note}" for note in candidate.notes) or "• Няма допълнителни предупреждения."
        answer = QMessageBox.question(
            self,
            "Потвърди ръчното одобрение",
            f"Надпис: {analysis.label or '—'}\n"
            f"DXF геометрия: {', '.join(candidate.handles)}\n"
            f"Слой: {candidate.layer}\n"
            f"Геом. L: {candidate.geometry_length_raw:g} | Зададено L: {candidate.declared_length_cm:g} "
            f"| Δ: {candidate.numeric_difference:g}\n\n"
            f"Контрол:\n{notes}\n\n"
            "Ще се запише само ръчно одобрената връзка Надпис ↔ DXF геометрия. "
            "Позиция, форма, зона и стъпка няма да бъдат създадени автоматично.\n\n"
            "Одобряваш ли тази връзка?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        try:
            confirmed = int(answer) == int(QMessageBox.StandardButton.Yes)
        except (TypeError, ValueError):
            confirmed = answer == QMessageBox.StandardButton.Yes
        if not confirmed:
            return

        try:
            saved_path = self._write_approved_geometry_link(analysis, candidate)
            saved_link = self._read_approved_geometry_link(analysis.annotation_handle)
            expected_handles = tuple(sorted(str(handle) for handle in candidate.handles))
            saved_handles = tuple(
                sorted(str(handle) for handle in (saved_link or {}).get("geometry_handles", []))
            )
            if saved_handles != expected_handles:
                raise RuntimeError(
                    "Файлът е записан, но контролната проверка не намира същата одобрена геометрия."
                )
        except Exception as exc:
            self.logger.exception("Failed to save approved geometry link")
            dialog.mark_error(str(exc))
            self._error(f"Връзката не беше записана.\n\n{exc}")
            return

        dialog.mark_approved(str(saved_path))
        if self._review_center is not None:
            self._review_center.mark_approved(analysis.annotation_handle)
        self._record_review_event(
            "APPROVED",
            {
                "annotation_handle": analysis.annotation_handle,
                "annotation_text": analysis.label,
                "geometry_handles": list(candidate.handles),
                "master_shape_code": candidate.master_shape_code,
                "master_shape_mirrored": candidate.master_shape_mirrored,
                "review_reason_codes": list(candidate.review_reason_codes),
                "geometry_length_raw": candidate.geometry_length_raw,
                "declared_length_cm": candidate.declared_length_cm,
            },
        )
        self.statusBar().showMessage(
            f"Ръчно одобрената връзка е записана: {saved_path}",
            10000,
        )

    def _approved_geometry_links_path(self) -> Path | None:
        if self.project is None:
            return None
        return Path(self.project.root) / "working" / "approved_geometry_links.json"

    def _read_approved_geometry_link(self, annotation_handle: str) -> dict | None:
        output_path = self._approved_geometry_links_path()
        if output_path is None or not output_path.exists():
            return None

        try:
            data = json.loads(output_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning(
                "Cannot read approved geometry links from %s: %s",
                output_path,
                exc,
            )
            return None

        links = data.get("links", []) if isinstance(data, dict) else []
        if not isinstance(links, list):
            return None

        wanted_handle = str(annotation_handle)
        for link in reversed(links):
            if not isinstance(link, dict):
                continue
            if str(link.get("annotation_handle", "")) != wanted_handle:
                continue
            result = dict(link)
            result["saved_path"] = str(output_path)
            return result
        return None

    @_locked_project_file("working/approved_geometry_links.json")
    def _write_approved_geometry_link(
        self,
        analysis: GeometryCandidateAnalysis,
        candidate: GeometryCandidate,
    ) -> Path:
        if self.project is None:
            raise RuntimeError("Няма отворен проект.")

        working_dir = Path(self.project.root) / "working"
        working_dir.mkdir(parents=True, exist_ok=True)
        output_path = working_dir / "approved_geometry_links.json"

        if output_path.exists():
            try:
                data = json.loads(output_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Съществуващият файл {output_path.name} не може да бъде прочетен. "
                    "Не е презаписан, за да не се загубят данни."
                ) from exc
            if not isinstance(data, dict) or not isinstance(data.get("links", []), list):
                raise RuntimeError(
                    f"Съществуващият файл {output_path.name} е с неочаквана структура. "
                    "Не е презаписан."
                )
        else:
            data = {
                "schema": "lion-rebar-approved-geometry-links/v1",
                "project_name": self.project.name,
                "links": [],
                "history": [],
            }

        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        links = list(data.get("links", []))
        history = list(data.get("history", []))
        retained_links = []
        for existing in links:
            if (
                isinstance(existing, dict)
                and str(existing.get("annotation_handle", "")) == str(analysis.annotation_handle)
            ):
                archived = dict(existing)
                archived["replaced_at"] = timestamp
                history.append(archived)
            else:
                retained_links.append(existing)

        link = {
            "annotation_handle": analysis.annotation_handle,
            "annotation_text": analysis.label,
            "resolved_annotation_text": analysis.label,
            "annotation_count": parse_rebar_text(analysis.label).count,
            "diameter_mm": analysis.diameter_mm,
            "declared_length_cm": analysis.declared_length_cm,
            "production_length_cm": candidate.effective_geometry_length_cm,
            "production_length_source": "USER_CONFIRMED_CANDIDATE",
            "production_segment_lengths_cm": list(candidate.canonical_segment_lengths_cm),
            "geometry_handles": list(candidate.handles),
            "geometry_layer": candidate.layer,
            "segment_count": candidate.segment_count,
            "geometry_length_raw": candidate.geometry_length_raw,
            "numeric_difference": candidate.numeric_difference,
            "distance_raw": candidate.distance_raw,
            "diameter_layer_match": candidate.diameter_layer_match,
            "candidate_confidence_before_approval": candidate.confidence,
            "candidate_basis": candidate.basis,
            "candidate_notes": list(candidate.notes),
            "master_shape_code": candidate.master_shape_code,
            "master_shape_name": candidate.master_shape_name,
            "canonical_segment_lengths_cm": list(candidate.canonical_segment_lengths_cm),
            "canonical_turn_angles_deg": list(candidate.canonical_turn_angles_deg),
            "approval_status": "ОДОБРЕНО РЪЧНО",
            "approved_at": timestamp,
            "approved_by": "USER",
            "automatic_outputs": {
                "position": False,
                "shape": False,
                "zone": False,
                "spacing": False,
                "specification_row": False,
            },
        }
        retained_links.append(link)
        data["schema"] = "lion-rebar-approved-geometry-links/v1"
        data["project_name"] = self.project.name
        data["updated_at"] = timestamp
        data["links"] = retained_links
        data["history"] = history

        atomic_write_text(
            output_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        return output_path

    def _clear_geometry_highlights(self) -> None:
        scene = self.scene_result.scene if self.scene_result is not None else None
        if scene is not None:
            for item in self._geometry_highlights:
                try:
                    scene.removeItem(item)
                except Exception:
                    pass
        self._geometry_highlights.clear()

    def _close_geometry_dialog(self) -> None:
        if self._geometry_dialog is not None:
            dialog = self._geometry_dialog
            self._geometry_dialog = None
            try:
                dialog.close()
            except RuntimeError:
                pass

    def _geometry_dialog_closed(self, dialog: GeometryCandidatesDialog) -> None:
        if self._geometry_dialog is dialog:
            self._geometry_dialog = None
        self._clear_geometry_highlights()

    def open_shape_builder(self) -> None:
        """Open MASTER Studio directly on the visual form constructor."""
        EngineStudio(self.engine_repository, self, start_tab="forms").exec()
        self._invalidate_manual_geometry_guard_cache()
        self._refresh_engine_label()
        self._check_project_engine_pin()

    def open_form_editor(self) -> None:
        """Отваря доставения редактор на форми в браузъра.

        Редакторът е в пакета от 23.09.2026 и вносителят му („Внеси форми от
        редактора“) работи, но дотук нищо не го отваряше: операторът трябваше
        да знае къде на диска стои един HTML файл. Отварянето е само
        показване — нищо не се записва и нито един engine не се пипа.

        Пуска се през браузъра по избор на оператора, не през асоциацията за
        ``.html``: на машината на собственика тя сочи Notepad и редакторът се
        отвори като 207 571 знака суров текст (``utils.open_in_browser``).
        """

        from lion_rebar.engine.draft_import import form_editor_path
        from lion_rebar.utils.open_in_browser import open_html_in_browser

        path = form_editor_path()
        if not path.is_file():
            self._error(f"Липсва редакторът на форми: {path}")
            return
        проблем = open_html_in_browser(path)
        if проблем:
            # Отказът не е авария: пътят се казва, за да може операторът да
            # отвори файла сам.
            self._info(
                f"{проблем}\nОтвори този файл в браузър:\n{path}"
            )
            return
        self.statusBar().showMessage(
            "Редакторът на форми е отворен в браузъра. Свалената .json се "
            "внася от MASTER → Engine Studio → Версии → „Внеси форми от "
            "редактора“.",
            15000,
        )

    def open_engine_studio(self) -> None:
        EngineStudio(self.engine_repository, self).exec()
        self._invalidate_manual_geometry_guard_cache()
        self._refresh_engine_label()
        self._check_project_engine_pin()

    def show_session_summary(self) -> None:
        summary = self.session_service.active_summary()
        if summary is None:
            self._info(
                "Няма активна сесия. Следващото действие ще започне нова автоматично."
            )
            return
        QMessageBox.information(
            self,
            "LION REBAR — текуща сесия",
            f"Сесия: {summary.session_id}\n"
            f"Начало: {summary.started_at}\n"
            f"Проекти: {summary.project_count}\n"
            f"Събития: {summary.event_count}\n"
            f"Инженерни решения: {summary.decision_count}\n"
            f"Последен запис: {summary.last_event_at or '—'}\n\n"
            "Всички решения се записват едновременно в проекта и в глобалната "
            "Session Database.",
        )

    def show_layer_classification(self) -> None:
        if self.project is None:
            self._info("Първо отвори проект с импортиран DXF.")
            return
        path = Path(self.project.root) / "working" / "layer_classification.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            self._error(f"Класификацията на слоевете не може да се отвори.\n\n{exc}")
            return
        summary = dict(payload.get("summary") or {})
        rows = [row for row in payload.get("layers", []) if isinstance(row, dict)]
        hidden = [
            str(row.get("layer") or "")
            for row in rows
            if not bool(row.get("default_visible", True))
        ]
        unknown = [
            str(row.get("layer") or "")
            for row in rows
            if str(row.get("role") or "") == "UNKNOWN"
        ]
        QMessageBox.information(
            self,
            "Класификация на DXF слоевете",
            f"Слоеве общо: {summary.get('layer_count', len(rows))}\n"
            f"Релевантни/консервативно запазени: {summary.get('relevant_layer_count', 0)}\n"
            f"100% шум: {summary.get('noise_layer_count', 0)}\n"
            f"Автоматично скрити: {summary.get('auto_hidden_layer_count', 0)}\n"
            f"Непознати — оставени видими: {summary.get('unknown_layer_count', 0)}\n\n"
            f"Скрити шумови слоеве: {', '.join(hidden) if hidden else 'няма'}\n"
            f"UNKNOWN: {', '.join(unknown[:20]) if unknown else 'няма'}"
            + (" ..." if len(unknown) > 20 else "")
            + f"\n\nПълен отчет: {path}",
        )

    def end_work_session(self) -> None:
        if self._batch_thread is not None and self._batch_thread.isRunning():
            self._info("Изчакай пакетният анализ да приключи.")
            return
        if self._export_thread is not None and self._export_thread.isRunning():
            self._info("Изчакай Excel + DXF export-а да приключи.")
            return
        summary = self.session_service.active_summary()
        if summary is None:
            self._info("Няма активна сесия за приключване.")
            return
        answer = QMessageBox.question(
            self,
            "Край на работната сесия",
            f"Сесия: {summary.session_id}\n"
            f"Проекти: {summary.project_count}\n"
            f"Събития: {summary.event_count}\n"
            f"Инженерни решения: {summary.decision_count}\n\n"
            "Ще бъдат създадени Decision Journal, Session Database extract, "
            "Layer Classification, Rule Candidates и възпроизводим AI пакет за "
            "всеки проект. Engine и Matcher няма да бъдат променяни.\n\n"
            "Да приключа ли сесията и да създам Learning Pack?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.project is not None:
            try:
                self.save_project()
            except Exception:
                pass
        # END SESSION must not depend on project/cloud diagnostic output.  The
        # Learning Pack builds its own project evidence in a local temp folder;
        # cloud publication happens only after the verified local archive exists.
        cloud_review = self._detect_cloud_review_folder()
        publish_dir = (
            cloud_review.parent / "06_LEARNING_PACKS"
            if cloud_review is not None
            else None
        )
        QApplicationOverrideCursor.set_wait()
        try:
            self.session_service.record_system_event(
                "END_SESSION_REQUESTED",
                {
                    "project_count": summary.project_count,
                    "event_count": summary.event_count,
                    "decision_count": summary.decision_count,
                },
            )
            result = self.session_service.end_current_session(publish_dir=publish_dir)
        except Exception as exc:
            self.logger.exception("Learning Pack creation failed")
            self._error(
                "Сесията не е приключена, защото Learning Pack не можа да бъде "
                "създаден и проверен нито в основната, нито в аварийната "
                f"локална папка. Данните в Session Database са запазени.\n\n{exc}"
            )
            return
        finally:
            QApplicationOverrideCursor.restore()
        self._refresh_session_label()
        message = (
            f"Проекти: {result.project_count}\n"
            f"Събития: {result.event_count}\n"
            f"Решения в журнала: {result.decision_count}\n"
            f"Активни етикетирани позиции: {result.active_decision_count}\n"
            f"Калибрационни групи: {result.calibration_group_count}\n"
            f"Кандидат-правила: {result.candidate_count}\n"
            "Автоматично активирани правила: 0\n"
            f"Независими чертожни фамилии: {result.independent_drawing_family_count}\n"
            f"Предупреждения: {result.warning_count}\n\n"
            f"Локален проверен Learning Pack: {result.path}\n"
            f"Локален стабилен LATEST: {result.latest_path}\n"
        )
        if result.local_storage_warning:
            message += (
                "\nСесията е приключена успешно чрез аварийно локално съхранение.\n"
                f"{result.local_storage_warning}\n"
            )
        if result.published_path is not None:
            message += (
                f"\nПубликувано копие: {result.published_path}\n"
                f"Публикуван LATEST: {result.published_latest_path}\n"
            )
        if result.publication_warning or result.local_storage_warning:
            if result.publication_warning:
                message += (
                    "\nСесията е приключена успешно. Публикуването в облачната "
                    "папка не успя, но локалният ZIP е валиден и запазен.\n\n"
                    f"{result.publication_warning}"
                )
            QMessageBox.warning(
                self,
                "Сесията е приключена — има предупреждение за съхранението",
                message,
            )
        else:
            message += (
                "\nУтрешната сесия ще започне автоматично с първото действие."
            )
            QMessageBox.information(
                self,
                "Сесията е приключена",
                message,
            )

    def _refresh_session_label(self) -> None:
        try:
            summary = self.session_service.active_summary()
        except Exception as exc:
            self.session_label.setText("Session DB ERROR")
            self.logger.warning("Session summary failed: %s", exc)
            return
        if summary is None:
            self.session_label.setText("Сесия: приключена")
            return
        short_id = summary.session_id.split("-")[-1]
        self.session_label.setText(
            f"Сесия: {short_id} | проекти {summary.project_count} | решения {summary.decision_count}"
        )

    def _set_project(self, project: ProjectModel) -> None:
        self._close_review_center()
        self._close_geometry_dialog()
        self._clear_geometry_highlights()
        self._invalidate_manual_geometry_guard_cache()
        # Project and drawing state switch as one unit.  A newly created or
        # opened project without a source DXF must never inherit the previous
        # project's scene, selectable handles or entity details.
        try:
            self.drawing_view.setScene(None)
        except RuntimeError:
            pass
        self.import_result = None
        self.scene_result = None
        self._loaded_source_generation_id = None
        self.layer_panel.set_layers([])
        self.entity_panel.clear()
        self._last_batch_report_path = None
        self._review_quick_mode_confirmed = False
        self.project = project
        try:
            self._flush_project_decision_outbox()
        except Exception as exc:
            self.logger.warning("Pending decision outbox will retry later: %s", exc)
        self.project_label.setText(f"Проект: {project.name}")
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} — {project.name}")
        self.logger.info("Opened project %s", project.root)
        self._refresh_session_label()

    def _refresh_engine_label(self) -> None:
        try:
            engine = self.engine_repository.load_current()
            label = f"Engine: {engine.engine_id} v{engine.version}"
            if not engine.production_ready:
                label += " [НЕ Е PRODUCTION]"
            self.engine_label.setText(label)
        except EngineRepositoryError as exc:
            self.engine_label.setText("Engine ERROR")
            self.logger.error("Engine load failed: %s", exc)

    def _check_project_engine_pin(self) -> None:
        if self.project is None or not self.project.engine_version:
            return
        current = self.engine_repository.load_current()
        differences = describe_engine_pin_mismatch(
            project_engine_id=self.project.engine_id,
            project_version=self.project.engine_version,
            project_sha256=self.project.engine_sha256,
            current_engine_id=current.engine_id,
            current_version=current.version,
            current_sha256=current.content_sha256,
        )
        if not differences:
            return

        # Добавена форма стига до ОТВОРЕНИЯ проект.
        #
        # Собственик, 22.09.2026: „искам, когато направя нова форма, да може да
        # се разпознава или поне избира в този чертеж, а на следващия вече да
        # си я има одобрена“.
        #
        # Пренася се само когато новият engine НЕ променя нищо, а единствено
        # добавя — условието е проверимо форма по форма
        # (``engine.additive_upgrade``). Тогава нито едно старо одобрение не
        # може да смени смисъла си и закотвянето не пази нищо. Всеки друг
        # случай минава по стария път: диалогът отдолу и ръчно решение.
        добавени = self.project_manager.migrate_additive_engine(
            self.project, current, by="Отваряне на проект"
        )
        if добавени:
            self._refresh_engine_label()
            self._session_project_event(
                "ADDITIVE_ENGINE_MIGRATION",
                {"to_version": current.version, "added_shapes": list(добавени)},
            )
            QMessageBox.information(
                self,
                "Проектът получи новите форми",
                "Engine %s носи %d нови форми спрямо тази на проекта, а нито една\n"
                "съществуваща не е променена: %s.\n\n"
                "Проектът е пренесен към нея. Нищо не се връща за преглед —\n"
                "старите решения са взети върху същите форми.\n\n"
                "Пусни пакетен анализ, за да се разпознаят новите."
                % (current.version, len(добавени), ", ".join(добавени)),
            )
            return

        # P0-4: не показвай "друга версия", когато версиите СЪВПАДАТ.
        # Реалното разминаване (id / version / SHA) се изписва точно.
        title = (
            "Проектът е свързан с друга engine версия"
            if any(item["field"] in ("engine_id", "version") for item in differences)
            else "Проектът е свързан със същата engine версия, но различен engine подпис"
        )
        lines = [
            f"{item['label']}:\n    проект: {item['project']}\n    активна програма: {item['current']}"
            for item in differences
        ]
        QMessageBox.information(
            self,
            title,
            "\n\n".join(lines)
            + "\n\nПроектът не се мигрира автоматично. Това предпазва старите резултати. "
            "Миграция ще се добави като отделно контролирано действие.",
        )

    def _show_cursor_coordinates(self, x: float, y: float) -> None:
        self.cursor_label.setText(f"X: {x:.3f} | Y: {y:.3f}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._export_thread is not None and self._export_thread.isRunning():
            QMessageBox.information(
                self,
                "Excel + DXF export още работи",
                "Изчакай progress прозорецът да достигне 100%. Затварянето по време на "
                "запис може да остави временни файлове.",
            )
            event.ignore()
            return
        if self._batch_thread is not None and self._batch_thread.isRunning():
            answer = QMessageBox.question(
                self,
                "Пакетният анализ още работи",
                "Да заявя ли контролирано прекратяване? Междинният отчет ще бъде запазен. "
                "Затвори програмата след съобщението за приключване.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._request_batch_cancel()
            event.ignore()
            return
        self._close_review_center()
        self._close_geometry_dialog()
        self._clear_geometry_highlights()
        if self.project:
            try:
                self.save_project()
            except Exception:
                pass
        if self.session_service.has_active_session:
            try:
                self.session_service.record_system_event(
                    "APP_CLOSED",
                    {
                        "project_name": self.project.name if self.project else None,
                        "session_left_active_for_resume": True,
                    },
                )
                self.session_service.checkpoint()
            except Exception as exc:
                self.logger.warning("Session close checkpoint failed: %s", exc)
        event.accept()

    def _info(self, message: str) -> None:
        QMessageBox.information(self, APP_NAME, message)

    def _error(self, message: str) -> None:
        QMessageBox.critical(self, f"{APP_NAME} — грешка", message)


class QApplicationOverrideCursor:
    """Small guard to avoid importing QApplication in multiple call sites."""

    @staticmethod
    def set_wait() -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)

    @staticmethod
    def restore() -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.restoreOverrideCursor()
