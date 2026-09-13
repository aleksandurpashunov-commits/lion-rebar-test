from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lion_rebar.dxf.geometry_candidates import (
    GeometryCandidate,
    GeometryCandidateAnalysis,
)


_APPROVED_LABEL = "ОДОБРЕНА ВРЪЗКА — РЪЧНО"
_APPROVED_BACKGROUND = QColor("#dcfce7")
_APPROVED_FOREGROUND = QColor("#166534")


class GeometryCandidatesDialog(QDialog):
    """Modeless review window for candidate preview and manual approval."""

    candidatePreviewRequested = Signal(object)
    candidateApprovalRequested = Signal(object)

    def __init__(
        self,
        analysis: GeometryCandidateAnalysis,
        parent=None,
        approved_link: dict | None = None,
    ):
        super().__init__(parent)
        self.analysis = analysis
        self._previewed_row: int | None = None
        self._approved_row: int | None = None
        self._approved_handles = self._handles_from_link(approved_link)
        self._approved_saved_path = str((approved_link or {}).get("saved_path", ""))

        self.setWindowTitle("Геометрични кандидати — РЪЧНА ПРОВЕРКА")
        self.resize(1280, 500)
        self.setMinimumSize(780, 340)
        self.setSizeGripEnabled(True)

        header_status = (
            "ВРЪЗКАТА Е ОДОБРЕНА РЪЧНО"
            if self._approved_handles
            else analysis.status
        )
        self.header = QLabel(
            f"Надпис: {analysis.label or '—'} | Ø{_format_number(analysis.diameter_mm)} | "
            f"L={_format_number(analysis.declared_length_cm)} | {header_status}"
        )
        self.header.setWordWrap(True)
        self.header.setStyleSheet("font-weight: 600;")

        warning = QLabel(
            "Работи се на две стъпки: 1) покажи кандидата в чертежа; "
            "2) одобри връзката след визуална проверка. Одобрението записва само "
            "връзката между надписа и DXF геометрията. Позиция, форма, зона и стъпка "
            "не се извеждат автоматично."
        )
        warning.setWordWrap(True)

        self.table = QTableWidget(len(analysis.candidates), 14)
        self.table.setHorizontalHeaderLabels(
            [
                "Ранг",
                "Оценка",
                "Handles",
                "Layer",
                "Сегм.",
                "Геом. L",
                "Зададено L",
                "Δ",
                "Разстояние",
                "Ø слой",
                "Основание",
                "MASTER форма",
                "Ориентационен подпис",
                "Контрол",
            ]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(13, QHeaderView.Stretch)
        self.table.itemDoubleClicked.connect(self._preview_row)
        self._populate()

        warning_parts = list(analysis.warnings)
        if self._approved_handles:
            warning_parts = [
                warning
                for warning in warning_parts
                if not warning.startswith("Това е ранжиране, не одобрена връзка")
            ]
            warning_parts.insert(0, "Одобрената връзка е заредена от проекта.")
        warnings = QLabel(" ".join(warning_parts))
        warnings.setWordWrap(True)
        warnings.setStyleSheet("color: #8a4b08;")

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("font-weight: 600;")

        self.show_button = QPushButton("1. Покажи в чертежа")
        self.show_button.clicked.connect(self._preview_selected)

        self.approve_button = QPushButton("2. ОДОБРИ ВРЪЗКАТА")
        self.approve_button.setEnabled(False)
        self.approve_button.clicked.connect(self._approve_selected)
        self.approve_button.setStyleSheet("font-weight: 700; padding: 6px 12px;")

        close_button = QPushButton("Затвори")
        close_button.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addWidget(self.show_button)
        buttons.addWidget(self.approve_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(warning)
        layout.addWidget(self.table, 1)
        layout.addWidget(warnings)
        layout.addWidget(self.result_label)
        layout.addLayout(buttons)

        self.table.itemSelectionChanged.connect(self._selection_changed)
        if self._approved_row is not None:
            self.table.selectRow(self._approved_row)
        elif analysis.candidates:
            self.table.selectRow(0)
        self._refresh_selection_message()

    @staticmethod
    def _handles_from_link(approved_link: dict | None) -> tuple[str, ...]:
        if not isinstance(approved_link, dict):
            return ()
        raw_handles = approved_link.get("geometry_handles", [])
        if not isinstance(raw_handles, (list, tuple)):
            return ()
        return tuple(sorted(str(handle) for handle in raw_handles if str(handle)))

    @staticmethod
    def _candidate_handles(candidate: GeometryCandidate) -> tuple[str, ...]:
        return tuple(sorted(str(handle) for handle in candidate.handles if str(handle)))

    def _is_approved_candidate(self, candidate: GeometryCandidate | None) -> bool:
        return bool(
            candidate is not None
            and self._approved_handles
            and self._candidate_handles(candidate) == self._approved_handles
        )

    def _populate(self) -> None:
        for row_index, candidate in enumerate(self.analysis.candidates):
            is_approved = self._is_approved_candidate(candidate)
            values = [
                candidate.rank,
                _APPROVED_LABEL if is_approved else candidate.confidence,
                ", ".join(candidate.handles),
                candidate.layer,
                candidate.segment_count,
                candidate.geometry_length_raw,
                candidate.declared_length_cm,
                candidate.numeric_difference,
                candidate.distance_raw,
                _format_number(candidate.layer_diameter_mm),
                candidate.basis,
                (
                    " / ".join(candidate.master_ambiguous_shape_codes)
                    + " — КОНФЛИКТ"
                    if candidate.master_ambiguous_shape_codes
                    else (
                        f"{candidate.master_shape_code} — {candidate.master_shape_name}"
                        if candidate.master_shape_code
                        else "—"
                    )
                ),
                candidate.canonical_signature or "—",
                "; ".join(candidate.notes) or "—",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(_display_value(value, column))
                if column in {0, 4, 5, 6, 7, 8, 9}:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setData(Qt.UserRole, row_index)
                if is_approved:
                    item.setBackground(_APPROVED_BACKGROUND)
                    item.setForeground(_APPROVED_FOREGROUND)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row_index, column, item)
            if is_approved:
                self._approved_row = row_index

    def _refresh_selection_message(self) -> None:
        row = self.table.currentRow()
        candidate = self._candidate_for_row(row)
        if self._is_approved_candidate(candidate):
            suffix = f" Файл: {self._approved_saved_path}" if self._approved_saved_path else ""
            self.result_label.setText(
                "Тази връзка вече е записана като „ОДОБРЕНО РЪЧНО“." + suffix
            )
            self.result_label.setStyleSheet("font-weight: 700; color: #166534;")
            self.approve_button.setEnabled(False)
            return

        self.result_label.setStyleSheet("font-weight: 600;")
        if row >= 0:
            self.result_label.setText(
                "Избраният ред още не е показан. Натисни „1. Покажи в чертежа“."
            )
        else:
            self.result_label.setText(
                "Избери ред и натисни „1. Покажи в чертежа“. Прозорецът няма да се затвори."
            )
        self.approve_button.setEnabled(False)

    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        if row != self._previewed_row:
            self._previewed_row = None
        self._refresh_selection_message()

    def _candidate_for_row(self, row: int) -> GeometryCandidate | None:
        if 0 <= row < len(self.analysis.candidates):
            return self.analysis.candidates[row]
        return None

    def _preview_row(self, item: QTableWidgetItem) -> None:
        row_data = item.data(Qt.UserRole)
        try:
            row = int(row_data)
        except (TypeError, ValueError):
            row = item.row()
        self.table.selectRow(row)
        self._preview(row)

    def _preview_selected(self) -> None:
        self._preview(self.table.currentRow())

    def _preview(self, row: int) -> None:
        candidate = self._candidate_for_row(row)
        if candidate is None:
            return
        self._previewed_row = row
        self.candidatePreviewRequested.emit(candidate)

        if self._is_approved_candidate(candidate):
            self.approve_button.setEnabled(False)
            self.result_label.setText(
                f"Показана одобрена връзка: {', '.join(candidate.handles)}. "
                "Геометрията е подчертана в червено."
            )
            self.result_label.setStyleSheet("font-weight: 700; color: #166534;")
        else:
            self.approve_button.setEnabled(True)
            self.result_label.setText(
                f"Показан кандидат: {', '.join(candidate.handles)}. "
                "Геометрията е подчертана в червено в основния чертеж. "
                "Прозорецът е преместен автоматично извън зоната на кандидата."
            )
            self.result_label.setStyleSheet("font-weight: 600;")

    def place_away_from(self, global_candidate_center: QPoint) -> None:
        """Keep the review controls visible without covering the highlighted candidate."""
        screen = QGuiApplication.screenAt(global_candidate_center) or self.screen()
        if screen is None:
            return

        available = screen.availableGeometry()
        margin = 12
        target_width = min(max(900, self.width()), max(640, available.width() - 2 * margin))
        target_height = min(410, max(320, available.height() - 2 * margin))
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

    def _approve_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row != self._previewed_row:
            self.approve_button.setEnabled(False)
            self.result_label.setText("Първо покажи точно избрания ред в чертежа.")
            self.result_label.setStyleSheet("font-weight: 700; color: #991b1b;")
            return
        candidate = self._candidate_for_row(row)
        if candidate is None:
            return
        if self._is_approved_candidate(candidate):
            self._refresh_selection_message()
            return
        self.candidateApprovalRequested.emit(candidate)

    def _clear_approved_row_style(self) -> None:
        if self._approved_row is None:
            return
        previous_candidate = self._candidate_for_row(self._approved_row)
        for column in range(self.table.columnCount()):
            item = self.table.item(self._approved_row, column)
            if item is None:
                continue
            item.setData(Qt.BackgroundRole, None)
            item.setData(Qt.ForegroundRole, None)
            font = item.font()
            font.setBold(False)
            item.setFont(font)
        if previous_candidate is not None:
            confidence_item = self.table.item(self._approved_row, 1)
            if confidence_item is not None:
                confidence_item.setText(previous_candidate.confidence)

    def mark_approved(self, saved_path: str) -> None:
        row = self.table.currentRow()
        candidate = self._candidate_for_row(row)
        if candidate is None:
            self.mark_error("Одобреният ред вече не е наличен.")
            return

        self._clear_approved_row_style()
        self._approved_row = row
        self._approved_handles = self._candidate_handles(candidate)
        self._approved_saved_path = saved_path

        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None:
                continue
            item.setBackground(_APPROVED_BACKGROUND)
            item.setForeground(_APPROVED_FOREGROUND)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        confidence_item = self.table.item(row, 1)
        if confidence_item is not None:
            confidence_item.setText(_APPROVED_LABEL)

        self.header.setText(
            f"Надпис: {self.analysis.label or '—'} | Ø{_format_number(self.analysis.diameter_mm)} | "
            f"L={_format_number(self.analysis.declared_length_cm)} | ВРЪЗКАТА Е ОДОБРЕНА РЪЧНО"
        )
        self.approve_button.setEnabled(False)
        self.result_label.setText(
            "Връзката е записана като „ОДОБРЕНО РЪЧНО“. "
            f"Файл: {saved_path}"
        )
        self.result_label.setStyleSheet("font-weight: 700; color: #166534;")

    def mark_error(self, message: str) -> None:
        self.result_label.setText(f"Грешка при запис: {message}")
        self.result_label.setStyleSheet("font-weight: 700; color: #991b1b;")


def _display_value(value, column: int) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        decimals = 3 if column in {5, 6, 7, 8} else 2
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return str(value)


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")
