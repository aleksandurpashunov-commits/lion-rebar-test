from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lion_rebar.dxf.inventory import (
    RebarInventoryRow,
    RebarInventorySummary,
    export_rebar_inventory_csv,
)


class RebarInventoryDialog(QDialog):
    handleActivated = Signal(str)

    def __init__(
        self,
        rows: list[RebarInventoryRow],
        summary: RebarInventorySummary,
        default_export_path: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.rows = rows
        self.summary = summary
        self.default_export_path = default_export_path
        self.setWindowTitle("ARM_N инвентар — директни атрибути BJ/FI/L")
        self.resize(1250, 720)

        self.summary_label = QLabel(self._summary_text())
        self.summary_label.setWordWrap(True)

        self.table = QTableWidget(len(rows), 11)
        self.table.setHorizontalHeaderLabels(
            [
                "№",
                "Надпис",
                "Брой",
                "Ø mm",
                "L cm",
                "Общо m",
                "INSERT handle",
                "Layer",
                "X",
                "Y",
                "Контрол",
            ]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(10, QHeaderView.Stretch)
        self.table.itemDoubleClicked.connect(self._activate_row)
        self._populate()

        warning = QLabel(
            "Това не е списък с проектантски позиции. Всеки ред е един реален ARM_N block reference "
            "с директно прочетени BJ/FI/L. Геометрия, стъпка и зона още не са свързани."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: 600;")

        export_button = QPushButton("Експорт CSV")
        export_button.clicked.connect(self._export_csv)
        close_button = QPushButton("Затвори")
        close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(export_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(warning)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

    def _summary_text(self) -> str:
        diameters = "; ".join(
            f"Ø{_format_number(item.diameter_mm)}: {item.bar_count} бр. / "
            f"{item.total_length_m:.3f} m"
            for item in self.summary.by_diameter
        ) or "няма"
        return (
            f"ARM_N блокове: {self.summary.annotation_count} | "
            f"Пълни BJ/FI/L: {self.summary.complete_count} | "
            f"Непълни: {self.summary.incomplete_count} | "
            f"Общо пръти: {self.summary.total_bar_count} | "
            f"Обща дължина: {self.summary.total_length_m:.3f} m\n"
            f"По диаметър: {diameters}"
        )

    def _populate(self) -> None:
        for row_index, row in enumerate(self.rows):
            values = [
                row.sequence,
                row.label,
                row.count,
                row.diameter_mm,
                row.length_cm,
                row.total_length_m,
                row.parent_insert_handle,
                row.layer,
                row.x,
                row.y,
                row.status,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(_display_value(value, column))
                if column in {0, 2, 3, 4, 5, 8, 9}:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setData(Qt.UserRole, row.handle)
                self.table.setItem(row_index, column, item)

    def _activate_row(self, item: QTableWidgetItem) -> None:
        handle = str(item.data(Qt.UserRole) or "")
        if handle:
            self.handleActivated.emit(handle)
            self.accept()

    def _export_csv(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Експорт на ARM_N инвентар",
            str(self.default_export_path),
            "CSV (*.csv)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        try:
            export_rebar_inventory_csv(path, self.rows, self.summary)
        except OSError as exc:
            QMessageBox.critical(self, "Грешка при експорт", str(exc))
            return
        QMessageBox.information(
            self,
            "Експортът е готов",
            f"Записан файл:\n{path}",
        )


def _display_value(value, column: int) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        decimals = 3 if column in {5, 8, 9} else 3
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return str(value)


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
