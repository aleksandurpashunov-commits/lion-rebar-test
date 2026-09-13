from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QFrame,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QWidget,
    QVBoxLayout,
)

from lion_rebar.config.constants import APP_VERSION
from lion_rebar.project.data_identity import IDENTITY_KEY, DataIdentity
from lion_rebar.project.non_production import (
    is_valid_non_production_decision,
)
from lion_rebar.review_center_model import (
    APPROVED_STATUS,
    DIAGNOSTIC_REVIEW_DECISIONS,
    F006_STRONG_STATUS,
    GEOMETRY_STRONG_STATUS,
    ReviewReport,
    build_control_sample,
    build_stratified_sample,
    bulk_eligible,
    control_sample_progress,
    f006_bulk_eligible,
    final_confirmation_eligible,
    final_confirmation_progress,
    geometry_bulk_eligible,
    is_missing_master_row,
    is_review_row_approved,
    is_review_row_resolved,
    load_review_report,
    missing_master_reason,
    report_fingerprint,
    review_decision_code,
    review_queue_rows,
)
from lion_rebar.ui.review_problem_dialog import ReviewProblemDialog

LOGGER = logging.getLogger("lion_rebar.review_center")
SEARCH_DEBOUNCE_MS = 180
REVIEW_COLUMN_WIDTHS: tuple[int, ...] = (
    118,
    108,
    118,
    260,
    58,
    48,
    68,
    82,
    58,
    72,
    72,
    110,
    78,
)

QUEUE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("WORK", "За ръчен преглед — всички нерешени"),
    ("GROUPS", "Групи за преглед — по един представител"),
    ("PROBLEMS", "Проблемни — изисква решение"),
    ("STRONG_REVIEW", "Силни — за бърз преглед"),
    ("SAMPLE", "Контролна извадка"),
    ("GEOMETRY_SAMPLE", "Контрол 10 — силни по геометрия"),
    ("F006_SAMPLE", "Контрол 5 — стремена F006"),
    ("REJECTED", "Решени проблеми / без верен кандидат"),
    ("APPROVED", "Одобрени в отчета"),
    ("ALL", "Всички реални позиции"),
)

#: Свиване от независимия одит (Фаза 2, 09.09.2026): 18 опашки в едно падащо
#: меню бяха твърде много за бърз ежедневен преглед, а по-голямата част бяха
#: просто подмножества на "За ръчен преглед" по причина. Бутоните за групово/
#: масово одобрение НЕ зависят от избраната опашка (проверено — работят
#: директно върху целия отчет), затова свиването е чисто визуално и не
#: променя нито едно производствено поведение. "PROBLEMS" обединява старите
#: AI_EXCEPTIONS/MISSING_MASTER/CONFLICT/MANUAL/RISK/NONE; "STRONG_REVIEW"
#: обединява AI_CONTROL/GEOMETRY_STRONG/STRONG/F006_STRONG — виж
#: ``review_center_model.review_queue_rows``, където старите кодове остават
#: изцяло функционални за обратна съвместимост на тестове.


def _shape_summary_identity(row: dict) -> tuple[str, str]:
    """Return an honest label/value pair for the selected-row summary."""

    ambiguous = [
        str(value).strip()
        for value in row.get("master_ambiguous_shape_codes", [])
        if str(value).strip()
    ]
    if ambiguous:
        return "MASTER", " / ".join(ambiguous)
    master = str(row.get("master_shape_code") or "").strip()
    if master:
        return "MASTER", master
    suggestion = str(row.get("suggested_shape_code") or "").strip()
    if suggestion:
        return "ПРЕДЛОЖЕНИЕ", suggestion
    return "MASTER", "—"


_OWNED_DIMENSION_PRODUCTION_SOURCES = frozenset(
    {
        "F006_BORDERLINE_CURVE_OWNED_DIMENSIONS",
        "F006_OWNED_CONTOUR_DIMENSIONS",
        "F006_ROUNDED_LOCAL_PRINTED",
        "F006_PRINTED_MASTER_EXACT",
        "STRICT_LOCAL_DIMENSIONS",
    }
)
_AUTOMATIC_DXF_PRODUCTION_SOURCES = frozenset(
    {
        "F007_CONTEXTUAL_MASTER_CANONICAL",
        "MATCHER_LOCAL_A_E_CANONICAL",
        "SINGLE_OPEN_LWPOLYLINE_TOPOLOGY",
    }
)


def _production_segment_source_label(row: dict) -> str:
    """Return an operator-facing provenance label for the segment vector.

    ``production_segments_cm`` means that the vector is safe for production;
    it does *not* mean that an operator entered it.  Automatic sources are
    therefore named explicitly.  Unknown evidence remains on the conservative
    legacy label instead of being guessed from a source-name substring.
    """

    if row.get("production_segments_cm"):
        evidence = row.get("production_segment_evidence")
        source = (
            str(evidence.get("source") or "").strip().upper()
            if isinstance(evidence, dict)
            else ""
        )
        if source in _OWNED_DIMENSION_PRODUCTION_SOURCES:
            return "АВТОМАТИЧНО ИЗВЕДЕНО ОТ СОБСТВЕНИ КОТИ"
        if source in _AUTOMATIC_DXF_PRODUCTION_SOURCES:
            return "АВТОМАТИЧНО ИЗВЕДЕНО ОТ DXF/MASTER ДОКАЗАТЕЛСТВА"
        return "операторско/производствено решение"
    if row.get("canonical_segment_lengths_cm"):
        return "DXF/надпис"
    if row.get("suggested_segments_cm"):
        return "предложение — изисква проверка"
    return "липсва"


def _dimension_evidence_html(row) -> str:
    """Показва производствените размери и откъде идват.

    Без това операторът вижда „Форма: — —“ и няма как да довърши
    инженерното решение: размерите на стремето съществуват в чертежа като
    котировки, но никъде не се изписват на екрана.
    """

    parts: list[str] = []
    segments = row.get("production_segments_cm") or []
    if segments:
        if len(segments) == 6:
            hook, height, width = segments[0], segments[1], segments[2]
            parts.append(
                f"<b>Размери на стремето:</b> {width:g} x {height:g} cm, "
                f"кука {hook:g} cm"
            )
        elif len(segments) == 3:
            width, height, hook = segments[0], segments[1], segments[2]
            parts.append(
                f"<b>Размери:</b> {width:g} / {height:g} / {hook:g} cm"
            )
        else:
            parts.append(
                "<b>Сегменти:</b> "
                + " + ".join(f"{float(value):g}" for value in segments)
                + " cm"
            )
    evidence = row.get("dimension_derived_evidence")
    if isinstance(evidence, dict):
        source = str(evidence.get("source") or "")
        confidence = str(evidence.get("confidence") or "")
        allowance = evidence.get("bend_allowance_cm")
        detail = f"<b>Източник на размерите:</b> {_escape(source)}"
        if confidence:
            detail += f" ({_escape(confidence)})"
        if allowance:
            detail += f"; добавка за огъване {float(allowance):g} cm"
        parts.append(detail)
    suggested = row.get("suggested_segments_cm") or []
    suggested_code = str(row.get("suggested_shape_code") or "")
    if suggested and not segments:
        code = suggested_code
        if len(suggested) == 3 and code == "F006":
            width, height, hook = suggested
            parts.append(
                f"<b style='color:#0369a1'>ПРЕДЛОЖЕНИЕ ({_escape(code)}):</b> "
                f"стреме {width:g} x {height:g} cm, кука {hook:g} cm "
                "<i>(изисква потвърждение — формата не е разпозната)</i>"
            )
        else:
            parts.append(
                f"<b style='color:#0369a1'>ПРЕДЛОЖЕНИЕ ({_escape(code)}):</b> "
                + " + ".join(f"{float(value):g}" for value in suggested) + " cm "
                "<i>(изисква потвърждение)</i>"
            )
    elif suggested_code and not segments:
        evidence = row.get("suggested_evidence")
        source = (
            str(evidence.get("source") or "")
            if isinstance(evidence, dict)
            else ""
        )
        parts.append(
            f"<b style='color:#0369a1'>РАЗПОЗНАТА ФАМИЛИЯ "
            f"({_escape(suggested_code)}):</b> "
            f"{_escape(source or 'локален инженерен надпис')} — "
            "<i>точните сегменти не се гадаят; потвърди ги в инженерното "
            "решение</i>"
        )

    entry = row.get("legend_entry")
    if isinstance(entry, dict):
        purpose = str(entry.get("purpose") or "")
        line = (
            f"<b>Легенда {_escape(str(entry.get('key') or '').upper())}:</b> "
            f"Ø{entry.get('diameter_mm')} L={entry.get('length_cm')} cm"
        )
        if purpose:
            line += f" — {_escape(purpose)}"
        parts.append(line)
    # Непотвърдено кръгче НЕ се показва: то почти винаги е на съседна
    # позиция и подвежда повече, отколкото помага.
    nearby = row.get("nearby_dimensions_cm") or []
    if nearby:
        parts.append(
            "<b>Котировки наблизо:</b> "
            + ", ".join(f"{float(value):g}" for value in nearby[:10]) + " cm"
        )
    if not parts:
        return (
            "<b>Размери:</b> <span style='color:#991b1b'>няма прочетени "
            "котировки около тази позиция</span><br>"
        )
    return "<br>".join(parts) + "<br>"


class ReviewCenterDialog(QDialog):
    """Modeless queue-based review of one completed batch report."""

    previewRequested = Signal(object)
    referenceGeometryRequested = Signal(object)
    locateAnnotationRequested = Signal(object)
    fullCandidatesRequested = Signal(object)
    approvalRequested = Signal(object)
    bulkApprovalRequested = Signal(object)
    geometryBulkApprovalRequested = Signal(object)
    f006BulkApprovalRequested = Signal(object)
    finalBulkConfirmationRequested = Signal(object)
    engineerDecisionRequested = Signal(object)
    formAssignmentRequested = Signal(object)
    decisionRecorded = Signal(str, object)
    decisionsChanged = Signal()
    diagnosticExportRequested = Signal()
    cancelSelectionRequested = Signal()

    SAMPLE_SIZE = 30
    GEOMETRY_SAMPLE_SIZE = 10
    F006_SAMPLE_SIZE = 5

    def __init__(
        self,
        report_path: Path,
        *,
        state_path: Path,
        approved_links: dict[str, dict] | None = None,
        embedded: bool = False,
        project_root: Path | str | None = None,
        operator_name: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.embedded = bool(embedded)
        if self.embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.report: ReviewReport = load_review_report(report_path)
        self._report_identity: dict[str, str] = {}
        try:
            raw_report = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            raw_report = {}
        if isinstance(raw_report, dict) and isinstance(raw_report.get(IDENTITY_KEY), dict):
            self._report_identity = {
                str(key): str(value or "")
                for key, value in raw_report[IDENTITY_KEY].items()
            }
        # Findings Д-1/Д-2 (независим одит): "Приложи към групата" и
        # "Върни групово решение" никога не са получавали project_root,
        # data_identity или пълния ред-списък — бутоните бяха свързани към
        # работещ, тестван backend, но диалогът нямаше собствена памет за
        # проектната идентичност. Тук се задава веднъж, fail-closed: ако
        # отчетът няма пълна вградена идентичност, груповите действия си
        # остават заключени (същото предупредително съобщение, но защото
        # данните наистина липсват, не защото никой не ги е подал).
        self.project_root = Path(project_root) if project_root else None
        self.operator_name = str(operator_name or "").strip()
        self.data_identity: DataIdentity | None = None
        if self._report_identity.get("project_id") and self._report_identity.get(
            "source_generation_id"
        ):
            self.data_identity = DataIdentity(
                project_id=str(self._report_identity.get("project_id") or ""),
                source_sha256=str(self._report_identity.get("source_sha256") or ""),
                source_generation_id=str(
                    self._report_identity.get("source_generation_id") or ""
                ),
                analysis_run_id=str(
                    self._report_identity.get("analysis_run_id") or ""
                ),
            )
        # Пълният ред-списък на текущия отчет — нужен на "Приложи към
        # групата", за да намери доказано еднаквите сестрински позиции.
        self._rows: list[dict] = [dict(row) for row in self.report.rows]
        self.state_path = Path(state_path)
        self._load_and_apply_state(approved_links)
        self._visible_rows: list[dict] = []
        self._previewed_handle: str | None = None
        self._faded_for_preview = False
        self._queue_cache: dict[str, list[dict]] = {}
        self._geometry_progress_cache = None
        self._f006_progress_cache = None

        self.setWindowTitle(f"LION REBAR — REVIEW CENTER v{APP_VERSION}")
        self.resize(520 if self.embedded else 1350, 760 if self.embedded else 620)
        self.setMinimumSize(360 if self.embedded else 920, 430 if self.embedded else 500)
        self.setSizeGripEnabled(not self.embedded)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-weight: 700; padding: 4px 0;")

        self.control_status_label = QLabel()
        self.control_status_label.setWordWrap(True)
        self.control_status_label.setStyleSheet(
            "font-weight: 700; color:#713f12; background:#fffbeb; "
            "padding:7px; border:1px solid #fde68a;"
        )
        if self.embedded:
            self.control_status_label.hide()

        self.selection_mode_label = QLabel()
        self.selection_mode_label.setWordWrap(True)
        self.selection_mode_label.setStyleSheet(
            "font-weight:700; color:#075985; background:#e0f2fe; "
            "padding:7px; border:1px solid #38bdf8;"
        )
        self.selection_mode_label.hide()

        self.review_reason_label = QLabel("Избери позиция от опашката.")
        self.review_reason_label.setWordWrap(True)
        self.review_reason_label.setStyleSheet(
            "font-weight:700; color:#7c2d12; background:#fff7ed; "
            "padding:7px; border:1px solid #fed7aa;"
        )
        self.position_summary_label = QLabel("Форма / брой / Ø / L / сегменти: —")
        self.position_summary_label.setWordWrap(True)
        self.position_summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.position_summary_label.setStyleSheet(
            "color:#0f172a; background:#f8fafc; padding:7px; "
            "border:1px solid #cbd5e1;"
        )

        self.queue_combo = QComboBox()
        self._populate_queue_combo()
        self.queue_combo.currentIndexChanged.connect(self._queue_changed)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Филтър по надпис, слой, форма или handle...")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_search_filter)
        self.search_edit.textChanged.connect(self._schedule_search_refresh)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Опашка:"))
        controls.addWidget(self.queue_combo, 1)
        controls.addWidget(QLabel("Търсене:"))
        controls.addWidget(self.search_edit, 1)

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels(
            [
                "Решение",
                "Статус",
                "AI",
                "Надпис",
                "Група",
                "Ø",
                "L",
                "MASTER",
                "Сегм.",
                "Δ DXF",
                "Разст.",
                "Слой",
                "Конфликт",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(REVIEW_COLUMN_WIDTHS):
            header.resizeSection(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        if self.embedded:
            for column in range(self.table.columnCount()):
                self.table.setColumnHidden(column, column not in {0, 3, 5, 6, 7})
            header.resizeSection(0, 105)
            header.resizeSection(3, 195)
            header.resizeSection(5, 42)
            header.resizeSection(6, 55)
            header.resizeSection(7, 65)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._preview_current())

        self.details = QTextBrowser()
        # Панелът е нисък при малки прозорци; без изрична минимална
        # височина размерите остават под ръба и операторът вижда празно.
        self.details.setMinimumHeight(120 if self.embedded else 260)
        self.details.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.details.setOpenExternalLinks(False)
        self.details.setMinimumWidth(0 if self.embedded else 390)

        self.diagnostics_toggle = QToolButton()
        self.diagnostics_toggle.setText("Техническа диагностика и history")
        self.diagnostics_toggle.setCheckable(True)
        self.diagnostics_toggle.setChecked(False)
        self.diagnostics_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.diagnostics_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.diagnostics_toggle.toggled.connect(self._toggle_diagnostics)
        self.details.hide()

        splitter = QSplitter(
            Qt.Orientation.Vertical if self.embedded else Qt.Orientation.Horizontal
        )
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 4 if self.embedded else 3)
        splitter.setStretchFactor(1, 2)
        self.content_splitter = splitter

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("font-weight: 600;")

        self.preview_button = QPushButton("1. Покажи")
        self.preview_button.setShortcut("Space")
        self.preview_button.clicked.connect(self._preview_current)

        self.candidates_button = QPushButton("Всички кандидати")
        self.candidates_button.clicked.connect(self._open_all_candidates)

        self.approve_button = QPushButton("Потвърди и следващ")
        self.approve_button.setShortcut("Return")
        self.approve_button.setStyleSheet("font-weight: 700; padding: 7px 12px;")
        self.approve_button.clicked.connect(self._approve_current)

        self.flag_button = QPushButton("Проблем / бележка")
        self.flag_button.setShortcut("R")
        self.flag_button.clicked.connect(self._flag_current)

        self.reject_button = QPushButton("Няма верен кандидат")
        self.reject_button.setShortcut("X")
        self.reject_button.setStyleSheet("font-weight:700; background:#fee2e2;")
        self.reject_button.clicked.connect(self._reject_current)

        self.assign_form_button = QPushButton("Инженерно решение")
        self.assign_form_button.setShortcut("A")
        self.assign_form_button.setStyleSheet("font-weight:800; background:#dbeafe;")
        self.assign_form_button.setToolTip(
            "Задай точна форма, брой, Ø, обща L, сегменти и причина; бележка се иска само при изключение."
        )
        self.assign_form_button.clicked.connect(self._engineer_decision_current)

        self.select_geometry_button = QPushButton("Избери друга геометрия")
        self.select_geometry_button.setStyleSheet("font-weight:700; background:#eef2ff;")
        self.select_geometry_button.setToolTip(
            "Оставя панела отворен и позволява Ctrl+клик върху правилните DXF обекти."
        )
        self.select_geometry_button.clicked.connect(self._assign_form_current)

        self.next_button = QPushButton("Остави за после")
        self.next_button.setShortcut("N")
        self.next_button.setToolTip(
            "Преминава към следващата позиция без одобрение и без промяна на броя нерешени."
        )
        self.next_button.clicked.connect(self._advance)

        self.cancel_edits_button = QPushButton("Откажи избора (Esc)")
        self.cancel_edits_button.setEnabled(False)
        self.cancel_edits_button.setToolTip(
            "Отказва текущия избор на DXF геометрия и запазва позицията нерешена."
        )
        self.cancel_edits_button.clicked.connect(
            lambda _checked=False: self.cancelSelectionRequested.emit()
        )

        self.bulk_button = QPushButton("Приеми останалите валидни силни")
        self.bulk_button.setStyleSheet("font-weight: 700;")
        self.bulk_button.clicked.connect(self._request_bulk_approval)

        self.geometry_bulk_button = QPushButton("Приеми останалите силни по геометрия")
        self.geometry_bulk_button.setStyleSheet("font-weight: 700;")
        self.geometry_bulk_button.clicked.connect(self._request_geometry_bulk_approval)

        self.apply_group_button = QPushButton("Приложи към групата")
        self.apply_group_button.setStyleSheet("font-weight: 700; background:#eff6ff;")
        self.apply_group_button.setToolTip(
            "Прилага решението на текущата позиция върху всички ДОКАЗАНО "
            "еднакви позиции. Показва преглед преди прилагане и може да бъде "
            "върнато.")
        self.apply_group_button.clicked.connect(self._apply_to_group_current)

        self.undo_group_button = QPushButton("Върни групово решение")
        self.undo_group_button.setToolTip(
            "Връща последното групово решение, докато не е направен export.")
        self.undo_group_button.clicked.connect(self._undo_last_group)

        self.apply_similar_button = QPushButton("Приложи към всички подобни")
        self.apply_similar_button.setToolTip(
            "Прилага решението на текущата позиция върху ВСИЧКИ доказано "
            "еднакви позиции в целия отчет (по-широко от „Приложи към "
            "групата“). Показва пълен преглед преди прилагане; действието "
            "е обратимо, докато не е направен export.")
        self.apply_similar_button.clicked.connect(self._apply_to_similar_current)

        self.undo_similar_button = QPushButton("Върни решение за подобни")
        self.undo_similar_button.setToolTip(
            "Връща последното „Приложи към всички подобни“, докато не е "
            "направен export.")
        self.undo_similar_button.clicked.connect(self._undo_last_similar)

        self.f006_bulk_button = QPushButton("Приеми останалите стремена F006")
        self.f006_bulk_button.setStyleSheet("font-weight: 700; background:#faf5ff;")
        self.f006_bulk_button.clicked.connect(self._request_f006_bulk_approval)

        self.final_bulk_button = QPushButton("Потвърди всички останали безопасни")
        self.final_bulk_button.setStyleSheet(
            "font-weight:800; background:#dcfce7; border:1px solid #86efac; padding:7px 12px;"
        )
        self.final_bulk_button.clicked.connect(self._request_final_bulk_confirmation)

        self.undo_bulk_button = QPushButton("Върни последното масово одобрение")
        self.undo_bulk_button.setToolTip(
            "Връща последната от четирите \"Приеми останалите ...\" операции "
            "(силни / по геометрия / F006 / финално потвърждение), докато не "
            "е направен production export.")
        self.undo_bulk_button.clicked.connect(self._undo_last_bulk_approval)

        self.report_button = QPushButton("Отвори HTML отчета")
        self.report_button.clicked.connect(self._open_html_report)

        self.diagnostic_button = QPushButton("Създай пакет за AI проверка")
        self.diagnostic_button.setStyleSheet("font-weight: 700; background:#ecfeff;")
        self.diagnostic_button.clicked.connect(
            lambda _checked=False: self.diagnosticExportRequested.emit()
        )

        close_button = QPushButton("Затвори")
        close_button.clicked.connect(self._request_close)

        daily_action_buttons = (
            self.approve_button,
            self.assign_form_button,
            self.select_geometry_button,
            self.next_button,
            self.preview_button,
            self.cancel_edits_button,
        )
        for button in daily_action_buttons:
            button.setMinimumHeight(32)

        primary_buttons = QGridLayout()
        primary_buttons.setHorizontalSpacing(6)
        primary_buttons.setVerticalSpacing(5)
        if self.embedded:
            primary_buttons.addWidget(self.approve_button, 0, 0)
            primary_buttons.addWidget(self.assign_form_button, 0, 1)
            primary_buttons.addWidget(self.select_geometry_button, 0, 2)
            primary_buttons.addWidget(self.next_button, 1, 0)
            primary_buttons.addWidget(self.preview_button, 1, 1)
            primary_buttons.addWidget(self.cancel_edits_button, 1, 2)
        else:
            primary_buttons.addWidget(self.approve_button, 0, 0)
            primary_buttons.addWidget(self.assign_form_button, 0, 1)
            primary_buttons.addWidget(self.select_geometry_button, 1, 0)
            primary_buttons.addWidget(self.next_button, 1, 1)
            primary_buttons.addWidget(self.preview_button, 2, 0)
            primary_buttons.addWidget(self.cancel_edits_button, 2, 1)

        self.expert_panel = QWidget(self)
        expert_buttons = QGridLayout(self.expert_panel)
        expert_buttons.setContentsMargins(0, 0, 0, 0)
        expert_widgets = (
            self.flag_button,
            self.reject_button,
            self.candidates_button,
            self.bulk_button,
            self.geometry_bulk_button,
            self.apply_group_button,
            self.undo_group_button,
            self.apply_similar_button,
            self.undo_similar_button,
            self.f006_bulk_button,
            self.final_bulk_button,
            self.undo_bulk_button,
            self.report_button,
            self.diagnostic_button,
            close_button,
        )
        columns = 2 if self.embedded else 3
        for index, widget in enumerate(expert_widgets):
            expert_buttons.addWidget(widget, index // columns, index % columns)
        self.expert_panel.hide()
        self.expert_toggle = QToolButton(self)
        self.expert_toggle.setText("Допълнителни действия")
        self.expert_toggle.setCheckable(True)
        self.expert_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.expert_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.expert_toggle.toggled.connect(self._toggle_expert_actions)

        warning = QLabel(
            "Работа: избери позиция → виж причината и данните → Покажи → "
            "Потвърди или отвори Инженерно решение. „Остави за после“ не записва решение. "
            "Филтрите променят само видимите редове."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#7c2d12; background:#fff7ed; padding:8px; border:1px solid #fed7aa;")
        if self.embedded:
            warning.hide()

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4 if self.embedded else 6)
        body_layout.addWidget(self.summary_label)
        body_layout.addWidget(self.control_status_label)
        body_layout.addWidget(warning)
        body_layout.addWidget(self.selection_mode_label)
        body_layout.addLayout(controls)
        body_layout.addWidget(self.review_reason_label)
        body_layout.addWidget(self.position_summary_label)
        body_layout.addWidget(self.diagnostics_toggle)
        body_layout.addWidget(splitter, 1)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)
        layout.addWidget(self.scroll_area, 1)
        # These controls remain visible even at 1280x720 / 150% scaling.
        layout.addWidget(self.result_label)
        layout.addLayout(primary_buttons)
        layout.addWidget(self.expert_toggle)
        layout.addWidget(self.expert_panel)

        self._rebuild_table()

    def _toggle_diagnostics(self, visible: bool) -> None:
        self.details.setVisible(bool(visible))
        self.diagnostics_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def _toggle_expert_actions(self, visible: bool) -> None:
        self.expert_panel.setVisible(bool(visible))
        if self.embedded:
            self.control_status_label.setVisible(bool(visible))
        self.expert_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def _request_close(self) -> None:
        if self.embedded and self.parentWidget() is not None:
            self.parentWidget().close()
            return
        self.close()

    def set_geometry_selection_active(self, active: bool, annotation_text: str = "") -> None:
        self.selection_mode_label.setVisible(bool(active))
        self.cancel_edits_button.setEnabled(bool(active))
        if active:
            self.selection_mode_label.setText(
                f"ИЗБОР НА ГЕОМЕТРИЯ за {annotation_text or 'текущата позиция'}: "
                "маркирай LINE/POLYLINE/ARC с Ctrl+клик. Ctrl+Enter завършва; Esc отказва."
            )
            self.result_label.setText(
                "Панелът остава отворен; избирай директно в чертежа."
            )
        else:
            self.selection_mode_label.clear()

    def _populate_queue_combo(self) -> None:
        for code, label in QUEUE_DEFINITIONS:
            self.queue_combo.addItem(label, code)

    def _queue_changed(self, _index: int = -1) -> None:
        self._search_timer.stop()
        self._rebuild_table(refresh_metadata=False)

    def _schedule_search_refresh(self, _text: str = "") -> None:
        self._search_timer.start()

    def _apply_search_filter(self) -> None:
        self._rebuild_table(refresh_metadata=False)

    def _refresh_queue_counts(self) -> None:
        started = perf_counter()
        selected_code = self._queue_code()
        self.queue_combo.blockSignals(True)
        try:
            labels = dict(QUEUE_DEFINITIONS)
            for index in range(self.queue_combo.count()):
                code = str(self.queue_combo.itemData(index) or "")
                count = len(self._rows_for_queue(code))
                self.queue_combo.setItemText(
                    index,
                    f"{labels.get(code, code)}: {count}",
                )
        finally:
            self.queue_combo.blockSignals(False)
        selected_index = self.queue_combo.findData(selected_code)
        if selected_index >= 0 and selected_index != self.queue_combo.currentIndex():
            self.queue_combo.setCurrentIndex(selected_index)
        self._log_timing("queue_counts", started, rows=len(self.report.rows))

    def _load_and_apply_state(self, approved_links: dict[str, dict] | None) -> None:
        """(Пре)зарежда ``self._state`` и извлечените от него полета.

        Извадено от ``__init__``, за да може да се извика повторно СЛЕД
        undo на масова операция — тогава ``review_center_state.json`` вече
        е презаписан на диска с байтовия snapshot отпреди операцията и
        диалогът трябва да отрази точно това, не остарялото си in-memory
        състояние.
        """

        self.approved_handles: set[str] = {
            str(handle) for handle in (approved_links or {}) if str(handle)
        }
        self._state = self._load_state()
        rejected_decisions = {
            str(handle)
            for handle, value in self._state.get("decisions", {}).items()
            if isinstance(value, dict)
            and str(value.get("decision") or "") in {
                "PROBLEM_RECORDED", "FLAGGED", "NO_VALID_CANDIDATE", "MISSING_MASTER", "EXCLUDED_BY_ENGINEER", "NON_PRODUCTION_POSITION"
            }
        }
        approved_decisions = {
            str(handle)
            for handle, value in self._state.get("decisions", {}).items()
            if isinstance(value, dict)
            and str(value.get("decision") or "")
            in {
                "APPROVED",
                "APPROVED_BULK_AFTER_SAMPLE",
                "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE",
                "APPROVED_BULK_AFTER_F006_SAMPLE",
                "APPROVED_BULK_FINAL_USER_CONFIRMATION",
                "MANUAL_ASSIGNED",
                "ENGINEER_RESOLVED",
            }
        }
        self.approved_handles.update(approved_decisions)
        self.approved_handles.difference_update(rejected_decisions)
        self.sample_handles = self._resolve_sample_handles()
        self.geometry_sample_handles = self._resolve_secondary_sample_handles(
            state_key="geometry_sample_handles",
            eligibility=geometry_bulk_eligible,
            size=self.GEOMETRY_SAMPLE_SIZE,
        )
        self.f006_sample_handles = self._resolve_secondary_sample_handles(
            state_key="f006_sample_handles",
            eligibility=f006_bulk_eligible,
            size=self.F006_SAMPLE_SIZE,
        )

    def _read_approved_links_from_disk(self) -> dict[str, dict]:
        """Пресъздава ledger-a от диска — огледало на MainWindow._read_all_approved_geometry_links.

        Нужно е след undo на масова операция: ledger файлът вече е
        възстановен на диска с байтовия snapshot отпреди операцията, а
        диалогът няма собствена жива връзка към MainWindow, за да си поиска
        обратно оригиналния ``approved_links`` аргумент.
        """

        if self.project_root is None:
            return {}
        path = Path(self.project_root) / "working" / "approved_geometry_links.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        links = data.get("links", []) if isinstance(data, dict) else []
        result: dict[str, dict] = {}
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict):
                    handle = str(link.get("annotation_handle") or "")
                    if handle:
                        result[handle] = dict(link)
        return result

    def _load_state(self) -> dict:
        fingerprint = report_fingerprint(self.report)
        data: dict = {}
        if self.state_path.is_file():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            identity_matches = (
                not self._report_identity
                or (
                    isinstance(loaded, dict)
                    and loaded.get(IDENTITY_KEY) == self._report_identity
                )
            )
            if (
                isinstance(loaded, dict)
                and loaded.get("report_fingerprint") == fingerprint
                and identity_matches
            ):
                data = loaded

        if not data:
            data = {
                "schema": "lion-rebar-review-center-state/v4",
                "project_name": self.report.project_name,
                "report_path": str(self.report.path),
                "report_fingerprint": fingerprint,
                "sample_handles": [],
                "geometry_sample_handles": [],
                "f006_sample_handles": [],
                "decisions": {},
            }
            if self._report_identity:
                data[IDENTITY_KEY] = dict(self._report_identity)
            # Problem decisions are project knowledge, not report-local state.
            # Re-import them when a new batch report gets a new fingerprint.
            persistent = self._read_persistent_problems()
            current_handles = {
                str(row.get("annotation_handle") or "")
                for row in self.report.rows
                if str(row.get("annotation_handle") or "")
            }
            data["decisions"] = {
                handle: dict(value)
                for handle, value in persistent.items()
                if handle in current_handles and isinstance(value, dict)
            }

        if not isinstance(data.get("decisions"), dict):
            data["decisions"] = {}
        if not isinstance(data.get("sample_handles"), list):
            data["sample_handles"] = []
        if not isinstance(data.get("geometry_sample_handles"), list):
            data["geometry_sample_handles"] = []
        if not isinstance(data.get("f006_sample_handles"), list):
            data["f006_sample_handles"] = []
        self._sync_existing_problem_decisions(data.get("decisions", {}))
        return data

    def _sync_existing_problem_decisions(self, decisions: dict) -> None:
        if not isinstance(decisions, dict):
            return
        current = self._read_persistent_problems()
        changed = False
        for raw_handle, raw in decisions.items():
            if not isinstance(raw, dict) or raw.get("decision") not in {"FLAGGED", "NO_VALID_CANDIDATE"}:
                continue
            handle = str(raw_handle)
            if current.get(handle) != raw:
                current[handle] = dict(raw)
                changed = True
        if not changed:
            return
        path = self._persistent_problems_path()
        payload = {
            "schema": "lion-rebar-review-problems/v1",
            "project_name": self.report.project_name,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "problems": current,
        }
        if self._report_identity:
            payload[IDENTITY_KEY] = dict(self._report_identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _persistent_problems_path(self) -> Path:
        return self.state_path.with_name("review_problems.json")

    def _read_persistent_problems(self) -> dict[str, dict]:
        path = self._persistent_problems_path()
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        problems = payload.get("problems", {}) if isinstance(payload, dict) else {}
        return {str(key): dict(value) for key, value in problems.items() if isinstance(value, dict)}

    def _persist_problem(self, handle: str, decision: dict) -> None:
        path = self._persistent_problems_path()
        current = self._read_persistent_problems()
        current[str(handle)] = dict(decision)
        payload = {
            "schema": "lion-rebar-review-problems/v1",
            "project_name": self.report.project_name,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "problems": current,
        }
        if self._report_identity:
            payload[IDENTITY_KEY] = dict(self._report_identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _clear_persistent_problem(self, handle: str) -> None:
        path = self._persistent_problems_path()
        current = self._read_persistent_problems()
        if str(handle) not in current:
            return
        current.pop(str(handle), None)
        payload = {
            "schema": "lion-rebar-review-problems/v1",
            "project_name": self.report.project_name,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "problems": current,
        }
        if self._report_identity:
            payload[IDENTITY_KEY] = dict(self._report_identity)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _resolve_sample_handles(self) -> tuple[str, ...]:
        existing = tuple(str(value) for value in self._state.get("sample_handles", []) if str(value))
        row_by_handle = {
            str(row.get("annotation_handle") or ""): dict(row)
            for row in self.report.rows
            if str(row.get("annotation_handle") or "")
        }
        if existing:
            filtered = tuple(
                handle
                for handle in existing
                if handle in row_by_handle and bulk_eligible(row_by_handle[handle])
            )
            removed = [handle for handle in existing if handle not in set(filtered)]
            decisions = self._state.setdefault("decisions", {})
            for handle in removed:
                row = row_by_handle.get(handle)
                if row is None or not is_missing_master_row(row):
                    continue
                previous = decisions.get(handle, {})
                decisions[handle] = {
                    "decision": "MISSING_MASTER",
                    "at": (
                        previous.get("at")
                        if isinstance(previous, dict) and previous.get("at")
                        else datetime.now().astimezone().isoformat(timespec="seconds")
                    ),
                    "annotation_text": row.get("annotation_text"),
                    "reason": missing_master_reason(row),
                    "migrated_from": (
                        previous.get("decision")
                        if isinstance(previous, dict)
                        else None
                    ),
                }
            self._state["schema"] = "lion-rebar-review-center-state/v4"
            self._state["sample_handles"] = list(filtered)
            self._state["sample_migration"] = {
                "removed_invalid_master": removed,
                "kept_supported": len(filtered),
            }
            self._save_state()
            return filtered

        sample = build_stratified_sample(self.report.rows, size=self.SAMPLE_SIZE)
        self._state["schema"] = "lion-rebar-review-center-state/v4"
        self._state["sample_handles"] = list(sample)
        self._save_state()
        return sample

    def _resolve_secondary_sample_handles(
        self,
        *,
        state_key: str,
        eligibility,
        size: int,
    ) -> tuple[str, ...]:
        existing = tuple(
            str(value)
            for value in self._state.get(state_key, [])
            if str(value)
        )
        preferred = list(existing)
        preferred.extend(
            sorted(
                handle
                for handle in self.approved_handles
                if handle not in set(existing)
                and self._decision(handle)
                not in {
                    "MANUAL_ASSIGNED",
                    "APPROVED_BULK_AFTER_SAMPLE",
                    "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE",
                    "APPROVED_BULK_AFTER_F006_SAMPLE",
                }
            )
        )
        sample = build_control_sample(
            self.report.rows,
            eligibility=eligibility,
            size=size,
            preferred_handles=preferred,
        )
        self._state["schema"] = "lion-rebar-review-center-state/v4"
        self._state[state_key] = list(sample)
        self._save_state()
        return sample

    def _save_state(self) -> None:
        started = perf_counter()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if self._report_identity:
            self._state[IDENTITY_KEY] = dict(self._report_identity)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)
        self._log_timing("save_state", started, rows=len(self._state.get("decisions", {})))

    def _queue_code(self) -> str:
        return str(self.queue_combo.currentData() or "WORK")

    def _rows_for_queue(self, code: str) -> list[dict]:
        normalized = str(code)
        cached = self._queue_cache.get(normalized)
        if cached is not None:
            return cached
        started = perf_counter()
        rows = review_queue_rows(
            self.report.rows,
            queue_code=normalized,
            sample_handles=self.sample_handles,
            geometry_sample_handles=self.geometry_sample_handles,
            f006_sample_handles=self.f006_sample_handles,
            decisions=self._state.get("decisions", {}),
            approved_handles=self.approved_handles,
        )
        self._queue_cache[normalized] = rows
        self._log_timing(f"queue:{normalized}", started, rows=len(rows))
        return rows

    def _invalidate_derived_state(self) -> None:
        self._queue_cache.clear()
        self._geometry_progress_cache = None
        self._f006_progress_cache = None
        # Всяко променено решение обезсилва точния Final Control snapshot.
        # НЕ трием артефактите: те са production доказателство. Freeze-ът
        # остава като INVALID с причина и одитна следа, а export-ът го
        # отказва по статус (final_control_lifecycle).
        from lion_rebar.project.final_control_lifecycle import invalidate_final_control

        invalidate_final_control(
            self.state_path.parent.parent,
            event="ENGINEER_DECISION",
            actor="REVIEW_CENTER",
            note="Променено решение в Review Center.",
        )

    def _rebuild_table(self, *, refresh_metadata: bool = True) -> None:
        started = perf_counter()
        code = self._queue_code()
        rows = self._rows_for_queue(code)
        query = self.search_edit.text().strip().casefold()
        if query:
            rows = [row for row in rows if query in self._search_blob(row)]
        self._visible_rows = rows
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                self._populate_row(row_index, row)
            if rows:
                self.table.setCurrentCell(0, 0)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        if rows:
            # Signals are intentionally blocked during the batch table update,
            # so render the selected evidence exactly once.
            self._render_details(rows[0])
        else:
            self._render_details(None)
            self.details.setHtml(self._empty_queue_html(code, filtered=bool(query)))
            self.review_reason_label.setText("Няма видима позиция в текущата опашка.")
            self.position_summary_label.setText(self._empty_queue_plain_text(code, filtered=bool(query)))
        self._previewed_handle = None
        if refresh_metadata:
            self._refresh_queue_counts()
            self._update_summary()
        self._refresh_buttons()
        self._log_timing(
            "table_rebuild",
            started,
            rows=len(rows),
            extra=f"queue={code}; metadata={int(refresh_metadata)}; query={int(bool(query))}",
        )

    def _empty_queue_plain_text(self, code: str, *, filtered: bool) -> str:
        if filtered:
            return "Филтърът не намира позиции. Изчисти търсенето; решения не са записани."
        if code != "WORK":
            return "Тази помощна опашка е празна. Смени опашката или отвори „Всички реални позиции“."
        progress = self._final_confirmation_progress()
        if progress.engineering_remaining:
            return (
                f"Има {progress.engineering_remaining} инженерни казуса, но не са видими тук. "
                "Изчисти търсенето и провери опашките за конфликт/липсваща MASTER форма."
            )
        if progress.safe_remaining:
            return (
                f"Няма инженерни казуси, но остават {progress.safe_remaining} позиции за изрично "
                "USER потвърждение. Нулева ръчна опашка не означава готов export."
            )
        return "Няма нерешени позиции. Следваща стъпка: изпълни „Финален контрол“; export не се отключва от този екран."

    def _empty_queue_html(self, code: str, *, filtered: bool) -> str:
        return f"<b>{_escape(self._empty_queue_plain_text(code, filtered=filtered))}</b>"

    def _populate_row(self, row_index: int, row: dict) -> None:
        handle = self._handle(row)
        decision = self._decision_label(handle, row)
        values = [
            decision,
            row.get("batch_status") or "—",
            self._ai_queue_label(handle, row),
            row.get("annotation_text") or "—",
            f"×{max(1, int(row.get('review_group_size') or 1))}",
            _fmt(row.get("diameter_mm")),
            _fmt(row.get("declared_length_cm")),
            (
                " / ".join(row.get("master_ambiguous_shape_codes", []))
                if row.get("master_ambiguous_shape_codes")
                else (row.get("master_shape_code") or "—")
            ),
            row.get("segment_count") or 0,
            _fmt(
                row.get("raw_numeric_difference")
                if row.get("raw_numeric_difference") is not None
                else row.get("numeric_difference")
            ),
            _fmt(row.get("distance_raw")),
            row.get("candidate_layer") or "—",
            (
                "ДА"
                if row.get("global_conflict")
                or row.get("geometry_owner_conflict")
                or row.get("master_conflict")
                else "НЕ"
            ),
        ]
        decision_code = self._decision(handle)
        approved = is_review_row_approved(
            row,
            decision_code=decision_code,
            approved_handles=self.approved_handles,
        )
        flagged = decision_code in {"FLAGGED", "NO_VALID_CANDIDATE"}
        missing_master = decision_code == "MISSING_MASTER" or is_missing_master_row(row)
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column in {4, 5, 6, 8, 9, 10}:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if approved:
                item.setBackground(QColor("#dcfce7"))
            elif missing_master:
                item.setBackground(QColor("#f3e8ff"))
            elif flagged:
                item.setBackground(QColor("#fee2e2"))
            elif row.get("global_conflict") or row.get("master_conflict"):
                item.setBackground(QColor("#fef3c7"))
            self.table.setItem(row_index, column, item)

    def _ai_queue_label(self, handle: str, row: dict) -> str:
        if handle in set(self.geometry_sample_handles):
            index = self.geometry_sample_handles.index(handle) + 1
            return f"КОНТРОЛ {index}/{len(self.geometry_sample_handles)}"
        if handle in set(self.f006_sample_handles):
            index = self.f006_sample_handles.index(handle) + 1
            return f"F006 КОНТРОЛ {index}/{len(self.f006_sample_handles)}"
        if geometry_bulk_eligible(row):
            return "ПАКЕТ СЛЕД 10/10"
        if f006_bulk_eligible(row):
            return "ПАКЕТ СЛЕД 5/5"
        if str(row.get("batch_status") or "") in {
            GEOMETRY_STRONG_STATUS,
            F006_STRONG_STATUS,
        }:
            return "ИЗВЪН ПАКЕТА"
        return str(row.get("matcher_disposition") or "—")

    def _search_blob(self, row: dict) -> str:
        values = [
            row.get("annotation_text"),
            row.get("annotation_handle"),
            row.get("candidate_layer"),
            row.get("master_shape_code"),
            row.get("master_shape_name"),
            ",".join(str(value) for value in row.get("master_ambiguous_shape_codes", [])),
            row.get("batch_status"),
            row.get("matcher_disposition"),
            row.get("review_group_key"),
            ",".join(str(value) for value in row.get("matcher_blocker_codes", [])),
            ",".join(str(value) for value in row.get("candidate_handles", [])),
        ]
        return " | ".join(str(value or "") for value in values).casefold()

    def _selection_changed(self) -> None:
        self._previewed_handle = None
        row = self.current_row()
        self._render_details(row)
        self._refresh_buttons()

    def current_row(self) -> dict | None:
        index = self.table.currentRow()
        if 0 <= index < len(self._visible_rows):
            return self._visible_rows[index]
        return None

    def _render_details(self, row: dict | None) -> None:
        if row is None:
            self.details.clear()
            self.review_reason_label.setText("Няма избрана позиция.")
            self.position_summary_label.setText("Форма / брой / Ø / L / сегменти: —")
            return
        reasons = [str(value) for value in row.get("review_reason_codes", []) if str(value)]
        reasons.extend(
            str(value) for value in row.get("matcher_blocker_codes", []) if str(value)
        )
        if row.get("global_conflict") or row.get("geometry_owner_conflict"):
            reasons.append("Конфликт за DXF собственост")
        if is_missing_master_row(row):
            reasons.append(missing_master_reason(row))
        has_reference_geometry = bool(row.get("reference_geometry_handles"))
        if not row.get("candidate_handles"):
            if has_reference_geometry:
                reasons.append(
                    "Няма потвърден геометричен кандидат — показана е "
                    "свързаната референтна геометрия за проверка"
                )
            else:
                reasons.append("Липсва надеждна DXF геометрия")
        elif "FAR_ASSOCIATION" in reasons and has_reference_geometry:
            reasons.append(
                "Далечен несигурен кандидат — показана е по-надеждната "
                "собствена геометрия на надписа вместо него"
            )
        unique_reasons = list(dict.fromkeys(value.strip() for value in reasons if value.strip()))
        reason_text = " · ".join(unique_reasons[:4])
        if not reason_text:
            reason_text = str(row.get("matcher_decision_summary") or "Визуално потвърждение на еднозначния кандидат")
        self.review_reason_label.setText(f"Защо е тук: {reason_text}")

        shape_label, shape_code = _shape_summary_identity(row)
        segments = (
            row.get("production_segments_cm")
            or row.get("canonical_segment_lengths_cm")
            or row.get("suggested_segments_cm")
            or []
        )
        segment_text = " + ".join(_fmt(value) for value in segments) or "—"
        source = _production_segment_source_label(row)
        self.position_summary_label.setText(
            f"Надпис: {row.get('annotation_text') or '—'}\n"
            f"{shape_label}: {shape_code} | брой: {_fmt(row.get('annotation_count'))} | "
            f"Ø: {_fmt(row.get('diameter_mm'))} mm | L: {_fmt(row.get('declared_length_cm'))} cm\n"
            f"Сегменти: {segment_text} cm | източник: {source}"
        )
        handles = ", ".join(row.get("candidate_handles", [])) or "—"
        flags = "<br>".join(_escape(value) for value in row.get("data_quality_flags", [])) or "—"
        quality_notes = (
            "<br>".join(_escape(value) for value in row.get("data_quality_notes", [])) or "—"
        )
        notes = "<br>".join(_escape(value) for value in row.get("candidate_notes", [])) or "—"
        warnings = "<br>".join(_escape(value) for value in row.get("analysis_warnings", [])[:8]) or "—"
        matcher_blockers = (
            "<br>".join(_escape(value) for value in row.get("matcher_blocker_codes", []))
            or "—"
        )
        alternatives = len(row.get("alternative_candidates", []))
        raw_difference = (
            row.get("raw_numeric_difference")
            if row.get("raw_numeric_difference") is not None
            else row.get("numeric_difference")
        )
        master_warning = (
            f"<p style='color:#7e22ce'><b>MASTER контрол:</b><br>{_escape(missing_master_reason(row))}</p>"
            if is_missing_master_row(row)
            else ""
        )
        group_size = max(1, int(row.get("review_group_size") or 1))
        unresolved_group = int(
            row.get("review_group_unresolved_count") or group_size
        )
        group_html = (
            "<p style='color:#1e3a8a; background:#eff6ff; padding:6px;'>"
            f"<b>Група: ×{group_size}</b> еквивалентни позиции; "
            f"нерешени в момента: {unresolved_group}.<br>"
            f"Текущ представител: {_escape(row.get('review_group_display_representative_handle') or row.get('annotation_handle') or '—')}. "
            "<b>Текущото решение важи само за този надпис.</b></p>"
            if group_size > 1
            else ""
        )
        decision_data = self._state.get("decisions", {}).get(self._handle(row), {})
        matcher_decision = row.get("matcher_decision")
        matcher_transforms = (
            matcher_decision.get("transforms", {})
            if isinstance(matcher_decision, dict)
            else {}
        )
        transform_explanation = (
            matcher_transforms.get("explanation")
            if isinstance(matcher_transforms, dict)
            else None
        )
        problem_html = ""
        if isinstance(decision_data, dict) and decision_data.get("decision") in {"FLAGGED", "NO_VALID_CANDIDATE"}:
            problem_html = (
                "<p style='color:#991b1b; background:#fef2f2; padding:6px;'>"
                f"<b>Записан проблем:</b> {_escape(decision_data.get('reason') or decision_data.get('reason_code') or '—')}<br>"
                f"<b>Бележка:</b> {_escape(decision_data.get('note') or '—')}</p>"
            )
        sample_html = ""
        handle = self._handle(row)
        if handle in set(self.geometry_sample_handles):
            sample_html = (
                "<p style='color:#1d4ed8; background:#eff6ff; padding:6px;'>"
                f"<b>Контролна извадка — силни по геометрия:</b> "
                f"{self.geometry_sample_handles.index(handle) + 1}/"
                f"{len(self.geometry_sample_handles)}. Този ред трябва да се провери визуално."
                "</p>"
            )
        elif handle in set(self.f006_sample_handles):
            sample_html = (
                "<p style='color:#7e22ce; background:#faf5ff; padding:6px;'>"
                f"<b>Контролна извадка — F006:</b> "
                f"{self.f006_sample_handles.index(handle) + 1}/"
                f"{len(self.f006_sample_handles)}. Този ред трябва да се провери визуално."
                "</p>"
            )
        self.details.setHtml(
            f"<h3>{_escape(row.get('annotation_text') or '—')}</h3>"
            # Размерите стоят ПЪРВИ: панелът е нисък и всичко под третия-
            # четвъртия ред остава скрито зад долния ръб. Точно те са
            # нужни, за да може операторът да довърши решението.
            f"<div style='background:#f8fafc;border:1px solid #cbd5e1;"
            f"padding:6px;margin-bottom:6px'>{_dimension_evidence_html(row)}</div>"
            f"<p><b>Статус:</b> {_escape(row.get('batch_status') or '—')}<br>"
            f"<b>Надпис handle:</b> {_escape(row.get('annotation_handle') or '—')}<br>"
            f"<b>Кандидат:</b> {_escape(handles)}<br>"
            f"<b>Форма:</b> {_escape(' / '.join(row.get('master_ambiguous_shape_codes', [])) if row.get('master_ambiguous_shape_codes') else (row.get('master_shape_code') or '—'))} — "
            f"{_escape(row.get('master_shape_name') or '')}<br>"
            f"<b>Сурова DXF L:</b> {_fmt(row.get('geometry_length_raw'))}<br>"
            f"<b>Производствена L от надписа:</b> {_fmt(row.get('declared_length_cm'))}<br>"
            f"<b>Сурова Δ DXF:</b> {_fmt(raw_difference)}<br>"
            f"<b>Matcher Δ след правило:</b> {_fmt(row.get('numeric_difference'))}<br>"
            f"<b>Разстояние:</b> {_fmt(row.get('distance_raw'))}<br>"
            f"<b>Алтернативи:</b> {alternatives}<br>"
            f"<b>MASTER двусмислие:</b> {'ДА' if row.get('master_conflict') else 'НЕ'}<br>"
            f"<b>Конфликт за DXF собственост:</b> {'ДА' if row.get('global_conflict') or row.get('geometry_owner_conflict') else 'НЕ'}<br>"
            f"<b>AI решение:</b> {_escape(row.get('matcher_disposition') or '—')} / "
            f"{_escape(row.get('matcher_evidence_band') or '—')}</p>"
            f"<p><b>Увереност за MASTER формата:</b> "
            f"{_escape(row.get('form_classification_confidence') or '—')}<br>"
            f"<b>Увереност за точната DXF собственост:</b> "
            f"{_escape(row.get('geometry_ownership_confidence') or '—')}</p>"
            f"{group_html}"
            f"{sample_html}"
            f"<p><b>AI обяснение:</b><br>{_escape(row.get('matcher_decision_summary') or '—')}</p>"
            f"<p><b>Нормализирани трансформации:</b><br>{_escape(transform_explanation or '—')}</p>"
            f"<p><b>AI blockers:</b><br>{matcher_blockers}</p>"
            f"<p><b>Блокиращи проверки на данните:</b><br>{flags}</p>"
            f"<p><b>Информационни бележки:</b><br>{quality_notes}</p>"
            f"<p><b>Контрол:</b><br>{notes}</p>"
            f"<p><b>Предупреждения:</b><br>{warnings}</p>"
            f"{master_warning}"
            f"{problem_html}"
        )

    def _preview_current(self) -> None:
        row = self.current_row()
        if row is None:
            return
        handle = self._handle(row)
        reasons = row.get("review_reason_codes") or []
        # Owner-confirmed 2026-09-12, verified against both real files: a
        # FAR_ASSOCIATION candidate NEVER coincides with the annotation's
        # own reference geometry (0 of 33 real cases) -- real label-to-
        # geometry pairs in this dialect are always drawn close together.
        # When both exist, the annotation's own (closer, text-linked)
        # geometry is strictly more trustworthy than the far candidate, so
        # the quick-preview default prefers it. candidate_handles itself
        # is untouched -- the far candidate stays reachable via "Всички
        # кандидати" for the rare case this default guess is wrong.
        prefer_reference_geometry = (
            "FAR_ASSOCIATION" in reasons and bool(row.get("reference_geometry_handles"))
        )
        if row.get("candidate_handles") and not prefer_reference_geometry:
            self.previewRequested.emit(dict(row))
            self._previewed_handle = handle
            self.result_label.setText(
                "Показан е първият кандидат в червено. Ако не е правилен, отвори „Всички кандидати“."
            )
            self.fade_for_preview()
        elif row.get("reference_geometry_handles"):
            # Owner-requested 2026-09-11/12 ("искам програмата да е ТОП, без
            # компромиси"): no PROVEN candidate exists, but the annotation's
            # own attached (reference-only) proxy geometry is already known
            # -- show it instead of forcing the operator to click through
            # every fragment by hand. Drawn in blue, never red, so it is
            # never mistaken for a proven match (see
            # ui/main_window.py::_review_preview_reference_geometry).
            self.referenceGeometryRequested.emit(dict(row))
            self._previewed_handle = handle
            self.result_label.setText(
                "Показана е собствената геометрия на надписа в синьо — "
                "СПРАВОЧНО, дължината НЕ е доказана. Ако не е вярна, "
                "отвори режим за избор на геометрия."
                if not prefer_reference_geometry else
                "Далечният кандидат изглежда несигурен — показана е СИНЯ "
                "собствена геометрия на надписа вместо него. Ако не е "
                "вярна, отвори „Всички кандидати“ за далечния вариант."
            )
            self.fade_for_preview()
        else:
            self.locateAnnotationRequested.emit(dict(row))
            self._previewed_handle = None
            self.result_label.setText(
                "Включен е режим за избор на геометрия. Панелът остава видим, докато маркираш линиите."
            )
        self._refresh_buttons()

    def _open_all_candidates(self) -> None:
        row = self.current_row()
        if row is not None:
            self.fullCandidatesRequested.emit(dict(row))

    def _engineer_decision_current(self) -> None:
        row = self.current_row()
        if row is None:
            return
        self.engineerDecisionRequested.emit(dict(row))

    def _assign_form_current(self) -> None:
        row = self.current_row()
        if row is None:
            return
        self.formAssignmentRequested.emit(dict(row))

    def _approve_current(self) -> None:
        row = self.current_row()
        if row is None:
            return
        handle = self._handle(row)
        decision = self._decision(handle)
        if decision in DIAGNOSTIC_REVIEW_DECISIONS or not final_confirmation_eligible(row):
            self.result_label.setText(
                "Тази позиция изисква пълно „Инженерно решение“ — форма, брой, Ø, L и сегменти."
            )
            self._refresh_buttons()
            return
        if handle != self._previewed_handle:
            self.result_label.setText("Първо покажи точно текущия кандидат в чертежа.")
            self._refresh_buttons()
            return
        self.approvalRequested.emit(dict(row))

    def _flag_current(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if is_missing_master_row(row):
            self._open_problem_dialog(row, default_reason="MISSING_MASTER")
            return
        self._open_problem_dialog(row, default_reason="OTHER")

    def _reject_current(self) -> None:
        row = self.current_row()
        if row is not None:
            self._open_problem_dialog(row, default_reason="NO_VALID_CANDIDATE")

    def _open_problem_dialog(self, row: dict, *, default_reason: str) -> None:
        dialog = ReviewProblemDialog(row=dict(row), default_reason=default_reason, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.result_payload or {}
        handle = self._handle(row)
        reason_code = str(payload.get("reason_code") or "OTHER")
        # Windows приемане: отметката "ИЗРИЧНО ИЗКЛЮЧИ" записваше наследения
        # EXCLUDED_BY_ENGINEER, който Финалният контрол третира като
        # БЛОКИРАЩ. Резултатът беше задънена улица: нула нерешени позиции, а
        # спецификацията се обявява за непълна и freeze не се създава.
        # Отметката вече записва същото терминално решение като пътя
        # "Инженерно решение".
        explicit_exclusion = bool(payload.get("explicit_exclusion"))
        decision_code = (
            "NON_PRODUCTION_POSITION" if explicit_exclusion else "PROBLEM_RECORDED"
        )
        if explicit_exclusion:
            # Терминалното изключване изисква мотив; без него Финалният
            # контрол го отхвърля като недоказано.
            note_text = str(payload.get("note") or "").strip()
            correct_text = str(payload.get("correct_specification") or "").strip()
            if not note_text and not correct_text:
                QMessageBox.warning(
                    self,
                    "Липсва мотив за изключване",
                    "Изключването на позиция от производствената спецификация "
                    "изисква инженерна бележка.\n\nПопълни „Как трябва да бъде“ "
                    "или бележката и запиши отново.",
                )
                return
        rejected: list[str] = []
        if payload.get("reject_candidates"):
            rejected.extend(str(value) for value in row.get("candidate_handles", []) if str(value))
            for alternative in row.get("alternative_candidates", []):
                if isinstance(alternative, dict):
                    rejected.extend(str(value) for value in alternative.get("handles", []) if str(value))
        decision = {
            "decision": decision_code,
            "action_class": (
                "EXCLUSION" if decision_code == "NON_PRODUCTION_POSITION" else "PROBLEM"
            ),
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "annotation_text": row.get("annotation_text"),
            # Финалният контрол валидира ТЕРМИНАЛНОТО изключване по
            # reason_code == NON_PRODUCTION_POSITION. Диагностичната причина
            # (напр. "Няма верен кандидат") се запазва отделно, за да не се
            # губи защо е изключена позицията.
            "reason_code": (
                "NON_PRODUCTION_POSITION" if explicit_exclusion else reason_code
            ),
            "diagnostic_reason_code": reason_code,
            "reason": payload.get("reason"),
            "correct_specification": payload.get("correct_specification"),
            "note": payload.get("note") or payload.get("correct_specification"),
            "rejected_candidate_handles": sorted(set(rejected)),
            "rejected_master_shape_code": row.get("master_shape_code"),
        }
        if explicit_exclusion:
            # Терминалното изключване изисква непразен evidence обект —
            # иначе Финалният контрол го отхвърля като недоказано.
            decision["evidence"] = {
                "source": "REVIEW_PROBLEM_DIALOG",
                "annotation_text": str(row.get("annotation_text") or ""),
                "diagnostic_reason_code": reason_code,
                "candidate_handles": [
                    str(value) for value in (row.get("candidate_handles") or []) if str(value)
                ],
                "master_shape_code": str(row.get("master_shape_code") or ""),
                "declared_length_cm": str(row.get("declared_length_cm") or ""),
                "note": str(payload.get("note") or payload.get("correct_specification") or ""),
            }
        self._state.setdefault("decisions", {})[handle] = decision
        self.approved_handles.discard(handle)
        self._save_state()
        self._persist_problem(handle, decision)
        self._invalidate_derived_state()
        self.decisionRecorded.emit(handle, dict(decision))
        self.result_label.setText(
            "Позицията е изключена от производствената спецификация с "
            "инженерно доказателство. Финалният контрол може да продължи."
            if decision_code == "NON_PRODUCTION_POSITION"
            else "Проблемът е записан. Позицията остава за решение — използвай „Инженерно решение“."
        )
        self._rebuild_table_preserve_next(handle)

    def mark_missing_master(self, annotation_handle: str, row: dict) -> None:
        handle = str(annotation_handle)
        decision = {
            "decision": "MISSING_MASTER",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "annotation_text": row.get("annotation_text"),
            "reason": missing_master_reason(row),
        }
        self._state.setdefault("decisions", {})[handle] = decision
        self.approved_handles.discard(handle)
        self._save_state()
        self._invalidate_derived_state()
        self.decisionRecorded.emit(handle, dict(decision))
        self.result_label.setText(
            "Връзката не е записана: кандидатът няма валидна одобрена MASTER форма."
        )
        self._rebuild_table_preserve_next(handle)

    def _advance(self) -> None:
        if not self._visible_rows:
            return
        current = self.table.currentRow()
        for offset in range(1, len(self._visible_rows) + 1):
            index = (current + offset) % len(self._visible_rows)
            row = self._visible_rows[index]
            if not self._is_resolved(row):
                self.table.selectRow(index)
                return
        if self._visible_rows:
            self.table.selectRow((current + 1) % len(self._visible_rows))

    def mark_approved(self, annotation_handle: str) -> None:
        handle = str(annotation_handle)
        self.approved_handles.add(handle)
        self._state.setdefault("decisions", {})[handle] = {
            "decision": "APPROVED",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self._save_state()
        self._clear_persistent_problem(handle)
        self._invalidate_derived_state()
        self.result_label.setText("Връзката е записана. Преминавам към следващата позиция.")
        self._rebuild_table_preserve_next(handle)

    def mark_engineer_resolved(
        self,
        annotation_handle: str,
        payload: dict,
    ) -> dict:
        handle = str(annotation_handle)
        annotation = payload.get("annotation") if isinstance(payload.get("annotation"), dict) else {}
        resolved = payload.get("resolved") if isinstance(payload.get("resolved"), dict) else {}
        non_production = str(payload.get("reason_code") or "").upper() == "NON_PRODUCTION_POSITION"
        decision_code = "NON_PRODUCTION_POSITION" if non_production else "ENGINEER_RESOLVED"
        # Project-layer save is the single authoritative writer.  In
        # particular, the non-production writer has already persisted the
        # complete structured evidence contract before this UI callback.
        decision = self._authoritative_engineer_decision(handle)
        if non_production and not is_valid_non_production_decision(decision):
            # Never overwrite a project-layer exclusion with a partial UI
            # payload. It stays visibly unresolved until the owner confirms it
            # again and the authoritative writer stores full evidence.
            self.approved_handles.discard(handle)
            self.result_label.setText(
                "Изключването няма пълно доказателство и остава за инженерно "
                "решение. Отвори го и потвърди отново."
            )
            self._rebuild_table_preserve_next(handle)
            return dict(decision)
        if not decision:
            decision = {
                "decision": decision_code,
                "action_class": payload.get("action_class"),
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "shape_code": resolved.get("shape_code"),
                "shape_name": resolved.get("shape_name"),
                "resolved_text": annotation.get("resolved_text"),
                "count": resolved.get("count"),
                "diameter_mm": resolved.get("diameter_mm"),
                "production_length_cm": resolved.get("production_length_cm"),
                "production_segment_lengths_cm": list(
                    resolved.get("segment_lengths_cm") or []
                ),
                "reason_code": payload.get("reason_code"),
                "reason": payload.get("reason"),
                "note": payload.get("note"),
                "evidence": (
                    payload.get("evidence")
                    or payload.get("diagnostic_problem")
                    or {}
                ),
            }
        if non_production:
            self.approved_handles.discard(handle)
        else:
            self.approved_handles.add(handle)
        self._state.setdefault("decisions", {})[handle] = decision
        # A valid non-production decision is already persisted byte-for-byte
        # by the project layer. Updating the in-memory view is sufficient and
        # avoids a second writer for the proof contract.
        if not non_production:
            self._save_state()
        self._clear_persistent_problem(handle)
        self._invalidate_derived_state()
        if non_production and is_valid_non_production_decision(decision):
            result_text = (
                "Позицията е мотивирано изключена като непроизводствена. "
                "Тя остава в одитната следа, но няма да влиза в Excel/DXF."
            )
        elif non_production:
            result_text = (
                "Изключването няма пълно доказателство и остава за инженерно "
                "решение. Отвори го и потвърди отново."
            )
        else:
            result_text = (
                f"Инженерното решение е записано: "
                f"{annotation.get('resolved_text') or handle}. "
                "Позицията ще участва във Final Control и Export."
            )
        self.result_label.setText(result_text)
        self._rebuild_table_preserve_next(handle)
        return dict(decision)

    def _authoritative_engineer_decision(self, handle: str) -> dict:
        """Reload the decision already written by the project layer."""

        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if payload.get("report_fingerprint") != report_fingerprint(self.report):
            return {}
        if self._report_identity and payload.get(IDENTITY_KEY) != self._report_identity:
            return {}
        decisions = payload.get("decisions") if isinstance(payload, dict) else {}
        value = decisions.get(str(handle), {}) if isinstance(decisions, dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    def mark_manual_assigned(self, annotation_handle: str, shape_code: str | None, shape_name: str) -> None:
        # Backward-compatible path for the old manual geometry dialog.
        handle = str(annotation_handle)
        self.approved_handles.add(handle)
        self._state.setdefault("decisions", {})[handle] = {
            "decision": "MANUAL_ASSIGNED",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "shape_code": shape_code,
            "shape_name": shape_name,
        }
        self._save_state()
        self._clear_persistent_problem(handle)
        self._invalidate_derived_state()
        self._rebuild_table_preserve_next(handle)

    def mark_manual_draft(self, annotation_handle: str, shape_name: str) -> None:
        handle = str(annotation_handle)
        decision = {
            "decision": "MISSING_MASTER",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": "Ръчно записана нова форма — чернова",
            "shape_name": shape_name,
        }
        self._state.setdefault("decisions", {})[handle] = decision
        self.approved_handles.discard(handle)
        self._save_state()
        self._invalidate_derived_state()
        self.decisionRecorded.emit(handle, dict(decision))
        self.result_label.setText(
            f"Запазена е чернова „{shape_name}“. Позицията остава извън спецификацията до MASTER одобрение."
        )
        self._rebuild_table_preserve_next(handle)

    def mark_bulk_approved(self, annotation_handles: list[str] | tuple[str, ...]) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for raw in annotation_handles:
            handle = str(raw)
            self.approved_handles.add(handle)
            self._state.setdefault("decisions", {})[handle] = {
                "decision": "APPROVED_BULK_AFTER_SAMPLE",
                "at": timestamp,
            }
        self._save_state()
        self._invalidate_derived_state()
        self.result_label.setText(f"Масово са записани {len(annotation_handles)} силни връзки.")
        self._rebuild_table()

    def mark_geometry_bulk_approved(
        self, annotation_handles: list[str] | tuple[str, ...]
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for raw in annotation_handles:
            handle = str(raw)
            self.approved_handles.add(handle)
            self._state.setdefault("decisions", {})[handle] = {
                "decision": "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE",
                "at": timestamp,
            }
        self._save_state()
        self._invalidate_derived_state()
        self.result_label.setText(
            f"Масово са записани {len(annotation_handles)} геометрично силни връзки. "
            "Диаметърът е взет от надписа."
        )
        self._rebuild_table()

    def mark_f006_bulk_approved(
        self, annotation_handles: list[str] | tuple[str, ...]
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for raw in annotation_handles:
            handle = str(raw)
            self.approved_handles.add(handle)
            self._state.setdefault("decisions", {})[handle] = {
                "decision": "APPROVED_BULK_AFTER_F006_SAMPLE",
                "at": timestamp,
            }
        self._save_state()
        self._invalidate_derived_state()
        self.result_label.setText(
            f"Масово са записани {len(annotation_handles)} автоматично разпознати стремена F006."
        )
        self._rebuild_table()

    def mark_final_bulk_approved(self, annotation_handles: list[str] | tuple[str, ...]) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for raw in annotation_handles:
            handle = str(raw)
            self.approved_handles.add(handle)
            self._state.setdefault("decisions", {})[handle] = {
                "decision": "APPROVED_BULK_FINAL_USER_CONFIRMATION",
                "action_class": "CONFIRMATION",
                "reason_code": "FINAL_USER_CONFIRMATION",
                "reason": "Инженерът потвърди всички останали безопасни предложения.",
                "at": timestamp,
            }
            self._clear_persistent_problem(handle)
        self._save_state()
        self._invalidate_derived_state()
        self.result_label.setText(
            f"Потвърдени са {len(annotation_handles)} останали безопасни позиции. "
            "Пусни „Финален контрол“ — всички AUTO + USER решения трябва да влязат в export-а."
        )
        self._rebuild_table()

    def mark_error(self, message: str) -> None:
        self.result_label.setText(message)
        self.result_label.setStyleSheet("font-weight:700; color:#b91c1c;")

    def decision_payload(self, annotation_handle: str) -> dict:
        """Return the current report-local diagnostic/resolution decision."""

        raw = self._state.get("decisions", {}).get(str(annotation_handle), {})
        return dict(raw) if isinstance(raw, dict) else {}

    def _rebuild_table_preserve_next(self, previous_handle: str) -> None:
        old_rows = list(self._visible_rows)
        old_index = next(
            (index for index, row in enumerate(old_rows) if self._handle(row) == previous_handle),
            -1,
        )
        self._rebuild_table()
        if self._visible_rows:
            target = min(max(old_index, 0), len(self._visible_rows) - 1)
            self.table.selectRow(target)
            # Keep the evidence pane deterministic even when Qt reuses the
            # same current-row index and emits no selection-change signal.
            self._render_details(self.current_row())
            self._refresh_buttons()
            self._advance_if_current_done()
            return
        current_code = self._queue_code()
        if self.search_edit.text().strip() and self._rows_for_queue(current_code):
            # The last matching row was resolved, but this queue still has
            # work.  Clear only the exhausted search filter so auto-next can
            # show the next real position instead of an apparently empty table.
            self.search_edit.clear()
            return
        if current_code not in {"WORK", "ALL", "APPROVED", "REJECTED"}:
            work_index = self.queue_combo.findData("WORK")
            if work_index >= 0 and self._rows_for_queue("WORK"):
                if self.search_edit.text().strip():
                    self.search_edit.clear()
                self.queue_combo.setCurrentIndex(work_index)

    def _advance_if_current_done(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if self._is_resolved(row):
            self._advance()

    def _request_bulk_approval(self) -> None:
        eligible = [
            dict(row)
            for row in self.report.rows
            if bulk_eligible(row)
            and self._decision(self._handle(dict(row))) not in DIAGNOSTIC_REVIEW_DECISIONS
            and not self._is_resolved(dict(row))
        ]
        if not eligible:
            self.result_label.setText("Няма останали допустими силни кандидати за масово приемане.")
            return
        if not self._sample_passed():
            self.result_label.setText(
                "Масовото приемане е заключено: одобри всички валидни позиции от контролната извадка."
            )
            return
        self.bulkApprovalRequested.emit(eligible)

    def _request_geometry_bulk_approval(self) -> None:
        progress = self._geometry_sample_progress()
        if not progress.passed:
            self.result_label.setText(
                self._control_block_message(
                    "Силни по геометрия",
                    progress,
                )
            )
            return
        sample_set = set(progress.sample_handles)
        eligible = [
            dict(row)
            for row in self.report.rows
            if geometry_bulk_eligible(row)
            and self._handle(dict(row)) not in sample_set
            and self._decision(self._handle(dict(row))) not in DIAGNOSTIC_REVIEW_DECISIONS
            and not self._is_resolved(dict(row))
        ]
        if not eligible:
            self.result_label.setText(
                "Няма останали допустими кандидати в опашката „Силни по геометрия“."
            )
            return
        self.geometryBulkApprovalRequested.emit(eligible)

    def _request_f006_bulk_approval(self) -> None:
        progress = self._f006_sample_progress()
        if not progress.passed:
            self.result_label.setText(
                self._control_block_message(
                    "Стремена F006",
                    progress,
                )
            )
            return
        sample_set = set(progress.sample_handles)
        eligible = [
            dict(row)
            for row in self.report.rows
            if f006_bulk_eligible(row)
            and self._handle(dict(row)) not in sample_set
            and self._decision(self._handle(dict(row))) not in DIAGNOSTIC_REVIEW_DECISIONS
            and not self._is_resolved(dict(row))
        ]
        if not eligible:
            self.result_label.setText("Няма останали допустими автоматично разпознати стремена F006.")
            return
        self.f006BulkApprovalRequested.emit(eligible)

    def _request_final_bulk_confirmation(self) -> None:
        progress = self._final_confirmation_progress()
        if progress.engineering_remaining > 0:
            self.result_label.setText(
                f"Общото потвърждение е заключено: реши още {progress.engineering_remaining} "
                "инженерни казуса чрез „Инженерно решение“."
            )
            return
        if progress.safe_remaining <= 0:
            self.result_label.setText("Няма останали безопасни позиции за общо потвърждение.")
            return
        safe_set = set(progress.safe_handles)
        rows = [
            dict(row) for row in self.report.rows
            if self._handle(dict(row)) in safe_set
            and final_confirmation_eligible(row)
            and not self._is_resolved(dict(row))
        ]
        if len(rows) != progress.safe_remaining:
            self.result_label.setText(
                "Списъкът за общо потвърждение се промени. Обнови Review Center и опитай отново."
            )
            return
        self.finalBulkConfirmationRequested.emit(rows)

    def _final_confirmation_progress(self):
        return final_confirmation_progress(
            self.report.rows,
            decisions=self._state.get("decisions", {}),
            approved_handles=self.approved_handles,
        )

    def _geometry_sample_progress(self):
        if self._geometry_progress_cache is None:
            self._geometry_progress_cache = control_sample_progress(
                self.report.rows,
                sample_handles=self.geometry_sample_handles,
                eligibility=geometry_bulk_eligible,
                approved_handles=self.approved_handles,
                decisions=self._state.get("decisions", {}),
            )
        return self._geometry_progress_cache

    def _f006_sample_progress(self):
        if self._f006_progress_cache is None:
            self._f006_progress_cache = control_sample_progress(
                self.report.rows,
                sample_handles=self.f006_sample_handles,
                eligibility=f006_bulk_eligible,
                approved_handles=self.approved_handles,
                decisions=self._state.get("decisions", {}),
            )
        return self._f006_progress_cache

    def _control_block_message(self, label: str, progress) -> str:
        if progress.problem_handles:
            annotations = {
                self._handle(row): str(row.get("annotation_text") or self._handle(row))
                for row in self.report.rows
            }
            problem_labels = ", ".join(
                annotations.get(handle, handle)
                for handle in progress.problem_handles[:3]
            )
            return (
                f"Масовото приемане „{label}“ е блокирано: "
                f"{len(progress.problem_handles)} контролна позиция е маркирана като проблем"
                f" ({problem_labels})."
            )
        if progress.total == 0:
            return f"Масовото приемане „{label}“ е заключено: няма валидна контролна извадка."
        return (
            f"Масовото приемане „{label}“ е заключено: "
            f"контролна извадка {progress.approved}/{progress.total}; "
            f"провери още {progress.pending}."
        )

    def _f006_sample_passed(self) -> bool:
        return self._f006_sample_progress().passed

    def _geometry_sample_passed(self) -> bool:
        return self._geometry_sample_progress().passed

    def _sample_passed(self) -> bool:
        if not self.sample_handles:
            return False
        return all(
            handle in self.approved_handles and self._decision(handle) == "APPROVED"
            for handle in self.sample_handles
        )

    def _update_summary(self) -> None:
        sample_total = len(self.sample_handles)
        sample_approved = sum(
            handle in self.approved_handles and self._decision(handle) == "APPROVED"
            for handle in self.sample_handles
        )
        sample_flagged = sum(self._decision(handle) in {"FLAGGED", "NO_VALID_CANDIDATE"} for handle in self.sample_handles)
        bulk_remaining = sum(
            bulk_eligible(row) and not self._is_resolved(dict(row))
            for row in self.report.rows
        )
        geometry_progress = self._geometry_sample_progress()
        f006_progress = self._f006_sample_progress()
        missing_master = len(self._rows_for_queue("MISSING_MASTER"))
        work_remaining = len(self._rows_for_queue("WORK"))
        group_remaining = len(self._rows_for_queue("GROUPS"))
        final_progress = self._final_confirmation_progress()
        self.summary_label.setText(
            f"Проект: {self.report.project_name} | Отчет: {self.report.generated_at} | "
            f"Решени/одобрени: {final_progress.approved_or_resolved}/{final_progress.actionable} | "
            f"Инженерни казуси: {final_progress.engineering_remaining} | "
            f"Безопасни за общо потвърждение: {final_progress.safe_remaining} | "
            f"Групи за преглед: {group_remaining} | "
            f"Без валидна MASTER форма: {missing_master}"
        )
        geometry_state = (
            "ГОТОВО"
            if geometry_progress.passed
            else (
                f"БЛОКИРАНО — проблеми: {len(geometry_progress.problem_handles)}"
                if geometry_progress.problem_handles
                else f"провери още {geometry_progress.pending}"
            )
        )
        f006_state = (
            "ГОТОВО"
            if f006_progress.passed
            else (
                f"БЛОКИРАНО — проблеми: {len(f006_progress.problem_handles)}"
                if f006_progress.problem_handles
                else f"провери още {f006_progress.pending}"
            )
        )
        self.control_status_label.setText(
            f"Силни по геометрия — Контролна извадка: "
            f"{geometry_progress.approved}/{geometry_progress.total} | "
            f"Допустими за пакетно приемане след контрол: "
            f"{geometry_progress.bulk_remaining} | Статус: {geometry_state}<br>"
            f"Стремена F006 — Контролна извадка: "
            f"{f006_progress.approved}/{f006_progress.total} | "
            f"Допустими за пакетно приемане след контрол: "
            f"{f006_progress.bulk_remaining} | Статус: {f006_state}"
        )
        if geometry_progress.problem_handles or f006_progress.problem_handles:
            self.control_status_label.setStyleSheet(
                "font-weight:700; color:#991b1b; background:#fef2f2; "
                "padding:7px; border:1px solid #fecaca;"
            )
        elif geometry_progress.passed and f006_progress.passed:
            self.control_status_label.setStyleSheet(
                "font-weight:700; color:#166534; background:#f0fdf4; "
                "padding:7px; border:1px solid #bbf7d0;"
            )
        else:
            self.control_status_label.setStyleSheet(
                "font-weight:700; color:#713f12; background:#fffbeb; "
                "padding:7px; border:1px solid #fde68a;"
            )

    def _refresh_buttons(self) -> None:
        row = self.current_row()
        has_candidate = bool(row and row.get("candidate_handles"))
        # Reference-only geometry (see reference_geometry_handles) only
        # changes what "1. Покажи" shows -- approve_button/candidates_button
        # stay gated on a REAL candidate below; this is never promoted to one.
        has_reference_geometry = bool(row and row.get("reference_geometry_handles"))
        handle = self._handle(row) if row else ""
        decision = self._decision(handle) if handle else ""
        approved = bool(
            row
            and is_review_row_approved(
                row,
                decision_code=decision,
                approved_handles=self.approved_handles,
            )
        )
        missing_master = bool(row and (is_missing_master_row(row) or decision == "MISSING_MASTER"))
        simple_confirmation = bool(
            row
            and decision not in DIAGNOSTIC_REVIEW_DECISIONS
            and final_confirmation_eligible(row)
        )
        self.preview_button.setEnabled(row is not None)
        self.preview_button.setText(
            "1. Покажи" if (has_candidate or has_reference_geometry) else "Покажи надписа и избери"
        )
        self.candidates_button.setEnabled(bool(row and (has_candidate or row.get("alternative_candidates"))))
        self.approve_button.setEnabled(
            has_candidate
            and not approved
            and not missing_master
            and simple_confirmation
            and handle == self._previewed_handle
        )
        if row is not None and not approved and not simple_confirmation:
            self.approve_button.setText("Изисква инженерно решение")
            self.approve_button.setToolTip(
                "Позицията има конфликт, разлика, quantity flag, липсваща форма/геометрия "
                "или записан проблем. Използвай „Инженерно решение“."
            )
        else:
            self.approve_button.setText("Потвърди и следващ")
            self.approve_button.setToolTip("")
        self.flag_button.setText(
            "Липсва MASTER" if missing_master else "Проблем / бележка"
        )
        self.flag_button.setEnabled(row is not None)
        self.reject_button.setEnabled(row is not None and not missing_master)
        self.assign_form_button.setEnabled(row is not None)
        self.assign_form_button.setText(
            "Редактирай инженерно решение" if approved else "Инженерно решение"
        )
        self.select_geometry_button.setEnabled(row is not None)
        self.next_button.setEnabled(row is not None)
        geometry_progress = self._geometry_sample_progress()
        f006_progress = self._f006_sample_progress()
        self.bulk_button.setEnabled(self._sample_passed())
        self.geometry_bulk_button.setEnabled(
            geometry_progress.passed and geometry_progress.bulk_remaining > 0
        )
        self.f006_bulk_button.setEnabled(
            f006_progress.passed and f006_progress.bulk_remaining > 0
        )
        self.geometry_bulk_button.setText(
            f"Приеми {geometry_progress.bulk_remaining} силни по геометрия"
            if geometry_progress.passed
            else (
                f"Силни по геометрия — контрол "
                f"{geometry_progress.approved}/{geometry_progress.total}"
            )
        )
        self.f006_bulk_button.setText(
            f"Приеми {f006_progress.bulk_remaining} стремена F006"
            if f006_progress.passed
            else (
                f"Стремена F006 — контрол "
                f"{f006_progress.approved}/{f006_progress.total}"
            )
        )
        self.geometry_bulk_button.setToolTip(
            "" if geometry_progress.passed else self._control_block_message(
                "Силни по геометрия", geometry_progress
            )
        )
        self.f006_bulk_button.setToolTip(
            "" if f006_progress.passed else self._control_block_message(
                "Стремена F006", f006_progress
            )
        )
        final_progress = self._final_confirmation_progress()
        self.final_bulk_button.setEnabled(final_progress.ready_for_final_confirmation)
        if final_progress.engineering_remaining > 0:
            self.final_bulk_button.setText(
                f"Реши още {final_progress.engineering_remaining} инженерни казуса"
            )
            self.final_bulk_button.setToolTip(
                "Общото потвърждение не приема конфликти, липсващи форми, неясна геометрия или блокирани размери."
            )
        elif final_progress.safe_remaining > 0:
            self.final_bulk_button.setText(
                f"Потвърди останалите {final_progress.safe_remaining} безопасни"
            )
            self.final_bulk_button.setToolTip(
                "Изрично USER потвърждение; Engine/Matcher не се променят."
            )
        else:
            self.final_bulk_button.setText("Всички позиции са решени")
            self.final_bulk_button.setToolTip("")

    def _decision(self, handle: str) -> str:
        return review_decision_code(
            self._state.get("decisions", {}),
            str(handle),
        )

    def _is_resolved(self, row: dict) -> bool:
        handle = self._handle(row)
        payload = self._state.get("decisions", {}).get(handle, {})
        return is_review_row_resolved(
            row,
            decision_code=self._decision(handle),
            decision_payload=payload if isinstance(payload, dict) else {},
            approved_handles=self.approved_handles,
        )

    def _decision_label(self, handle: str, row: dict) -> str:
        decision = self._decision(handle)
        if decision == "MISSING_MASTER" or is_missing_master_row(row):
            return "ЛИПСВА MASTER"
        if decision in {"NO_VALID_CANDIDATE", "FLAGGED", "MISSING_MASTER", "PROBLEM_RECORDED"}:
            return "ПРОБЛЕМ — НЕРЕШЕН"
        if decision == "NON_PRODUCTION_POSITION":
            payload = self._state.get("decisions", {}).get(handle, {})
            return (
                "НЕПРОИЗВОДСТВЕНА"
                if is_valid_non_production_decision(payload)
                else "НЕПРОИЗВОДСТВЕНА — ЛИПСВА ДОКАЗАТЕЛСТВО"
            )
        if decision == "EXCLUDED_BY_ENGINEER":
            return "ИЗКЛЮЧЕНА — БЛОКИРА"
        if decision in {"MANUAL_ASSIGNED", "ENGINEER_RESOLVED"}:
            return "ИНЖЕНЕРНО РЕШЕНО"
        if decision == "APPROVED_BULK_FINAL_USER_CONFIRMATION":
            return "ОБЩО ПОТВЪРДЕНО"
        if handle in self.approved_handles or row.get("batch_status") == APPROVED_STATUS:
            return "ОДОБРЕНО"
        if handle in set(self.sample_handles):
            return "ИЗВАДКА"
        return "—"

    @staticmethod
    def _handle(row: dict | None) -> str:
        return str((row or {}).get("annotation_handle") or "")

    def fade_for_preview(self) -> None:
        """Make the modeless window transparent so it cannot hide the highlighted bar."""
        if self.embedded:
            self._faded_for_preview = False
            self.setWindowOpacity(1.0)
            return
        self._faded_for_preview = True
        self.setWindowOpacity(0.18)
        self.lower()
        parent = self.parentWidget()
        if parent is not None:
            parent.raise_()
            parent.activateWindow()

    def _restore_after_preview(self) -> None:
        if self.embedded:
            self._faded_for_preview = False
            self.setWindowOpacity(1.0)
            return
        if not self._faded_for_preview and abs(self.windowOpacity() - 1.0) < 1e-6:
            return
        self._faded_for_preview = False
        self.setWindowOpacity(1.0)
        self.raise_()
        self.activateWindow()

    def enterEvent(self, event) -> None:
        self._restore_after_preview()
        super().enterEvent(event)

    def showEvent(self, event) -> None:
        self._restore_after_preview()
        super().showEvent(event)

    def place_away_from(self, global_candidate_center: QPoint) -> None:
        if self.embedded:
            return
        screen = QGuiApplication.screenAt(global_candidate_center) or self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 12
        target_width = min(max(980, self.width()), max(760, available.width() - 2 * margin))
        target_height = min(470, max(360, available.height() - 2 * margin))
        self.resize(target_width, target_height)
        x = available.center().x() - target_width // 2
        x = max(available.left() + margin, min(x, available.right() - target_width - margin + 1))
        if global_candidate_center.y() >= available.center().y():
            y = available.top() + margin
        else:
            y = available.bottom() - target_height - margin + 1
        y = max(available.top() + margin, min(y, available.bottom() - target_height - margin + 1))
        self.move(x, y)
        self.raise_()
        self.activateWindow()

    def _apply_to_group_current(self) -> None:
        """Прилага решението на текущата позиция върху доказано еднаквите.

        Backend-ът съществуваше и беше тестван, но нямаше нито един
        извикващ — независим одит го отбеляза двукратно.
        """

        row = self.current_row()
        if row is None:
            return
        project_root = getattr(self, "project_root", None)
        identity = getattr(self, "data_identity", None)
        if project_root is None or identity is None:
            QMessageBox.warning(
                self, "Няма проектна идентичност",
                "Груповото решение изисква отворен проект с валидна "
                "data-identity.")
            return
        shape_code = str(row.get("master_shape_code") or "").strip()
        if not shape_code:
            QMessageBox.warning(
                self, "Няма форма",
                "Позицията няма разпозната MASTER форма. Реши я поотделно "
                "с „Инженерно решение“, преди да прилагаш към група.")
            return

        from lion_rebar.ui.apply_to_group_dialog import ApplyToGroupDialog

        changes = {"master_shape_code": shape_code}
        segments = row.get("production_segments_cm")
        if segments:
            changes["production_segments_cm"] = list(segments)
        dialog = ApplyToGroupDialog(
            row, list(self._rows), changes, Path(project_root), identity,
            operator=getattr(self, "operator_name", "") or "оператор",
            parent=self)
        if dialog.exec() and dialog.operation_id:
            self.decisionsChanged.emit()
            self._refresh_buttons()

    def _undo_last_group(self) -> None:
        """Връща последното групово решение.

        Диалогът обещаваше, че действието може да бъде върнато, но нямаше
        път до ``undo_group_operation`` — обещание без изпълнение.
        """

        project_root = getattr(self, "project_root", None)
        if project_root is None:
            QMessageBox.warning(self, "Няма проект",
                                "Отвори проект, преди да връщаш решения.")
            return
        from lion_rebar.review.apply_to_group import (
            GroupApplicationError,
            load_group_operations,
            undo_group_operation,
        )

        operations = load_group_operations(Path(project_root))
        applied = [item for item in operations
                   if item.get("action") == "APPLY_TO_GROUP"]
        undone = {item.get("operation_id") for item in operations
                  if item.get("action") == "UNDO_GROUP"}
        pending = [item for item in applied
                   if item.get("operation_id") not in undone]
        if not pending:
            QMessageBox.information(
                self, "Няма какво да се върне",
                "Няма приложено групово решение, което да не е върнато.")
            return
        target = pending[-1]
        count = len(target.get("affected_handles") or [])
        answer = QMessageBox.question(
            self, "Върни групово решение",
            f"Последното групово решение е засегнало {count} позиции.\n"
            f"Автор: {target.get('applied_by')}\n"
            f"Бележка: {target.get('note')}\n\n"
            "Да бъде ли върнато?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = undo_group_operation(
                Path(project_root), str(target.get("operation_id")),
                undone_by=getattr(self, "operator_name", "") or "оператор",
                note="връщане от Review Center")
        except GroupApplicationError as error:
            QMessageBox.critical(self, "Връщането е отказано", str(error))
            return
        QMessageBox.information(
            self, "Върнато",
            f"Възстановени са {restored} позиции.\n\n"
            "Финалният контрол е обезсилен — пусни го наново.")
        self.decisionsChanged.emit()
        self._refresh_buttons()

    def _apply_to_similar_current(self) -> None:
        """Прилага решението на текущата позиция върху всички доказано
        еднакви позиции в ЦЕЛИЯ отчет (по-широко от "Приложи към групата").

        Backend-ът (``review/apply_to_similar.py``) съществуваше и беше
        тестван за преглед, но нямаше нито един UI извикващ и нямаше
        функция за реално прилагане — същият клас дефект, който одитът
        намери за "Приложи към групата" преди Фаза 0.
        """

        row = self.current_row()
        if row is None:
            return
        project_root = getattr(self, "project_root", None)
        identity = getattr(self, "data_identity", None)
        if project_root is None or identity is None:
            QMessageBox.warning(
                self, "Няма проектна идентичност",
                "Действието изисква отворен проект с валидна data-identity.")
            return
        shape_code = str(row.get("master_shape_code") or "").strip()
        if not shape_code:
            QMessageBox.warning(
                self, "Няма форма",
                "Позицията няма разпозната MASTER форма. Реши я поотделно "
                "с „Инженерно решение“, преди да прилагаш към подобни.")
            return

        from lion_rebar.ui.apply_to_similar_dialog import ApplyToSimilarDialog

        changes = {"master_shape_code": shape_code}
        segments = row.get("production_segments_cm")
        if segments:
            changes["production_segments_cm"] = list(segments)
        dialog = ApplyToSimilarDialog(
            row, list(self._rows), changes, Path(project_root), identity,
            operator=getattr(self, "operator_name", "") or "оператор",
            parent=self)
        if dialog.exec() and dialog.operation_id:
            self.decisionsChanged.emit()
            self._refresh_buttons()

    def _undo_last_similar(self) -> None:
        """Връща последното "Приложи към всички подобни"."""

        project_root = getattr(self, "project_root", None)
        if project_root is None:
            QMessageBox.warning(self, "Няма проект",
                                "Отвори проект, преди да връщаш решения.")
            return
        from lion_rebar.review.apply_to_similar import (
            SimilarApplicationError,
            active_similar_operations,
            undo_similar_operation,
        )

        pending = active_similar_operations(Path(project_root))
        if not pending:
            QMessageBox.information(
                self, "Няма какво да се върне",
                "Няма приложено „Приложи към подобни“ решение, което да не "
                "е върнато.")
            return
        target = pending[-1]
        count = len(target.get("affected_handles") or [])
        answer = QMessageBox.question(
            self, "Върни решение за подобни",
            f"Последното действие е засегнало {count} позиции.\n"
            f"Автор: {target.get('applied_by')}\n"
            f"Бележка: {target.get('note')}\n\n"
            "Да бъде ли върнато?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = undo_similar_operation(
                Path(project_root), str(target.get("operation_id")),
                undone_by=getattr(self, "operator_name", "") or "оператор",
                note="връщане от Review Center")
        except SimilarApplicationError as error:
            QMessageBox.critical(self, "Връщането е отказано", str(error))
            return
        QMessageBox.information(
            self, "Върнато",
            f"Възстановени са {restored} позиции.\n\n"
            "Финалният контрол е обезсилен — пусни го наново.")
        self.decisionsChanged.emit()
        self._refresh_buttons()

    def _undo_last_bulk_approval(self) -> None:
        """Връща последното от четирите масови "Приеми останалите ..." одобрения.

        Project Instructions, т. 12: "undo / audit trail за масовите
        решения" — до тази поправка масовите одобрения (силни / по
        геометрия / F006 / финално потвърждение) нямаха никакъв път назад.
        """

        project_root = getattr(self, "project_root", None)
        if project_root is None:
            QMessageBox.warning(self, "Няма проект",
                                "Отвори проект, преди да връщаш решения.")
            return
        from lion_rebar.review.bulk_approval_operations import (
            BulkApprovalError,
            active_bulk_operations,
            undo_bulk_operation,
        )

        pending = active_bulk_operations(Path(project_root))
        if not pending:
            QMessageBox.information(
                self, "Няма какво да се върне",
                "Няма масово одобрение, което да не е върнато.")
            return
        target = pending[-1]
        count = len(target.get("affected_handles") or [])
        mode_labels = {
            "STRICT_SAMPLE": "Приеми останалите валидни силни",
            "GEOMETRY_SAMPLE": "Приеми останалите силни по геометрия",
            "F006_SAMPLE": "Приеми останалите стремена F006",
            "FINAL_USER_CONFIRMATION": "Потвърди всички останали безопасни",
        }
        mode_label = mode_labels.get(str(target.get("mode") or ""), str(target.get("mode") or ""))
        answer = QMessageBox.question(
            self, "Върни масово одобрение",
            f"Последната масова операция („{mode_label}“) е засегнала {count} позиции.\n"
            f"Автор: {target.get('applied_by')}\n\n"
            "Да бъде ли върната?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = undo_bulk_operation(
                Path(project_root), str(target.get("operation_id")),
                undone_by=getattr(self, "operator_name", "") or "оператор",
                note="връщане от Review Center")
        except BulkApprovalError as error:
            QMessageBox.critical(self, "Връщането е отказано", str(error))
            return
        # ledger и review_center_state.json вече са байт-точно възстановени
        # на диска от undo_bulk_operation -- диалогът трябва да отрази
        # точно това, не остарялото си in-memory състояние.
        self._load_and_apply_state(self._read_approved_links_from_disk())
        self._invalidate_derived_state()
        self._rebuild_table()
        QMessageBox.information(
            self, "Върнато",
            f"Възстановени са {restored} позиции.\n\n"
            "Финалният контрол е обезсилен — пусни го наново.")
        self.decisionsChanged.emit()
        self._refresh_buttons()

    def _open_html_report(self) -> None:
        candidate = self.report.path.with_suffix(".html")
        if not candidate.is_file():
            candidates = sorted(self.report.path.parent.glob("*_geometry_batch.html"))
            candidate = candidates[0] if candidates else candidate
        if not candidate.is_file():
            QMessageBox.information(self, "Review Center", "HTML отчетът не е намерен.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))

    @staticmethod
    def _log_timing(
        operation: str,
        started: float,
        *,
        rows: int,
        extra: str = "",
    ) -> None:
        elapsed_ms = (perf_counter() - started) * 1000.0
        suffix = f"; {extra}" if extra else ""
        message = "Review Center timing: %s %.1f ms; rows=%d%s"
        if elapsed_ms >= 50.0:
            LOGGER.info(message, operation, elapsed_ms, rows, suffix)
        else:
            LOGGER.debug(message, operation, elapsed_ms, rows, suffix)


def _fmt(value) -> str:
    try:
        if value is None or value == "":
            return "—"
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return str(round(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _escape(value) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
