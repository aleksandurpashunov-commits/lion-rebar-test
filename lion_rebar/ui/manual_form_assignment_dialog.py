from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextBrowser,
    QVBoxLayout,
)

from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.manual_assignment import F006_COMPOSITE_KIND, ManualGeometrySummary
from lion_rebar.manual_dimensions import (
    DIMENSION_SOURCE_ANNOTATION,
    DIMENSION_SOURCE_DXF,
    DIMENSION_SOURCE_MANUAL,
    ManualDimensionResolution,
    length_mismatch_is_material,
    resolve_manual_dimensions,
)

DRAFT_CODE = "__NEW_DRAFT__"
SELECT_SHAPE_CODE = "__SELECT_SHAPE__"


class ManualFormAssignmentDialog(QDialog):
    """Owner-controlled mapping of selected DXF geometry to one MASTER form.

    Geometry determines the shape. Production length is explicitly controlled by
    the owner and is never silently replaced by a schematic DXF measurement.
    """

    REASONS = (
        "Не е разпознато автоматично",
        "Грешна MASTER форма",
        "Намерена е само част от желязото",
        "Стреме / затворена форма",
        "Столче",
        "Фиба",
        "DXF и надпис/размери си противоречат",
        "Липсва MASTER форма",
        "Друга причина",
    )

    def __init__(
        self,
        *,
        row: dict,
        summary: ManualGeometrySummary,
        shapes: Iterable[ShapeDefinition],
        parent=None,
    ):
        super().__init__(parent)
        self.row = dict(row)
        self.summary = summary
        self.shapes = tuple(
            shape
            for shape in shapes
            if shape.active and str(shape.state).upper() != "RETIRED"
        )
        self.result_payload: dict | None = None
        self._dimension_resolution: ManualDimensionResolution | None = None
        self._dimension_error = ""

        self.setWindowTitle("Задай форма — ръчна корекция")
        self.resize(780, 690)
        self.setMinimumSize(660, 540)

        intro = QLabel(
            "Това действие записва изрична връзка Надпис ↔ избрана DXF геометрия ↔ MASTER форма. "
            "Формата се определя от геометрията, а производствената дължина се избира отделно. "
            "При нова форма се създава готов кандидат-пакет за Engine Studio. "
            "MASTER библиотеката не се променя автоматично."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            "background:#eff6ff; border:1px solid #bfdbfe; padding:8px; color:#1e3a8a;"
        )

        form = QFormLayout()
        self.annotation_label = QLabel(str(row.get("annotation_text") or "—"))
        self.annotation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Надпис:", self.annotation_label)

        self.handles_label = QLabel(", ".join(summary.handles) or "—")
        self.handles_label.setWordWrap(True)
        self.handles_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Избрани handles:", self.handles_label)

        self.shape_combo = QComboBox()
        self.shape_combo.addItem("— Избери изрично MASTER форма —", SELECT_SHAPE_CODE)
        self.shape_combo.insertSeparator(self.shape_combo.count())
        sorted_shapes = sorted(self.shapes, key=lambda item: (item.code, item.name))
        for shape in sorted_shapes:
            self.shape_combo.addItem(f"{shape.code} — {shape.name}", shape.code)
        self.shape_combo.insertSeparator(self.shape_combo.count())
        self.shape_combo.addItem("Нова / липсваща форма — запази като чернова", DRAFT_CODE)
        self.shape_combo.currentIndexChanged.connect(self._shape_changed)
        form.addRow("Форма:", self.shape_combo)

        self.shape_choice_warning = QLabel()
        self.shape_choice_warning.setWordWrap(True)
        self.shape_choice_warning.setStyleSheet(
            "color:#9a3412; background:#fff7ed; border:1px solid #fdba74; padding:6px;"
        )
        self.shape_choice_warning.setVisible(False)
        form.addRow("", self.shape_choice_warning)

        self.draft_name = QLineEdit()
        self.draft_name.setPlaceholderText("Например: столче h=45 с крачета")
        self.draft_name.setVisible(False)
        self.draft_name.textChanged.connect(self._refresh_accept)
        self.draft_name_label = QLabel("Име на черновата:")
        self.draft_name_label.setVisible(False)
        form.addRow(self.draft_name_label, self.draft_name)

        self.reason_combo = QComboBox()
        self.reason_combo.addItems(self.REASONS)
        form.addRow("Причина:", self.reason_combo)

        self.dimension_source_combo = QComboBox()
        declared = _optional_float(row.get("declared_length_cm"))
        annotation_label = (
            f"От надписа — производствена L = {declared:g} cm (препоръчано)"
            if declared is not None
            else "От надписа — няма валидна L"
        )
        self.dimension_source_combo.addItem(annotation_label, DIMENSION_SOURCE_ANNOTATION)
        self.dimension_source_combo.addItem(
            f"От измерената DXF геометрия — L = {summary.total_length_cm:.2f} cm",
            DIMENSION_SOURCE_DXF,
        )
        self.dimension_source_combo.addItem(
            "Ръчно въведени размери на сегментите",
            DIMENSION_SOURCE_MANUAL,
        )
        self.dimension_source_combo.currentIndexChanged.connect(self._dimension_changed)
        form.addRow("Източник на размерите:", self.dimension_source_combo)

        self.manual_lengths_edit = QLineEdit()
        self.manual_lengths_edit.setPlaceholderText("Например: 61 + 18 + 61")
        self.manual_lengths_edit.textChanged.connect(self._refresh_accept)
        self.manual_lengths_label = QLabel("Размери на сегментите:")
        form.addRow(self.manual_lengths_label, self.manual_lengths_edit)

        self.dimension_status = QLabel()
        self.dimension_status.setWordWrap(True)
        self.dimension_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Контрол на L:", self.dimension_status)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(
            "По желание — ако оставиш празно, програмата ще създаде техническа бележка"
        )
        form.addRow("Бележка:", self.note_edit)

        self.training_check = QCheckBox("Запази геометричния пример за бъдещо обучение на разпознаването")
        # Only an exact, physically connected contour is safe as a recognition
        # prototype.  Composite symbols and virtually healed gaps are valuable
        # diagnostic cases, but must not silently become training truth.
        self._safe_training_example = bool(
            self.summary.training_eligible
            and self.summary.physical_connected
            and not self.summary.inferred_composite
        )
        self.training_check.setChecked(self._safe_training_example)
        if not self._safe_training_example:
            self.training_check.setEnabled(False)
            self.training_check.setToolTip(
                "Оставено е изключено: примерът е схематичен или съдържа физически "
                "прекъснати DXF компоненти. Той ще остане в диагностичния пакет."
            )

        self.confirm_check = QCheckBox(
            "Потвърждавам, че избраните DXF обекти образуват точно тази армировъчна позиция"
        )
        self.confirm_check.stateChanged.connect(self._refresh_accept)

        self.details = QTextBrowser()
        self.details.setHtml(self._summary_html())

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color:#b91c1c; font-weight:700;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Запази задаването")
        self.save_button.setEnabled(False)
        buttons.accepted.connect(self._accept_assignment)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.training_check)
        layout.addWidget(self.confirm_check)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.details, 1)
        layout.addWidget(buttons)

        ambiguous_codes = tuple(
            str(value)
            for value in row.get("master_ambiguous_shape_codes", [])
            if str(value)
        )
        requires_explicit_shape = bool(row.get("master_conflict") or len(ambiguous_codes) > 1)
        preferred_code = str(row.get("master_shape_code") or "")
        preferred_index = self.shape_combo.findData(preferred_code)
        if requires_explicit_shape:
            self.shape_combo.setCurrentIndex(self.shape_combo.findData(SELECT_SHAPE_CODE))
            self.shape_choice_warning.setText(
                "Конфликт между MASTER форми"
                + (f": {', '.join(ambiguous_codes)}." if ambiguous_codes else ".")
                + " Изборът е умишлено празен — посочи правилния код ръчно."
            )
            self.shape_choice_warning.setVisible(True)
        elif preferred_index >= 0:
            self.shape_combo.setCurrentIndex(preferred_index)
        else:
            self.shape_combo.setCurrentIndex(self.shape_combo.findData(SELECT_SHAPE_CODE))

        if (
            not requires_explicit_shape
            and (self.summary.closed or self.summary.composite_kind == F006_COMPOSITE_KIND)
        ):
            preferred = next(
                (
                    shape.code
                    for shape in sorted_shapes
                    if shape.closed and "СТРЕМ" in f"{shape.name} {shape.category}".upper()
                ),
                None,
            ) or next((shape.code for shape in sorted_shapes if shape.closed), None)
            if preferred:
                index = self.shape_combo.findData(preferred)
                if index >= 0:
                    self.shape_combo.setCurrentIndex(index)
        if self.summary.composite_kind == F006_COMPOSITE_KIND:
            self.reason_combo.setCurrentText("Стреме / затворена форма")
            self.dimension_source_combo.setCurrentIndex(
                self.dimension_source_combo.findData(DIMENSION_SOURCE_DXF)
            )
        elif declared is not None:
            self.dimension_source_combo.setCurrentIndex(
                self.dimension_source_combo.findData(DIMENSION_SOURCE_ANNOTATION)
            )
        else:
            self.dimension_source_combo.setCurrentIndex(
                self.dimension_source_combo.findData(DIMENSION_SOURCE_DXF)
            )

        self._shape_changed()
        self._dimension_changed()

    def _summary_html(self) -> str:
        lengths = " + ".join(f"{value:.2f}" for value in self.summary.segment_lengths_cm) or "—"
        turns = ", ".join(f"{value:.1f}°" for value in self.summary.turn_angles_deg) or "—"
        warnings = "<br>".join(_escape(value) for value in self.summary.warnings) or "—"
        composite = (
            f"<br><b>Съставна форма:</b> {_escape(self.summary.composite_kind or '—')}"
            f"<br><b>Компоненти в DXF:</b> {self.summary.component_count}"
            if self.summary.inferred_composite
            else ""
        )
        inference = (
            f"<p style='color:#166534; background:#f0fdf4; padding:6px;'>"
            f"<b>Инженерно сглобяване:</b> {_escape(self.summary.inference_note or '')}</p>"
            if self.summary.inferred_composite
            else ""
        )
        gap_evidence = ""
        if self.summary.near_gap_healed:
            gap_evidence = (
                "<br><b>Контролирана празнина:</b> "
                f"{self.summary.near_gap_bridge_gap_raw or 0.0:.3f} cm"
                + (
                    " между "
                    + _escape(" / ".join(self.summary.near_gap_bridge_handles))
                    if self.summary.near_gap_bridge_handles
                    else ""
                )
            )
        return (
            "<h3>Извлечена геометрия — използва се за формата</h3>"
            f"<p><b>Типове:</b> {_escape(', '.join(self.summary.entity_types) or '—')}<br>"
            f"<b>Слоеве:</b> {_escape(', '.join(self.summary.layers) or '—')}<br>"
            f"<b>Логически свързана:</b> {'ДА' if self.summary.connected else 'НЕ'}<br>"
            f"<b>Физически свързана в DXF:</b> {'ДА' if self.summary.physical_connected else 'НЕ'}<br>"
            f"<b>Допустим обучаващ прототип:</b> {'ДА' if self.summary.training_eligible else 'НЕ'}"
            f"<br><b>Дъгови сегменти:</b> {self.summary.curve_segment_count}"
            f"{gap_evidence}<br>"
            f"<b>Разклонена:</b> {'ДА' if self.summary.branched else 'НЕ'}<br>"
            f"<b>Затворена:</b> {'ДА' if self.summary.closed else 'НЕ'}"
            f"{composite}<br>"
            f"<b>Сегменти:</b> {self.summary.segment_count}<br>"
            f"<b>Измерени DXF дължини:</b> {_escape(lengths)}<br>"
            f"<b>Измерена DXF обща L:</b> {self.summary.total_length_cm:.2f}<br>"
            f"<b>Ъгли на контура:</b> {_escape(turns)}<br>"
            f"<b>Подпис:</b> {_escape(self.summary.canonical_signature or '—')}</p>"
            f"{inference}"
            f"<p><b>Предупреждения:</b><br>{warnings}</p>"
        )

    def _shape_changed(self) -> None:
        selected_code = str(self.shape_combo.currentData() or "")
        is_draft = selected_code == DRAFT_CODE
        self.draft_name.setVisible(is_draft)
        self.draft_name_label.setVisible(is_draft)
        if is_draft:
            self.reason_combo.setCurrentText("Липсва MASTER форма")
            self.training_check.setChecked(self._safe_training_example)
        self.save_button.setText(
            "Запази кандидат-пакета" if is_draft else "Запази задаването"
        )
        self._refresh_accept()

    def _dimension_changed(self) -> None:
        is_manual = str(self.dimension_source_combo.currentData() or "") == DIMENSION_SOURCE_MANUAL
        self.manual_lengths_edit.setVisible(is_manual)
        self.manual_lengths_label.setVisible(is_manual)
        self._refresh_accept()

    def _resolve_dimensions(self) -> ManualDimensionResolution | None:
        source = str(self.dimension_source_combo.currentData() or "")
        try:
            resolution = resolve_manual_dimensions(
                source=source,
                declared_length_cm=self.row.get("declared_length_cm"),
                measured_segment_lengths_cm=self.summary.segment_lengths_cm,
                manual_segment_text=self.manual_lengths_edit.text(),
                expected_segment_count=self.summary.segment_count,
            )
        except ValueError as exc:
            self._dimension_error = str(exc)
            self._dimension_resolution = None
            return None
        self._dimension_error = ""
        self._dimension_resolution = resolution
        return resolution

    def _refresh_accept(self) -> None:
        selected_code = str(self.shape_combo.currentData() or "")
        shape_selected = selected_code not in {"", SELECT_SHAPE_CODE}
        geometry_ok = self.summary.connected and not self.summary.branched and self.summary.segment_count > 0
        draft_ok = selected_code != DRAFT_CODE or bool(self.draft_name.text().strip())
        confirmed = self.confirm_check.isChecked()
        dimensions = self._resolve_dimensions()
        dimension_ok = dimensions is not None and dimensions.production_length_cm > 0
        self.save_button.setEnabled(
            bool(shape_selected and geometry_ok and draft_ok and confirmed and dimension_ok)
        )

        problems: list[str] = []
        if not shape_selected:
            problems.append("Избери изрично правилната MASTER форма.")
        if not self.summary.connected:
            problems.append("Избраните обекти не образуват една логически свързана геометрия.")
        elif self.summary.inferred_composite:
            problems.append(
                "Информация: разпознато е съставно стреме — затворен контур с отделени ключове. "
                "Записването е разрешено само след визуално потвърждение."
            )
        if self.summary.branched:
            problems.append("Избраната геометрия е разклонена.")
        if selected_code == DRAFT_CODE and not self.draft_name.text().strip():
            problems.append("Въведи име за новата форма.")
        if selected_code and selected_code != DRAFT_CODE:
            shape = next((item for item in self.shapes if item.code == selected_code), None)
            if shape is not None and bool(shape.closed) != bool(self.summary.closed):
                problems.append(
                    "Внимание: избраната MASTER форма е "
                    + ("затворена" if shape.closed else "отворена")
                    + ", а избраната DXF геометрия е "
                    + ("затворена." if self.summary.closed else "отворена.")
                )

        if dimensions is None:
            self.dimension_status.setText(self._dimension_error or "Невалиден източник на размерите.")
            self.dimension_status.setStyleSheet("color:#b91c1c; font-weight:700;")
            problems.append(self._dimension_error)
        else:
            diff = dimensions.geometry_difference_cm
            manual_segments = dimensions.production_segment_lengths_cm
            segment_text = (
                " + ".join(f"{value:g}" for value in manual_segments)
                if manual_segments
                else "не са зададени — използва се общата L"
            )
            self.dimension_status.setText(
                f"Производствена L: {dimensions.production_length_cm:.2f} cm | "
                f"DXF контролна L: {dimensions.measured_geometry_length_cm:.2f} cm | "
                f"Разлика: {diff:+.2f} cm | Сегменти: {segment_text}"
            )
            material_geometry_mismatch = length_mismatch_is_material(
                dimensions.production_length_cm,
                dimensions.measured_geometry_length_cm,
            )
            declared_mismatch = (
                dimensions.declared_length_cm is not None
                and length_mismatch_is_material(
                    dimensions.declared_length_cm,
                    dimensions.production_length_cm,
                )
            )
            if material_geometry_mismatch or declared_mismatch:
                self.dimension_status.setStyleSheet("color:#9a3412; font-weight:700;")
            else:
                self.dimension_status.setStyleSheet("color:#166534; font-weight:700;")
            if material_geometry_mismatch:
                problems.append(
                    "Производствената L и измерената DXF L се различават. "
                    "Това е допустимо само при схематичен чертеж или изрична ръчна корекция."
                )
            if declared_mismatch:
                problems.append(
                    "Ръчно зададената производствена L не съвпада с L от надписа. "
                    "Провери размерните текстове и остави бележка за корекцията."
                )

        self.warning_label.setText(" ".join(value for value in problems if value))

    def _accept_assignment(self) -> None:
        self._refresh_accept()
        if not self.save_button.isEnabled() or self._dimension_resolution is None:
            QMessageBox.warning(self, "Задай форма", "Попълни и потвърди задължителните данни.")
            return
        code = str(self.shape_combo.currentData() or "")
        if code in {"", SELECT_SHAPE_CODE}:
            QMessageBox.warning(self, "Задай форма", "Избери изрично правилната MASTER форма.")
            return
        if code == DRAFT_CODE:
            shape_name = self.draft_name.text().strip()
            shape_code = None
            mode = "DRAFT"
        else:
            shape = next((item for item in self.shapes if item.code == code), None)
            shape_name = shape.name if shape is not None else code
            shape_code = code
            mode = "EXISTING_MASTER"
        note = self.note_edit.text().strip() or self._automatic_note(
            shape_code=shape_code,
            shape_name=shape_name,
        )
        self.result_payload = {
            "mode": mode,
            "shape_code": shape_code,
            "shape_name": shape_name,
            "reason": self.reason_combo.currentText(),
            "note": note,
            "save_training_example": self.training_check.isChecked(),
            "geometry": self.summary.to_dict(),
            "dimensions": self._dimension_resolution.to_dict(),
            "annotation": {
                "handle": str(self.row.get("annotation_handle") or ""),
                "text": str(self.row.get("annotation_text") or ""),
                "count": self.row.get("annotation_count"),
                "diameter_mm": self.row.get("diameter_mm"),
                "declared_length_cm": self.row.get("declared_length_cm"),
            },
        }
        self.accept()

    def _automatic_note(self, *, shape_code: str | None, shape_name: str) -> str:
        declared = _optional_float(self.row.get("declared_length_cm"))
        parts = [
            "Автоматична техническа бележка",
            f"MASTER={shape_code or 'НОВА ФОРМА'} ({shape_name})",
            f"DXF handles={','.join(self.summary.handles)}",
            f"сегменти={self.summary.segment_count}",
            f"DXF L={self.summary.total_length_cm:.2f} cm",
            "физически свързана="
            + ("ДА" if self.summary.physical_connected else "НЕ"),
        ]
        if declared is not None:
            parts.append(f"надпис L={declared:.2f} cm")
        gap_total = (
            getattr(self.summary, "near_gap_bridge_gap_raw", None)
            or getattr(self.summary, "gap_total_raw", 0.0)
        )
        if gap_total:
            parts.append(f"контролирано прекъсване={float(gap_total):.2f} cm")
        if self.summary.inferred_composite:
            parts.append("схематична съставна геометрия")
        return "; ".join(parts) + "."


def _optional_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
