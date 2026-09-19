from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lion_rebar.config.constants import (
    ACTIVATION_PHRASE,
    APPROVAL_PHRASE,
    CONTROLLED_REVISION_PHRASE,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.legacy_import import (
    LegacyImportError,
    import_legacy_html,
    import_legacy_xlsx,
    import_shape_draft_json,
)
from lion_rebar.engine.models import (
    CategoryDefinition,
    EnginePackage,
    ParameterRelation,
    RuleDefinition,
    SegmentDefinition,
    ShapeDefinition,
)
from lion_rebar.engine.recognition_audit import (
    audit_engine_generated_regression,
    audit_engine_recognition,
)
from lion_rebar.engine.repository import (
    EngineRepository,
    EngineRepositoryError,
    EngineValidationError,
    EngineVersionInfo,
)
from lion_rebar.engine.test_runner import (
    build_control_test,
    required_dimension_codes,
    run_engine_tests,
)
from lion_rebar.engine.validation import has_errors, issue_summary, validate_engine
from lion_rebar.ui.approval_dialogs import ApprovalDialog
from lion_rebar.ui.shape_preview import ShapePreview


class EngineStudio(QDialog):
    """Local MASTER editor with immutable publishing and rollback."""

    def __init__(self, repository: EngineRepository, parent=None, start_tab: str | None = None):
        super().__init__(parent)
        self.repository = repository
        self.draft: EnginePackage | None = None
        self._loading_form = False
        self._start_tab = start_tab
        self.setWindowTitle("LION REBAR MASTER Studio")
        # QDialog по подразбиране няма бутони за минимизиране/максимизиране.
        # При по-нисък екран старият фиксиран размер 1380x900 скриваше долните
        # бутони (включително „Публикувай черновата“) зад taskbar-а.
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self._resize_to_available_screen()

        self.current_label = QLabel()
        self.draft_label = QLabel("Няма отворена чернова")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_versions_tab(), "Версии")
        self.tabs.addTab(self._build_forms_tab(), "Форми")
        self.tabs.addTab(self._build_rules_tab(), "Правила")
        self.tabs.addTab(self._build_recognition_laws_tab(), "Закони за разпознаване")
        self.tabs.addTab(self._build_categories_tab(), "Листове и категории")
        self.tabs.addTab(self._build_tests_tab(), "Контролни тестове")
        self.tabs.addTab(self._build_assets_tab(), "MASTER файлове")
        self.tabs.addTab(self._build_raw_tab(), "Разширен JSON")

        self.validate_button = QPushButton("Провери черновата")
        self.validate_button.clicked.connect(self.validate_draft)
        self.save_draft_button = QPushButton("Запази черновата")
        self.save_draft_button.clicked.connect(self.save_draft)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.reject)

        header = QHBoxLayout()
        header.addWidget(self.current_label, 1)
        header.addWidget(self.draft_label, 1)
        footer = QHBoxLayout()
        footer.addWidget(self.validate_button)
        footer.addWidget(self.save_draft_button)
        footer.addStretch(1)
        footer.addWidget(close_buttons)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(footer)

        self.refresh_versions()
        drafts = self.repository.list_drafts()
        if drafts:
            self._load_draft_path(drafts[-1].path)
        else:
            self._set_editor_enabled(False)
        if self._start_tab == "forms":
            self.tabs.setCurrentIndex(1)

    def _resize_to_available_screen(self) -> None:
        """Keep the complete Studio UI inside the usable desktop area."""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 760)
            self.setMinimumSize(800, 560)
            return

        available = screen.availableGeometry()
        target_width = min(1380, max(800, int(available.width() * 0.92)))
        target_height = min(820, max(560, int(available.height() * 0.84)))
        self.resize(target_width, target_height)
        self.setMinimumSize(min(900, target_width), min(600, target_height))

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    def _build_versions_tab(self) -> QWidget:
        widget = QWidget()
        self.versions_table = QTableWidget(0, 8)
        self.versions_table.setHorizontalHeaderLabels(
            ["Активна", "Тип", "Engine ID", "Версия", "Статус", "Production", "Заглавие", "Файл"]
        )
        self.versions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.versions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.versions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.versions_table.horizontalHeader().setStretchLastSection(True)

        buttons = QGridLayout()
        actions = [
            ("Отвори избраната чернова", self.open_selected_draft),
            ("Чернова от активната версия", self.clone_current),
            ("Нова празна чернова", self.new_empty_draft),
            ("Импорт MASTER HTML/XLSX", self.import_legacy_master),
            ("Импорт .lionengine", self.import_bundle),
            ("Публикувай черновата", self.publish_draft),
            ("Активирай / върни версия", self.activate_selected_version),
            ("Експорт .lionengine", self.export_engine),
        ]
        for index, (label, callback) in enumerate(actions):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button, index // 4, index % 4)
            if callback == self.publish_draft:
                self.publish_draft_button = button
                button.setToolTip("Публикува и активира отворената чернова след контролно одобрение.")

        action_group = QGroupBox("Действия по версиите")
        action_group.setLayout(buttons)

        note = QLabel(
            "Публикуваните версии са неизменяеми. За промяна се създава нова чернова. "
            "Стар проект остава свързан с използваната engine версия."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(widget)
        layout.addWidget(note)
        # Действията са над таблицата, за да са винаги видими дори на
        # лаптоп с ниска вертикална резолюция или Windows taskbar.
        layout.addWidget(action_group)
        layout.addWidget(self.versions_table, 1)
        return widget

    def refresh_versions(self) -> None:
        current = self.repository.load_current()
        self.current_label.setText(
            f"Активен engine: <b>{current.engine_id} v{current.version}</b>"
            + (" — PRODUCTION" if current.production_ready else " — НЕ Е PRODUCTION")
        )
        rows: list[tuple[str, EngineVersionInfo]] = []
        rows.extend(("DRAFT", info) for info in self.repository.list_drafts())
        rows.extend(("PUBLISHED", info) for info in self.repository.list_published())
        self.versions_table.setRowCount(len(rows))
        for row, (kind, info) in enumerate(rows):
            values = [
                "ДА" if info.current else "",
                kind,
                info.engine_id,
                info.version,
                info.state,
                "ДА" if info.production_ready else "НЕ",
                info.title,
                str(info.path),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, {"kind": kind, "path": str(info.path), "engine_id": info.engine_id, "version": info.version})
                self.versions_table.setItem(row, column, item)

    def _selected_version(self) -> dict[str, str] | None:
        row = self.versions_table.currentRow()
        if row < 0:
            return None
        item = self.versions_table.item(row, 0)
        value = item.data(Qt.UserRole) if item else None
        return value if isinstance(value, dict) else None

    def open_selected_draft(self) -> None:
        selected = self._selected_version()
        if not selected:
            self._info("Избери ред от таблицата.")
            return
        if selected["kind"] != "DRAFT":
            self._info("Публикувана версия не се редактира. Създай чернова от активната версия.")
            return
        self._load_draft_path(Path(selected["path"]))

    def clone_current(self) -> None:
        version, ok = QInputDialog.getText(self, "Нова версия", "Версия на черновата, например 0.3.2:")
        if not ok:
            return
        by, ok = QInputDialog.getText(self, "Автор", "Кой създава черновата:")
        if not ok:
            return
        try:
            draft = self.repository.clone_current_to_draft(version.strip(), by.strip() or "OWNER")
            self._set_draft(draft)
            self.refresh_versions()
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def new_empty_draft(self) -> None:
        engine_id, ok = QInputDialog.getText(self, "Engine ID", "Engine ID:", text="lion-rebar-master")
        if not ok:
            return
        title, ok = QInputDialog.getText(self, "Заглавие", "Заглавие:", text="LION REBAR MASTER")
        if not ok:
            return
        version, ok = QInputDialog.getText(self, "Версия", "Версия:", text="0.1.0")
        if not ok:
            return
        try:
            self._set_draft(self.repository.create_empty_draft(engine_id.strip(), title.strip(), version.strip()))
            self.refresh_versions()
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def import_legacy_master(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт на MASTER engine/library",
            "",
            "MASTER files (*.html *.xlsx);;HTML (*.html);;Excel (*.xlsx)",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            engine = import_legacy_html(path) if path.suffix.lower() == ".html" else import_legacy_xlsx(path)
            attached = self.repository.attach_asset(
                path,
                "MASTER_ENGINE_HTML" if path.suffix.lower() == ".html" else "MASTER_LIBRARY_XLSX",
                "Imported through MASTER Studio",
            )
            engine.source_assets = [asset for asset in engine.source_assets if asset.filename != path.name]
            engine.source_assets.append(attached)
            current = self.repository.load_current()
            if current.engine_id == engine.engine_id and current.version != engine.version:
                engine.parent_version = current.version
            if any(info.version == engine.version and info.engine_id == engine.engine_id for info in self.repository.list_published()):
                new_version, ok = QInputDialog.getText(
                    self,
                    "Нова версия",
                    f"Версия {engine.version} вече е публикувана. Въведи нова версия за черновата:",
                )
                if not ok:
                    return
                engine.version = new_version.strip()
                engine.parent_version = current.version if current.engine_id == engine.engine_id else None
            self.repository.save_draft(engine)
            self._set_draft(engine)
            self.refresh_versions()
            self._info(f"Импортирани са {len(engine.shapes)} форми като чернова. Нищо не е активирано автоматично.")
        except (LegacyImportError, EngineRepositoryError, OSError) as exc:
            self._error(str(exc))

    def import_bundle(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Импорт .lionengine", "", "LION engine (*.lionengine)")
        if not filename:
            return
        try:
            engine = self.repository.import_bundle_as_draft(Path(filename))
            self._set_draft(engine)
            self.refresh_versions()
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def publish_draft(self) -> None:
        if self.draft is None:
            self._info("Няма отворена чернова.")
            return
        if not self._commit_all_tabs():
            return
        dialog = ApprovalDialog("Публикуване на engine", APPROVAL_PHRASE, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.data()
        try:
            path = self.repository.publish(
                self.draft,
                approved_by=data.by,
                approval_note=data.note,
                phrase=data.phrase,
                activate=True,
            )
            self.refresh_versions()
            self._info(f"Публикувана и активирана е версия {self.draft.version}.\n{path}")
        except EngineValidationError as exc:
            self._show_validation_issues(exc.issues, title="Публикуването е блокирано")
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def activate_selected_version(self) -> None:
        selected = self._selected_version()
        if not selected or selected["kind"] != "PUBLISHED":
            self._info("Избери публикувана версия.")
            return
        dialog = ApprovalDialog("Активиране / връщане на версия", ACTIVATION_PHRASE, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.data()
        try:
            self.repository.activate_version(
                selected["engine_id"],
                selected["version"],
                by=data.by,
                reason=data.note,
                phrase=data.phrase,
            )
            self.refresh_versions()
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def export_engine(self) -> None:
        engine = self.draft or self.repository.load_current()
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Експорт engine пакет",
            f"{engine.engine_id}_v{engine.version}.lionengine",
            "LION engine (*.lionengine)",
        )
        if not filename:
            return
        try:
            path = self.repository.export_bundle(engine, Path(filename))
            self._info(f"Създаден пакет:\n{path}")
        except EngineRepositoryError as exc:
            self._error(str(exc))

    # ------------------------------------------------------------------
    # Forms
    # ------------------------------------------------------------------
    def _build_forms_tab(self) -> QWidget:
        widget = QWidget()
        splitter = QSplitter()
        self.form_list = QListWidget()
        self.form_list.currentRowChanged.connect(self._load_selected_form)
        splitter.addWidget(self.form_list)

        editor = QWidget()
        self.shape_code = QLineEdit()
        self.shape_name = QLineEdit()
        self.shape_category = QLineEdit()
        self.shape_state = QComboBox()
        self.shape_state.addItems(["DRAFT", "APPROVED", "LOCKED", "RETIRED"])
        self.shape_revision = QLineEdit()
        self.shape_revision.setReadOnly(True)
        self.shape_initial_direction = QComboBox()
        self.shape_initial_direction.addItems(["RIGHT", "UP", "LEFT", "DOWN"])
        self.shape_closed = QCheckBox()
        self.shape_symmetric = QCheckBox()
        self.shape_mirror = QCheckBox()
        self.shape_locked_label = QLabel("—")
        self.shape_family = QLineEdit()
        self.shape_family.setPlaceholderText("напр. STAIR.LANDING")
        self.shape_alias = QLineEdit()
        self.shape_alias.setPlaceholderText("канонична форма, напр. F016")
        self.shape_relations = QPlainTextEdit()
        self.shape_relations.setMaximumHeight(90)
        self.shape_relations.setPlaceholderText(
            '[{"kind":"EQUAL","left_segment":1,"right_segment":3}]'
        )
        self.shape_orientation = QPlainTextEdit()
        self.shape_orientation.setMaximumHeight(70)
        self.shape_notes = QPlainTextEdit()
        self.shape_notes.setMaximumHeight(90)

        form = QFormLayout()
        form.addRow("Код:", self.shape_code)
        form.addRow("Име:", self.shape_name)
        form.addRow("Категория:", self.shape_category)
        form.addRow("Статус:", self.shape_state)
        form.addRow("Ревизия:", self.shape_revision)
        form.addRow("Заключена:", self.shape_locked_label)
        form.addRow("Начална посока:", self.shape_initial_direction)
        flags = QHBoxLayout()
        flags.addWidget(QLabel("Затворена"))
        flags.addWidget(self.shape_closed)
        flags.addWidget(QLabel("Симетрична"))
        flags.addWidget(self.shape_symmetric)
        flags.addWidget(QLabel("Огледална допустима"))
        flags.addWidget(self.shape_mirror)
        form.addRow("Свойства:", flags)
        form.addRow("Семейство:", self.shape_family)
        form.addRow("Alias на форма:", self.shape_alias)
        form.addRow("Връзки между параметри (JSON):", self.shape_relations)
        form.addRow("Ориентация по шаблон:", self.shape_orientation)
        form.addRow("Бележки:", self.shape_notes)

        self.segments_table = QTableWidget(0, 19)
        self.segments_table.setHorizontalHeaderLabels(
            [
                "№",
                "Код",
                "Тип",
                "Фикс. cm",
                "Ъгъл след",
                "Посока",
                "Метод",
                "Proj X",
                "Proj Y",
                "R cm",
                "Ъгъл режим",
                "Ъгъл код",
                "Мин°",
                "Макс°",
                "Посока чертане",
                "Размер",
                "Offset",
                "Задълж.",
                "Бележка",
            ]
        )
        self.segments_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.segments_table.horizontalHeader().setStretchLastSection(True)
        self.segments_table.itemChanged.connect(self._preview_from_editor)

        segment_buttons = QHBoxLayout()
        add_segment = QPushButton("Добави сегмент")
        add_segment.clicked.connect(self.add_segment)
        remove_segment = QPushButton("Премахни сегмент")
        remove_segment.clicked.connect(self.remove_segment)
        segment_buttons.addWidget(add_segment)
        segment_buttons.addWidget(remove_segment)
        segment_buttons.addStretch(1)

        form_buttons = QHBoxLayout()
        actions = [
            ("Нова форма", self.new_shape),
            ("Импорт draft JSON", self.import_shape_json),
            ("Създай matcher тест", self.create_matcher_test_for_shape),
            ("Контролирана ревизия", self.controlled_revision),
            ("Запази формата", self.save_current_form),
            ("Одобри формата", self.approve_current_shape),
            ("Изтрий чернова", self.delete_current_shape),
        ]
        for label, callback in actions:
            button = QPushButton(label)
            button.clicked.connect(callback)
            form_buttons.addWidget(button)

        self.shape_preview = ShapePreview()
        self.preview_rotation = QComboBox()
        self.preview_rotation.addItems(["0", "45", "90", "135", "180", "225", "270", "315"])
        self.preview_rotation.currentTextChanged.connect(self._update_shape_preview_transform)
        self.preview_mirror = QCheckBox("Огледай визуализацията")
        self.preview_mirror.toggled.connect(self._update_shape_preview_transform)
        preview_controls = QHBoxLayout()
        preview_controls.addWidget(QLabel("Покажи формата при завъртане:"))
        preview_controls.addWidget(self.preview_rotation)
        preview_controls.addWidget(self.preview_mirror)
        preview_controls.addStretch(1)

        right_layout = QVBoxLayout(editor)
        right_layout.addLayout(form)
        right_layout.addLayout(segment_buttons)
        right_layout.addWidget(self.segments_table, 1)
        right_layout.addLayout(form_buttons)
        right_layout.addLayout(preview_controls)
        right_layout.addWidget(self.shape_preview, 1)
        splitter.addWidget(editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(widget)
        layout.addWidget(splitter)
        return widget

    def _update_shape_preview_transform(self, *_args) -> None:
        try:
            rotation = float(self.preview_rotation.currentText())
        except (TypeError, ValueError, AttributeError):
            rotation = 0.0
        mirror = bool(getattr(self, "preview_mirror", None) and self.preview_mirror.isChecked())
        self.shape_preview.set_view_transform(rotation, mirror)

    def _refresh_form_list(self, select_code: str | None = None) -> None:
        self.form_list.blockSignals(True)
        self.form_list.clear()
        selected_row = -1
        if self.draft:
            for index, shape in enumerate(sorted(self.draft.shapes, key=lambda item: item.code)):
                marker = "🔒" if shape.locked else "✎"
                self.form_list.addItem(f"{marker} {shape.code} — {shape.name} (rev.{shape.revision}, {shape.state})")
                self.form_list.item(index).setData(Qt.UserRole, shape.code)
                if select_code and shape.code == select_code:
                    selected_row = index
        self.form_list.blockSignals(False)
        if self.form_list.count():
            self.form_list.setCurrentRow(max(selected_row, 0))
        else:
            self._clear_form_editor()

    def _selected_shape(self) -> ShapeDefinition | None:
        if self.draft is None:
            return None
        item = self.form_list.currentItem()
        code = item.data(Qt.UserRole) if item else None
        return self.draft.find_shape(str(code)) if code else None

    def _load_selected_form(self, _row: int) -> None:
        shape = self._selected_shape()
        if shape is None:
            self._clear_form_editor()
            return
        self._loading_form = True
        self.shape_code.setText(shape.code)
        self.shape_name.setText(shape.name)
        self.shape_category.setText(shape.category)
        self.shape_state.setCurrentText(shape.state)
        self.shape_revision.setText(str(shape.revision))
        self.shape_initial_direction.setCurrentText(shape.initial_direction)
        self.shape_closed.setChecked(shape.closed)
        self.shape_symmetric.setChecked(shape.symmetric)
        self.shape_mirror.setChecked(shape.mirror_allowed)
        self.shape_locked_label.setText("ДА" if shape.locked else "НЕ")
        self.shape_family.setText(shape.family_id)
        self.shape_alias.setText(shape.alias_of or "")
        self.shape_relations.setPlainText(
            json.dumps(
                [relation.__dict__ for relation in shape.parameter_relations],
                ensure_ascii=False,
                indent=2,
            )
            if shape.parameter_relations
            else ""
        )
        self.shape_orientation.setPlainText(shape.template_orientation)
        self.shape_notes.setPlainText(shape.notes)
        self.segments_table.setRowCount(len(shape.segments))
        for row, segment in enumerate(sorted(shape.segments, key=lambda item: item.index)):
            values = [
                str(segment.index),
                segment.length_code,
                segment.segment_type,
                _fmt_optional(segment.fixed_length_cm),
                _fmt_optional(segment.bend_after_deg),
                segment.bend_direction or "",
                segment.geometry_method,
                segment.projection_x_code or "",
                segment.projection_y_code or "",
                _fmt_optional(segment.radius_cm),
                segment.angle_mode,
                segment.angle_parameter_code or "",
                _fmt_optional(segment.min_angle_deg),
                _fmt_optional(segment.max_angle_deg),
                segment.drawing_direction or "",
                segment.dimension_position,
                _fmt_optional(segment.dimension_offset),
                "ДА" if segment.required else "НЕ",
                segment.notes,
            ]
            for column, value in enumerate(values):
                self.segments_table.setItem(row, column, QTableWidgetItem(value))
        self._loading_form = False
        self._set_shape_editable(not shape.locked)
        self.shape_preview.set_shape(shape)
        self._update_shape_preview_transform()

    def _clear_form_editor(self) -> None:
        self._loading_form = True
        for field in (self.shape_code, self.shape_name, self.shape_category, self.shape_revision):
            field.clear()
        self.shape_family.clear()
        self.shape_alias.clear()
        self.shape_relations.clear()
        self.shape_orientation.clear()
        self.shape_notes.clear()
        self.shape_locked_label.setText("—")
        self.segments_table.setRowCount(0)
        self._loading_form = False
        self.shape_preview.set_shape(None)
        self._update_shape_preview_transform()

    def _set_shape_editable(self, enabled: bool) -> None:
        for widget in (
            self.shape_code,
            self.shape_name,
            self.shape_category,
            self.shape_state,
            self.shape_initial_direction,
            self.shape_closed,
            self.shape_symmetric,
            self.shape_mirror,
            self.shape_family,
            self.shape_alias,
            self.shape_relations,
            self.shape_orientation,
            self.shape_notes,
            self.segments_table,
        ):
            widget.setEnabled(enabled)

    def add_segment(self) -> None:
        shape = self._selected_shape()
        if shape and shape.locked:
            self._info("Заключена форма. Първо създай контролирана ревизия.")
            return
        row = self.segments_table.rowCount()
        self.segments_table.insertRow(row)
        defaults = [
            str(row + 1),
            f"L{row + 1}",
            "LINE",
            "",
            "",
            "",
            "DIRECT_LENGTH",
            "",
            "",
            "",
            "AUTO",
            "",
            "",
            "",
            "",
            "AUTO",
            "6",
            "ДА",
            "",
        ]
        for column, value in enumerate(defaults):
            self.segments_table.setItem(row, column, QTableWidgetItem(value))
        self._preview_from_editor()

    def remove_segment(self) -> None:
        shape = self._selected_shape()
        if shape and shape.locked:
            self._info("Заключена форма. Първо създай контролирана ревизия.")
            return
        row = self.segments_table.currentRow()
        if row >= 0:
            self.segments_table.removeRow(row)
        for index in range(self.segments_table.rowCount()):
            self._set_cell(index, 0, str(index + 1))
        self._preview_from_editor()

    def new_shape(self) -> None:
        if self.draft is None:
            self._info("Първо отвори или създай чернова.")
            return
        numeric = [int(shape.code[1:]) for shape in self.draft.shapes if shape.code.startswith("F") and shape.code[1:].isdigit()]
        code = f"F{(max(numeric, default=9) + 1):03d}"
        shape = ShapeDefinition(
            code=code,
            name="НОВА ФОРМА",
            state="DRAFT",
            revision=1,
            locked=False,
            segments=[SegmentDefinition(index=1, length_code="L1")],
        )
        self.draft.shapes.append(shape)
        self._refresh_form_list(code)

    def import_shape_json(self) -> None:
        if self.draft is None:
            self._info("Първо отвори чернова.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Импорт на shape draft JSON", "", "JSON (*.json)")
        if not filename:
            return
        try:
            shapes = import_shape_draft_json(Path(filename))
        except (LegacyImportError, OSError) as exc:
            self._error(str(exc))
            return
        existing = {shape.code.upper() for shape in self.draft.shapes}
        added = []
        conflicts = []
        for shape in shapes:
            if shape.code.upper() in existing:
                conflicts.append(shape.code)
                continue
            self.draft.shapes.append(shape)
            existing.add(shape.code.upper())
            added.append(shape.code)
        self._refresh_form_list(added[0] if added else None)
        message = f"Добавени: {', '.join(added) or 'няма'}."
        if conflicts:
            message += f" Пропуснати конфликти: {', '.join(conflicts)}."
        self._info(message)

    def create_matcher_test_for_shape(self) -> None:
        """Turn one real set of segment dimensions into a permanent profile."""

        if self.draft is None or not self.save_current_form():
            return
        shape = self._selected_shape()
        if shape is None:
            return
        codes = required_dimension_codes(shape)
        default_value = "; ".join(f"{code}=100" for code in codes)
        prompt = (
            "Въведи един реален, проверен пример за размерите в cm.\n"
            "Този тест ще учи matcher-а да различава формата от сходните форми.\n"
            f"Нужни кодове: {', '.join(codes) if codes else 'няма — размерите са фиксирани'}"
        )
        dimensions_text, ok = QInputDialog.getText(
            self,
            f"Matcher тест за {shape.code}",
            prompt,
            text=default_value,
        )
        if not ok:
            return
        existing_ids = {
            str(test.get("test_id") or test.get("ID_ТЕСТ") or "")
            for test in self.draft.tests
        }
        counter = 1
        while True:
            test_id = f"T-{shape.code}-{counter:03d}"
            if test_id not in existing_ids:
                break
            counter += 1
        try:
            control_test = build_control_test(
                shape,
                dimensions_text,
                test_id=test_id,
            )
        except ValueError as exc:
            self._error(str(exc))
            return
        self.draft.tests.append(control_test)
        self._info(
            f"Създаден е {test_id} за {shape.code}.\n"
            f"Обща контролна дължина: {control_test['expected_total_cm']:.2f} cm.\n"
            "Пусни „Изпълни engine + matcher тестовете“ преди публикуване."
        )

    def controlled_revision(self) -> None:
        if self.draft is None:
            return
        shape = self._selected_shape()
        if shape is None:
            return
        if not shape.locked:
            self._info("Формата вече е редактируема чернова.")
            return
        dialog = ApprovalDialog(
            f"Контролирана ревизия на {shape.code}",
            CONTROLLED_REVISION_PHRASE,
            note_label="Причина за промяната:",
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.data()
        try:
            self.repository.create_controlled_shape_revision(
                self.draft,
                shape.code,
                authorized_by=data.by,
                reason=data.note,
                phrase=data.phrase,
            )
            self._refresh_form_list(shape.code)
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def save_current_form(self) -> bool:
        shape = self._selected_shape()
        if shape is None:
            return True
        if shape.locked:
            self._info("Заключената форма не може да се запише без контролирана ревизия.")
            return False
        try:
            candidate = self._shape_from_editor(shape)
        except ValueError as exc:
            self._error(str(exc))
            return False
        if self.draft:
            duplicate = next(
                (item for item in self.draft.shapes if item is not shape and item.code.upper() == candidate.code.upper()),
                None,
            )
            if duplicate:
                self._error(f"Кодът {candidate.code} вече съществува.")
                return False
        shape.__dict__.update(candidate.__dict__)
        self._refresh_form_list(shape.code)
        return True

    def approve_current_shape(self) -> None:
        if self.draft is None or not self.save_current_form():
            return
        shape = self._selected_shape()
        if shape is None:
            return
        by, ok = QInputDialog.getText(self, "Одобрение", "Одобрил:")
        if not ok:
            return
        note, ok = QInputDialog.getMultiLineText(self, "Одобрение", "Бележка / основание:")
        if not ok:
            return
        try:
            self.repository.approve_shape(self.draft, shape.code, approved_by=by, note=note)
            self._refresh_form_list(shape.code)
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def delete_current_shape(self) -> None:
        if self.draft is None:
            return
        shape = self._selected_shape()
        if shape is None:
            return
        if shape.locked:
            self._info("Заключена форма не се изтрива. Може да се създаде нова версия и формата да се маркира RETIRED.")
            return
        if QMessageBox.question(self, "Изтриване", f"Да се изтрие ли {shape.code} от черновата?") != QMessageBox.Yes:
            return
        self.draft.shapes.remove(shape)
        self._refresh_form_list()

    def _shape_from_editor(self, base: ShapeDefinition) -> ShapeDefinition:
        code = self.shape_code.text().strip().upper()
        name = self.shape_name.text().strip()
        if not code or not name:
            raise ValueError("Кодът и името на формата са задължителни.")
        relation_text = self.shape_relations.toPlainText().strip()
        try:
            relation_data = json.loads(relation_text) if relation_text else []
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Невалиден JSON във връзките между параметри: {exc.msg}."
            ) from exc
        if not isinstance(relation_data, list) or any(
            not isinstance(item, dict) for item in relation_data
        ):
            raise ValueError(
                "Връзките между параметри трябва да са JSON списък от обекти."
            )
        parameter_relations = [
            ParameterRelation.from_dict(item) for item in relation_data
        ]
        segments: list[SegmentDefinition] = []
        for row in range(self.segments_table.rowCount()):
            segments.append(
                SegmentDefinition(
                    index=row + 1,
                    length_code=self._cell(row, 1).strip() or f"L{row + 1}",
                    segment_type=self._cell(row, 2).strip().upper() or "LINE",
                    fixed_length_cm=_float_or_none(self._cell(row, 3)),
                    bend_after_deg=_float_or_none(self._cell(row, 4)),
                    bend_direction=self._cell(row, 5).strip().upper() or None,
                    geometry_method=(
                        self._cell(row, 6).strip().upper() or "DIRECT_LENGTH"
                    ),
                    projection_x_code=self._cell(row, 7).strip() or None,
                    projection_y_code=self._cell(row, 8).strip() or None,
                    radius_cm=_float_or_none(self._cell(row, 9)),
                    required=self._cell(row, 17).strip().upper()
                    not in {"НЕ", "NO", "FALSE", "0"},
                    drawing_direction=self._cell(row, 14).strip().upper() or None,
                    dimension_position=(
                        self._cell(row, 15).strip().upper() or "AUTO"
                    ),
                    dimension_offset=_float_or_none(self._cell(row, 16)) or 6.0,
                    angle_mode=self._cell(row, 10).strip().upper() or "AUTO",
                    angle_parameter_code=self._cell(row, 11).strip() or None,
                    min_angle_deg=_float_or_none(self._cell(row, 12)),
                    max_angle_deg=_float_or_none(self._cell(row, 13)),
                    notes=self._cell(row, 18).strip(),
                    raw=(base.segments[row].raw if row < len(base.segments) else {}),
                )
            )
        return ShapeDefinition(
            code=code,
            name=name,
            category=self.shape_category.text().strip(),
            state=self.shape_state.currentText(),
            revision=base.revision,
            locked=False,
            active=base.active,
            initial_direction=self.shape_initial_direction.currentText(),
            closed=self.shape_closed.isChecked(),
            symmetric=self.shape_symmetric.isChecked(),
            mirror_allowed=self.shape_mirror.isChecked(),
            template_orientation=self.shape_orientation.toPlainText().strip(),
            notes=self.shape_notes.toPlainText().strip(),
            segments=segments,
            rendering=base.rendering,
            recognition=base.recognition,
            dimensions=base.dimensions,
            tests=base.tests,
            family_id=self.shape_family.text().strip().upper(),
            alias_of=self.shape_alias.text().strip().upper() or None,
            parameter_relations=parameter_relations,
            legacy_raw=base.legacy_raw,
            controlled_revision=base.controlled_revision,
        )

    def _preview_from_editor(self, *_args) -> None:
        if self._loading_form:
            return
        shape = self._selected_shape()
        if shape is None or shape.locked:
            return
        try:
            self.shape_preview.set_shape(self._shape_from_editor(shape))
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    def _build_rules_tab(self) -> QWidget:
        widget = QWidget()
        self.rules_table = QTableWidget(0, 9)
        self.rules_table.setHorizontalHeaderLabels(
            ["Код", "Име", "Scope", "Priority", "Enabled", "WHEN JSON", "THEN JSON", "Ръчно одобрение", "Бележки"]
        )
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.rules_table.horizontalHeader().setStretchLastSection(True)
        buttons = QHBoxLayout()
        add = QPushButton("Добави правило")
        add.clicked.connect(self.add_rule)
        remove = QPushButton("Премахни правило")
        remove.clicked.connect(self.remove_rule)
        save = QPushButton("Приложи правилата")
        save.clicked.connect(self.save_rules)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(save)
        buttons.addStretch(1)
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Правилата са декларативни данни. Произволен Python код не се допуска в engine пакета."))
        layout.addWidget(self.rules_table, 1)
        layout.addLayout(buttons)
        return widget

    def _refresh_rules(self) -> None:
        rules = self.draft.rules if self.draft else []
        self.rules_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            values = [
                rule.code,
                rule.name,
                rule.scope,
                str(rule.priority),
                "ДА" if rule.enabled else "НЕ",
                json.dumps(rule.when, ensure_ascii=False),
                json.dumps(rule.then, ensure_ascii=False),
                "ДА" if rule.requires_manual_approval else "НЕ",
                rule.notes,
            ]
            for column, value in enumerate(values):
                self.rules_table.setItem(row, column, QTableWidgetItem(value))

    def add_rule(self) -> None:
        row = self.rules_table.rowCount()
        self.rules_table.insertRow(row)
        defaults = [f"R-{row + 1:03d}", "Ново правило", "GENERAL", "100", "ДА", "{}", "[]", "НЕ", ""]
        for column, value in enumerate(defaults):
            self.rules_table.setItem(row, column, QTableWidgetItem(value))

    def remove_rule(self) -> None:
        row = self.rules_table.currentRow()
        if row >= 0:
            self.rules_table.removeRow(row)

    def save_rules(self) -> bool:
        if self.draft is None:
            return True
        rules: list[RuleDefinition] = []
        try:
            for row in range(self.rules_table.rowCount()):
                rules.append(
                    RuleDefinition(
                        code=self._table_cell(self.rules_table, row, 0).strip(),
                        name=self._table_cell(self.rules_table, row, 1).strip(),
                        scope=self._table_cell(self.rules_table, row, 2).strip(),
                        priority=int(self._table_cell(self.rules_table, row, 3) or 100),
                        enabled=_yes(self._table_cell(self.rules_table, row, 4)),
                        when=json.loads(self._table_cell(self.rules_table, row, 5) or "{}"),
                        then=json.loads(self._table_cell(self.rules_table, row, 6) or "[]"),
                        requires_manual_approval=_yes(self._table_cell(self.rules_table, row, 7)),
                        notes=self._table_cell(self.rules_table, row, 8),
                    )
                )
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(f"Грешка в правилата: {exc}")
            return False
        self.draft.rules = rules
        return True

    # ------------------------------------------------------------------
    # Recognition laws
    # ------------------------------------------------------------------
    def _build_recognition_laws_tab(self) -> QWidget:
        widget = QWidget()
        defaults = RecognitionPolicy()
        self.recognition_fields: dict[str, QLineEdit] = {}
        self.recognition_checks: dict[str, QCheckBox] = {}
        form = QFormLayout()
        labels = {
            "endpoint_tolerance_raw": "Допуск обща крайна точка:",
            "max_component_segments": "Макс. сегменти във форма:",
            "max_path_candidates": "Макс. проверявани вериги:",
            "min_search_distance_raw": "Мин. радиус търсене:",
            "max_search_distance_raw": "Твърд макс. радиус:",
            "search_distance_ratio": "Радиус спрямо L:",
            "extended_search_distance_raw": "Втори радиус — максимум (само ръчен контрол):",
            "extended_search_distance_ratio": "Втори радиус спрямо L:",
            "max_length_difference_abs": "Мин. допустима абсолютна ΔL:",
            "max_length_difference_ratio": "Твърда ΔL спрямо L:",
            "angle_match_tolerance_deg": "Допуск на ъглите (°):",
            "length_ratio_tolerance": "Допуск на пропорциите:",
            "master_ambiguity_score_gap": "Праг за конфликт между MASTER форми:",
            "schematic_scale_min": "Мин. допустим схематичен мащаб:",
            "schematic_scale_max": "Макс. допустим схематичен мащаб:",
            "schematic_scale_exact_tolerance": "Допуск за мащаб 1:1:",
            "schematic_scale_score_penalty": "Наказание за схематичен мащаб:",
            "subset_chain_score_penalty": "Наказание за непълна MASTER верига:",
            "collinear_merge_tolerance_deg": "Сливане на колинеарни сегменти (°):",
            "leader_attach_distance_raw": "Мин. близост за лидер към надпис:",
            "leader_attach_height_ratio": "Близост за лидер спрямо височината:",
            "beam_n8_hook_length_cm": "Гредово N8 — фиксиран винкел (cm):",
            "beam_n8_long_segment_tolerance_cm": "Гредово N8 — допуск на дългия сегмент (cm):",
            "beam_n8_short_segment_max_ratio": "Гредово N8 — макс. визуално късо рамо / дълго:",
            "beam_n8_min_raw_turn_deg": "Гредово N8 — мин. сурова чупка (°):",
            "beam_n8_max_raw_turn_deg": "Гредово N8 — макс. сурова чупка (°):",
        }
        for key, label in labels.items():
            field = QLineEdit(str(getattr(defaults, key)))
            self.recognition_fields[key] = field
            form.addRow(label, field)

        check_labels = {
            "reject_explicit_diameter_mismatch": "Отхвърляй изрично грешен диаметър",
            "reject_branched_candidate": "Отхвърляй разклонени геометрии",
            "allow_reversed_direction": "Приемай обратно изчертан ред на сегментите",
            "rotation_invariant": "Игнорирай абсолютното завъртане",
            "mirror_requires_shape_permission": "Огледало без разрешение от MASTER = ръчен контрол",
            "prefer_maximal_master_chain": "Предпочитай пълната MASTER форма пред отделен сегмент",
            "include_text_mtext_annotations": "Разпознавай и валидни TEXT / MTEXT надписи",
            "use_visual_text_extent": "Мери близостта до видимата рамка на надписа",
            "use_attached_leader_points": "Използвай свързаните лидерни линии при търсене",
            "beam_n8_middle_rule_enabled": "Прилагай одобреното правило за средно N8 в греди: L-15 + 15 cm, 90°",
        }
        for key, label in check_labels.items():
            check = QCheckBox(label)
            check.setChecked(bool(getattr(defaults, key)))
            self.recognition_checks[key] = check
            form.addRow("", check)

        self.recognition_laws_text = QPlainTextEdit()
        self.recognition_laws_text.setReadOnly(True)
        self.recognition_laws_text.setPlainText("\n".join(defaults.laws_bg()))
        reset_button = QPushButton("Върни безопасните стойности")
        reset_button.clicked.connect(self._reset_recognition_laws)
        save_button = QPushButton("Запиши законите в черновата")
        save_button.clicked.connect(self.save_recognition_laws)
        buttons = QHBoxLayout()
        buttons.addWidget(reset_button)
        buttons.addWidget(save_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(widget)
        note = QLabel(
            "Тези стойности са контролирани закони на engine-а. Променят се в чернова и влизат в сила "
            "само след проверка и публикуване. Силен кандидат никога не се одобрява автоматично."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.recognition_laws_text, 1)
        return widget

    def _reset_recognition_laws(self) -> None:
        defaults = RecognitionPolicy()
        for key, field in self.recognition_fields.items():
            field.setText(str(getattr(defaults, key)))
        for key, check in self.recognition_checks.items():
            check.setChecked(bool(getattr(defaults, key)))

    def _refresh_recognition_laws(self) -> None:
        if not hasattr(self, "recognition_fields"):
            return
        policy = RecognitionPolicy.from_settings(self.draft.settings if self.draft else None)
        for key, field in self.recognition_fields.items():
            field.setText(str(getattr(policy, key)))
        for key, check in self.recognition_checks.items():
            check.setChecked(bool(getattr(policy, key)))
        self.recognition_laws_text.setPlainText("\n".join(policy.laws_bg()))

    def save_recognition_laws(self) -> bool:
        if self.draft is None:
            return False
        current = RecognitionPolicy.from_settings(self.draft.settings)
        values = current.to_dict()
        try:
            for key, field in self.recognition_fields.items():
                raw = field.text().strip().replace(",", ".")
                default = getattr(current, key)
                values[key] = int(raw) if isinstance(default, int) and not isinstance(default, bool) else float(raw)
            for key, check in self.recognition_checks.items():
                values[key] = check.isChecked()
            policy = RecognitionPolicy.from_settings(values)
        except (TypeError, ValueError) as exc:
            self._error(f"Невалидна стойност в законите за разпознаване: {exc}")
            return False
        self.draft.settings = dict(self.draft.settings)
        self.draft.settings["geometry_recognition"] = policy.to_dict()
        self.recognition_laws_text.setPlainText("\n".join(policy.laws_bg()))
        return True

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------
    def _build_categories_tab(self) -> QWidget:
        widget = QWidget()
        self.categories_table = QTableWidget(0, 5)
        self.categories_table.setHorizontalHeaderLabels(["Код", "Име на Excel лист", "Aliases (;) ", "Enabled", "Sort"])
        self.categories_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        buttons = QHBoxLayout()
        add = QPushButton("Добави категория")
        add.clicked.connect(self.add_category)
        remove = QPushButton("Премахни категория")
        remove.clicked.connect(self.remove_category)
        save = QPushButton("Приложи категориите")
        save.clicked.connect(self.save_categories)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(save)
        buttons.addStretch(1)
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel(
                "Промяна на имена/структура на листове влияе на Excel експорта. Публикувай такава промяна само с нов шаблон и контролни тестове."
            )
        )
        layout.addWidget(self.categories_table, 1)
        layout.addLayout(buttons)
        return widget

    def _refresh_categories(self) -> None:
        categories = self.draft.categories if self.draft else []
        self.categories_table.setRowCount(len(categories))
        for row, category in enumerate(categories):
            values = [
                category.code,
                category.sheet_name,
                "; ".join(category.aliases),
                "ДА" if category.enabled else "НЕ",
                str(category.sort_order),
            ]
            for column, value in enumerate(values):
                self.categories_table.setItem(row, column, QTableWidgetItem(value))

    def add_category(self) -> None:
        row = self.categories_table.rowCount()
        self.categories_table.insertRow(row)
        for column, value in enumerate([f"CAT_{row + 1}", "Нов лист", "", "ДА", str((row + 1) * 10)]):
            self.categories_table.setItem(row, column, QTableWidgetItem(value))

    def remove_category(self) -> None:
        row = self.categories_table.currentRow()
        if row >= 0:
            self.categories_table.removeRow(row)

    def save_categories(self) -> bool:
        if self.draft is None:
            return True
        categories: list[CategoryDefinition] = []
        try:
            for row in range(self.categories_table.rowCount()):
                aliases = [value.strip() for value in self._table_cell(self.categories_table, row, 2).split(";") if value.strip()]
                categories.append(
                    CategoryDefinition(
                        code=self._table_cell(self.categories_table, row, 0).strip(),
                        sheet_name=self._table_cell(self.categories_table, row, 1).strip(),
                        aliases=aliases,
                        enabled=_yes(self._table_cell(self.categories_table, row, 3)),
                        sort_order=int(self._table_cell(self.categories_table, row, 4) or 100),
                    )
                )
        except ValueError as exc:
            self._error(f"Грешка в категориите: {exc}")
            return False
        self.draft.categories = categories
        return True

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    def _build_tests_tab(self) -> QWidget:
        widget = QWidget()
        self.tests_table = QTableWidget(0, 6)
        self.tests_table.setHorizontalHeaderLabels(["Тест", "Форма", "Очаквано cm", "Изчислено cm", "Резултат", "Съобщение"])
        self.tests_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tests_table.horizontalHeader().setStretchLastSection(True)
        run_button = QPushButton("Изпълни engine + matcher тестовете")
        run_button.clicked.connect(self.run_tests)
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel(
                "Production engine не се публикува, ако има неуспешен тест. "
                "Всеки контролен тест автоматично става matcher профил; после всички форми "
                "се проверяват при завъртане, огледало, обратна посока и схематичен мащаб."
            )
        )
        layout.addWidget(run_button)
        layout.addWidget(self.tests_table, 1)
        return widget

    def run_tests(self) -> None:
        if self.draft is None:
            self._info("Няма отворена чернова.")
            return
        # Заключената MASTER форма е само за четене и няма какво да се
        # записва. Собственик, 19.09.2026: при отворена чернова v0.3.15 и
        # заключена форма в списъка бутонът връщаше „Заключената форма не може
        # да се запише без контролирана ревизия." и тестовете НЕ се пускаха —
        # таблицата оставаше празна без обяснение. Същата защита вече стои
        # пред публикуването (``_commit_all_tabs``).
        shape = self._selected_shape()
        if shape is not None and not shape.locked and not self.save_current_form():
            return
        results = run_engine_tests(self.draft)
        audit = audit_engine_recognition(self.draft)
        generated_audit = audit_engine_generated_regression(self.draft)
        self.tests_table.setRowCount(len(results) + 2)
        for row, result in enumerate(results):
            values = [
                result.test_id,
                result.shape_code,
                _fmt_optional(result.expected_cm),
                _fmt_optional(result.calculated_cm),
                "УСПЕШЕН" if result.passed else "ГРЕШКА",
                result.message,
            ]
            for column, value in enumerate(values):
                self.tests_table.setItem(row, column, QTableWidgetItem(value))
        audit_values = [
            "MATCHER-MATRIX",
            f"{audit.shape_count} форми / {audit.profile_count} профила",
            str(audit.case_count),
            str(audit.passed_count),
            "УСПЕШЕН" if audit.passed else "ГРЕШКА",
            (
                f"{audit.passed_count}/{audit.case_count} трансформации; "
                f"конфликтни случаи: {audit.ambiguous_count}"
            ),
        ]
        for column, value in enumerate(audit_values):
            self.tests_table.setItem(
                len(results),
                column,
                QTableWidgetItem(str(value)),
            )
        generated_values = [
            "MATCHER-GENERATED",
            f"{generated_audit.shape_count} форми / {generated_audit.profile_count} профила",
            str(generated_audit.case_count),
            str(generated_audit.passed_count),
            "УСПЕШЕН" if generated_audit.passed else "ГРЕШКА",
            (
                "Pairwise: параметри, LINE/LWPOLYLINE и координатен толеранс; "
                f"конфликтни случаи: {generated_audit.ambiguous_count}"
            ),
        ]
        for column, value in enumerate(generated_values):
            self.tests_table.setItem(
                len(results) + 1,
                column,
                QTableWidgetItem(str(value)),
            )
        passed = sum(result.passed for result in results)
        self._info(
            f"Engine тестове: {passed}/{len(results)}\n"
            f"Matcher матрица: {audit.passed_count}/{audit.case_count}\n"
            f"Генерирана матрица: {generated_audit.passed_count}/{generated_audit.case_count}\n"
            f"Конфликтни, но разпознати случаи: {audit.ambiguous_count}"
        )

    # ------------------------------------------------------------------
    # Assets and raw JSON
    # ------------------------------------------------------------------
    def _build_assets_tab(self) -> QWidget:
        widget = QWidget()
        self.assets_table = QTableWidget(0, 5)
        self.assets_table.setHorizontalHeaderLabels(["Role", "Файл", "SHA256", "Път", "Бележки"])
        self.assets_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.assets_table.horizontalHeader().setStretchLastSection(True)
        attach = QPushButton("Прикачи MASTER файл към черновата")
        attach.clicked.connect(self.attach_asset)
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel("Оригиналните MASTER файлове се копират в registry и се следят с SHA256. Те не се редактират на място.")
        )
        layout.addWidget(attach)
        layout.addWidget(self.assets_table, 1)
        return widget

    def _refresh_assets(self) -> None:
        assets = self.draft.source_assets if self.draft else []
        self.assets_table.setRowCount(len(assets))
        for row, asset in enumerate(assets):
            values = [asset.role, asset.filename, asset.sha256, asset.relative_path or "", asset.notes]
            for column, value in enumerate(values):
                self.assets_table.setItem(row, column, QTableWidgetItem(value))

    def attach_asset(self) -> None:
        if self.draft is None:
            self._info("Първо отвори чернова.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Прикачи MASTER файл")
        if not filename:
            return
        roles = ["MASTER_ENGINE_HTML", "MASTER_LIBRARY_XLSX", "MASTER_EXCEL_TEMPLATE", "CONTROL_DXF", "CONTROL_REVIEW_XLSX", "OTHER"]
        role, ok = QInputDialog.getItem(self, "Role", "Тип на файла:", roles, editable=False)
        if not ok:
            return
        notes, _ = QInputDialog.getText(self, "Бележки", "Бележки:")
        try:
            self.draft.source_assets.append(self.repository.attach_asset(Path(filename), role, notes))
            self._refresh_assets()
        except OSError as exc:
            self._error(str(exc))

    def _build_raw_tab(self) -> QWidget:
        widget = QWidget()
        self.raw_editor = QPlainTextEdit()
        self.raw_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        reload_button = QPushButton("Презареди JSON от модела")
        reload_button.clicked.connect(self.refresh_raw)
        apply_button = QPushButton("Провери и приложи JSON")
        apply_button.clicked.connect(self.apply_raw_json)
        buttons = QHBoxLayout()
        buttons.addWidget(reload_button)
        buttons.addWidget(apply_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel(
                "Разширен режим. Позволява добавяне на нови полета и правила без промяна на Python кода. Невалиден JSON не се прилага."
            )
        )
        layout.addLayout(buttons)
        layout.addWidget(self.raw_editor, 1)
        return widget

    def refresh_raw(self) -> None:
        self.raw_editor.setPlainText(
            json.dumps(self.draft.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) if self.draft else ""
        )

    def apply_raw_json(self) -> None:
        try:
            data = json.loads(self.raw_editor.toPlainText())
            candidate = EnginePackage.from_dict(data)
            candidate.state = "DRAFT"
            issues = validate_engine(candidate, parent=None, for_publish=False)
            if has_errors(issues):
                self._show_validation_issues(issues, "JSON не е приложен")
                return
            self._set_draft(candidate)
            self._info("JSON е приложен към текущата чернова. Запази черновата, за да се запише на диск.")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._error(f"Невалиден JSON: {exc}")

    # ------------------------------------------------------------------
    # Draft lifecycle helpers
    # ------------------------------------------------------------------
    def _load_draft_path(self, path: Path) -> None:
        try:
            self._set_draft(self.repository.load_draft(path))
        except (OSError, ValueError) as exc:
            self._error(str(exc))

    def _set_draft(self, draft: EnginePackage) -> None:
        self.draft = draft
        self.draft_label.setText(f"Чернова: <b>{draft.engine_id} v{draft.version}</b>")
        self._set_editor_enabled(True)
        self._refresh_form_list()
        self._refresh_rules()
        self._refresh_recognition_laws()
        self._refresh_categories()
        self._refresh_assets()
        self.refresh_raw()
        self.tests_table.setRowCount(0)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, enabled)
        self.validate_button.setEnabled(enabled)
        self.save_draft_button.setEnabled(enabled)

    def _commit_all_tabs(self) -> bool:
        if self.draft is None:
            return False

        # A locked MASTER form is read-only and therefore has nothing to save.
        # Publishing/validating the engine must not force a controlled revision
        # merely because a locked form is currently selected in the Forms tab.
        shape = self._selected_shape()
        if shape is not None and not shape.locked and not self.save_current_form():
            return False

        return self.save_rules() and self.save_recognition_laws() and self.save_categories()

    def save_draft(self) -> None:
        if self.draft is None:
            self._info("Няма отворена чернова.")
            return
        if not self._commit_all_tabs():
            return
        try:
            path = self.repository.save_draft(self.draft)
            self.refresh_raw()
            self.refresh_versions()
            self._info(f"Черновата е записана:\n{path}")
        except EngineValidationError as exc:
            self._show_validation_issues(exc.issues, "Черновата не е записана")
        except EngineRepositoryError as exc:
            self._error(str(exc))

    def validate_draft(self) -> None:
        if self.draft is None:
            return
        if not self._commit_all_tabs():
            return
        parent = None
        if self.draft.parent_version:
            try:
                parent = self.repository.load_published(self.draft.engine_id, self.draft.parent_version)
            except EngineRepositoryError:
                parent = None
        issues = validate_engine(self.draft, parent=parent, for_publish=False)
        if issues:
            self._show_validation_issues(issues, "Резултат от проверката")
        else:
            self._info("Няма открити структурни грешки.")

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def _cell(self, row: int, column: int) -> str:
        return self._table_cell(self.segments_table, row, column)

    @staticmethod
    def _table_cell(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text() if item else ""

    def _set_cell(self, row: int, column: int, value: str) -> None:
        item = self.segments_table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.segments_table.setItem(row, column, item)
        item.setText(value)

    def _show_validation_issues(self, issues, title: str) -> None:
        summary = issue_summary(issues)
        text = "\n".join(
            f"[{issue.severity}] {issue.code} | {issue.path}\n{issue.message}"
            for issue in issues[:80]
        )
        if len(issues) > 80:
            text += f"\n… и още {len(issues) - 80} проблема."
        QMessageBox.warning(
            self,
            title,
            f"Грешки: {summary.get('ERROR', 0)}, предупреждения: {summary.get('WARNING', 0)}\n\n{text}",
        )

    def _info(self, message: str) -> None:
        QMessageBox.information(self, "LION REBAR", message)

    def _error(self, message: str) -> None:
        QMessageBox.critical(self, "LION REBAR — грешка", message)


def _float_or_none(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"Невалидно число: {value}") from exc


def _fmt_optional(value: Any) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"
    except (TypeError, ValueError):
        return str(value)


def _yes(value: str) -> bool:
    return value.strip().upper() in {"ДА", "YES", "TRUE", "1", "Y"}
