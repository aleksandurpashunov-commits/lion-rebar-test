from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QDoubleValidator,
    QGuiApplication,
    QIntValidator,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
    QVBoxLayout,
)

from lion_rebar.config.constants import APP_VERSION
from lion_rebar.engine.bend_allowance import (
    explain_mismatch,
    segments_match_total,
)
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.engineer_decision import (
    DECLARED_BEND_ALLOWANCE_MAX_CM,
    REASON_OPTIONS,
    declared_bend_allowance,
    classify_engineer_action,
    engineer_note_policy,
    engineer_reason_from_problem,
    parse_segment_text,
    positive_float,
    positive_int,
    production_annotation_text,
    reason_label,
)
from lion_rebar.export.engine_shape_parameters import parameters_for_shape
from lion_rebar.export.errors import ExcelExportError
from lion_rebar.review.decision_reuse import (
    ПРИЛОЖИМО,
    ПРОТИВОРЕЧИЕ,
    САМО_ФОРМА,
    Предложение,
    ПредложениетоНеЕПриложимо,
)
from lion_rebar.review.decision_suggestions import (
    CONFLICT,
    READY,
    DecisionSuggestion,
    ExplicitSuggestionAcceptanceRequired,
    SuggestionNotAcceptable,
)
from lion_rebar.ui.shape_preview import ShapePreview


_GEOMETRYLESS_REASON_CODES = frozenset(
    {
        "NO_GEOMETRY",
        "WRONG_GEOMETRY",
        "WRONG_OBJECT",
        "PARTIAL_GEOMETRY",
        "DUPLICATE_GEOMETRY",
    }
)


class _SegmentLineEdit(QLineEdit):
    focused = Signal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.segment_key = str(key).upper()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self.focused.emit(self.segment_key)


class EngineerDecisionDialog(QDialog):
    """Complete owner decision for one actionable rebar annotation."""

    _DEFAULT_WIDTH = 780
    _DEFAULT_HEIGHT = 580
    _MIN_WIDTH = 560
    _MIN_HEIGHT = 420
    _SCREEN_MARGIN = 24
    _GEOMETRY_SETTINGS_KEY = "engineer_decision/geometry"

    def __init__(
        self,
        *,
        row: dict,
        shapes: Iterable[ShapeDefinition],
        existing_link: dict | None = None,
        problem_decision: dict | None = None,
        engine_version: str = "",
        project_name: str = "",
        project_version: str = "",
        decision_suggestion: DecisionSuggestion | None = None,
        reuse_proposal: "Предложение | None" = None,
        reuse_index=None,
        parent=None,
    ):
        super().__init__(parent)
        self.row = dict(row)
        self.existing_link = dict(existing_link or {})
        self.problem_decision = dict(problem_decision or {})
        self.engine_version = str(engine_version or "")
        self.project_name = str(project_name or "")
        self.project_version = str(project_version or "")
        self.decision_suggestion = decision_suggestion
        self._decision_suggestion_accepted = False
        self._accepted_suggestion_fields: dict = {}
        self._applying_decision_suggestion = False
        # ПРЕИЗПОЛЗВАНЕ с разделени етапи. ``reuse_index`` е нужен, за да се
        # провери НЕПОСРЕДСТВЕНО преди приемането дали основанието още е в
        # сила: диалогът може да е отворен отпреди оттеглянето.
        self.reuse_proposal = reuse_proposal
        self.reuse_index = reuse_index
        self._reuse_accepted = False
        self._reuse_accepted_fields: dict = {}
        self._reuse_refused_reason = ""
        self.shapes = tuple(
            shape
            for shape in shapes
            if shape.active and str(shape.state).upper() != "RETIRED"
        )
        self._shape_by_code = {str(shape.code).upper(): shape for shape in self.shapes}
        self.segment_edits: dict[str, _SegmentLineEdit] = {}
        self._segment_source_labels: dict[str, QLabel] = {}
        self._segment_indexes_by_key: dict[str, tuple[int, ...]] = {}
        self._segment_input_loading = False
        self._quick_parse_error = ""
        self._initial_segments: tuple[float, ...] = ()
        self._initial_segment_values_by_key: dict[str, float] = {}
        self.result_payload: dict | None = None
        # MainWindow already owns the persistent QSettings instance. Reuse it
        # when available instead of creating a second settings store for this
        # short-lived modal dialog. Standalone/tests remain side-effect free.
        self._ui_settings = getattr(parent, "_ui_settings", None)
        self.setWindowTitle("Инженерно решение — пълна производствена спецификация")
        self._configure_initial_size()

        intro = QLabel(
            "Задай окончателната производствена истина. След запис позицията става "
            "ENGINEER_RESOLVED и участва във Финален контрол, Excel и DXF. "
            "Engine/Matcher не се променят автоматично."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("background:#eef2ff; border:1px solid #c7d2fe; padding:6px;")

        self.reuse_frame = self._build_reuse_frame()
        self.suggestion_frame = self._build_decision_suggestion_frame()
        if self.reuse_proposal is not None:
            # Едно поле за предложения, не две. Новият път е водещият: той
            # казва и ЗАЩО случаят е намерен, и кое поле от какво е подкрепено.
            self.suggestion_frame.hide()

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(5)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.form_layout = form
        source_text = str(row.get("annotation_text") or "")
        source_label = QLabel(source_text or "—")
        source_label.setWordWrap(True)
        source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Оригинален надпис:", source_label)

        problem_text = self._problem_text()
        if problem_text:
            problem_label = QLabel(problem_text)
            problem_label.setWordWrap(True)
            problem_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            problem_label.setStyleSheet(
                "background:#fff7ed; border:1px solid #fed7aa; padding:7px; color:#7c2d12;"
            )
            form.addRow("Записан проблем:", problem_label)

        self.shape_combo = QComboBox()
        self.shape_combo.setEditable(True)
        self.shape_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.shape_combo.addItem("— Избери MASTER форма —", "")
        for shape in sorted(self.shapes, key=lambda item: (item.code, item.name)):
            self.shape_combo.addItem(f"{shape.code} — {shape.name}", shape.code)
        completer = self.shape_combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        # Предложената форма се ползва САМО когато няма разпозната. Тя идва
        # от котировките в чертежа и вече е показана в Review Center;
        # операторът не бива да я преписва на ръка от единия екран в другия.
        preferred = str(
            self.existing_link.get("master_shape_code")
            or row.get("master_shape_code")
            or row.get("suggested_shape_code")
            or ""
        )
        self._is_suggestion = bool(
            not self.existing_link.get("master_shape_code")
            and not row.get("master_shape_code")
            and row.get("suggested_shape_code")
        )
        index = self.shape_combo.findData(preferred)
        if index >= 0:
            self.shape_combo.setCurrentIndex(index)
        self._last_shape_code = preferred.upper()
        self.shape_combo.currentIndexChanged.connect(self._shape_changed)
        form.addRow("MASTER форма:", self.shape_combo)

        self.shape_hint = QLabel()
        self.shape_hint.setWordWrap(True)
        form.addRow("Параметри на формата:", self.shape_hint)

        self.count_edit = QLineEdit(
            str(
                _int_value(self.existing_link.get("production_count"))
                or _int_value(self.existing_link.get("annotation_count"))
                or _int_value(row.get("annotation_count"))
                or ""
            )
        )
        self.count_edit.setValidator(QIntValidator(1, 1_000_000, self))
        self.count_edit.textChanged.connect(self._refresh)
        form.addRow("Производствен брой:", self.count_edit)

        self.diameter_edit = QLineEdit(
            _text_number(
                self.existing_link.get("production_diameter_mm")
                if self.existing_link.get("production_diameter_mm") is not None
                else self.existing_link.get("diameter_mm")
                if self.existing_link.get("diameter_mm") is not None
                else row.get("diameter_mm")
            )
        )
        self.diameter_edit.setValidator(QDoubleValidator(0.1, 100.0, 3, self))
        self.diameter_edit.textChanged.connect(self._refresh)
        form.addRow("Производствен Ø [mm]:", self.diameter_edit)

        preferred_length = (
            self.existing_link.get("production_length_cm")
            if self.existing_link.get("production_length_cm") is not None
            else row.get("declared_length_cm")
        )
        self.length_edit = QLineEdit(_text_number(preferred_length))
        self.length_edit.setValidator(QDoubleValidator(0.1, 20_000.0, 3, self))
        self.length_edit.textChanged.connect(self._refresh)
        form.addRow("Обща производствена L [cm]:", self.length_edit)

        # ЗАЯВЕНА ДОБАВКА ЗА ОГЪВАНЕ. Празно = важат само измерените правила
        # (закръглянето до 5 и таваните на F006/F009). Числото се приема само
        # когато ТОЧНО затваря уравнението сбор + добавка = L, и се записва
        # като част от решението, с автор и време. Виж
        # ``engine.bend_allowance.declared_allowance_closes``.
        self.bend_allowance_edit = QLineEdit("")
        self.bend_allowance_edit.setPlaceholderText(
            "празно = по правилата на програмата"
        )
        self.bend_allowance_edit.setValidator(
            QDoubleValidator(0.0, DECLARED_BEND_ALLOWANCE_MAX_CM, 2, self)
        )
        self.bend_allowance_edit.setToolTip(
            "Число, с което ТОЗИ детайл затваря: сбор на участъците + добавка = "
            "обща дължина. Приема се само ако наистина затваря уравнението."
        )
        self.bend_allowance_edit.textChanged.connect(self._refresh)
        form.addRow("Заявена добавка за огъване [cm]:", self.bend_allowance_edit)

        segments = (
            self.existing_link.get("production_segment_lengths_cm")
            or self.existing_link.get("canonical_segment_lengths_cm")
            or row.get("production_segments_cm")
            or row.get("canonical_segment_lengths_cm")
            or []
        )
        suggested_segments = list(row.get("suggested_segments_cm") or [])
        if not segments and suggested_segments:
            # F006 се въвежда като шест сегмента: кука, страна, страна,
            # страна, страна, кука. Предложението идва като три размера
            # (широчина, височина, кука) и се разгъва тук.
            if len(suggested_segments) == 3 and str(
                    row.get("suggested_shape_code") or "").upper() == "F006":
                width, height, hook = suggested_segments
                segments = [hook, height, width, height, width, hook]
            else:
                segments = suggested_segments
        self._initial_segments = tuple(
            value
            for raw in segments
            for value in [positive_float(raw)]
            if value is not None
        )
        self.segment_fields_widget = QWidget(self)
        self.segment_fields_layout = QGridLayout(self.segment_fields_widget)
        self.segment_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.segment_fields_layout.setHorizontalSpacing(8)
        self.segment_fields_layout.setVerticalSpacing(5)
        form.addRow("Сегменти по MASTER [cm]:", self.segment_fields_widget)

        self.segments_edit = QLineEdit(" + ".join(_text_number(value) for value in self._initial_segments))
        self.segments_edit.setPlaceholderText("Бързо въвеждане: 40 + 14 + 40")
        if getattr(self, "_is_suggestion", False):
            self.segments_edit.setStyleSheet("background:#eff6ff;")
            self.segments_edit.setToolTip(
                "Предложение от котировките в чертежа. Провери го срещу "
                "оригинала, преди да запишеш."
            )
        self.segments_edit.textChanged.connect(self._quick_segments_changed)
        form.addRow("Бързо въвеждане:", self.segments_edit)

        candidate_handles = [str(v) for v in row.get("candidate_handles", []) if str(v)]
        existing_handles = [str(v) for v in self.existing_link.get("geometry_handles", []) if str(v)]
        self._force_geometryless = bool(
            row.get("force_geometryless_engineer_decision")
        )
        self._available_handles = (
            [] if self._force_geometryless else existing_handles or candidate_handles
        )
        self.use_candidate_check = QCheckBox("Използвай показаната/избраната DXF геометрия")
        self.use_candidate_check.setChecked(bool(self._available_handles))
        self.use_candidate_check.setEnabled(bool(self._available_handles))
        self.use_candidate_check.setToolTip(
            "Handles: " + (", ".join(self._available_handles) if self._available_handles else "няма")
        )
        self.use_candidate_check.stateChanged.connect(self._refresh)
        form.addRow("Геометрия:", self.use_candidate_check)
        geometry_notice = str(row.get("manual_geometry_notice") or "").strip()
        if not geometry_notice and not self._available_handles:
            geometry_notice = (
                "Няма сигурна производствена ос в DXF. F006/F007 може да се зададе "
                "изрично без geometry handle, но само с точни производствени "
                "сегменти, коригираща причина и инженерна бележка."
            )
        if geometry_notice:
            self.geometry_notice_label = QLabel(geometry_notice)
            self.geometry_notice_label.setWordWrap(True)
            self.geometry_notice_label.setStyleSheet(
                "background:#ecfeff; border:1px solid #67e8f9; "
                "padding:7px; color:#155e75;"
            )
            form.addRow("Контрол на DXF:", self.geometry_notice_label)

        self.reason_combo = QComboBox()
        for code, label in REASON_OPTIONS:
            self.reason_combo.addItem(label, code)
        existing_decision = self.existing_link.get("engineer_decision")
        existing_reason = (
            existing_decision.get("reason_code")
            if isinstance(existing_decision, dict)
            else None
        )
        default_reason = (
            existing_reason
            or self._mapped_problem_reason()
            or ("CONFIRM_CORRECT" if candidate_handles else "NO_GEOMETRY")
        )
        idx = self.reason_combo.findData(default_reason)
        if idx >= 0:
            self.reason_combo.setCurrentIndex(idx)
        self.reason_combo.currentIndexChanged.connect(self._refresh)
        form.addRow("Причина:", self.reason_combo)

        self.action_label = QLabel("—")
        self.action_label.setStyleSheet("font-weight:700; color:#1e3a8a;")
        form.addRow("Тип знание:", self.action_label)

        note_container = QWidget(self)
        note_layout = QVBoxLayout(note_container)
        note_layout.setContentsMargins(0, 0, 0, 0)
        note_layout.setSpacing(4)
        self.note_requirement_label = QLabel()
        self.note_requirement_label.setWordWrap(True)
        note_layout.addWidget(self.note_requirement_label)
        self.note_toggle_button = QPushButton("Добави бележка")
        self.note_toggle_button.setCheckable(True)
        self.note_toggle_button.setMaximumWidth(190)
        note_layout.addWidget(self.note_toggle_button)
        self.note_edit = QPlainTextEdit()
        existing_note = (
            existing_decision.get("operator_note", existing_decision.get("note"))
            if isinstance(existing_decision, dict)
            else ""
        )
        self.note_edit.setPlainText(str(existing_note or ""))
        self.note_edit.setPlaceholderText(
            "Операторска бележка — попълва се само когато е нужна или полезна."
        )
        self.note_edit.setMaximumHeight(80)
        self.note_edit.setVisible(bool(existing_note))
        self.note_toggle_button.setChecked(bool(existing_note))
        self.note_toggle_button.toggled.connect(self.note_edit.setVisible)
        self.note_edit.textChanged.connect(self._refresh)
        note_layout.addWidget(self.note_edit)
        form.addRow("Бележка:", note_container)

        self.confirm_check = QCheckBox(
            "Потвърждавам окончателното инженерно решение"
        )
        self.confirm_check.setToolTip(
            "Потвърждавам изключението и окончателната производствена спецификация"
        )
        self.confirm_check.stateChanged.connect(self._refresh)
        self.confirm_check.hide()

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Запиши инженерното решение")
        self.save_button.setToolTip("Запиши инженерното решение (Ctrl+Enter)")
        self.save_button.setEnabled(False)
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.cancel_button.setText("Отказ")
        self.cancel_button.setToolTip("Отказ (Esc)")
        self.button_box.accepted.connect(self._accept)
        self.button_box.rejected.connect(self.reject)

        self.shape_preview = ShapePreview(self)
        self.shape_preview.setMinimumHeight(150)
        self.shape_preview.setMaximumHeight(200)
        self.shape_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.shape_preview.segmentSelected.connect(self._preview_segment_selected)

        # Only the data-heavy body scrolls. Confirmation, validation status
        # and action buttons stay visible even on a 1366x768 laptop with
        # Windows display scaling enabled.
        self.scroll_content = QWidget(self)
        content_layout = QVBoxLayout(self.scroll_content)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(6)
        content_layout.addWidget(intro)
        content_layout.addWidget(self.reuse_frame)
        content_layout.addWidget(self.suggestion_frame)
        content_layout.addLayout(form)
        content_layout.addWidget(self.shape_preview)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("engineerDecisionScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setWidget(self.scroll_content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.confirm_check)
        layout.addWidget(self.status_label)
        layout.addWidget(self.button_box)

        self._save_shortcuts: list[QShortcut] = []
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._accept_if_ready)
            self._save_shortcuts.append(shortcut)
        self._cancel_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._cancel_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._cancel_shortcut.activated.connect(self.reject)

        self._shape_changed()
        self._restore_window_geometry()

    # -- ПРЕИЗПОЛЗВАНЕ: показване, приемане, отказ ------------------------
    def _build_reuse_frame(self) -> QFrame:
        """Предложението от предишно решение — видимо и обяснено.

        Показва петте неща, които операторът трябва да види, преди да реши:
        какво се предлага, от кое решение и чертеж идва, защо случаят е намерен
        за сходен, кое поле е подкрепено от ТОЗИ чертеж и какво остава за него.

        Бутон за приемане се показва САМО при ``ПРИЛОЖИМО``. Видимо
        предложение, което не може да бъде ползвано, не се предлага за
        приемане — то е само сведение, че за този надпис има предишно решение
        и защо то не стига.
        """

        frame = QFrame(self)
        frame.setObjectName("decisionReuseProposal")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(5)
        self.reuse_label = QLabel(frame)
        self.reuse_label.setWordWrap(True)
        self.reuse_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.reuse_label)
        self.reuse_accept_button = QPushButton("Приеми предложението", frame)
        self.reuse_accept_button.setMaximumWidth(260)
        self.reuse_accept_button.clicked.connect(self._accept_reuse_proposal)
        layout.addWidget(self.reuse_accept_button)

        предложение = self.reuse_proposal
        if предложение is None or предложение.състояние not in {
            ПРИЛОЖИМО, САМО_ФОРМА, ПРОТИВОРЕЧИЕ,
        }:
            frame.hide()
            return frame
        frame.show()
        self.reuse_label.setText(self._reuse_text(предложение))
        if предложение.състояние == ПРИЛОЖИМО:
            frame.setStyleSheet(
                "QFrame#decisionReuseProposal {"
                "background:#ecfdf5; border:1px solid #34d399;}"
            )
        else:
            frame.setStyleSheet(
                "QFrame#decisionReuseProposal {"
                "background:#fff7ed; border:1px solid #fb923c;}"
            )
            self.reuse_accept_button.hide()
        return frame

    def _reuse_text(self, предложение: "Предложение") -> str:
        редове = [
            "ПРЕИЗПОЛЗВАНЕ НА ПРЕДИШНО РЕШЕНИЕ — %s. %s"
            % (предложение.състояние, предложение.обяснение),
        ]
        случай = предложение.случай
        if случай is not None:
            редове.append(
                "Какво се предлага: форма %s (%s), %d рамена; правило за "
                "разчитане: %s."
                % (случай.форма.код, случай.форма.име, случай.форма.рамена,
                   случай.правило)
            )
            редове.append(
                "Размерите на донора (%s) НЕ се пренасят."
                % "/".join(
                    _text_number(стойност)
                    for стойност in случай.донорски_размери_cm
                )
            )
        if предложение.произход:
            произход = предложение.произход[0]
            редове.append(
                "Произход: решение %s от %s, чертеж „%s“, позиция %s."
                % (произход.event_id[:8] or "—", произход.occurred_at or "—",
                   произход.проект or "—", произход.handle or "—")
            )
        if предложение.защо_е_намерен:
            редове.append(
                "Защо е намерен за сходен: " + "; ".join(предложение.защо_е_намерен)
            )
        if предложение.полета:
            редове.append("Поле по поле:")
            for поле in предложение.полета:
                редове.append(
                    "  • %s = %s — %s (източник: %s). %s"
                    % (поле.име, поле.стойност, поле.присъда, поле.източник,
                       поле.обяснение)
                )
        if предложение.остава_за_оператора:
            редове.append(
                "Остава за теб: " + "; ".join(предложение.остава_за_оператора)
            )
        if self._reuse_refused_reason:
            редове.append("ОТКАЗ: " + self._reuse_refused_reason)
        return "\n".join(редове)

    def _accept_reuse_proposal(self) -> None:
        """Изрично приемане. Проверява състоянието НЕПОСРЕДСТВЕНО преди това."""

        предложение = self.reuse_proposal
        if предложение is None:
            return
        # Диалогът може да е отворен ПРЕДИ оттеглянето на решението-основание.
        # Затова тук се пита наново, вместо да се вярва на заснетото състояние.
        if self.reuse_index is not None:
            try:
                отпаднали = self.reuse_index.отпаднало_основание(
                    предложение.основание()
                )
            except Exception:                                  # noqa: BLE001
                отпаднали = ()
            if отпаднали:
                self._reuse_refused_reason = (
                    "решението-основание вече не е в сила (оттеглено или "
                    "отменено): " + ", ".join(отпаднали)
                    + ". Предложението НЕ е приложено."
                )
                self.reuse_accept_button.setEnabled(False)
                self.reuse_label.setText(self._reuse_text(предложение))
                self.reuse_label.setStyleSheet("color:#b91c1c; font-weight:700;")
                return
        try:
            стойности = предложение.за_приемане(изрично_прието=True)
        except ПредложениетоНеЕПриложимо as грешка:
            self._reuse_refused_reason = str(грешка)
            self.reuse_accept_button.setEnabled(False)
            self.reuse_label.setText(self._reuse_text(предложение))
            self.reuse_label.setStyleSheet("color:#b91c1c; font-weight:700;")
            return

        код = str(стойности.get("shape_code") or "").upper()
        индекс = self.shape_combo.findData(код)
        форма = self._shape_by_code.get(код)
        if индекс < 0 or форма is None:
            self._reuse_refused_reason = (
                "предложената форма %s не е активна в заключения Engine — "
                "избери ръчно" % (код or "—")
            )
            self.reuse_accept_button.setEnabled(False)
            self.reuse_label.setText(self._reuse_text(предложение))
            self.reuse_label.setStyleSheet("color:#b91c1c; font-weight:700;")
            return

        участъци = tuple(
            float(стойност)
            for стойност in стойности.get("segment_lengths_cm") or ()
        )
        self._applying_decision_suggestion = True
        try:
            self.shape_combo.setCurrentIndex(индекс)
            self._last_shape_code = код
            self._initial_segments = участъци
            self._rebuild_segment_fields(форма, участъци)
            self.shape_preview.set_shape(форма)
            for етикет in self._segment_source_labels.values():
                if "MASTER" not in етикет.text():
                    етикет.setText("от данните на ТАЗИ позиция")
            self.count_edit.setText(str(int(стойности["count"])))
            self.diameter_edit.setText(_text_number(стойности["diameter_mm"]))
            self.length_edit.setText(
                _text_number(стойности["production_length_cm"])
            )
            индекс_причина = self.reason_combo.findData(
                str(стойности.get("reason_code") or "")
            )
            if индекс_причина >= 0:
                self.reason_combo.setCurrentIndex(индекс_причина)
            if стойности.get("geometry_mode") == "NO_DXF_GEOMETRY":
                # Изрично СЛЕД натискане, не като страничен ефект от показването.
                self.use_candidate_check.setChecked(False)
            # Бележката на донора НЕ се копира: иска се нова за ТОЗИ случай.
            self.note_toggle_button.setChecked(True)
        finally:
            self._applying_decision_suggestion = False

        self._reuse_accepted = True
        self._reuse_accepted_fields = self._suggestion_fields_from_prefill(
            стойности
        )
        # ПРАЗНИЯТ ЛИСТ отпада, преценката — не.
        #
        # При решение без DXF геометрия бележката е задължителна
        # (`NO_RELIABLE_GEOMETRY`) и това правило е отпреди преизползването:
        # важи и когато операторът решава сам. Но да го караме да пише на ръка
        # „вярно" при всяко приемане е търкане без стойност — а и такава
        # бележка не казва нищо на следващия, който ще чете журнала.
        #
        # Затова се предлага ПРОСЛЕДИМ ФАКТ: откъде идва формата и откъде идва
        # размерът. Нарочно НЕ съдържа твърдение от името на оператора (няма
        # „проверено", няма „вярно") — това си остава негова дума и той може да
        # я допише. Бележката на донора НЕ се копира никога.
        if not self.note_edit.toPlainText().strip():
            self.note_edit.setPlainText(self._предложена_бележка(стойности))
        self.reuse_label.setText(
            "Предложението е приложено след изрично действие. Провери формата, "
            "размерите и причината — можеш да редактираш всичко преди запис. "
            "Геометрия, бележка и принадлежност НЕ са копирани. Бележката е "
            "попълнена с произхода — допълни я, ако има какво да добавиш."
        )
        self.reuse_accept_button.setText("Предложението е приложено")
        self.reuse_accept_button.setEnabled(False)
        self._refresh()

    def _предложена_бележка(self, стойности: dict) -> str:
        """Проследим факт за журнала, не твърдение от името на оператора."""

        предложение = self.reuse_proposal
        случай = getattr(предложение, "случай", None)
        if предложение is None or случай is None:
            return ""
        произход = предложение.произход[0] if предложение.произход else None
        части = [
            "Прието предложение за преизползване.",
        ]
        if произход is not None:
            части.append(
                "Източник: решение %s от %s, чертеж „%s“, позиция %s."
                % (
                    (произход.event_id or "—")[:8],
                    произход.occurred_at or "—",
                    произход.проект or "—",
                    произход.handle or "—",
                )
            )
        по_име = {поле.име: поле for поле in предложение.полета}
        участъци = по_име.get("segment_lengths_cm")
        части.append(
            "Формата %s идва от предишното решение; размерите НЕ са на донора."
            % случай.форма.код
        )
        if участъци is not None:
            части.append(
                "Участъци %s — източник: %s."
                % (
                    " + ".join(
                        _text_number(стойност)
                        for стойност in (участъци.стойност or ())
                    ) or "—",
                    участъци.източник,
                )
            )
        if случай.без_геометрия:
            части.append(
                "Позицията няма начертана геометрия — решението стъпва на "
                "надписа и на инженерна преценка."
            )
        return " ".join(части)

    def _reuse_audit(self, values: dict) -> dict | None:
        """Одитът на преизползването — влиза в плика на решението."""

        предложение = self.reuse_proposal
        if предложение is None or предложение.състояние not in {
            ПРИЛОЖИМО, САМО_ФОРМА, ПРОТИВОРЕЧИЕ,
        }:
            return None
        if предложение.състояние != ПРИЛОЖИМО:
            изход = "ПОКАЗАНО_НЕПРИЛОЖИМО"
        elif not self._reuse_accepted:
            изход = "ПОКАЗАНО_НЕПОЛЗВАНО"
        else:
            крайни = self._suggestion_fields_from_values(values)
            изход = (
                "ПРИЕТО_БЕЗ_ПРОМЯНА"
                if крайни == self._reuse_accepted_fields
                else "ПРИЕТО_И_РЕДАКТИРАНО"
            )
        одит = {
            "схема": "lion-rebar-preizpolzvane-ui-odit/v1",
            "показано": True,
            "състояние": предложение.състояние,
            "изход": изход,
            "изрично_прието": self._reuse_accepted,
            "отказ": self._reuse_refused_reason or None,
            # ОСНОВАНИЕТО се записва винаги, когато е прието: по него се
            # намира какво да се провери наново, ако решението бъде оттеглено.
            "основание": list(предложение.основание()) if self._reuse_accepted else [],
            "предложение": предложение.речник(),
        }
        if self._reuse_accepted:
            try:
                одит["приемане"] = предложение.одит()
            except ПредложениетоНеЕПриложимо:
                одит["приемане"] = None
        return одит

    def _build_decision_suggestion_frame(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("previousEngineerDecisionSuggestion")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(8, 7, 8, 7)
        frame_layout.setSpacing(5)
        self.suggestion_label = QLabel(frame)
        self.suggestion_label.setWordWrap(True)
        self.suggestion_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        frame_layout.addWidget(self.suggestion_label)
        self.use_suggestion_button = QPushButton(
            "Използвай предложението", frame
        )
        self.use_suggestion_button.setMaximumWidth(230)
        self.use_suggestion_button.clicked.connect(
            self._apply_decision_suggestion
        )
        frame_layout.addWidget(self.use_suggestion_button)

        suggestion = self.decision_suggestion
        if suggestion is None or suggestion.status not in {READY, CONFLICT}:
            frame.hide()
            return frame
        frame.show()
        if suggestion.status == CONFLICT:
            frame.setStyleSheet(
                "QFrame#previousEngineerDecisionSuggestion {"
                "background:#fff7ed; border:1px solid #fb923c;}"
            )
            self.suggestion_label.setText(
                "Предишни инженерни решения: КОНФЛИКТ. "
                + suggestion.explanation
                + " Избери решението ръчно."
            )
            self.use_suggestion_button.hide()
            return frame

        proposal = suggestion.proposal
        assert proposal is not None
        segment_text = " + ".join(
            _text_number(value) for value in proposal.segment_lengths_cm
        )
        source_text = ""
        if suggestion.provenance:
            source = suggestion.provenance[0]
            source_text = (
                f" Източник: {source.project_name or 'проект —'}, "
                f"{source.occurred_at or 'дата —'}, "
                f"позиция {source.annotation_handle or '—'}."
            )
        self.suggestion_label.setText(
            "Предишно инженерно решение — само предложение; нищо не е "
            "попълнено автоматично. "
            f"{proposal.shape_code} | {proposal.count} бр. | "
            f"Ø{_text_number(proposal.diameter_mm)} | "
            f"L={_text_number(proposal.production_length_cm)} cm | "
            f"{segment_text}.{source_text}"
        )
        frame.setStyleSheet(
            "QFrame#previousEngineerDecisionSuggestion {"
            "background:#ecfdf5; border:1px solid #34d399;}"
        )
        return frame

    def _apply_decision_suggestion(self) -> None:
        suggestion = self.decision_suggestion
        if suggestion is None:
            return
        try:
            prefill = suggestion.accepted_prefill(
                explicitly_accepted=True,
            )
        except (ExplicitSuggestionAcceptanceRequired, SuggestionNotAcceptable) as exc:
            self.suggestion_label.setText(str(exc))
            self.suggestion_label.setStyleSheet("color:#b91c1c; font-weight:700;")
            return

        shape_code = str(prefill.get("shape_code") or "").upper()
        shape_index = self.shape_combo.findData(shape_code)
        shape = self._shape_by_code.get(shape_code)
        if shape_index < 0 or shape is None:
            self.suggestion_label.setText(
                f"Предложението сочи {shape_code or 'неизвестна форма'}, която "
                "не е активна в заключения Engine. Избери ръчно."
            )
            self.suggestion_label.setStyleSheet("color:#b91c1c; font-weight:700;")
            self.use_suggestion_button.setEnabled(False)
            return

        segments = tuple(float(value) for value in prefill.get("segment_lengths_cm") or ())
        self._applying_decision_suggestion = True
        try:
            self.shape_combo.setCurrentIndex(shape_index)
            self._last_shape_code = shape_code
            self._initial_segments = segments
            self._rebuild_segment_fields(shape, segments)
            self.shape_preview.set_shape(shape)
            for label in self._segment_source_labels.values():
                if "MASTER" not in label.text():
                    label.setText("прието предишно инженерно решение")
            self.count_edit.setText(str(int(prefill["count"])))
            self.diameter_edit.setText(_text_number(prefill["diameter_mm"]))
            self.length_edit.setText(
                _text_number(prefill["production_length_cm"])
            )
            reason_index = self.reason_combo.findData(
                str(prefill.get("reason_code") or "")
            )
            if reason_index >= 0:
                self.reason_combo.setCurrentIndex(reason_index)
            if prefill.get("geometry_mode") == "NO_DXF_GEOMETRY":
                # This is an explicit post-click change, never an automatic
                # side effect of merely showing the suggestion.  Keeping the
                # current handles together with NO_GEOMETRY would manufacture
                # a contradictory decision.  Clearing them also activates the
                # existing mandatory fresh-note policy.
                self.use_candidate_check.setChecked(False)
                self.note_toggle_button.setChecked(True)
        finally:
            self._applying_decision_suggestion = False

        self._decision_suggestion_accepted = True
        self._accepted_suggestion_fields = self._suggestion_fields_from_prefill(
            prefill
        )
        if prefill.get("geometry_mode") == "NO_DXF_GEOMETRY":
            applied_text = (
                "Предложението е приложено след изрично действие. Текущата DXF "
                "геометрия е изключена, защото предишното решение е било без "
                "геометрия. Въведи нова бележка за този случай; стара бележка не "
                "се копира."
            )
        else:
            applied_text = (
                "Предложението е приложено след изрично действие. Провери формата, "
                "размерите и причината; можеш да редактираш всичко преди запис. "
                "Геометрия и стара бележка не са копирани."
            )
        self.suggestion_label.setText(applied_text)
        self.use_suggestion_button.setText("Предложението е приложено")
        self.use_suggestion_button.setEnabled(False)
        self._refresh()

    @staticmethod
    def _suggestion_fields_from_prefill(prefill: dict) -> dict:
        return {
            "shape_code": str(prefill.get("shape_code") or "").upper(),
            "count": int(prefill.get("count") or 0),
            "diameter_mm": _text_number(prefill.get("diameter_mm")),
            "production_length_cm": _text_number(
                prefill.get("production_length_cm")
            ),
            "segment_lengths_cm": [
                _text_number(value)
                for value in prefill.get("segment_lengths_cm") or ()
            ],
            "turn_angles_deg": [
                _text_number(value)
                for value in prefill.get("turn_angles_deg") or ()
            ],
            "reason_code": str(prefill.get("reason_code") or "").upper(),
        }

    def _suggestion_fields_from_values(self, values: dict) -> dict:
        return {
            "shape_code": str(values.get("shape_code") or "").upper(),
            "count": int(values.get("count") or 0),
            "diameter_mm": _text_number(values.get("diameter_mm")),
            "production_length_cm": _text_number(values.get("length")),
            "segment_lengths_cm": [
                _text_number(value) for value in values.get("segments") or ()
            ],
            "turn_angles_deg": [
                _text_number(value) for value in values.get("turn_angles") or ()
            ],
            "reason_code": str(values.get("reason_code") or "").upper(),
        }

    def _decision_suggestion_audit(self, values: dict) -> dict | None:
        suggestion = self.decision_suggestion
        if suggestion is None or suggestion.status not in {READY, CONFLICT}:
            return None
        if suggestion.status == CONFLICT:
            outcome = "CONFLICT_NOT_APPLIED"
        elif not self._decision_suggestion_accepted:
            outcome = "SHOWN_NOT_USED"
        else:
            final_fields = self._suggestion_fields_from_values(values)
            outcome = (
                "ACCEPTED_UNCHANGED"
                if final_fields == self._accepted_suggestion_fields
                else "ACCEPTED_THEN_EDITED"
            )
        return {
            "schema": "lion-rebar-decision-suggestion-ui-audit/v1",
            "displayed": True,
            "status": suggestion.status,
            "outcome": outcome,
            "explicitly_accepted": self._decision_suggestion_accepted,
            "suggestion": suggestion.to_dict(),
            "acceptance": (
                suggestion.acceptance_audit()
                if self._decision_suggestion_accepted
                else None
            ),
        }

    def _available_screen_geometry(self):
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else None
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _configure_initial_size(self) -> None:
        available = self._available_screen_geometry()
        if available is None:
            self.setMinimumSize(self._MIN_WIDTH, self._MIN_HEIGHT)
            self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
            return

        usable_width = max(360, available.width() - self._SCREEN_MARGIN * 2)
        usable_height = max(320, available.height() - self._SCREEN_MARGIN * 2)
        minimum_width = max(360, min(self._MIN_WIDTH, usable_width))
        minimum_height = max(320, min(self._MIN_HEIGHT, usable_height))
        target_width = max(
            minimum_width,
            min(self._DEFAULT_WIDTH, int(available.width() * 0.88)),
        )
        target_height = max(
            minimum_height,
            min(self._DEFAULT_HEIGHT, int(available.height() * 0.88)),
        )
        self.setMinimumSize(minimum_width, minimum_height)
        self.resize(target_width, target_height)

    def _restore_window_geometry(self) -> None:
        if self._ui_settings is None:
            return
        try:
            geometry = self._ui_settings.value(self._GEOMETRY_SETTINGS_KEY)
            if geometry is not None:
                self.restoreGeometry(geometry)
        except (AttributeError, RuntimeError, TypeError):
            return
        self._fit_to_available_screen()

    def _save_window_geometry(self) -> None:
        if self._ui_settings is None:
            return
        try:
            self._ui_settings.setValue(
                self._GEOMETRY_SETTINGS_KEY,
                self.saveGeometry(),
            )
            self._ui_settings.sync()
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _fit_to_available_screen(self) -> None:
        available = self._available_screen_geometry()
        if available is None:
            return
        margin = min(
            self._SCREEN_MARGIN,
            max(0, min(available.width(), available.height()) // 20),
        )
        maximum_width = max(self.minimumWidth(), available.width() - margin * 2)
        maximum_height = max(self.minimumHeight(), available.height() - margin * 2)
        if self.width() > maximum_width or self.height() > maximum_height:
            self.resize(
                min(self.width(), maximum_width),
                min(self.height(), maximum_height),
            )

        frame = self.frameGeometry()
        left = available.left() + margin
        top = available.top() + margin
        right = available.right() - margin - frame.width() + 1
        bottom = available.bottom() - margin - frame.height() + 1
        self.move(
            max(left, min(frame.left(), max(left, right))),
            max(top, min(frame.top(), max(top, bottom))),
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._fit_to_available_screen()

    def done(self, result: int) -> None:  # noqa: N802 - Qt override
        self._save_window_geometry()
        super().done(result)

    def _accept_if_ready(self) -> None:
        # A keyboard shortcut must never bypass the exact same validation gate
        # as the visible Save button.
        if self.save_button.isEnabled():
            self._accept()

    def _rebuild_segment_fields(
        self,
        shape: ShapeDefinition | None,
        values: tuple[float, ...],
    ) -> None:
        while self.segment_fields_layout.count():
            item = self.segment_fields_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.segment_edits.clear()
        self._segment_source_labels.clear()
        self._segment_indexes_by_key.clear()
        self._initial_segment_values_by_key.clear()
        if shape is None:
            empty = QLabel("Избери MASTER форма")
            empty.setStyleSheet("color:#6b7280;")
            self.segment_fields_layout.addWidget(empty, 0, 0, 1, 3)
            return

        ordered = sorted(shape.segments, key=lambda item: item.index)
        positions_by_key: dict[str, list[int]] = {}
        for position, segment in enumerate(ordered):
            key = str(segment.length_code or f"L{segment.index}").strip().upper()
            positions_by_key.setdefault(key, []).append(position)
        self._segment_indexes_by_key = {
            key: tuple(positions) for key, positions in positions_by_key.items()
        }
        self._segment_input_loading = True
        try:
            for row_index, (key, positions) in enumerate(positions_by_key.items()):
                segment = ordered[positions[0]]
                numbers = ", ".join(str(ordered[pos].index) for pos in positions)
                suffix = " (свързани)" if len(positions) > 1 else " (независим)"
                if segment.fixed_length_cm is not None:
                    suffix = " (MASTER фиксиран)"
                label = QLabel(f"{key} — сегм. {numbers}{suffix}")
                label.setMinimumWidth(145)
                edit = _SegmentLineEdit(key, self)
                edit.setValidator(QDoubleValidator(0.1, 20_000.0, 3, edit))
                edit.setMinimumWidth(80)
                edit.setMaximumWidth(125)
                initial = None
                if segment.fixed_length_cm is not None:
                    initial = float(segment.fixed_length_cm)
                    edit.setReadOnly(True)
                elif positions[0] < len(values):
                    initial = float(values[positions[0]])
                if initial is not None:
                    edit.setText(_text_number(initial))
                    self._initial_segment_values_by_key[key] = initial
                source = QLabel(self._initial_source_text(segment, initial))
                source.setStyleSheet("color:#475569;")
                source.setWordWrap(True)
                source.setMinimumWidth(80)
                self.segment_edits[key] = edit
                self._segment_source_labels[key] = source
                self.segment_fields_layout.addWidget(label, row_index, 0)
                self.segment_fields_layout.addWidget(edit, row_index, 1)
                self.segment_fields_layout.addWidget(source, row_index, 2)
                edit.textChanged.connect(
                    lambda _text, current_key=key: self._segment_field_changed(current_key)
                )
                edit.focused.connect(self._focus_segment)
        finally:
            self._segment_input_loading = False
        self._sync_quick_from_fields()

    def _initial_source_text(self, segment, initial: float | None) -> str:
        if segment.fixed_length_cm is not None:
            return "MASTER — фиксирана стойност"
        if initial is None:
            return "липсва стойност"
        if self.existing_link:
            return "записано инженерно решение"
        if self._is_suggestion:
            return "предложение от надпис/DXF"
        return "изходна спецификация"

    def _segment_field_changed(self, key: str) -> None:
        if self._segment_input_loading:
            return
        self._quick_parse_error = ""
        source = self._segment_source_labels.get(key)
        edit = self.segment_edits.get(key)
        if source is not None and edit is not None and not edit.isReadOnly():
            current = positive_float(edit.text())
            initial = self._initial_segment_values_by_key.get(key)
            if current is None:
                source.setText("липсва стойност")
            elif initial is not None and abs(current - initial) <= 1e-6:
                source.setText("изходна стойност")
            else:
                source.setText("операторска корекция")
        self._sync_quick_from_fields()
        self._focus_segment(key)
        self._refresh()

    def _sync_quick_from_fields(self) -> None:
        shape = self._shape_by_code.get(str(self.shape_combo.currentData() or "").upper())
        if shape is None:
            return
        ordered = sorted(shape.segments, key=lambda item: item.index)
        texts = []
        for segment in ordered:
            key = str(segment.length_code or f"L{segment.index}").strip().upper()
            edit = self.segment_edits.get(key)
            texts.append(edit.text().strip() if edit is not None else "")
        self.segments_edit.blockSignals(True)
        self.segments_edit.setText(" + ".join(texts))
        self.segments_edit.blockSignals(False)

    def _quick_segments_changed(self) -> None:
        if self._segment_input_loading:
            return
        shape = self._shape_by_code.get(str(self.shape_combo.currentData() or "").upper())
        if shape is None:
            self._refresh()
            return
        ordered = sorted(shape.segments, key=lambda item: item.index)
        raw = self.segments_edit.text().strip()
        if not raw:
            self._segment_input_loading = True
            try:
                for edit in self.segment_edits.values():
                    if not edit.isReadOnly():
                        edit.clear()
            finally:
                self._segment_input_loading = False
            self._quick_parse_error = ""
            self._refresh()
            return
        try:
            values = parse_segment_text(raw)
        except ValueError as exc:
            self._quick_parse_error = f"Бързо въвеждане: {exc}"
            self._refresh()
            return
        if len(values) != len(ordered):
            self._quick_parse_error = (
                f"Бързо въвеждане: очаквани са {len(ordered)} сегмента по MASTER, "
                f"а са въведени {len(values)}."
            )
            self._refresh()
            return
        values_by_key: dict[str, float] = {}
        for position, segment in enumerate(ordered):
            key = str(segment.length_code or f"L{segment.index}").strip().upper()
            value = float(values[position])
            if key in values_by_key and abs(values_by_key[key] - value) > 1e-6:
                self._quick_parse_error = (
                    f"{key} е свързан MASTER параметър и трябва да е еднакъв "
                    "във всички негови сегменти."
                )
                self._refresh()
                return
            values_by_key[key] = value
        self._quick_parse_error = ""
        self._segment_input_loading = True
        try:
            for key, value in values_by_key.items():
                edit = self.segment_edits.get(key)
                if edit is not None and not edit.isReadOnly():
                    edit.setText(_text_number(value))
        finally:
            self._segment_input_loading = False
        for key in values_by_key:
            source = self._segment_source_labels.get(key)
            if source is not None and not self.segment_edits[key].isReadOnly():
                source.setText("операторска корекция")
        self._refresh()

    def _segments_from_fields(self, shape: ShapeDefinition) -> tuple[float, ...]:
        values: list[float] = []
        for segment in sorted(shape.segments, key=lambda item: item.index):
            key = str(segment.length_code or f"L{segment.index}").strip().upper()
            edit = self.segment_edits.get(key)
            if edit is None:
                raise ValueError(f"Липсва поле за MASTER параметър {key}.")
            if str(shape.code or "").upper() == "F001" and not edit.text().strip():
                return ()
            value = positive_float(edit.text(), field=key)
            assert value is not None
            if segment.fixed_length_cm is not None and abs(value - float(segment.fixed_length_cm)) > 1e-6:
                raise ValueError(f"{key} е фиксиран от MASTER на {segment.fixed_length_cm:g} cm.")
            values.append(value)
        return tuple(values)

    def _partial_segment_values(self, shape: ShapeDefinition | None) -> tuple[float | None, ...]:
        if shape is None:
            return ()
        result: list[float | None] = []
        for segment in sorted(shape.segments, key=lambda item: item.index):
            key = str(segment.length_code or f"L{segment.index}").strip().upper()
            edit = self.segment_edits.get(key)
            result.append(positive_float(edit.text()) if edit is not None else None)
        return tuple(result)

    def _segment_sources(self, shape: ShapeDefinition | None) -> tuple[str, ...]:
        if shape is None:
            return ()
        result: list[str] = []
        for segment in sorted(shape.segments, key=lambda item: item.index):
            key = str(segment.length_code or f"L{segment.index}").strip().upper()
            label = self._segment_source_labels.get(key)
            result.append(label.text() if label is not None else "")
        return tuple(result)

    def _focus_segment(self, key: str) -> None:
        normalized = str(key or "").upper()
        self.shape_preview.select_segment(normalized)
        for field_key, edit in self.segment_edits.items():
            if edit.isReadOnly():
                base = "background:#f1f5f9;"
            elif field_key == normalized:
                base = "border:2px solid #ea580c; background:#fff7ed;"
            else:
                base = ""
            edit.setStyleSheet(base)

    def _preview_segment_selected(self, key: str) -> None:
        edit = self.segment_edits.get(str(key or "").upper())
        if edit is not None:
            edit.setFocus(Qt.FocusReason.OtherFocusReason)
            edit.selectAll()

    def _note_policy(self, action_class: str, reason_code: str, handles) -> object:
        return engineer_note_policy(
            action_class=action_class,
            reason_code=reason_code,
            geometry_handles=handles,
            structured_evidence={
                "candidate_basis": self.row.get("candidate_basis"),
                "candidate_notes": self.row.get("candidate_notes"),
                "diagnostic_problem": self._diagnostic_problem(),
                "review_reason_codes": self.row.get("review_reason_codes"),
            },
        )

    def _apply_note_policy(self, policy) -> None:
        self.note_requirement_label.setText(policy.explanation)
        if policy.required:
            self.note_requirement_label.setStyleSheet("color:#b91c1c; font-weight:700;")
            self.note_toggle_button.hide()
            self.note_edit.show()
        else:
            self.note_requirement_label.setStyleSheet("color:#1e3a8a;")
            self.note_toggle_button.show()
            self.note_edit.setVisible(
                self.note_toggle_button.isChecked() or bool(self.note_edit.toPlainText().strip())
            )
        self.confirm_check.setVisible(bool(policy.acknowledgement_required))

    def _preview_status(self, segments, length: float | None) -> str:
        known = [float(value) for value in segments if value is not None]
        if not known:
            return "Сегменти: непълни"
        total = sum(known)
        if len(known) != len(segments) or length is None:
            return f"Σ={total:g} cm | липсва стойност"
        shape_code = str(self.shape_combo.currentData() or "").upper()
        if segments_match_total(shape_code, total, length):
            return f"Σ={total:g} cm | L={length:g} cm | валидно"
        try:
            заявена = declared_bend_allowance(
                {"bend_allowance_cm": self.bend_allowance_edit.text()}
            )
        except ValueError:
            заявена = None
        if заявена is not None and segments_match_total(
            shape_code, total, length, declared_allowance_cm=заявена
        ):
            return (
                f"Σ={total:g} cm | L={length:g} cm | валидно със заявена "
                f"добавка {заявена:g} cm"
            )
        return f"Σ={total:g} cm | L={length:g} cm | НЕВАЛИДНО"

    def _values(self) -> dict:
        reason_code = str(self.reason_combo.currentData() or "OTHER")
        note = self.note_edit.toPlainText().strip()
        if reason_code == "NON_PRODUCTION_POSITION":
            note_policy = self._note_policy("EXCLUSION", reason_code, ())
            if note_policy.required and not note:
                raise ValueError(note_policy.explanation)
            return {
                "non_production": True,
                "shape_code": "",
                "count": None,
                "diameter_mm": None,
                "length": None,
                "segments": (),
                "turn_angles": (),
                "bend_allowance_cm": None,
                "reason_code": reason_code,
                "handles": [],
                "action_class": "EXCLUSION",
                "note": note,
                "note_policy": note_policy,
            }

        shape_code = str(self.shape_combo.currentData() or "").upper()
        shape = self._shape_by_code.get(shape_code)
        if shape is None:
            raise ValueError("Избери активна MASTER/Engine форма от списъка.")
        count = positive_int(self.count_edit.text(), field="Брой")
        diameter = positive_float(self.diameter_edit.text(), field="Диаметър")
        length = positive_float(self.length_edit.text(), field="Обща дължина")
        assert count is not None and diameter is not None and length is not None
        bend_allowance = declared_bend_allowance(
            {"bend_allowance_cm": self.bend_allowance_edit.text()}
        )
        if self._quick_parse_error:
            raise ValueError(self._quick_parse_error)
        segments = self._segments_from_fields(shape)
        if shape_code == "F001" and not segments:
            segments = (length,)
        if shape_code != "F001" and not segments:
            raise ValueError("За огъната форма въведи производствените сегменти.")
        # ДОБАВКА ЗА ОГЪВАНЕ (Product Owner, 2026-08-25):
        # описва се каквото е НАДПИСАНО в чертежа. При огънатите форми
        # сборът на страните е по-малък от общата дължина -- разликата е
        # добавката за огъване на проектанта и НЕ е наш проблем.
        # Реален случай: 56N8x115 е стреме 25 x 20 с кука 10;
        # 2*(25+20)+2*10 = 110 при L=115.
        #
        # Приема се недостиг до 8 cm. НАДВИШАВАНЕ не се приема: сегменти,
        # чийто сбор е по-голям от общата дължина, са грешка, не добавка.
        # Единен източник на истина (lion_rebar.engine.bend_allowance).
        нужна_е_добавка = bool(segments) and not segments_match_total(
            shape_code, sum(segments), length
        )
        if нужна_е_добавка and not segments_match_total(
            shape_code, sum(segments), length, declared_allowance_cm=bend_allowance
        ):
            raise ValueError(
                explain_mismatch(
                    shape_code,
                    sum(segments),
                    length,
                    declared_allowance_cm=bend_allowance,
                )
            )
        bend_allowance = bend_allowance if нужна_е_добавка else None

        turn_angles = tuple(self.row.get("canonical_turn_angles_deg") or ())
        if (
            self._decision_suggestion_accepted
            and self.decision_suggestion is not None
            and self.decision_suggestion.proposal is not None
            and self.decision_suggestion.proposal.shape_code == shape_code
        ):
            turn_angles = tuple(
                self.decision_suggestion.proposal.turn_angles_deg
            )
        try:
            parameters = parameters_for_shape(
                shape,
                segments,
                production_length_cm=length,
                turn_angles_deg=turn_angles,
                declared_allowance_cm=bend_allowance,
            )
        except ExcelExportError as exc:
            raise ValueError(f"MASTER/Engine проверка: {exc}") from exc
        segments = tuple(parameters.expanded_segments_cm)
        handles = self._available_handles if self.use_candidate_check.isChecked() else []
        if handles and reason_code == "NO_GEOMETRY":
            raise ValueError(
                "Причина NO_GEOMETRY не може да се запише с активна DXF "
                "геометрия. Изключи геометрията или избери точната причина."
            )
        if not handles and reason_code not in _GEOMETRYLESS_REASON_CODES:
            raise ValueError(
                "Без DXF връзка избери изрична причина NO_GEOMETRY/WRONG_GEOMETRY "
                "(или съответната причина за невалидна/частична геометрия)."
            )
        action_class = classify_engineer_action(
            self.row,
            shape_code=shape_code,
            geometry_handles=handles,
            count=count,
            diameter_mm=diameter,
            production_length_cm=length,
            production_segments_cm=segments,
            reason_code=reason_code,
        )
        if not handles and action_class == "CONFIRMATION":
            raise ValueError(
                "Потвърждение без DXF геометрия не е допустимо. Избери коригираща причина."
            )
        note_policy = self._note_policy(action_class, reason_code, handles)
        if note_policy.required and not note:
            raise ValueError(note_policy.explanation)
        return {
            "non_production": False,
            "shape_code": shape_code,
            "count": count,
            "diameter_mm": diameter,
            "length": length,
            "segments": segments,
            "turn_angles": turn_angles,
            "bend_allowance_cm": bend_allowance,
            "reason_code": reason_code,
            "handles": list(handles),
            "action_class": action_class,
            "note": note,
            "note_policy": note_policy,
        }

    def _shape_changed(self) -> None:
        code = str(self.shape_combo.currentData() or "").upper()
        changed = bool(self._last_shape_code and code and code != self._last_shape_code)
        if changed:
            # Do not let dimensions from a foreign candidate survive a form
            # change.  The engineer must state the new production segments.
            self._initial_segments = ()
            self.segments_edit.blockSignals(True)
            self.segments_edit.clear()
            self.segments_edit.blockSignals(False)
            self._quick_parse_error = ""
        self._last_shape_code = code
        shape = self._shape_by_code.get(code)
        self._rebuild_segment_fields(shape, () if changed else self._initial_segments)
        self.shape_preview.set_shape(shape)
        if shape is None:
            self.shape_hint.setText("—")
        else:
            ordered = sorted(shape.segments, key=lambda item: item.index)
            self.shape_hint.setText(
                f"{len(ordered)} сегмента: "
                + " + ".join(str(item.length_code or f'L{item.index}') for item in ordered)
            )
        self._refresh()

    def _refresh(self) -> None:
        shape = self._shape_by_code.get(str(self.shape_combo.currentData() or "").upper())
        partial_segments = self._partial_segment_values(shape)
        partial_length = positive_float(self.length_edit.text())
        try:
            values = self._values()
            policy = values["note_policy"]
            self._apply_note_policy(policy)
            self.action_label.setText(values["action_class"])
            if values.get("non_production"):
                self.status_label.setText(
                    "Резултат: НЕ Е ПРОИЗВОДСТВЕНА ПОЗИЦИЯ — ще бъде изключена от Excel/DXF, "
                    "но ще остане в одитната следа."
                )
            else:
                resolved_text = production_annotation_text(
                    count=values["count"],
                    diameter_mm=values["diameter_mm"],
                    production_length_cm=values["length"],
                )
                self.status_label.setText(
                    f"Резултат: {resolved_text} | {values['shape_code']} | "
                    f"{' + '.join(_text_number(v) for v in values['segments'])} | "
                    f"{reason_label(values['reason_code'])}"
                )
                partial_segments = tuple(values["segments"])
                partial_length = float(values["length"])
            self.status_label.setStyleSheet("color:#166534; font-weight:700;")
            valid = not policy.acknowledgement_required or self.confirm_check.isChecked()
        except ValueError as exc:
            self.action_label.setText("—")
            self.status_label.setText(str(exc))
            self.status_label.setStyleSheet("color:#b91c1c; font-weight:700;")
            reason_code = str(self.reason_combo.currentData() or "OTHER")
            fallback_action = (
                "EXCLUSION"
                if reason_code == "NON_PRODUCTION_POSITION"
                else "CONFIRMATION"
                if reason_code == "CONFIRM_CORRECT"
                else "CORRECTION"
            )
            handles = self._available_handles if self.use_candidate_check.isChecked() else []
            self._apply_note_policy(self._note_policy(fallback_action, reason_code, handles))
            valid = False
        self.shape_preview.set_production_values(
            partial_segments,
            sources=self._segment_sources(shape),
            total_length_cm=partial_length,
            status_text=self._preview_status(partial_segments, partial_length),
        )
        self.save_button.setEnabled(valid)

    def _accept(self) -> None:
        try:
            values = self._values()
        except ValueError as exc:
            QMessageBox.warning(self, "Инженерно решение", str(exc))
            return
        shape = self._shape_by_code.get(str(values["shape_code"]).upper())
        resolved_text = (
            ""
            if values.get("non_production")
            else production_annotation_text(
                count=values["count"],
                diameter_mm=values["diameter_mm"],
                production_length_cm=values["length"],
            )
        )
        suggestion_audit = self._decision_suggestion_audit(values)
        self.result_payload = {
            "schema": "lion-rebar-engineer-decision/v2",
            "shape_definition": shape.to_dict() if shape is not None else None,
            "action_class": values["action_class"],
            "reason_code": values["reason_code"],
            "reason": reason_label(values["reason_code"]),
            "note": values["note"],
            "operator_note": values["note"],
            "note_policy": {
                "required": values["note_policy"].required,
                "code": values["note_policy"].code,
                "explanation": values["note_policy"].explanation,
            },
            "context": {
                "application_version": APP_VERSION,
                "engine_version": self.engine_version,
                "project_name": self.project_name,
                "project_version": self.project_version,
            },
            # Immutable, JSON-safe snapshot of the exact Review Center row that
            # entered this editor.  This is especially important after the
            # operator has selected replacement DXF geometry: regenerating the
            # context later from the base report would record a hybrid of the
            # old automatic candidate and the new manual selection.
            "row_context": _decision_context_snapshot(self.row),
            "annotation": {
                "handle": str(self.row.get("annotation_handle") or ""),
                "text": str(self.row.get("annotation_text") or ""),
                "resolved_text": resolved_text,
                "resolved_count": values["count"],
                "resolved_diameter_mm": values["diameter_mm"],
                "resolved_length_cm": values["length"],
            },
            "original": {
                "count": self.row.get("annotation_count"),
                "diameter_mm": self.row.get("diameter_mm"),
                "declared_length_cm": self.row.get("declared_length_cm"),
                # Предложението на програмата е част от „предложеното“.
                # Ако операторът го приеме както е, това е потвърждение,
                # не корекция — и не иска обяснение.
                "shape_code": (self.row.get("master_shape_code")
                               or self.row.get("suggested_shape_code")),
                "shape_name": self.row.get("master_shape_name"),
                "segment_lengths_cm": list(
                    self.row.get("canonical_segment_lengths_cm")
                    or self.row.get("production_segments_cm")
                    or self.row.get("suggested_segments_cm")
                    or []),
                "geometry_handles": list(self.row.get("candidate_handles") or []),
                "candidate_basis": self.row.get("candidate_basis"),
                "geometry_provenance": self.row.get("geometry_provenance"),
                "data_quality_flags": list(
                    self.row.get("data_quality_flags") or []
                ),
                "batch_status": self.row.get("batch_status"),
                "matcher_disposition": self.row.get("matcher_disposition"),
                "review_reason_codes": list(self.row.get("review_reason_codes") or []),
                "geometry_length_raw": self.row.get("geometry_length_raw"),
                "raw_numeric_difference": self.row.get("raw_numeric_difference"),
            },
            "resolved": {
                "count": values["count"],
                "diameter_mm": values["diameter_mm"],
                "production_length_cm": values["length"],
                "segment_lengths_cm": list(values["segments"]),
                "shape_code": values["shape_code"],
                "shape_name": shape.name if shape is not None else values["shape_code"],
                "shape_revision": int(shape.revision) if shape is not None else None,
                "turn_angles_deg": list(values.get("turn_angles") or ()),
                "bend_allowance_cm": values.get("bend_allowance_cm"),
            },
            "geometry": {
                "handles": values["handles"],
                "layer": str(self.row.get("candidate_layer") or ""),
                "basis": str(self.row.get("candidate_basis") or "OWNER_ENGINEER_DECISION"),
                "notes": list(self.row.get("candidate_notes") or []),
                "segment_count": len(values["segments"]),
            },
            "diagnostic_problem": self._diagnostic_problem(),
            "decision_suggestion": suggestion_audit,
            # Ключът е ``preizpolzvane`` нарочно на латиница: по него
            # ``review/decision_reuse.зависими_за_повторна_проверка`` намира
            # кои приети решения зависят от кое основание.
            "preizpolzvane": self._reuse_audit(values),
        }
        self.accept()

    def _mapped_problem_reason(self) -> str | None:
        problem = self._diagnostic_problem()
        return engineer_reason_from_problem(problem.get("reason_code"))

    def _problem_note(self) -> str:
        problem = self._diagnostic_problem()
        parts: list[str] = []
        correct = str(problem.get("correct_specification") or "").strip()
        note = str(problem.get("note") or "").strip()
        if correct:
            parts.append(f"Как трябва да бъде: {correct}")
        if note:
            parts.append(note)
        return "\n".join(parts)

    def _problem_text(self) -> str:
        problem = self._diagnostic_problem()
        if not problem:
            return ""
        reason = str(
            problem.get("reason")
            or problem.get("reason_code")
            or ""
        ).strip()
        correct = str(problem.get("correct_specification") or "").strip()
        values = []
        if reason:
            values.append(f"Какво не е наред: {reason}")
        if correct:
            values.append(f"Как трябва да бъде: {correct}")
        return "\n".join(values)

    def _diagnostic_problem(self) -> dict:
        code = str(self.problem_decision.get("decision") or "").upper()
        if code not in {
            "PROBLEM_RECORDED",
            "FLAGGED",
            "NO_VALID_CANDIDATE",
            "MISSING_MASTER",
            "EXCLUDED_BY_ENGINEER",
            "NON_PRODUCTION_POSITION",
        }:
            return {}
        return dict(self.problem_decision)


#: Максимален недостиг между сбора на сегментите и общата дължина.
#: Разликата идва от огъвките и е отговорност на проектанта. Измерено в
#: реален чертеж: при 43 от 60 стремета точно 5 cm, единични 4 и 6.
BEND_ALLOWANCE_LIMIT_CM = 8.0


def _decision_context_snapshot(row: dict) -> dict:
    """Capture only deterministic pre-decision fields used by rc40 matching."""

    list_fields = {
        "candidate_handles",
        "canonical_segment_lengths_cm",
        "canonical_turn_angles_deg",
        "production_segments_cm",
        "suggested_segments_cm",
        "suggested_turn_angles_deg",
        "matcher_blocker_codes",
        "review_reason_codes",
        "data_quality_flags",
        "candidate_notes",
        "alternative_candidates",
    }
    field_names = (
        "annotation_handle",
        "annotation_text",
        "annotation_count",
        "diameter_mm",
        "declared_length_cm",
        "master_shape_code",
        "master_shape_name",
        "suggested_shape_code",
        "suggested_shape_name",
        "candidate_handles",
        "candidate_layer",
        "candidate_basis",
        "geometry_provenance",
        "canonical_segment_lengths_cm",
        "canonical_turn_angles_deg",
        "production_segments_cm",
        "suggested_segments_cm",
        "suggested_turn_angles_deg",
        "matcher_blocker_codes",
        "review_reason_codes",
        "batch_status",
        "matcher_disposition",
        "matcher_evidence_band",
        "candidate_confidence",
        "contextual_rule_code",
        "canonical_signature",
        "numeric_difference",
        "alternative_candidate_count",
        "frame_category_code",
        "data_quality_flags",
        "candidate_notes",
        "alternative_candidates",
        "global_conflict",
        "geometry_owner_conflict",
        "master_conflict",
    )
    snapshot = {}
    for name in field_names:
        value = row.get(name)
        snapshot[name] = list(value or []) if name in list_fields else value
    return snapshot


def _int_value(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text_number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:.6f}".rstrip("0").rstrip(".")
